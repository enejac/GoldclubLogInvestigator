// HttpGuiSniff.exe - READ-ONLY WinDivert sniff of Godot <-> ruleta EmbedIO (:8090).
// Reassembles TCP payloads into HTTP request/response lines for board-mapping.
//
// Usage: HttpGuiSniff.exe <durationMs> [<portsCsv=8090>]
//
// Flags: WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY (never inject/divert/drop).
// Direction (bridge = middleware listen port, usually 8090):
//   C2S = Godot (ephemeral) -> middleware :8090  (PUT /api/action, GET /api/data)
//   S2C = middleware :8090 -> Godot               (HTTP responses / free-flow JSON)

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class HttpGuiSniff
{
    private const int LAYER_NETWORK = 0;
    private const ulong WINDIVERT_FLAG_SNIFF = 0x0001;
    private const ulong WINDIVERT_FLAG_RECV_ONLY = 0x0004;
    private const int WINDIVERT_SHUTDOWN_RECV = 0x1;
    private const int WINDIVERT_SHUTDOWN_BOTH = 0x3;
    private const int ADDR_SIZE = 80;
    private const int MAX_BUF_PER_FLOW = 256 * 1024;
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

    private sealed class FlowBuf
    {
        public readonly StringBuilder Text = new StringBuilder(4096);
        public string LastInterestingHash = "";
    }

    private static void CloseOnce()
    {
        if (Interlocked.Exchange(ref s_closed, 1) != 0) { return; }
        if (s_handle != INVALID_HANDLE_VALUE && s_handle != IntPtr.Zero)
        {
            try { WinDivertShutdown(s_handle, WINDIVERT_SHUTDOWN_BOTH); } catch { }
            WinDivertClose(s_handle);
        }
    }

    private static int Main(string[] args)
    {
        if (args.Length < 1)
        {
            Console.Error.WriteLine("usage: HttpGuiSniff.exe <durationMs> [<portsCsv=8090>]");
            return 1;
        }

        int durationMs = ParseIntOr(args[0], 60000);
        if (durationMs < 1000) { durationMs = 1000; }

        int[] ports = (args.Length >= 2) ? ParsePorts(args[1]) : new[] { 8090 };
        if (ports.Length == 0) { ports = new[] { 8090 }; }

        string portCond = "";
        for (int i = 0; i < ports.Length; i++)
        {
            if (i > 0) { portCond += " or "; }
            portCond += "tcp.SrcPort == " + ports[i] + " or tcp.DstPort == " + ports[i];
        }
        // Prefer payload-bearing segments; SYN/FIN still useful for session edges.
        string filter = "loopback and tcp and (" + portCond + ")";

        bool[] isBridgePort = new bool[65536];
        foreach (int p in ports) { if (p >= 0 && p < 65536) { isBridgePort[p] = true; } }

        Console.WriteLine("HTTPGUISNIFF mode=SNIFF (read-only; never diverts/injects/drops)");
        Console.WriteLine("FILTER " + filter);
        Console.WriteLine("PORTS " + string.Join(",", Array.ConvertAll(ports, x => x.ToString())));
        Console.WriteLine("DURATION_MS " + durationMs);
        Console.WriteLine("DIRLEGEND C2S=Godot->middleware  S2C=middleware->Godot");
        Console.WriteLine("START_WALL " + DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz", CultureInfo.InvariantCulture));

        IntPtr handle = WinDivertOpen(filter, LAYER_NETWORK, 0, WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY);
        if (handle == INVALID_HANDLE_VALUE || handle == IntPtr.Zero)
        {
            Console.Error.WriteLine("WinDivertOpen FAILED GetLastError=" + Marshal.GetLastWin32Error());
            return 2;
        }
        Console.WriteLine("OPEN ok");

        s_handle = handle;
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) { e.Cancel = true; CloseOnce(); };
        AppDomain.CurrentDomain.ProcessExit += delegate { CloseOnce(); };

        const int MAX = 65535;
        byte[] recvBuf = new byte[MAX];
        byte[] recvAddr = new byte[ADDR_SIZE];
        var flows = new Dictionary<string, FlowBuf>();

        long pktCount = 0;
        long httpCount = 0;
        var sw = System.Diagnostics.Stopwatch.StartNew();
        int finished = 0;

        Thread watchdog = new Thread(delegate()
        {
            while (Interlocked.CompareExchange(ref finished, 0, 0) == 0)
            {
                if (sw.ElapsedMilliseconds > durationMs)
                {
                    try { WinDivertShutdown(handle, WINDIVERT_SHUTDOWN_RECV); } catch { }
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
                if (sw.ElapsedMilliseconds > durationMs) { break; }

                uint recvLen;
                bool ok = WinDivertRecv(handle, recvBuf, (uint)recvBuf.Length, out recvLen, recvAddr);
                if (!ok)
                {
                    int err = Marshal.GetLastWin32Error();
                    if (err == 232) { break; }
                    if (sw.ElapsedMilliseconds > durationMs) { break; }
                    Thread.Sleep(5);
                    continue;
                }

                int ipVer = (recvBuf[0] >> 4) & 0x0F;
                if (ipVer != 4) { continue; }
                int ipHdr = (recvBuf[0] & 0x0F) * 4;
                if (ipHdr < 20 || ipHdr + 20 > recvLen) { continue; }
                if (recvBuf[9] != 6) { continue; } // TCP
                int totalLen = (recvBuf[2] << 8) | recvBuf[3];

                int sport = (recvBuf[ipHdr + 0] << 8) | recvBuf[ipHdr + 1];
                int dport = (recvBuf[ipHdr + 2] << 8) | recvBuf[ipHdr + 3];
                int tcpHdr = ((recvBuf[ipHdr + 12] >> 4) & 0x0F) * 4;
                if (tcpHdr < 20 || ipHdr + tcpHdr > recvLen) { continue; }

                int payloadOff = ipHdr + tcpHdr;
                int payloadLen = totalLen - ipHdr - tcpHdr;
                if (payloadLen < 0 || payloadOff + payloadLen > recvLen)
                {
                    payloadLen = (int)recvLen - payloadOff;
                    if (payloadLen < 0) { payloadLen = 0; }
                }
                if (payloadLen <= 0) { continue; }

                string dir;
                if (isBridgePort[dport] && !isBridgePort[sport]) { dir = "C2S"; }
                else if (isBridgePort[sport] && !isBridgePort[dport]) { dir = "S2C"; }
                else { dir = "???"; }

                pktCount++;
                string flowKey = dir + "|" + sport + ">" + dport;
                FlowBuf flow;
                if (!flows.TryGetValue(flowKey, out flow))
                {
                    flow = new FlowBuf();
                    flows[flowKey] = flow;
                }

                AppendAscii(flow.Text, recvBuf, payloadOff, payloadLen);
                if (flow.Text.Length > MAX_BUF_PER_FLOW)
                {
                    flow.Text.Remove(0, flow.Text.Length - (MAX_BUF_PER_FLOW / 2));
                }

                httpCount += EmitCompleteHttp(flow, sw.ElapsedMilliseconds, sport, dport, dir);
            }
        }
        finally
        {
            Interlocked.Exchange(ref finished, 1);
            // Flush partial interesting buffers.
            foreach (KeyValuePair<string, FlowBuf> kv in flows)
            {
                string[] parts = kv.Key.Split('|');
                string dir = parts.Length > 0 ? parts[0] : "???";
                int sport = 0, dport = 0;
                if (parts.Length > 1)
                {
                    string[] sd = parts[1].Split('>');
                    if (sd.Length == 2)
                    {
                        int.TryParse(sd[0], out sport);
                        int.TryParse(sd[1], out dport);
                    }
                }
                EmitPartialInteresting(kv.Value, sw.ElapsedMilliseconds, sport, dport, dir, ref httpCount);
            }
            CloseOnce();
            Console.WriteLine("END_WALL " + DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz", CultureInfo.InvariantCulture));
            Console.WriteLine("PACKETS " + pktCount);
            Console.WriteLine("HTTP_MSGS " + httpCount);
            Console.WriteLine("CLOSE ok");
        }

        return 0;
    }

    private static int EmitCompleteHttp(FlowBuf flow, long tMs, int sport, int dport, string dir)
    {
        int emitted = 0;
        while (true)
        {
            string buf = flow.Text.ToString();
            int hdrEnd = buf.IndexOf("\r\n\r\n", StringComparison.Ordinal);
            if (hdrEnd < 0) { return emitted; }

            string head = buf.Substring(0, hdrEnd);
            int contentLen = ParseContentLength(head);
            int bodyStart = hdrEnd + 4;
            if (contentLen < 0)
            {
                // No Content-Length: emit head-only for requests; for responses wait a bit via chunk heuristic.
                if (head.StartsWith("GET ", StringComparison.Ordinal) ||
                    head.StartsWith("PUT ", StringComparison.Ordinal) ||
                    head.StartsWith("POST ", StringComparison.Ordinal) ||
                    head.StartsWith("DELETE ", StringComparison.Ordinal) ||
                    head.StartsWith("HEAD ", StringComparison.Ordinal))
                {
                    EmitHttp(tMs, sport, dport, dir, head, "");
                    emitted++;
                    flow.Text.Remove(0, bodyStart);
                    continue;
                }
                // Response without length: take whatever we have up to next HTTP/ or drain small.
                int next = FindNextHttp(buf, bodyStart);
                string body = (next > bodyStart) ? buf.Substring(bodyStart, next - bodyStart) : buf.Substring(bodyStart);
                if (!ShouldLog(head, body, flow))
                {
                    flow.Text.Remove(0, (next > bodyStart) ? next : buf.Length);
                    continue;
                }
                EmitHttp(tMs, sport, dport, dir, head, TrimBody(body));
                emitted++;
                flow.Text.Remove(0, (next > bodyStart) ? next : buf.Length);
                continue;
            }

            if (buf.Length < bodyStart + contentLen) { return emitted; }
            string bodyFull = buf.Substring(bodyStart, contentLen);
            if (ShouldLog(head, bodyFull, flow))
            {
                EmitHttp(tMs, sport, dport, dir, head, TrimBody(bodyFull));
                emitted++;
            }
            flow.Text.Remove(0, bodyStart + contentLen);
        }
    }

    private static void EmitPartialInteresting(FlowBuf flow, long tMs, int sport, int dport, string dir, ref long httpCount)
    {
        string buf = flow.Text.ToString().Trim();
        if (buf.Length < 8) { return; }
        if (!(buf.StartsWith("GET ", StringComparison.Ordinal) ||
              buf.StartsWith("PUT ", StringComparison.Ordinal) ||
              buf.StartsWith("POST ", StringComparison.Ordinal) ||
              buf.StartsWith("HTTP/", StringComparison.Ordinal)))
        {
            return;
        }
        int hdrEnd = buf.IndexOf("\r\n\r\n", StringComparison.Ordinal);
        string head = hdrEnd >= 0 ? buf.Substring(0, hdrEnd) : buf;
        string body = hdrEnd >= 0 ? buf.Substring(hdrEnd + 4) : "";
        if (!ShouldLog(head, body, flow)) { return; }
        EmitHttp(tMs, sport, dport, dir, head, TrimBody(body));
        httpCount++;
    }

    private static bool ShouldLog(string head, string body, FlowBuf flow)
    {
        string first = FirstLine(head);
        bool isPut = first.StartsWith("PUT ", StringComparison.OrdinalIgnoreCase);
        bool isPost = first.StartsWith("POST ", StringComparison.OrdinalIgnoreCase);
        if (isPut || isPost) { return true; }

        string joined = head + "\n" + body;
        if (ContainsInteresting(joined))
        {
            string hash = StableHash(joined);
            if (hash == flow.LastInterestingHash) { return false; }
            flow.LastInterestingHash = hash;
            return true;
        }

        // Log non-trivial GET /api/action* requests; skip pure age polls unless body interesting.
        if (first.StartsWith("GET ", StringComparison.OrdinalIgnoreCase) &&
            first.IndexOf("/api/action", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }
        return false;
    }

    private static bool ContainsInteresting(string s)
    {
        string u = s.ToLowerInvariant();
        string[] keys = {
            "setchip", "menucommands", "paytable", "neighbours", "neighbor",
            "bet", "stake", "credit", "meter", "chip", "spin", "layout",
            "roulette", "touch", "place", "clear", "cancel", "repeat"
        };
        for (int i = 0; i < keys.Length; i++)
        {
            if (u.IndexOf(keys[i], StringComparison.Ordinal) >= 0) { return true; }
        }
        return false;
    }

    private static void EmitHttp(long tMs, int sport, int dport, string dir, string head, string body)
    {
        string first = FirstLine(head);
        string method = "";
        string path = "";
        string status = "";
        if (first.StartsWith("HTTP/", StringComparison.OrdinalIgnoreCase))
        {
            string[] p = first.Split(new[] { ' ' }, 3, StringSplitOptions.RemoveEmptyEntries);
            if (p.Length >= 2) { status = p[1]; }
        }
        else
        {
            string[] p = first.Split(new[] { ' ' }, 3, StringSplitOptions.RemoveEmptyEntries);
            if (p.Length >= 2) { method = p[0]; path = p[1]; }
        }

        string wall = DateTimeOffset.Now.ToString("HH:mm:ss.fff", CultureInfo.InvariantCulture);
        Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "HTTP t={0,7} wall={1} dir={2} sport={3} dport={4} method={5} status={6} path={7} body={8}",
            tMs, wall, dir, sport, dport,
            EscapeField(method), EscapeField(status), EscapeField(path), EscapeField(body)));
    }

    private static string TrimBody(string body)
    {
        if (body == null) { return ""; }
        body = body.Replace("\r", "\\r").Replace("\n", "\\n");
        if (body.Length > 1200) { body = body.Substring(0, 1200) + "..."; }
        return body;
    }

    private static string EscapeField(string s)
    {
        if (string.IsNullOrEmpty(s)) { return "-"; }
        return s.Replace(" ", "%20");
    }

    private static string FirstLine(string head)
    {
        int i = head.IndexOf('\n');
        if (i < 0) { return head.Trim(); }
        return head.Substring(0, i).TrimEnd('\r');
    }

    private static int ParseContentLength(string head)
    {
        string[] lines = head.Split(new[] { "\r\n" }, StringSplitOptions.None);
        for (int i = 0; i < lines.Length; i++)
        {
            string line = lines[i];
            if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase))
            {
                string v = line.Substring("Content-Length:".Length).Trim();
                int n;
                if (int.TryParse(v, out n) && n >= 0) { return n; }
            }
        }
        return -1;
    }

    private static int FindNextHttp(string buf, int from)
    {
        int best = -1;
        string[] marks = { "\r\nGET ", "\r\nPUT ", "\r\nPOST ", "\r\nHTTP/" };
        for (int i = 0; i < marks.Length; i++)
        {
            int at = buf.IndexOf(marks[i], from, StringComparison.Ordinal);
            if (at >= 0 && (best < 0 || at < best)) { best = at + 2; }
        }
        return best;
    }

    private static void AppendAscii(StringBuilder sb, byte[] b, int off, int len)
    {
        for (int i = 0; i < len; i++)
        {
            byte v = b[off + i];
            if (v == 9 || v == 10 || v == 13 || (v >= 32 && v < 127))
            {
                sb.Append((char)v);
            }
            else
            {
                sb.Append('.');
            }
        }
    }

    private static string StableHash(string s)
    {
        unchecked
        {
            int h = 23;
            for (int i = 0; i < s.Length; i++) { h = h * 31 + s[i]; }
            return h.ToString("X8", CultureInfo.InvariantCulture);
        }
    }

    private static int ParseIntOr(string s, int fallback)
    {
        int v;
        return int.TryParse(s, out v) ? v : fallback;
    }

    private static int[] ParsePorts(string csv)
    {
        string[] parts = csv.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        var list = new List<int>();
        foreach (string p in parts)
        {
            int v;
            if (int.TryParse(p.Trim(), out v) && v > 0 && v < 65536) { list.Add(v); }
        }
        return list.ToArray();
    }
}
