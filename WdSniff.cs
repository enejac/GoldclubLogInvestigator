// WdSniff.exe - READ-ONLY, direction-labeled WinDivert sniffer for the CommCtrlSAS
// loopback SAS bus (ports 31100/31101/31150). Stage 0 recon: it NEVER injects, NEVER
// modifies, NEVER drops a single packet.
//
// Usage: WdSniff.exe <durationMs> [<portsCsv=31100,31101,31150>]
//
// It opens WinDivert in SNIFF mode (WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY).
// SNIFF means the kernel hands us a COPY of each matching packet while the ORIGINAL
// continues on its way untouched, and RECV_ONLY makes WinDivertSend impossible from
// this handle -- so this tool physically cannot disrupt the live bus (no divert, no
// re-inject, no possibility of an accidental write). This is the zero-disruption
// alternative to the divert-and-reinject pattern in WdInject.cs.
//
// For every captured TCP packet it prints one time-ordered line with:
//   wall clock, ms-since-start, DIRECTION label, src/dst port, TCP seq/ack, flags,
//   payload length, and the FULL payload hex (0x1B bridge framing left intact).
//
// Direction is derived from the bridge port roles (CommCtrlSAS owns/listens on
// 31100/31101/31150; Aurum connects as the client from an ephemeral port):
//   * S2C = server->client = CommCtrlSAS -> Aurum  (a packet whose SOURCE port is a
//           bridge port). This is the SAS HOST POLL direction.
//   * C2S = client->server = Aurum -> CommCtrlSAS  (a packet whose DEST port is a
//           bridge port). This is the SAS SLAVE RESPONSE direction.
//
// The open handle is tracked statically and released exactly once (CloseOnce) from the
// finally, Ctrl-C, and ProcessExit, mirroring WdInject.cs. WinDivert installs its
// kernel-driver service on open and unloads it when the LAST handle closes, so a clean
// release here keeps the driver reusable (never wedged). We NEVER sc-stop / sc-delete.

using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Threading;

internal static class WdSniff
{
    private const int LAYER_NETWORK = 0;

    // WinDivert 2.2 open flags.
    private const ulong WINDIVERT_FLAG_SNIFF = 0x0001;
    private const ulong WINDIVERT_FLAG_RECV_ONLY = 0x0004;

    // WinDivertShutdown "how" values.
    private const int WINDIVERT_SHUTDOWN_RECV = 0x1;
    private const int WINDIVERT_SHUTDOWN_BOTH = 0x3;

    private const int ADDR_SIZE = 80;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    [DllImport("WinDivert.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr WinDivertOpen(string filter, int layer, short priority, ulong flags);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertRecv(IntPtr handle, byte[] pPacket, uint packetLen, out uint pRecvLen, byte[] pAddr);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertShutdown(IntPtr handle, int how);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertClose(IntPtr handle);

    private static IntPtr s_handle = INVALID_HANDLE_VALUE;
    private static int s_closed = 0;

    private static void CloseOnce()
    {
        if (Interlocked.Exchange(ref s_closed, 1) != 0)
        {
            return;
        }
        if (s_handle != INVALID_HANDLE_VALUE && s_handle != IntPtr.Zero)
        {
            try { WinDivertShutdown(s_handle, WINDIVERT_SHUTDOWN_BOTH); }
            catch { }
            WinDivertClose(s_handle);
        }
    }

    private static int Main(string[] args)
    {
        if (args.Length < 1)
        {
            Console.Error.WriteLine("usage: WdSniff.exe <durationMs> [<portsCsv=31100,31101,31150>]");
            return 1;
        }

        int durationMs = ParseIntOr(args[0], 60000);
        if (durationMs < 1000) { durationMs = 1000; }

        int[] ports;
        if (args.Length >= 2)
        {
            ports = ParsePorts(args[1]);
            if (ports.Length == 0) { ports = new[] { 31100, 31101, 31150 }; }
        }
        else
        {
            ports = new[] { 31100, 31101, 31150 };
        }

        // Build a port-scoped filter. We deliberately do NOT add tcp.PayloadLength > 0:
        // SYN/SYN-ACK/ACK/FIN/RST are exactly what we want to see across an Aurum
        // bring-up (the reconnect + enumeration handshake), so capture every TCP
        // segment on the bridge ports and label payload length per packet instead.
        string portCond = "";
        for (int i = 0; i < ports.Length; i++)
        {
            if (i > 0) { portCond += " or "; }
            portCond += "tcp.SrcPort == " + ports[i] + " or tcp.DstPort == " + ports[i];
        }
        string filter = "loopback and tcp and (" + portCond + ")";

        // A fast membership test for direction labeling.
        bool[] isBridgePort = new bool[65536];
        foreach (int p in ports) { if (p >= 0 && p < 65536) { isBridgePort[p] = true; } }

        Console.WriteLine("WDSNIFF mode=SNIFF (read-only copy; never diverts/injects/drops)");
        Console.WriteLine("FILTER " + filter);
        Console.WriteLine("PORTS " + string.Join(",", Array.ConvertAll(ports, x => x.ToString())));
        Console.WriteLine("DURATION_MS " + durationMs);
        Console.WriteLine("DIRLEGEND S2C=CommCtrlSAS->Aurum(host-poll)  C2S=Aurum->CommCtrlSAS(slave-response)");
        Console.WriteLine("START_WALL " + DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz", CultureInfo.InvariantCulture));

        IntPtr handle = WinDivertOpen(filter, LAYER_NETWORK, 0, WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY);
        if (handle == INVALID_HANDLE_VALUE || handle == IntPtr.Zero)
        {
            int err = Marshal.GetLastWin32Error();
            Console.Error.WriteLine("WinDivertOpen FAILED GetLastError=" + err);
            return 2;
        }
        Console.WriteLine("OPEN ok");

        s_handle = handle;
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) { e.Cancel = true; CloseOnce(); };
        AppDomain.CurrentDomain.ProcessExit += delegate { CloseOnce(); };

        const int MAX = 65535;
        byte[] recvBuf = new byte[MAX];
        byte[] recvAddr = new byte[ADDR_SIZE];

        long count = 0;
        var startWall = DateTimeOffset.Now;
        var sw = System.Diagnostics.Stopwatch.StartNew();
        int finished = 0;

        // Watchdog: after durationMs, shut down RECV so a blocked WinDivertRecv returns
        // and the loop exits cleanly. Send is irrelevant (RECV_ONLY handle).
        Thread watchdog = new Thread(delegate()
        {
            while (Interlocked.CompareExchange(ref finished, 0, 0) == 0)
            {
                if (sw.ElapsedMilliseconds > durationMs)
                {
                    try { WinDivertShutdown(handle, WINDIVERT_SHUTDOWN_RECV); }
                    catch { }
                    return;
                }
                Thread.Sleep(50);
            }
        });
        watchdog.IsBackground = true;
        watchdog.Start();

        try
        {
            while (true)
            {
                if (sw.ElapsedMilliseconds > durationMs)
                {
                    break;
                }

                uint recvLen;
                bool ok = WinDivertRecv(handle, recvBuf, (uint)recvBuf.Length, out recvLen, recvAddr);
                if (!ok)
                {
                    int err = Marshal.GetLastWin32Error();
                    // 232 ERROR_NO_DATA: queue drained after a shutdown -> done.
                    if (err == 232) { break; }
                    if (sw.ElapsedMilliseconds > durationMs) { break; }
                    // Transient; keep going.
                    Thread.Sleep(5);
                    continue;
                }

                // addr flags byte (offset 10): bit1 Outbound, bit2 Loopback.
                bool outbound = (recvAddr[10] & 0x02) != 0;
                bool loopback = (recvAddr[10] & 0x04) != 0;

                // Parse IPv4 + TCP (loopback SAS bus is IPv4 127.0.0.1).
                int ipVer = (recvBuf[0] >> 4) & 0x0F;
                if (ipVer != 4) { continue; }
                int ipHdr = (recvBuf[0] & 0x0F) * 4;
                if (ipHdr < 20 || ipHdr + 20 > recvLen) { continue; }
                int proto = recvBuf[9];
                if (proto != 6) { continue; } // TCP
                int totalLen = (recvBuf[2] << 8) | recvBuf[3];

                int sport = (recvBuf[ipHdr + 0] << 8) | recvBuf[ipHdr + 1];
                int dport = (recvBuf[ipHdr + 2] << 8) | recvBuf[ipHdr + 3];
                long seq = ReadU32BE(recvBuf, ipHdr + 4);
                long ack = ReadU32BE(recvBuf, ipHdr + 8);
                int tcpHdr = ((recvBuf[ipHdr + 12] >> 4) & 0x0F) * 4;
                if (tcpHdr < 20 || ipHdr + tcpHdr > recvLen) { continue; }
                byte flags = recvBuf[ipHdr + 13];

                int payloadOff = ipHdr + tcpHdr;
                int payloadLen = totalLen - ipHdr - tcpHdr;
                if (payloadLen < 0 || payloadOff + payloadLen > recvLen)
                {
                    payloadLen = (int)recvLen - payloadOff;
                    if (payloadLen < 0) { payloadLen = 0; }
                }

                string dir;
                if (isBridgePort[sport] && !isBridgePort[dport]) { dir = "S2C"; }
                else if (isBridgePort[dport] && !isBridgePort[sport]) { dir = "C2S"; }
                else if (isBridgePort[sport] && isBridgePort[dport]) { dir = "B2B"; } // both bridge ports (unexpected)
                else { dir = "???"; }

                string hex = payloadLen > 0 ? BytesToHex(recvBuf, payloadOff, payloadLen) : "-";

                var now = DateTimeOffset.Now;
                count++;
                // One compact, greppable, time-ordered line per packet.
                Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                    "PKT t={0,7} wall={1} dir={2} sport={3} dport={4} seq={5} ack={6} flags={7} ob={8} lb={9} len={10} hex={11}",
                    sw.ElapsedMilliseconds,
                    now.ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture),
                    dir, sport, dport, seq, ack, FlagStr(flags),
                    outbound ? 1 : 0, loopback ? 1 : 0,
                    payloadLen, hex));
            }
        }
        finally
        {
            Interlocked.Exchange(ref finished, 1);
            CloseOnce();
            Console.WriteLine("END_WALL " + DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz", CultureInfo.InvariantCulture));
            Console.WriteLine("PACKETS " + count);
            Console.WriteLine("CLOSE ok");
        }

        return 0;
    }

    private static string FlagStr(byte f)
    {
        // FIN SYN RST PSH ACK URG ECE CWR -> compact letters for set bits.
        string s = "";
        if ((f & 0x80) != 0) { s += "C"; } // CWR
        if ((f & 0x40) != 0) { s += "E"; } // ECE
        if ((f & 0x20) != 0) { s += "U"; } // URG
        if ((f & 0x10) != 0) { s += "A"; } // ACK
        if ((f & 0x08) != 0) { s += "P"; } // PSH
        if ((f & 0x04) != 0) { s += "R"; } // RST
        if ((f & 0x02) != 0) { s += "S"; } // SYN
        if ((f & 0x01) != 0) { s += "F"; } // FIN
        if (s.Length == 0) { s = "."; }
        return s;
    }

    private static string BytesToHex(byte[] b, int off, int len)
    {
        char[] c = new char[len * 2];
        const string H = "0123456789ABCDEF";
        for (int i = 0; i < len; i++)
        {
            byte v = b[off + i];
            c[i * 2] = H[(v >> 4) & 0xF];
            c[i * 2 + 1] = H[v & 0xF];
        }
        return new string(c);
    }

    private static long ReadU32BE(byte[] b, int off)
    {
        return ((long)b[off] << 24) | ((long)b[off + 1] << 16) | ((long)b[off + 2] << 8) | b[off + 3];
    }

    private static int ParseIntOr(string s, int fallback)
    {
        int v;
        return int.TryParse(s, out v) ? v : fallback;
    }

    private static int[] ParsePorts(string csv)
    {
        string[] parts = csv.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        var list = new System.Collections.Generic.List<int>();
        foreach (string p in parts)
        {
            int v;
            if (int.TryParse(p.Trim(), out v) && v > 0 && v < 65536) { list.Add(v); }
        }
        return list.ToArray();
    }
}
