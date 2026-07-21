// WdInject.exe - inject a single SAS payload into the live CommCtrlSAS -> Aurum
// loopback TCP stream using WinDivert 2.2.2.
//
// Usage: WdInject.exe <payloadHexIncluding1BPrefix> [<srcPort=31150>] [<observeMs=8000>] [<ackGraceMs=600>]
//
// It opens WinDivert in divert mode on the outbound loopback flow whose TCP source
// port is <srcPort> (CommCtrlSAS 31150 -> Aurum <ephemeral>). It forwards every
// captured packet unchanged so normal SAS polling keeps flowing, then injects a
// crafted segment carrying our payload into the same flow with recomputed checksums.
//
// Sequence-number anchoring (how we learn where to inject):
//   * PAYLOAD anchor (preferred, proven): on the first segment WITH payload we set
//     SEQ = origSeq + origPayloadLen (right after the forwarded data).
//   * ACK anchor (fallback for an idle-but-ESTABLISHED connection): when the physical
//     SAS link is missing, CommCtrlSAS emits no payload on 31150 but still sends bare
//     ACKs whose seq == SND.NXT. For a pure ACK origPayloadLen == 0, so SEQ = seq is
//     the correct injection point. We wait <ackGraceMs> (preferring a payload segment
//     if one arrives in that window) and then anchor on the latest pure-ACK seq.
//   * If ackGraceMs == 0, ACK-anchoring is disabled (legacy payload-only behavior).
//
// After injection it keeps forwarding for ~1500 ms so the stack moves and Aurum can
// ACK/process, then exits. A fully silent connection (no outbound segments at all)
// still cannot be injected and will time out after <observeMs>.

using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

internal static class WdInject
{
    private const int LAYER_NETWORK = 0;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    [DllImport("WinDivert.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr WinDivertOpen(string filter, int layer, short priority, ulong flags);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertRecv(IntPtr handle, byte[] pPacket, uint packetLen, out uint pRecvLen, byte[] pAddr);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertSend(IntPtr handle, byte[] pPacket, uint packetLen, out uint pSendLen, byte[] pAddr);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertHelperCalcChecksums(byte[] pPacket, uint packetLen, byte[] pAddr, ulong flags);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertClose(IntPtr handle);

    [DllImport("WinDivert.dll", SetLastError = true)]
    private static extern bool WinDivertShutdown(IntPtr handle, int how);

    // WINDIVERT_ADDRESS is 80 bytes; treat as opaque blob captured from Recv and reused.
    private const int ADDR_SIZE = 80;
    private const int WINDIVERT_SHUTDOWN_RECV = 0x1;
    private const int WINDIVERT_SHUTDOWN_BOTH = 0x3;
    private const long SEQ_MASK = 0xFFFFFFFFL;

    // The open handle is tracked statically so it can ALWAYS be released -- including on
    // Ctrl-C / ProcessExit -- not just on a normal return. A leaked (never-closed) handle
    // keeps WinDivert's kernel driver referenced, which is what makes a later stop hang
    // in STOP_PENDING. CloseOnce() is idempotent so the finally / signal handlers are safe.
    private static IntPtr s_handle = INVALID_HANDLE_VALUE;
    private static int s_closed = 0;

    // Shared with the watchdog so it can wake a blocked WinDivertRecv when the ACK grace
    // window has elapsed (recv-only shutdown; send stays open so we can still inject).
    private static int s_havePendingAck = 0;
    private static int s_recvShutForAck = 0;
    private static long s_firstAckMs = 0;

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
            Console.Error.WriteLine("usage: WdInject.exe <payloadHex> [<srcPort=31150>] [<observeMs=8000>] [<ackGraceMs=600>]");
            return 1;
        }

        byte[] payload;
        try
        {
            payload = HexToBytes(args[0]);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("bad payload hex: " + ex.Message);
            return 1;
        }

        int srcPort = args.Length >= 2 ? ParseIntOr(args[1], 31150) : 31150;
        int observeMs = args.Length >= 3 ? ParseIntOr(args[2], 8000) : 8000;
        int ackGraceMs = args.Length >= 4 ? ParseIntOr(args[3], 600) : 600;

        // Keep args sane and bounded so a bad value can never disable the timeouts.
        if (srcPort < 1 || srcPort > 65535) { srcPort = 31150; }
        if (observeMs < 100) { observeMs = 100; }
        if (ackGraceMs < 0) { ackGraceMs = 0; }
        // The ACK-grace wait must fit comfortably inside the overall observe window.
        if (ackGraceMs > observeMs - 100) { ackGraceMs = Math.Max(0, observeMs - 100); }

        // Capture ACK-only segments too (not just payload): an idle-but-ESTABLISHED
        // connection still emits bare ACKs. Exclude TCP control packets (SYN/RST/FIN).
        // Established payload segments also have Ack==1, so they are still captured.
        string filter = "outbound and loopback and ip and tcp and tcp.SrcPort == " + srcPort +
                        " and tcp.Ack == 1 and tcp.Syn == 0 and tcp.Rst == 0 and tcp.Fin == 0";

        Console.WriteLine("FILTER " + filter);
        Console.WriteLine("PAYLOAD " + args[0].ToUpperInvariant() + " (" + payload.Length + " bytes)");
        Console.WriteLine("PARAMS srcPort=" + srcPort + " observeMs=" + observeMs + " ackGraceMs=" + ackGraceMs +
                          (ackGraceMs == 0 ? " (ACK-anchoring disabled; payload-only)" : ""));

        IntPtr handle = WinDivertOpen(filter, LAYER_NETWORK, 0, 0);
        if (handle == INVALID_HANDLE_VALUE || handle == IntPtr.Zero)
        {
            int err = Marshal.GetLastWin32Error();
            Console.Error.WriteLine("WinDivertOpen FAILED GetLastError=" + err);
            return 2;
        }
        Console.WriteLine("OPEN ok");

        // Release the driver handle on every exit path so it can never be orphaned.
        s_handle = handle;
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) { CloseOnce(); };
        AppDomain.CurrentDomain.ProcessExit += delegate { CloseOnce(); };

        const int MAX = 65535;
        byte[] recvBuf = new byte[MAX];
        byte[] recvAddr = new byte[ADDR_SIZE];

        // Pending pure-ACK candidate (used only when ackGraceMs > 0). We keep refreshing
        // it with the latest ACK seq while we wait for the grace window to elapse.
        bool haveAck = false;
        long firstAckMs = 0;
        long ackSeq = 0;
        int ackDport = 0;
        int ackIpHdr = 0;
        int ackTcpHdr = 0;
        byte[] ackHdr = null;
        byte[] ackAddr = new byte[ADDR_SIZE];

        bool injected = false;
        Stopwatch overall = Stopwatch.StartNew();
        Stopwatch sinceInject = null;
        int injectedFlag = 0;
        int finishedFlag = 0;
        int rc = 0;

        try
        {
            Thread watchdog = new Thread(delegate()
            {
                while (Interlocked.CompareExchange(ref finishedFlag, 0, 0) == 0)
                {
                    bool notInjected = Interlocked.CompareExchange(ref injectedFlag, 0, 0) == 0;
                    bool noPacketTimeout =
                        notInjected &&
                        overall.ElapsedMilliseconds > observeMs;
                    bool postInjectTimeout =
                        Interlocked.CompareExchange(ref injectedFlag, 0, 0) == 1 &&
                        sinceInject != null &&
                        sinceInject.ElapsedMilliseconds > 1500;

                    if (noPacketTimeout || postInjectTimeout)
                    {
                        WinDivertShutdown(handle, WINDIVERT_SHUTDOWN_BOTH);
                        return;
                    }

                    // ACK grace elapsed with a pending pure-ACK candidate and still no
                    // payload: wake a blocked Recv (recv-only) so the main loop can anchor
                    // on the ACK. Send stays open so the injection can still go out.
                    bool ackGraceFired =
                        ackGraceMs > 0 &&
                        notInjected &&
                        Interlocked.CompareExchange(ref s_havePendingAck, 0, 0) == 1 &&
                        (overall.ElapsedMilliseconds - Interlocked.Read(ref s_firstAckMs)) >= ackGraceMs;
                    if (ackGraceFired && Interlocked.Exchange(ref s_recvShutForAck, 1) == 0)
                    {
                        WinDivertShutdown(handle, WINDIVERT_SHUTDOWN_RECV);
                    }

                    Thread.Sleep(50);
                }
            });
            watchdog.IsBackground = true;
            watchdog.Start();

            while (true)
            {
                if (!injected && overall.ElapsedMilliseconds > observeMs)
                {
                    Console.Error.WriteLine("TIMEOUT no data packet seen on srcPort " + srcPort +
                                            " within " + observeMs + "ms");
                    rc = 3;
                    break;
                }
                if (injected && sinceInject != null && sinceInject.ElapsedMilliseconds > 1500)
                {
                    break;
                }

                // Deferred ACK anchor: the grace window has elapsed and no payload segment
                // arrived, so anchor on the latest pure-ACK seq (nextSeq = ackSeq).
                if (!injected && haveAck && ackGraceMs > 0 &&
                    (overall.ElapsedMilliseconds - firstAckMs) >= ackGraceMs)
                {
                    long nextSeq = ackSeq & SEQ_MASK;
                    Console.WriteLine("OBSERVED dport=" + ackDport + " seq=" + ackSeq + " ack=-" +
                                      " ipHdr=" + ackIpHdr + " tcpHdr=" + ackTcpHdr + " origPayloadLen=0");
                    Console.WriteLine("ANCHOR mode=ack graceMs=" + ackGraceMs);

                    uint injSentAck;
                    bool sokAck = BuildAndInject(handle, ackHdr, ackIpHdr, ackTcpHdr, nextSeq, payload, ackAddr, out injSentAck);
                    if (!sokAck)
                    {
                        int err = Marshal.GetLastWin32Error();
                        Console.Error.WriteLine("INJECT WinDivertSend FAILED GetLastError=" + err);
                        rc = 5;
                        break;
                    }

                    Console.WriteLine("INJECTED seq=" + nextSeq + " dport=" + ackDport +
                                      " len=" + payload.Length + " sent=" + injSentAck);
                    injected = true;
                    Interlocked.Exchange(ref injectedFlag, 1);
                    sinceInject = Stopwatch.StartNew();
                    continue;
                }

                uint recvLen;
                bool ok = WinDivertRecv(handle, recvBuf, (uint)recvBuf.Length, out recvLen, recvAddr);
                if (!ok)
                {
                    if (!injected && overall.ElapsedMilliseconds > observeMs)
                    {
                        Console.Error.WriteLine("TIMEOUT no data packet seen on srcPort " + srcPort +
                                                " within " + observeMs + "ms");
                        rc = 3;
                        break;
                    }
                    if (injected && sinceInject != null && sinceInject.ElapsedMilliseconds > 1500)
                    {
                        break;
                    }

                    int err = Marshal.GetLastWin32Error();
                    // ERROR_NO_DATA / timeout style; keep looping unless fatal.
                    if (err == 232 /* ERROR_NO_DATA */)
                    {
                        Thread.Sleep(10);
                        continue;
                    }
                    // A recv-only shutdown for ACK-grace (or the post-inject window) makes
                    // Recv fail; that is expected, not fatal, while we still have work.
                    if ((!injected && haveAck) || injected)
                    {
                        Thread.Sleep(10);
                        continue;
                    }
                    Console.Error.WriteLine("WinDivertRecv FAILED GetLastError=" + err);
                    rc = 4;
                    break;
                }

                // Forward the original packet unchanged so normal traffic keeps flowing.
                uint sendLen;
                WinDivertSend(handle, recvBuf, recvLen, out sendLen, recvAddr);

                if (injected)
                {
                    continue;
                }

                // Parse the (IPv4 + TCP) headers of the captured packet.
                int ipHdr = (recvBuf[0] & 0x0F) * 4;
                if (ipHdr < 20 || ipHdr + 20 > recvLen)
                {
                    continue;
                }
                int totalLen = (recvBuf[2] << 8) | recvBuf[3];
                int tcpHdr = ((recvBuf[ipHdr + 12] >> 4) & 0x0F) * 4;
                if (tcpHdr < 20 || ipHdr + tcpHdr > recvLen)
                {
                    continue;
                }
                long seq = ReadU32BE(recvBuf, ipHdr + 4);
                long ack = ReadU32BE(recvBuf, ipHdr + 8);
                int dport = (recvBuf[ipHdr + 2] << 8) | recvBuf[ipHdr + 3];
                int origPayloadLen = totalLen - ipHdr - tcpHdr;
                if (origPayloadLen < 0)
                {
                    origPayloadLen = (int)recvLen - ipHdr - tcpHdr;
                }

                if (origPayloadLen > 0)
                {
                    // PAYLOAD anchor (proven path): inject right after this segment.
                    long nextSeq = (seq + origPayloadLen) & SEQ_MASK;

                    Console.WriteLine("OBSERVED dport=" + dport + " seq=" + seq + " ack=" + ack +
                                      " ipHdr=" + ipHdr + " tcpHdr=" + tcpHdr +
                                      " origPayloadLen=" + origPayloadLen);
                    Console.WriteLine("ANCHOR mode=payload");

                    uint injSent;
                    bool sok = BuildAndInject(handle, recvBuf, ipHdr, tcpHdr, nextSeq, payload, recvAddr, out injSent);
                    if (!sok)
                    {
                        int err = Marshal.GetLastWin32Error();
                        Console.Error.WriteLine("INJECT WinDivertSend FAILED GetLastError=" + err);
                        rc = 5;
                        break;
                    }

                    Console.WriteLine("INJECTED seq=" + nextSeq + " dport=" + dport +
                                      " len=" + payload.Length + " sent=" + injSent);
                    injected = true;
                    Interlocked.Exchange(ref injectedFlag, 1);
                    sinceInject = Stopwatch.StartNew();
                    continue;
                }

                // Pure ACK (origPayloadLen == 0).
                if (ackGraceMs <= 0)
                {
                    // Legacy payload-only mode: ignore ACKs, keep waiting for payload.
                    continue;
                }

                // Record/refresh the ACK candidate; prefer a payload segment if one arrives
                // within the grace window, otherwise anchor on the latest pure-ACK seq.
                ackSeq = seq;
                ackDport = dport;
                ackIpHdr = ipHdr;
                ackTcpHdr = tcpHdr;
                if (ackHdr == null || ackHdr.Length != ipHdr + tcpHdr)
                {
                    ackHdr = new byte[ipHdr + tcpHdr];
                }
                Array.Copy(recvBuf, 0, ackHdr, 0, ipHdr + tcpHdr);
                Array.Copy(recvAddr, 0, ackAddr, 0, ADDR_SIZE);
                if (!haveAck)
                {
                    firstAckMs = overall.ElapsedMilliseconds;
                    Interlocked.Exchange(ref s_firstAckMs, firstAckMs);
                    haveAck = true;
                    Interlocked.Exchange(ref s_havePendingAck, 1);
                    Console.WriteLine("ACKCAND seq=" + ackSeq + " dport=" + ackDport +
                                      " (waiting up to " + ackGraceMs + "ms for payload)");
                }
            }
        }
        finally
        {
            Interlocked.Exchange(ref finishedFlag, 1);
            CloseOnce();
            Console.WriteLine("CLOSE ok injected=" + injected);
        }

        return rc;
    }

    // Build the injected packet (original IP+TCP header + our payload), fix lengths/flags,
    // recompute checksums, and send it into the same flow. Returns the WinDivertSend result.
    private static bool BuildAndInject(IntPtr handle, byte[] srcHdr, int ipHdr, int tcpHdr,
                                       long nextSeq, byte[] payload, byte[] addr, out uint injSent)
    {
        injSent = 0;
        int newLen = ipHdr + tcpHdr + payload.Length;
        byte[] outBuf = new byte[newLen];
        Array.Copy(srcHdr, 0, outBuf, 0, ipHdr + tcpHdr);

        // IP total length.
        outBuf[2] = (byte)((newLen >> 8) & 0xFF);
        outBuf[3] = (byte)(newLen & 0xFF);
        // Zero IP checksum (recomputed later).
        outBuf[10] = 0;
        outBuf[11] = 0;
        // TCP sequence number = nextSeq.
        outBuf[ipHdr + 4] = (byte)((nextSeq >> 24) & 0xFF);
        outBuf[ipHdr + 5] = (byte)((nextSeq >> 16) & 0xFF);
        outBuf[ipHdr + 6] = (byte)((nextSeq >> 8) & 0xFF);
        outBuf[ipHdr + 7] = (byte)(nextSeq & 0xFF);
        // Ensure PSH|ACK set.
        outBuf[ipHdr + 13] = (byte)(outBuf[ipHdr + 13] | 0x18);
        // Zero TCP checksum (recomputed later).
        outBuf[ipHdr + 16] = 0;
        outBuf[ipHdr + 17] = 0;
        // Payload.
        Array.Copy(payload, 0, outBuf, ipHdr + tcpHdr, payload.Length);

        if (!WinDivertHelperCalcChecksums(outBuf, (uint)newLen, addr, 0))
        {
            int err = Marshal.GetLastWin32Error();
            Console.Error.WriteLine("CalcChecksums FAILED GetLastError=" + err);
        }

        return WinDivertSend(handle, outBuf, (uint)newLen, out injSent, addr);
    }

    private static int ParseIntOr(string s, int fallback)
    {
        int v;
        return int.TryParse(s, out v) ? v : fallback;
    }

    private static long ReadU32BE(byte[] b, int off)
    {
        return ((long)b[off] << 24) | ((long)b[off + 1] << 16) | ((long)b[off + 2] << 8) | b[off + 3];
    }

    private static byte[] HexToBytes(string hex)
    {
        hex = hex.Trim();
        if (hex.Length % 2 != 0)
        {
            throw new ArgumentException("odd hex length");
        }
        byte[] o = new byte[hex.Length / 2];
        for (int i = 0; i < o.Length; i++)
        {
            o[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
        }
        return o;
    }
}
