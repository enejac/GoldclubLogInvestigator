/*
 * WdPollInject.cs  --  WinDivert loopback HOST-POLL injector for the CommCtrlSAS
 * SAS bus (port 31150). It is the MIRROR IMAGE of WdRespond.cs:
 *   * WdRespond ANSWERS host polls (slave role) -- and on .171 saw none.
 *   * WdPollInject ORIGINATES host polls (host role) -- it injects the verbatim
 *     0x1B-framed general polls 1B81 / 1B80 INTO the live server->client stream
 *     CommCtrlSAS:31150 -> Aurum:<ephemeral>, the same direction CommCtrlSAS uses
 *     to relay real serial polls on the working .90 cabinet.
 *
 * It is forked from ButtonInject.cs (timed multi-frame splice with seq/ack-delta
 * + drain discipline) but is TIME-DRIVEN by a fixed cadence (default 200ms) rather
 * than a schedule list, because the .171 poll connection is mostly idle and the
 * cadence must be held independently of when real packets arrive.
 *
 * SPLICE DISCIPLINE (identical math to ButtonInject / WdRespond)
 * -------------------------------------------------------------
 *   server->client (31150 -> Aurum) : SeqNum += delta   (our injected bytes shift
 *                                      every subsequent real server byte forward)
 *   client->server (Aurum -> 31150) : AckNum -= delta   (so CommCtrlSAS only ever
 *                                      sees acks for bytes it really sent)
 * Each injected poll grows delta by its frame length (2 bytes). On exit we DRAIN
 * delta back to 0 by swallowing real server->client payload + forging acks (like
 * ButtonInject). On the idle 31150 poll connection the server emits no payload to
 * swallow, so drain is BEST-EFFORT: if it cannot complete in the reserve window we
 * still release cleanly via CloseOnce() (a left-over delta only RSTs the SAS link,
 * which is recoverable -- the documented socket-reconnect churn).
 *
 * HARD RULE: VERBATIM BYTES ONLY. The poll frames are 1B81 / 1B80, taken verbatim
 * from the .90 capture. This tool invents NO other poll/long-poll/CRC byte.
 * General polls are single SAS bytes; 0x1B is CommCtrlSAS's bridge framing prefix;
 * there is no CRC on these.
 *
 * MODES
 *   * passthru : divert the connection and forward every packet unchanged (delta
 *                stays 0). Proves the divert/rewrite/forward is safe. Injects nothing.
 *   * poll     : additionally inject the alternating poll cadence server->client.
 *
 * WinDivert hygiene (identical policy to WdInject / WdRespond)
 *   * Idempotent CloseOnce() (WinDivertShutdown + Close) from finally / Ctrl-C /
 *     ProcessExit. NEVER sc stop/delete per run (that wedges STOP_PENDING); the
 *     driver auto-unloads on last handle close (orchestrator -RemoveDriver path).
 *
 * Build (x64, .NET Framework):
 *   csc /platform:x64 /optimize+ /out:WdPollInject.exe WdPollInject.cs
 * Requires WinDivert.dll + WinDivert64.sys next to the exe at runtime.
 *
 * Usage:
 *   WdPollInject.exe <mode=passthru|poll> <ephemPort> <runSeconds> [intervalMs=200]
 *                    [drainReserveSec=3] [pollFrames=1B81,1B80] [serverPort=31150]
 */

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Threading;

internal static class WdPollInject
{
    private const int LAYER_NETWORK = 0;
    private const ulong FLAG_NONE = 0;
    private const ulong NO_IP_CHECKSUM = 1;
    private const int ADDR_SIZE = 80;
    private const int WINDIVERT_SHUTDOWN_BOTH = 0x3;
    private static readonly IntPtr INVALID = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential, Size = ADDR_SIZE)]
    private struct WinDivertAddress
    {
        public long Timestamp;
        public uint Flags;
        public uint Reserved2;
        public ulong U0, U1, U2, U3, U4, U5, U6, U7;
    }

    [DllImport("WinDivert.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr WinDivertOpen(string filter, int layer, short priority, ulong flags);
    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertRecv(IntPtr handle, byte[] pPacket, uint packetLen, out uint recvLen, ref WinDivertAddress addr);
    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertSend(IntPtr handle, byte[] pPacket, uint packetLen, out uint sendLen, ref WinDivertAddress addr);
    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertHelperCalcChecksums(byte[] pPacket, uint packetLen, ref WinDivertAddress addr, ulong flags);
    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertShutdown(IntPtr handle, int how);
    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertClose(IntPtr handle);

    private static IntPtr _handle = INVALID;
    private static int _closed = 0;
    private static volatile bool _running = true;

    private static int _serverPort = 31150;   // CommCtrlSAS (server) side of the poll conn
    private static int _ephem = 0;             // Aurum (client) ephemeral port

    // Shared splice state (guarded by _sync because the inject thread and the recv
    // thread both touch it).
    private static readonly object _sync = new object();
    private static long _delta = 0;

    // Server->client (CommCtrlSAS:31150 -> Aurum) template + live state, learned from
    // observed traffic. Never fabricated.
    private static bool _haveS2c = false;
    private static byte[] _s2cHdr;             // IP+TCP header of an S2C frame
    private static int _s2cIpLen = 20, _s2cTcpLen = 20;
    private static WinDivertAddress _s2cAddr;
    private static uint _serverSndNxt = 0;     // real server SND.NXT (max seq+payLen seen S2C)
    private static bool _haveServerSnd = false;
    private static int _serverWindow = 8192;   // last server advertised window
    private static uint _s2cAckField = 0;      // last ack value the server sent (its view of client seq)

    // Client->server (Aurum -> 31150) template + live state (needed to forge acks on
    // Aurum's behalf during drain).
    private static bool _haveC2s = false;
    private static byte[] _c2sHdr;
    private static int _c2sIpLen = 20, _c2sTcpLen = 20;
    private static WinDivertAddress _c2sAddr;
    private static uint _clientSndNxt = 0;      // Aurum's SND.NXT (max cseq+payLen seen C2S)
    private static bool _haveClient = false;
    private static int _clientWindow = 8192;

    private static volatile bool _draining = false;

    private static void CloseOnce()
    {
        if (Interlocked.Exchange(ref _closed, 1) != 0) { return; }
        if (_handle != INVALID && _handle != IntPtr.Zero)
        {
            try { WinDivertShutdown(_handle, WINDIVERT_SHUTDOWN_BOTH); } catch { }
            WinDivertClose(_handle);
        }
    }

    private static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("usage: WdPollInject.exe <passthru|poll> <ephemPort> <runSeconds> [intervalMs=200] [drainReserveSec=3] [pollFrames=1B81,1B80] [serverPort=31150]");
            return 2;
        }

        string mode = args[0].Trim().ToLowerInvariant();
        if (mode != "passthru" && mode != "poll")
        {
            Console.Error.WriteLine("bad mode '" + args[0] + "' (expected passthru|poll)");
            return 2;
        }

        if (!int.TryParse(args[1], out _ephem) || _ephem < 1 || _ephem > 65535)
        {
            Console.Error.WriteLine("bad ephemPort '" + args[1] + "' (expected 1..65535)");
            return 2;
        }

        int runSeconds = ParseIntOr(args[2], 30);
        if (runSeconds < 1) { runSeconds = 1; }

        int intervalMs = args.Length >= 4 ? ParseIntOr(args[3], 200) : 200;
        if (intervalMs < 20) { intervalMs = 20; }        // never poll faster than 20ms
        int drainReserveSec = args.Length >= 5 ? ParseIntOr(args[4], 3) : 3;
        if (drainReserveSec < 0) { drainReserveSec = 0; }
        if (drainReserveSec > runSeconds - 1) { drainReserveSec = Math.Max(0, runSeconds - 1); }

        string framesSpec = args.Length >= 6 ? args[5] : "1B81,1B80";
        if (args.Length >= 7) { _serverPort = ParseIntOr(args[6], 31150); }
        if (_serverPort < 1 || _serverPort > 65535) { _serverPort = 31150; }

        // Parse the VERBATIM poll frames. We accept ONLY the 0x1B-framed general polls
        // 1B81 / 1B80 (the byte facts from the .90 capture). Anything else is refused so
        // this tool can never be coaxed into emitting an invented SAS byte.
        var frames = new List<byte[]>();
        var frameLabels = new List<string>();
        foreach (var part in framesSpec.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string h = part.Trim().ToUpperInvariant();
            if (h != "1B81" && h != "1B80")
            {
                Console.Error.WriteLine("REFUSE: poll frame '" + part + "' is not a verbatim general poll (only 1B81 / 1B80 allowed).");
                return 3;
            }
            frames.Add(HexToBytes(h));
            frameLabels.Add(h);
        }
        if (frames.Count == 0)
        {
            Console.Error.WriteLine("REFUSE: no poll frames given (expected e.g. 1B81,1B80).");
            return 3;
        }

        string filter = string.Format(CultureInfo.InvariantCulture,
            "loopback and tcp and ((tcp.SrcPort == {0} and tcp.DstPort == {1}) or (tcp.SrcPort == {1} and tcp.DstPort == {0}))",
            _serverPort, _ephem);

        Log("WDPOLLINJECT mode={0} serverPort={1} ephem={2} runSeconds={3} intervalMs={4} drainReserveSec={5}",
            mode, _serverPort, _ephem, runSeconds, intervalMs, drainReserveSec);
        Log("POLL FRAMES (verbatim) = {0}", string.Join(",", frameLabels.ToArray()));
        Log("FILTER {0}", filter);
        if (mode == "passthru") { Log("[*] passthru: forwards every packet unchanged (delta stays 0); injects nothing."); }
        else { Log("[*] poll: injects the alternating cadence server->client every {0}ms; maintains delta + ack rewrite; drains on exit.", intervalMs); }

        _handle = WinDivertOpen(filter, LAYER_NETWORK, 0, FLAG_NONE);
        if (_handle == INVALID || _handle == IntPtr.Zero)
        {
            Log("FATAL WinDivertOpen failed err={0}", Marshal.GetLastWin32Error());
            return 1;
        }
        Log("OPEN ok");

        Console.CancelKeyPress += delegate (object s, ConsoleCancelEventArgs e) { e.Cancel = true; _running = false; CloseOnce(); };
        AppDomain.CurrentDomain.ProcessExit += delegate { CloseOnce(); };

        var sw = Stopwatch.StartNew();
        int drainStartMs = (runSeconds - drainReserveSec) * 1000;

        long injected = 0, s2c = 0, c2s = 0, swallowed = 0, s2cTemplates = 0, c2sTemplates = 0;
        int frameIdx = 0;

        // Watchdog: hard stop at runSeconds (wakes a blocked Recv via Close).
        var watchdog = new Thread(delegate ()
        {
            for (int i = 0; i < runSeconds && _running; i++) { Thread.Sleep(1000); }
            _running = false;
            CloseOnce();
        });
        watchdog.IsBackground = true;
        watchdog.Start();

        // Inject cadence thread (poll mode only). Time-driven so the cadence is held even
        // when the connection is otherwise idle (no real S2C packets to anchor on).
        Thread injector = null;
        if (mode == "poll")
        {
            injector = new Thread(delegate ()
            {
                long nextTick = sw.ElapsedMilliseconds + intervalMs;
                while (_running)
                {
                    long now = sw.ElapsedMilliseconds;
                    if (now < nextTick)
                    {
                        int sleep = (int)Math.Min(50, nextTick - now);
                        if (sleep > 0) { Thread.Sleep(sleep); }
                        continue;
                    }
                    nextTick += intervalMs;

                    if (!_running || _draining) { continue; }

                    lock (_sync)
                    {
                        if (!_haveS2c || !_haveServerSnd)
                        {
                            // No S2C template/seq learned yet -- cannot inject safely.
                            continue;
                        }
                        byte[] frame = frames[frameIdx % frames.Count];
                        string label = frameLabels[frameIdx % frameLabels.Count];
                        frameIdx++;

                        uint injSeq = (uint)(_serverSndNxt + (uint)_delta);
                        uint injAck = _haveClient ? _clientSndNxt : _s2cAckField;
                        SendS2cData(injSeq, injAck, _serverWindow, frame, 0, frame.Length);
                        _delta += frame.Length;
                        injected++;
                        Log("INJECT #{0} frame={1} clientSeq={2} bytes={3} delta={4} at {5}ms",
                            injected, label, injSeq, frame.Length, _delta, sw.ElapsedMilliseconds);
                    }
                }
            });
            injector.IsBackground = true;
            injector.Start();
        }

        var packet = new byte[65535];
        var addr = new WinDivertAddress();

        try
        {
            while (_running)
            {
                uint recvLen;
                if (!WinDivertRecv(_handle, packet, (uint)packet.Length, out recvLen, ref addr))
                {
                    if (!_running) { break; }
                    continue;
                }

                int ipLen = (packet[0] & 0x0F) * 4;
                if (ipLen < 20 || ipLen + 20 > recvLen) { ForwardUnchanged(packet, recvLen, ref addr); continue; }
                int t = ipLen;
                int srcPort = ReadU16(packet, t + 0);
                int dstPort = ReadU16(packet, t + 2);
                int tcpLen = ((packet[t + 12] >> 4) & 0x0F) * 4;
                if (tcpLen < 20 || ipLen + tcpLen > recvLen) { ForwardUnchanged(packet, recvLen, ref addr); continue; }
                int totalLen = ReadU16(packet, 2);
                int payLen = totalLen - ipLen - tcpLen;
                if (payLen < 0) { payLen = (int)recvLen - ipLen - tcpLen; if (payLen < 0) { payLen = 0; } }
                byte flags = packet[t + 13];
                bool isSyn = (flags & 0x02) != 0;

                bool s2cDir = (srcPort == _serverPort && dstPort == _ephem);

                // Enter drain near the end so a non-zero delta gets a chance to settle.
                if (!_draining && sw.ElapsedMilliseconds >= drainStartMs)
                {
                    lock (_sync) { if (_delta > 0) { _draining = true; Log("DRAIN window begin (delta={0}); ceasing new injects", _delta); } }
                }

                if (s2cDir)
                {
                    s2c++;
                    lock (_sync)
                    {
                        uint seq = ReadU32(packet, t + 4);
                        uint ackf = ReadU32(packet, t + 8);
                        uint realNext = (uint)(seq + (uint)payLen);
                        if (!_haveServerSnd || Seq32Ge(realNext, _serverSndNxt)) { _serverSndNxt = realNext; _haveServerSnd = true; }
                        _serverWindow = ReadU16(packet, t + 14);
                        _s2cAckField = ackf;
                        if (!_haveS2c && !isSyn)
                        {
                            _s2cIpLen = ipLen; _s2cTcpLen = tcpLen;
                            _s2cHdr = new byte[ipLen + tcpLen];
                            Array.Copy(packet, 0, _s2cHdr, 0, ipLen + tcpLen);
                            _s2cAddr = addr; _haveS2c = true; s2cTemplates++;
                            Log("S2C template captured seq={0} ack={1} payLen={2} win={3}", seq, ackf, payLen, _serverWindow);
                        }

                        if (_draining && _delta > 0 && payLen > 0 && _haveC2s)
                        {
                            // Swallow up to delta real server bytes (don't forward them to
                            // Aurum -- it already received our injected bytes in their place),
                            // ack them back to the server, and forward any tail at seq+delta.
                            int swallow = (int)Math.Min((long)payLen, _delta);
                            int tail = payLen - swallow;
                            InjectAckToServer(_clientSndNxt, realNext, _clientWindow);
                            if (tail > 0)
                            {
                                int off = ipLen + tcpLen + swallow;
                                SendS2cData((uint)(seq + (uint)_delta), ackf, _serverWindow, packet, off, tail);
                            }
                            _delta -= swallow;
                            swallowed += swallow;
                            if (_delta == 0)
                            {
                                Log("DRAIN complete -> connection back in sync (swallowed={0})", swallowed);
                                _running = false; CloseOnce();
                            }
                            continue;
                        }

                        // Normal forward with seq rewrite.
                        if (_delta != 0) { WriteU32(packet, t + 4, (uint)(seq + (uint)_delta)); }
                    }
                    ForwardUnchanged(packet, recvLen, ref addr);
                }
                else
                {
                    c2s++;
                    lock (_sync)
                    {
                        uint cseq = ReadU32(packet, t + 4);
                        uint cnext = (uint)(cseq + (uint)payLen);
                        if (!_haveClient || Seq32Ge(cnext, _clientSndNxt)) { _clientSndNxt = cnext; _haveClient = true; }
                        _clientWindow = ReadU16(packet, t + 14);
                        if (!_haveC2s && !isSyn)
                        {
                            _c2sIpLen = ipLen; _c2sTcpLen = tcpLen;
                            _c2sHdr = new byte[ipLen + tcpLen];
                            Array.Copy(packet, 0, _c2sHdr, 0, ipLen + tcpLen);
                            _c2sAddr = addr; _haveC2s = true; c2sTemplates++;
                            Log("C2S template captured seq={0} win={1}", cseq, _clientWindow);
                        }
                        // Rewrite Aurum's ack DOWN by delta so CommCtrlSAS only sees acks for
                        // bytes it really sent.
                        if (_delta != 0)
                        {
                            uint ack = ReadU32(packet, t + 8);
                            WriteU32(packet, t + 8, (uint)(ack - (uint)_delta));
                        }
                    }
                    ForwardUnchanged(packet, recvLen, ref addr);
                }
            }
        }
        catch (Exception ex) { Log("EXCEPTION {0}", ex.Message); }
        finally
        {
            _running = false;
            CloseOnce();
            long finalDelta; lock (_sync) { finalDelta = _delta; }
            Log("DONE mode={0} injected={1} s2c={2} c2s={3} swallowed={4} s2cTemplates={5} c2sTemplates={6} finalDelta={7}",
                mode, injected, s2c, c2s, swallowed, s2cTemplates, c2sTemplates, finalDelta);
            if (finalDelta != 0) { Log("WARNING: stopped with delta={0} -- the 31150 SAS link may RST/reconnect (recoverable churn).", finalDelta); }
            else { Log("Connection left in sync (delta=0)."); }
            Log("CLOSE ok");
        }
        return 0;
    }

    private static void ForwardUnchanged(byte[] packet, uint len, ref WinDivertAddress addr)
    {
        WinDivertHelperCalcChecksums(packet, len, ref addr, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, packet, len, out sent, ref addr))
        {
            Log("WARN forward failed err={0}", Marshal.GetLastWin32Error());
        }
    }

    // Inject a server->client (CommCtrlSAS:31150 -> Aurum) data segment carrying the
    // verbatim poll bytes at the given client-space seq. Uses the learned S2C template.
    private static void SendS2cData(uint seq, uint ack, int window, byte[] src, int srcOff, int payLen)
    {
        int ip = _s2cIpLen, t = ip;
        int total = ip + _s2cTcpLen + payLen;
        var pkt = new byte[total];
        Array.Copy(_s2cHdr, 0, pkt, 0, ip + _s2cTcpLen);
        WriteU16(pkt, 2, (uint)total);                 // IP total length
        WriteU16(pkt, 4, (uint)(ReadU16(pkt, 4) + 1)); // bump IP id
        WriteU16(pkt, 10, 0);                          // zero IP checksum (recomputed)
        WriteU32(pkt, t + 4, seq);
        WriteU32(pkt, t + 8, ack);
        pkt[t + 12] = (byte)((_s2cTcpLen / 4) << 4);   // data offset
        pkt[t + 13] = 0x18;                            // PSH|ACK
        WriteU16(pkt, t + 14, (uint)window);
        WriteU16(pkt, t + 16, 0);                      // zero TCP checksum (recomputed)
        WriteU16(pkt, t + 18, 0);                      // urgent ptr
        Array.Copy(src, srcOff, pkt, ip + _s2cTcpLen, payLen);
        var a = _s2cAddr;
        WinDivertHelperCalcChecksums(pkt, (uint)total, ref a, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, pkt, (uint)total, out sent, ref a))
        {
            Log("WARN s2c-data send failed err={0}", Marshal.GetLastWin32Error());
        }
    }

    // Forge a bare ACK from Aurum -> CommCtrlSAS:31150 (used during drain to ack the
    // real server bytes we swallowed). Uses the learned C2S template.
    private static void InjectAckToServer(uint seq, uint ack, int window)
    {
        int ip = _c2sIpLen, t = ip;
        int total = ip + _c2sTcpLen;
        var pkt = new byte[total];
        Array.Copy(_c2sHdr, 0, pkt, 0, ip + _c2sTcpLen);
        WriteU16(pkt, 2, (uint)total);
        WriteU16(pkt, 4, (uint)(ReadU16(pkt, 4) + 1));
        WriteU16(pkt, 10, 0);
        WriteU32(pkt, t + 4, seq);
        WriteU32(pkt, t + 8, ack);
        pkt[t + 12] = (byte)((_c2sTcpLen / 4) << 4);
        pkt[t + 13] = 0x10;                            // ACK
        WriteU16(pkt, t + 14, (uint)window);
        WriteU16(pkt, t + 16, 0);
        WriteU16(pkt, t + 18, 0);
        var a = _c2sAddr;
        WinDivertHelperCalcChecksums(pkt, (uint)total, ref a, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, pkt, (uint)total, out sent, ref a))
        {
            Log("WARN ack-to-server send failed err={0}", Marshal.GetLastWin32Error());
        }
    }

    private static bool Seq32Ge(uint a, uint b) { return (int)(a - b) >= 0; }
    private static int ReadU16(byte[] b, int o) { return (b[o] << 8) | b[o + 1]; }
    private static uint ReadU32(byte[] b, int o) { return ((uint)b[o] << 24) | ((uint)b[o + 1] << 16) | ((uint)b[o + 2] << 8) | b[o + 3]; }
    private static void WriteU16(byte[] b, int o, uint v) { b[o] = (byte)((v >> 8) & 0xFF); b[o + 1] = (byte)(v & 0xFF); }
    private static void WriteU32(byte[] b, int o, uint v) { b[o] = (byte)((v >> 24) & 0xFF); b[o + 1] = (byte)((v >> 16) & 0xFF); b[o + 2] = (byte)((v >> 8) & 0xFF); b[o + 3] = (byte)(v & 0xFF); }

    private static int ParseIntOr(string s, int fallback) { int v; return int.TryParse(s, out v) ? v : fallback; }

    private static byte[] HexToBytes(string hex)
    {
        hex = hex.Trim();
        if (hex.Length % 2 != 0) { throw new ArgumentException("odd hex length"); }
        var o = new byte[hex.Length / 2];
        for (int i = 0; i < o.Length; i++) { o[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16); }
        return o;
    }

    private static void Log(string fmt, params object[] a)
    {
        Console.WriteLine("[{0:HH:mm:ss.fff}] {1}", DateTime.Now, string.Format(CultureInfo.InvariantCulture, fmt, a));
        Console.Out.Flush();
    }
}
