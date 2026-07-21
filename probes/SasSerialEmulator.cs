/*
 * SasSerialEmulator.cs  --  OFFLINE-SAFE scaffold for a SAS HOST poll generator
 * (Option A in aft/investigations/com11-emulator-plan.md).
 *
 * PURPOSE
 * -------
 * Plans / prints the SAS host general-poll stream (80/81) that an EXTERNAL serial
 * peer on the COM11 / VE-MULTIPLEXER line would emit to bring the cabinet's SAS
 * slave (Aurum) online. This file is a SCAFFOLD, not a live tool.
 *
 * HARD SAFETY MODEL (this is the whole point of the file)
 * -------------------------------------------------------
 *   * DEFAULT = DRY RUN. With no flags it PRINTS the poll plan/byte cadence and
 *     EXITS. It opens NOTHING.
 *   * It will NOT open a serial port unless ALL of these are passed together:
 *         --port COMxx  --live  --allow-write  --i-understand-live
 *     Missing any one => it refuses (exit 2) and stays dry-run.
 *   * Even fully-armed, on the lab cabinet it is EXPECTED to fail to open COM11
 *     because CommCtrlSAS owns the port (sharing violation). That failure is the
 *     designed proof that the local-serial path needs Option B/C (stop/replace
 *     CommCtrlSAS), which is out of scope. The scaffold reports the error and
 *     exits; it does not retry, force, or steal the handle.
 *   * It NEVER invents protocol bytes. The only polls it emits are the VERBATIM
 *     general polls observed on .90: 0x80 and 0x81 (raw serial form, no 0x1B).
 *     The 0x1B prefix is the loopback BRIDGE framing and is NOT used on serial.
 *   * No AFT/long-poll/0x72 logic. No CRC payloads. General polls are single
 *     bytes with no CRC. (CRC-16/KERMIT helper is included only for reference and
 *     is never used to fabricate a frame here.)
 *
 * Build (offline; System.IO.Ports is in System.dll on .NET Framework 4.x):
 *   csc /platform:x64 /optimize+ /out:SasSerialEmulator.exe SasSerialEmulator.cs
 *
 * Usage:
 *   SasSerialEmulator.exe                       # dry-run: print plan, exit
 *   SasSerialEmulator.exe --seconds 10 --interval-ms 200   # dry-run plan only
 *   SasSerialEmulator.exe --port COM11 --live --allow-write --i-understand-live
 *                                               # ARMED: attempts open (will fail
 *                                               # while CommCtrlSAS owns COM11)
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO.Ports;
using System.Threading;

internal static class SasSerialEmulator
{
    // Verbatim general-poll bytes observed on the .90 reference cabinet. RAW serial
    // form (NO 0x1B bridge prefix). These are the ONLY bytes this scaffold will emit.
    private static readonly byte[] PollToggle = { 0x81, 0x80 };

    private static int _baud = 921600;          // from CommControler.ini (<11> <921600>)
    private static int _intervalMs = 200;       // observed ~200 ms cadence on .90
    private static int _seconds = 10;
    private static string _port = null;
    private static bool _live = false;
    private static bool _allowWrite = false;
    private static bool _understand = false;

    private static int Main(string[] args)
    {
        if (!ParseArgs(args)) { return 2; }

        Log("SAS SERIAL EMULATOR (scaffold) — Option A host poll generator");
        Log("baud={0} intervalMs={1} seconds={2} pollBytes={3}",
            _baud, _intervalMs, _seconds, ToHexCsv(PollToggle));
        Log("NOTE: raw serial polls are single bytes 80/81 with NO 0x1B prefix and NO CRC.");
        Log("      The 0x1B prefix belongs to the loopback bridge, not the serial line.");

        bool armed = _live && _allowWrite && _understand && !string.IsNullOrEmpty(_port);

        if (!armed)
        {
            Log("");
            Log("MODE = DRY RUN (no port opened, nothing written).");
            if (_live || _allowWrite || _understand || _port != null)
            {
                Log("REFUSE-ARM: a live open requires ALL of:");
                Log("            --port COMxx --live --allow-write --i-understand-live");
                Log("            (got port={0} live={1} allowWrite={2} understand={3})",
                    _port ?? "(none)", _live, _allowWrite, _understand);
            }
            PrintPlan();
            Log("DRY RUN complete. No hardware was touched.");
            return 0;
        }

        // ---- ARMED PATH ----------------------------------------------------------
        // Even here we only emit verbatim 80/81 at the planned cadence. On the lab
        // cabinet this open is EXPECTED to fail (CommCtrlSAS owns COM11). We surface
        // the error and exit; we never force/steal the handle.
        Log("");
        Log("MODE = ARMED LIVE. Attempting to open {0} @ {1} baud ...", _port, _baud);
        Log("(Expected on the lab cabinet: open FAILS because CommCtrlSAS owns COM11.)");

        SerialPort sp = null;
        try
        {
            sp = new SerialPort(_port, _baud, Parity.None, 8, StopBits.One);
            sp.WriteTimeout = 1000;
            sp.Open();
        }
        catch (Exception ex)
        {
            Log("OPEN FAILED ({0}): {1}", ex.GetType().Name, ex.Message);
            Log("This is the designed outcome while CommCtrlSAS holds COM11.");
            Log("Local-serial emulation therefore requires Option B/C (stop/replace");
            Log("CommCtrlSAS) — OUT OF SCOPE this round. Exiting without writing.");
            if (sp != null) { try { sp.Dispose(); } catch { } }
            return 1;
        }

        Log("OPEN ok (UNEXPECTED on the lab cabinet). Emitting verbatim 80/81 only.");
        int idx = 0;
        var deadline = DateTime.UtcNow.AddSeconds(_seconds);
        try
        {
            while (DateTime.UtcNow < deadline)
            {
                byte poll = PollToggle[idx % PollToggle.Length];
                idx++;
                sp.Write(new byte[] { poll }, 0, 1);
                Log("TX poll {0:X2} (#{1})", poll, idx);
                Thread.Sleep(_intervalMs);
            }
        }
        catch (Exception ex)
        {
            Log("WRITE error: {0}", ex.Message);
        }
        finally
        {
            try { sp.Close(); sp.Dispose(); } catch { }
            Log("CLOSE ok. Polls emitted={0}.", idx);
        }
        return 0;
    }

    private static void PrintPlan()
    {
        Log("");
        Log("--- POLL PLAN (what an external host on COM11/MUX would emit) ---");
        int n = Math.Max(1, (_seconds * 1000) / Math.Max(1, _intervalMs));
        int show = Math.Min(n, 10);
        for (int i = 0; i < show; i++)
        {
            byte poll = PollToggle[i % PollToggle.Length];
            Log("  t+{0,5}ms  TX {1:X2}  (raw serial general poll, address 1)",
                i * _intervalMs, poll);
        }
        if (n > show) { Log("  ... ({0} polls total over {1}s @ {2}ms)", n, _seconds, _intervalMs); }
        Log("Expected slave behavior (from .90): poll 81 -> 00 ; poll 80 -> (silence).");
    }

    // SAS CRC-16/KERMIT — reference only, ported from Get-SasCrc16 / WdRespond.
    // NOT used to fabricate any frame in this scaffold (general polls carry no CRC).
    private static ushort Crc16Kermit(byte[] body)
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
        return (ushort)crc;
    }

    private static bool ParseArgs(string[] args)
    {
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i].Trim().ToLowerInvariant();
            switch (a)
            {
                case "--live": _live = true; break;
                case "--allow-write": _allowWrite = true; break;
                case "--i-understand-live": _understand = true; break;
                case "--dry-run": /* default; explicit no-op */ break;
                case "--port":
                    if (i + 1 >= args.Length) { Console.Error.WriteLine("--port needs a value"); return false; }
                    _port = args[++i].Trim();
                    break;
                case "--baud":
                    if (i + 1 >= args.Length || !int.TryParse(args[++i], out _baud)) { Console.Error.WriteLine("--baud needs an int"); return false; }
                    break;
                case "--interval-ms":
                    if (i + 1 >= args.Length || !int.TryParse(args[++i], out _intervalMs)) { Console.Error.WriteLine("--interval-ms needs an int"); return false; }
                    if (_intervalMs < 1) { _intervalMs = 1; }
                    break;
                case "--seconds":
                    if (i + 1 >= args.Length || !int.TryParse(args[++i], out _seconds)) { Console.Error.WriteLine("--seconds needs an int"); return false; }
                    if (_seconds < 1) { _seconds = 1; }
                    break;
                case "-h":
                case "--help":
                    Console.WriteLine("usage: SasSerialEmulator.exe [--seconds N] [--interval-ms N] [--baud N]");
                    Console.WriteLine("       [--port COMxx --live --allow-write --i-understand-live]");
                    Console.WriteLine("default: DRY RUN (prints plan, opens nothing).");
                    return false;
                default:
                    Console.Error.WriteLine("unknown arg: " + args[i]);
                    return false;
            }
        }
        return true;
    }

    private static string ToHexCsv(byte[] b)
    {
        var parts = new List<string>();
        foreach (byte x in b) { parts.Add(x.ToString("X2", CultureInfo.InvariantCulture)); }
        return string.Join(",", parts.ToArray());
    }

    private static void Log(string fmt, params object[] a)
    {
        Console.WriteLine(string.Format(CultureInfo.InvariantCulture, fmt, a));
        Console.Out.Flush();
    }
}
