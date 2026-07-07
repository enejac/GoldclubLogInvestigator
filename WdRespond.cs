/*
 * WdRespond.cs  --  WinDivert table-driven SAS-slave responder for the CommCtrlSAS
 * loopback SAS bus (ports 31100 / 31150). It is the continuous responder sibling of
 * WdInject.cs (single 0x72 inject) and ButtonInject.cs (timed button splice), forked
 * from their proven WinDivert + per-connection seq/ack-delta plumbing.
 *
 * WHAT IT DOES
 * ------------
 * The SAS bus is TWO loopback TCP connections (verified by Stage 0 capture on .90):
 *   * POLL  conn : CommCtrlSAS:31150 (server)  ->  Aurum:<ephemA> (client)
 *                  carries the 0x1B-framed host general polls 1B80 / 1B81 (PSH);
 *                  Aurum answers with a bare ACK (len 0) on THIS connection.
 *   * REPLY conn : Aurum:<ephemB> (client)     ->  CommCtrlSAS:31100 (server)
 *                  carries the slave reply 00 (PSH); CommCtrlSAS bare-ACKs it.
 * So on the working reference cabinet the slave reply 00 is emitted by AURUM onto the
 * SEPARATE 31100 connection, not as a same-stream answer to the 31150 poll.
 *
 * WdRespond impersonates the slave's reply channel: when it sees a host poll on the
 * 31150 connection whose 0x1B-stripped frame is a KNOWN table key, it injects the
 * table's VERBATIM response onto the 31100 reply connection in the Aurum->CommCtrlSAS
 * direction, while maintaining that connection's send-delta and rewriting the matching
 * server->client ACKs down by the delta (ButtonInject's splice discipline) so the link
 * stays in sync. On exit it warns if the delta is non-zero.
 *
 * HARD RULES (table-driven only; never guess bytes)
 * -------------------------------------------------
 *   * The poll->response map is supplied ENTIRELY on the command line by the
 *     orchestrator, which builds it VERBATIM from aft\captures\sas-response-table-*.json.
 *     WdRespond invents nothing. A poll not in the map -> it stays SILENT and logs
 *     UNKNOWN_POLL (passthru forwards it unchanged regardless).
 *   * It REFUSES TO START in gp/enum mode with an empty map (exit non-zero).
 *   * Single-byte replies (e.g. the 00 idle reply) need no CRC. For any multi-byte
 *     reply that must be re-framed, ReframeCrc16Kermit() ports Get-SasCrc16 from
 *     Invoke-WinDivertAft.ps1; by default replies are sent VERBATIM (the table already
 *     carries the slave's real trailing CRC).
 *
 * MODES (staged, mirrors aft/investigations/171-landing-plan.md)
 *   * passthru : observe only. Forward every packet unchanged (delta stays 0). For
 *                each host poll, log what it WOULD answer from the map. Sends nothing.
 *   * gp       : additionally answer GENERAL POLLS (single-byte 80/81 keys) only.
 *   * enum     : additionally answer ALL map keys (general polls + any long polls).
 *
 * WinDivert hygiene (identical policy to WdInject.cs / WdSniff.cs)
 *   * Idempotent CloseOnce() from finally / Ctrl-C / ProcessExit.
 *   * NEVER sc stop / sc delete per run (that wedges STOP_PENDING). The driver auto-
 *     unloads when the last handle closes; safe teardown is the orchestrator's
 *     -RemoveDriver path.
 *
 * Build (x64, .NET Framework):
 *   csc /platform:x64 /optimize+ /out:WdRespond.exe WdRespond.cs
 * Requires WinDivert.dll + WinDivert64.sys next to the exe at runtime.
 *
 * Usage:
 *   WdRespond.exe <mode=passthru|gp|enum> <runSeconds> <map> [drainReserveSec=3] [pollPort=31150] [replyPort=31100]
 *     map = comma-separated  pollHexNo1B=respHex  pairs, e.g.  81=00,80=
 *           (an empty response after '=' records a known poll that has NO reply)
 */

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Threading;

internal static class WdRespond
{
    private const int LAYER_NETWORK = 0;
    private const ulong FLAG_NONE = 0;
    private const ulong NO_IP_CHECKSUM = 1;
    private const int ADDR_SIZE = 80;
    private static readonly IntPtr INVALID = new IntPtr(-1);

    private const int WINDIVERT_SHUTDOWN_BOTH = 0x3;

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

    // Bridge ports (server side, owned by CommCtrlSAS).
    private static int _pollPort = 31150;
    private static int _replyPort = 31100;

    // REPLY-connection (31100) splice state. Aurum is the CLIENT, CommCtrlSAS the SERVER.
    // We learn a packet template + live seq/ack from observed traffic on this connection
    // and never fabricate one. _delta = bytes we have injected on Aurum's behalf.
    private static bool _haveReplyTemplate = false;
    private static byte[] _replyHdr;           // IP+TCP header bytes of a client->server (Aurum->31100) frame
    private static int _replyIpLen = 20;
    private static int _replyTcpLen = 20;
    private static WinDivertAddress _replyAddr;
    private static int _replyEphem = 0;        // Aurum's ephemeral port on the reply conn
    private static uint _aurumSndNxt = 0;       // next seq Aurum (client) would use
    private static uint _srvSeq = 0;            // CommCtrlSAS (server) seq -> our ack value
    private static int _replyWindow = 8192;
    private static long _delta = 0;

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
            Console.Error.WriteLine("usage: WdRespond.exe <passthru|gp|enum> <runSeconds> <map=81=00,80=> [drainReserveSec=3] [pollPort=31150] [replyPort=31100]");
            return 2;
        }

        string mode = args[0].Trim().ToLowerInvariant();
        if (mode != "passthru" && mode != "gp" && mode != "enum")
        {
            Console.Error.WriteLine("bad mode '" + args[0] + "' (expected passthru|gp|enum)");
            return 2;
        }
        int runSeconds = ParseIntOr(args[1], 30);
        if (runSeconds < 1) { runSeconds = 1; }

        // Parse the VERBATIM poll->response map supplied by the orchestrator from the
        // JSON response table. Keys/values are uppercase hex; an empty value = "known
        // poll, no reply" (recorded so it is explicit, never answered).
        var map = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
        var knownNoReply = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var part in args[2].Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var kv = part.Split('=');
            if (kv.Length != 2) { Console.Error.WriteLine("bad map item: " + part); return 2; }
            string k = kv[0].Trim().ToUpperInvariant();
            string v = kv[1].Trim().ToUpperInvariant();
            if (k.Length == 0) { continue; }
            if (v.Length == 0) { knownNoReply.Add(k); continue; }
            try { map[k] = HexToBytes(v); }
            catch (Exception ex) { Console.Error.WriteLine("bad map response hex for " + k + ": " + ex.Message); return 2; }
        }

        int drainReserveSec = args.Length >= 4 ? ParseIntOr(args[3], 3) : 3;
        if (drainReserveSec < 0) { drainReserveSec = 0; }
        if (args.Length >= 5) { _pollPort = ParseIntOr(args[4], 31150); }
        if (args.Length >= 6) { _replyPort = ParseIntOr(args[5], 31100); }

        // gp/enum MUST have something verbatim to answer with, or they refuse to start.
        if (mode != "passthru" && map.Count == 0)
        {
            Console.Error.WriteLine("REFUSE: mode=" + mode + " requires a non-empty poll->response map (from the response table). Aborting; nothing injected.");
            return 3;
        }

        // Which keys this mode is allowed to answer. gp: single-byte general polls only.
        // enum: every map key. passthru: none (observe only).
        var answerable = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (mode == "gp")
        {
            foreach (var k in map.Keys) { if (k.Length == 2) { answerable.Add(k); } }
        }
        else if (mode == "enum")
        {
            foreach (var k in map.Keys) { answerable.Add(k); }
        }

        string filter = "loopback and tcp and (" +
            "tcp.SrcPort == " + _pollPort + " or tcp.DstPort == " + _pollPort + " or " +
            "tcp.SrcPort == " + _replyPort + " or tcp.DstPort == " + _replyPort + ")";

        Log("WDRESPOND mode={0} runSeconds={1} pollPort={2} replyPort={3} drainReserveSec={4}", mode, runSeconds, _pollPort, _replyPort, drainReserveSec);
        Log("MAP entries={0} knownNoReply={1} answerableThisMode={2}", map.Count, knownNoReply.Count, answerable.Count);
        foreach (var kv in map) { Log("  MAP {0} -> {1}{2}", kv.Key, ToHex(kv.Value, 0, kv.Value.Length), answerable.Contains(kv.Key) ? " [answerable]" : " [observe-only this mode]"); }
        foreach (var k in knownNoReply) { Log("  MAP {0} -> (known no-reply)", k); }
        Log("FILTER {0}", filter);

        ulong openFlags = (mode == "passthru") ? FLAG_NONE : FLAG_NONE; // divert in all modes; passthru just forwards unchanged
        _handle = WinDivertOpen(filter, LAYER_NETWORK, 0, openFlags);
        if (_handle == INVALID || _handle == IntPtr.Zero)
        {
            Log("FATAL WinDivertOpen failed err={0}", Marshal.GetLastWin32Error());
            return 1;
        }
        Log("OPEN ok");

        Console.CancelKeyPress += delegate (object s, ConsoleCancelEventArgs e) { e.Cancel = true; _running = false; CloseOnce(); };
        AppDomain.CurrentDomain.ProcessExit += delegate { CloseOnce(); };

        var watchdog = new Thread(delegate ()
        {
            for (int i = 0; i < runSeconds && _running; i++) { Thread.Sleep(1000); }
            _running = false;
            CloseOnce();
        });
        watchdog.IsBackground = true;
        watchdog.Start();

        var packet = new byte[65535];
        var addr = new WinDivertAddress();
        var sw = Stopwatch.StartNew();
        int drainStartMs = (runSeconds - drainReserveSec) * 1000;
        bool draining = false;

        long pollSeen = 0, pollKnown = 0, pollUnknown = 0, injected = 0, replyTemplates = 0;

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

                bool onReplyConn = (srcPort == _replyPort || dstPort == _replyPort);
                bool onPollConn = (srcPort == _pollPort || dstPort == _pollPort);

                // ---- Learn / maintain the REPLY connection (31100) splice template ----
                if (onReplyConn)
                {
                    if (dstPort == _replyPort)
                    {
                        // Aurum (client) -> CommCtrlSAS:31100. Real client traffic.
                        uint cseq = ReadU32(packet, t + 4);
                        uint cnext = (uint)(cseq + (uint)payLen);
                        if (Seq32Ge(cnext, _aurumSndNxt) || _aurumSndNxt == 0) { _aurumSndNxt = cnext; }
                        _replyWindow = ReadU16(packet, t + 14);
                        if (!_haveReplyTemplate && (flags & 0x02) == 0) // not a SYN; usable data/ack template
                        {
                            CaptureReplyTemplate(packet, ipLen, tcpLen, ref addr, srcPort);
                            replyTemplates++;
                        }
                        // Rewrite real client seq up by delta so the server's view stays consistent.
                        if (_delta != 0) { WriteU32(packet, t + 4, (uint)(cseq + (uint)_delta)); }
                        ForwardUnchanged(packet, recvLen, ref addr);
                        continue;
                    }
                    else
                    {
                        // CommCtrlSAS:31100 (server) -> Aurum (client). Carries the ACK of
                        // our injected replies; rewrite ack DOWN by delta so Aurum only ever
                        // sees acks for bytes it really sent (prevents an unexpected-ack RST).
                        _srvSeq = ReadU32(packet, t + 4);
                        if (!_haveReplyTemplate && (flags & 0x02) == 0)
                        {
                            // Synthesize the client-side template from this server frame:
                            // client seq = server ack, client ack = server seq.
                            CaptureReplyTemplateFromServer(packet, ipLen, tcpLen, ref addr, srcPort, dstPort);
                            replyTemplates++;
                        }
                        if (_delta != 0)
                        {
                            uint sack = ReadU32(packet, t + 8);
                            WriteU32(packet, t + 8, (uint)(sack - (uint)_delta));
                        }
                        ForwardUnchanged(packet, recvLen, ref addr);
                        continue;
                    }
                }

                // ---- POLL connection (31150) ----
                if (onPollConn && srcPort == _pollPort && payLen > 0)
                {
                    // CommCtrlSAS:31150 -> Aurum : a host poll/command frame. Forward it
                    // unchanged (we never touch the poll stream), then maybe answer it on
                    // the 31100 reply connection.
                    ForwardUnchanged(packet, recvLen, ref addr);

                    string key = StripLeading1b(packet, ipLen + tcpLen, payLen);
                    pollSeen++;

                    byte[] resp;
                    bool known = map.TryGetValue(key, out resp);
                    bool noReply = knownNoReply.Contains(key);
                    if (known || noReply) { pollKnown++; } else { pollUnknown++; }

                    bool allowAnswer = answerable.Contains(key) && known && resp != null && resp.Length > 0;

                    if (!known && !noReply)
                    {
                        Log("POLL key={0} UNKNOWN_POLL (silent; not in table)", key);
                    }
                    else if (mode == "passthru")
                    {
                        Log("POLL key={0} WOULD-ANSWER {1} (passthru: sent nothing)", key, known && resp != null ? ToHex(resp, 0, resp.Length) : "(none)");
                    }
                    else if (allowAnswer && !draining)
                    {
                        if (!_haveReplyTemplate)
                        {
                            Log("POLL key={0} answerable but NO_REPLY_CHANNEL yet (no 31100 frame observed); staying silent", key);
                        }
                        else
                        {
                            InjectSlaveReply(resp);
                            injected++;
                            Log("ANSWER key={0} -> {1} on replyConn seq={2} delta={3} ({4})", key, ToHex(resp, 0, resp.Length), _aurumSndNxt + (uint)(_delta - resp.Length), _delta, mode);
                        }
                    }
                    else
                    {
                        Log("POLL key={0} known but not answered (mode={1}, answerable={2})", key, mode, answerable.Contains(key));
                    }
                    continue;
                }

                // Any other packet on the watched connections (bare ACKs, SYN/FIN/RST,
                // Aurum's poll-conn ACKs/`01`): forward unchanged. We never modify the
                // poll connection's sequence space.
                ForwardUnchanged(packet, recvLen, ref addr);

                // Begin drain near the end so a non-zero reply-delta is given a chance to
                // settle before we close (passthru never accrues delta).
                if (!draining && _delta != 0 && sw.ElapsedMilliseconds >= drainStartMs)
                {
                    draining = true;
                    Log("DRAIN window begin (delta={0}); ceasing new replies", _delta);
                }
            }
        }
        catch (Exception ex) { Log("EXCEPTION {0}", ex.Message); }
        finally
        {
            CloseOnce();
            Log("DONE mode={0} pollSeen={1} pollKnown={2} pollUnknown={3} injected={4} replyTemplates={5} finalDelta={6}",
                mode, pollSeen, pollKnown, pollUnknown, injected, replyTemplates, _delta);
            if (_delta != 0) { Log("WARNING: stopped with reply-delta={0} -- the 31100 link may RST/reconnect (recoverable).", _delta); }
            Log("CLOSE ok");
        }
        return 0;
    }

    // Forward a captured packet unchanged (recompute checksums defensively; in practice
    // an unmodified packet is byte-identical, but CalcChecksums is cheap and safe).
    private static void ForwardUnchanged(byte[] packet, uint len, ref WinDivertAddress addr)
    {
        WinDivertHelperCalcChecksums(packet, len, ref addr, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, packet, len, out sent, ref addr))
        {
            Log("WARN forward failed err={0}", Marshal.GetLastWin32Error());
        }
    }

    // Capture the Aurum->CommCtrlSAS:31100 client header as the reply template.
    private static void CaptureReplyTemplate(byte[] packet, int ipLen, int tcpLen, ref WinDivertAddress addr, int srcPort)
    {
        _replyIpLen = ipLen;
        _replyTcpLen = tcpLen;
        _replyHdr = new byte[ipLen + tcpLen];
        Array.Copy(packet, 0, _replyHdr, 0, ipLen + tcpLen);
        _replyAddr = addr;
        _replyEphem = srcPort;
        _haveReplyTemplate = true;
        Log("REPLY template (client frame) captured ephem={0} aurumSndNxt={1}", _replyEphem, _aurumSndNxt);
    }

    // Synthesize the client header template from a server (31100->Aurum) frame: the
    // client's seq is the server's ack, the client's ack is the server's seq.
    private static void CaptureReplyTemplateFromServer(byte[] packet, int ipLen, int tcpLen, ref WinDivertAddress addr, int srcPort, int dstPort)
    {
        uint srvSeq = ReadU32(packet, ipLen + 4);
        uint srvAck = ReadU32(packet, ipLen + 8);
        _replyIpLen = ipLen;
        _replyTcpLen = tcpLen;
        _replyHdr = new byte[ipLen + tcpLen];
        Array.Copy(packet, 0, _replyHdr, 0, ipLen + tcpLen);
        // Swap IPs (src<->dst) and ports so the template is the CLIENT direction.
        SwapBe(_replyHdr, 12, 16, 4);              // IPv4 src/dst addresses
        SwapBe(_replyHdr, ipLen + 0, ipLen + 2, 2); // TCP src/dst ports
        _replyAddr = addr;
        _replyEphem = dstPort;
        if (_aurumSndNxt == 0) { _aurumSndNxt = srvAck; }
        _srvSeq = srvSeq;
        _haveReplyTemplate = true;
        Log("REPLY template (from server frame) synthesized ephem={0} aurumSndNxt={1} srvSeq={2}", _replyEphem, _aurumSndNxt, _srvSeq);
    }

    // Inject a slave reply (Aurum->CommCtrlSAS:31100) carrying the verbatim table bytes
    // at Aurum's current send point + delta, growing the delta by the reply length.
    private static void InjectSlaveReply(byte[] payload)
    {
        int ip = _replyIpLen, t = ip;
        int total = ip + _replyTcpLen + payload.Length;
        var pkt = new byte[total];
        Array.Copy(_replyHdr, 0, pkt, 0, ip + _replyTcpLen);

        WriteU16(pkt, 2, (uint)total);                 // IP total length
        WriteU16(pkt, 4, (uint)(ReadU16(pkt, 4) + 1)); // bump IP id
        WriteU16(pkt, 10, 0);                          // zero IP checksum (recomputed)
        uint seq = (uint)(_aurumSndNxt + (uint)_delta);
        WriteU32(pkt, t + 4, seq);                     // our seq
        WriteU32(pkt, t + 8, _srvSeq);                 // ack the server's latest seq
        // data offset (5 = 20-byte TCP header) + PSH|ACK
        pkt[t + 12] = 0x50;
        pkt[t + 13] = 0x18;
        WriteU16(pkt, t + 14, (uint)_replyWindow);
        WriteU16(pkt, t + 16, 0);                      // zero TCP checksum (recomputed)
        WriteU16(pkt, t + 18, 0);                      // urgent ptr
        Array.Copy(payload, 0, pkt, ip + _replyTcpLen, payload.Length);

        var a = _replyAddr;
        WinDivertHelperCalcChecksums(pkt, (uint)total, ref a, NO_IP_CHECKSUM);
        uint sent;
        if (!WinDivertSend(_handle, pkt, (uint)total, out sent, ref a))
        {
            Log("WARN reply inject failed err={0}", Marshal.GetLastWin32Error());
            return;
        }
        _delta += payload.Length;
    }

    // SAS CRC-16/KERMIT (poly 0x1021 reflected 0x8408), little-endian on the wire --
    // ported verbatim from Get-SasCrc16 in Invoke-WinDivertAft.ps1. Provided so a reply
    // that must be RE-FRAMED can carry a valid trailing CRC. Verbatim table replies do
    // not need this (their CRC is already correct); kept for completeness per spec.
    private static byte[] ReframeCrc16Kermit(byte[] body)
    {
        int crc = 0;
        foreach (byte b in body)
        {
            crc ^= b;
            for (int i = 0; i < 8; i++)
            {
                crc = ((crc & 1) != 0) ? ((crc >> 1) ^ 0x8408) : (crc >> 1);
                crc &= 0xFFFF;
            }
        }
        var framed = new byte[body.Length + 2];
        Array.Copy(body, framed, body.Length);
        framed[body.Length] = (byte)(crc & 0xFF);
        framed[body.Length + 1] = (byte)((crc >> 8) & 0xFF);
        return framed;
    }

    private static string StripLeading1b(byte[] b, int off, int len)
    {
        if (len <= 0) { return ""; }
        int start = off, n = len;
        if (b[off] == 0x1B) { start = off + 1; n = len - 1; }
        if (n <= 0) { return ""; }
        return ToHex(b, start, n);
    }

    private static void SwapBe(byte[] b, int offA, int offB, int n)
    {
        for (int i = 0; i < n; i++)
        {
            byte tmp = b[offA + i];
            b[offA + i] = b[offB + i];
            b[offB + i] = tmp;
        }
    }

    private static bool Seq32Ge(uint a, uint b) { return (int)(a - b) >= 0; }
    private static int ReadU16(byte[] b, int o) { return (b[o] << 8) | b[o + 1]; }
    private static uint ReadU32(byte[] b, int o) { return ((uint)b[o] << 24) | ((uint)b[o + 1] << 16) | ((uint)b[o + 2] << 8) | b[o + 3]; }
    private static void WriteU16(byte[] b, int o, uint v) { b[o] = (byte)((v >> 8) & 0xFF); b[o + 1] = (byte)(v & 0xFF); }
    private static void WriteU32(byte[] b, int o, uint v) { b[o] = (byte)((v >> 24) & 0xFF); b[o + 1] = (byte)((v >> 16) & 0xFF); b[o + 2] = (byte)((v >> 8) & 0xFF); b[o + 3] = (byte)(v & 0xFF); }

    private static string ToHex(byte[] b, int off, int len)
    {
        const string H = "0123456789ABCDEF";
        char[] c = new char[len * 2];
        for (int i = 0; i < len; i++)
        {
            byte v = b[off + i];
            c[i * 2] = H[(v >> 4) & 0xF];
            c[i * 2 + 1] = H[v & 0xF];
        }
        return new string(c);
    }

    private static byte[] HexToBytes(string hex)
    {
        hex = hex.Trim();
        if (hex.Length % 2 != 0) { throw new ArgumentException("odd hex length"); }
        var o = new byte[hex.Length / 2];
        for (int i = 0; i < o.Length; i++) { o[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16); }
        return o;
    }

    private static int ParseIntOr(string s, int fallback) { int v; return int.TryParse(s, out v) ? v : fallback; }

    private static void Log(string fmt, params object[] a)
    {
        Console.WriteLine("[{0:HH:mm:ss.fff}] {1}", DateTime.Now, string.Format(CultureInfo.InvariantCulture, fmt, a));
        Console.Out.Flush();
    }
}
