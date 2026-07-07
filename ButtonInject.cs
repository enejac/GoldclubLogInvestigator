/*
 * ButtonInject.cs  --  WinDivert TCP splicer that injects a TIMED SEQUENCE of
 * button-deck frames into the CommCtrl(:30800) <-> OneHand connection.
 *
 * It is the multi-frame sibling of DallasSplice.cs. It diverts exactly ONE
 * connection (by the consumer's ephemeral port), forwards every packet while
 * maintaining a per-connection sequence offset (delta):
 *     server->client : SeqNum += delta
 *     client->server : AckNum -= delta
 *
 * Each scheduled button is injected server->client as a "<code>\r\n" frame at
 * its scheduled time (ms from start); delta grows by the frame length. After
 * the sequence, it DRAINS the offset back to 0 by swallowing an equal number
 * of real (poll-response) server->client bytes and ACKing them to CommCtrl, so
 * the connection ends perfectly in sync (no RST / no 'unexpected stop' reboot).
 *
 * A real mechanical press surfaces on this exact bus as the same "<code>\r\n"
 * server->client push (verified by capture: Spin=271, Help=263, Collect=261,
 * etc.), so an injected frame is indistinguishable to OneHand.
 *
 * Build (x64, .NET Framework):
 *   csc /platform:x64 /nologo /out:ButtonInject.exe ButtonInject.cs
 * Requires WinDivert.dll + WinDivert64.sys next to the exe at runtime.
 *
 * Usage:
 *   ButtonInject.exe <ephemPort> <runSeconds> <drainReserveSec> <schedule>
 *     schedule = comma-separated  code@ms  pairs, e.g.
 *                271@2000,271@6000,263@42000,263@45000,261@48000
 */

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class ButtonInject
{
    private const int SERVER_PORT = 30800;
    private const int LAYER_NETWORK = 0;
    private const ulong FLAG_NONE = 0;
    private const ulong NO_IP_CHECKSUM = 1;
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

    private static long _delta = 0;
    private static bool _haveS2c = false, _haveC2s = false;
    private static byte[] _s2cHdr, _c2sHdr;
    private static int _s2cIpLen = 20, _c2sIpLen = 20;
    private static WinDivertAddress _s2cAddr, _c2sAddr;
    private static uint _clientSndNxt = 0;
    private static int _clientWindow = 8192;

    private struct Sched { public int Ms; public byte[] Frame; public string Code; }

    private static int Main(string[] args)
    {
        if (args.Length < 4)
        {
            Console.Error.WriteLine("usage: ButtonInject.exe <ephemPort> <runSeconds> <drainReserveSec> <code@ms,code@ms,...>");
            return 2;
        }

        int ephem = int.Parse(args[0]);
        int runSeconds = int.Parse(args[1]);
        int drainReserveSec = int.Parse(args[2]);
        string scheduleSpec = args[3];

        var schedule = new List<Sched>();
        foreach (var part in scheduleSpec.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var kv = part.Split('@');
            if (kv.Length != 2) { Console.Error.WriteLine("bad schedule item: " + part); return 2; }
            string code = kv[0].Trim();
            int ms = int.Parse(kv[1].Trim());
            schedule.Add(new Sched { Ms = ms, Frame = Encoding.ASCII.GetBytes(code + "\r\n"), Code = code });
        }
        schedule.Sort((a, b) => a.Ms.CompareTo(b.Ms));
        int schedIdx = 0;

        string filter = string.Format(
            "loopback and tcp and ((tcp.SrcPort == {0} and tcp.DstPort == {1}) or (tcp.SrcPort == {1} and tcp.DstPort == {0}))",
            SERVER_PORT, ephem);

        Log("ephem={0} runSeconds={1} drainReserveSec={2} items={3}", ephem, runSeconds, drainReserveSec, schedule.Count);
        foreach (var s in schedule) Log("  scheduled code={0} at {1}ms", s.Code, s.Ms);

        _handle = WinDivertOpen(filter, LAYER_NETWORK, 0, FLAG_NONE);
        if (_handle == INVALID) { Log("FATAL: WinDivertOpen failed err={0}", Marshal.GetLastWin32Error()); return 1; }
        Log("WinDivert opened (divert mode). Watching connection...");

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
        bool draining = false;
        long s2c = 0, c2s = 0, drained = 0, injectedCount = 0;
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

                bool allInjected = schedIdx >= schedule.Count;
                if (allInjected && !draining && _delta > 0 && sw.ElapsedMilliseconds >= drainStartMs)
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

                    if (draining && _delta > 0 && payLen > 0 && _haveC2s)
                    {
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
                        if (_delta == 0)
                        {
                            Log("DRAIN complete -> connection back in sync (drained={0})", drained);
                            _running = false; try { WinDivertClose(_handle); } catch { } break;
                        }
                        continue;
                    }

                    // Normal forward with seq rewrite.
                    if (_delta != 0) WriteU32(packet, t + 4, (uint)(seq + (uint)_delta));
                    Reinject(packet, recvLen, ref addr);

                    // Inject every scheduled frame whose time has arrived, chained
                    // contiguously after this real segment in the client's seq space.
                    if (!draining && _haveS2c)
                    {
                        uint realNext = (uint)(seq + (uint)payLen);
                        while (schedIdx < schedule.Count && sw.ElapsedMilliseconds >= schedule[schedIdx].Ms)
                        {
                            byte[] frame = schedule[schedIdx].Frame;
                            uint injSeq = (uint)(realNext + (uint)_delta);
                            SendS2cData(injSeq, srvAck, win, frame, 0, frame.Length);
                            _delta += frame.Length;
                            injectedCount++;
                            Log("INJECT #{0} code={1} clientSeq={2} bytes={3} delta={4} at {5}ms",
                                injectedCount, schedule[schedIdx].Code, injSeq, frame.Length, _delta, sw.ElapsedMilliseconds);
                            schedIdx++;
                        }
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

        Log("DONE. s2c={0} c2s={1} injected={2} drained={3} finalDelta={4}", s2c, c2s, injectedCount, drained, _delta);
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

    private static bool Seq32Gt(uint a, uint b) { return (int)(a - b) > 0; }
    private static int ReadU16(byte[] b, int o) { return (b[o] << 8) | b[o + 1]; }
    private static uint ReadU32(byte[] b, int o) { return ((uint)b[o] << 24) | ((uint)b[o + 1] << 16) | ((uint)b[o + 2] << 8) | b[o + 3]; }
    private static void WriteU16(byte[] b, int o, uint v) { b[o] = (byte)((v >> 8) & 0xFF); b[o + 1] = (byte)(v & 0xFF); }
    private static void WriteU32(byte[] b, int o, uint v) { b[o] = (byte)((v >> 24) & 0xFF); b[o + 1] = (byte)((v >> 16) & 0xFF); b[o + 2] = (byte)((v >> 8) & 0xFF); b[o + 3] = (byte)(v & 0xFF); }

    private static void Log(string fmt, params object[] a)
    {
        Console.WriteLine("[{0:HH:mm:ss.fff}] {1}", DateTime.Now, string.Format(fmt, a));
        Console.Out.Flush();
    }
}
