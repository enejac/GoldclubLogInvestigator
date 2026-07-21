// AutoPlay.exe - synthetic keystroke harness for OneHand EGM games.
//
// Purpose: legitimate QA volume generation. It presses ordinary game buttons
// (Spin / Bet+ / Bet- / MaxBet / Collect / GameSelect) by sending real
// keyboard input (SendInput) to the foreground game window, exactly as the
// physical button deck would. No memory writes, no value injection, no driver.
//
// HARDWARE NOTE: on the real cabinet these are NOT keyboard keys. The slot's
// mechanical buttons are wired to a motherboard COM port; the hardware layer
// turns each physical button into a key code that the game consumes. The codes
// in slot\hwdrivers\Keyboard.xml are those button codes (they happen to line up
// with the numpad key codes below), so emulating the matching keyboard
// scancodes reproduces a real button-deck press to the game:
//   Spin               = 271 -> numpad Enter  (VK_RETURN ext / scan 0x1C ext)
//   BetPlus            = 268 -> numpad *       (VK_MULTIPLY / scan 0x37)
//   BetMinus           = 266 -> numpad .       (VK_DECIMAL  / scan 0x53)
//   MaxBet             = 270 -> numpad +       (VK_ADD      / scan 0x4E)
//   Collect            = 261 -> numpad 5       (VK_NUMPAD5  / scan 0x4C)
//   MultiGameSelection = 267 -> numpad /       (VK_DIVIDE ext / scan 0x35 ext)
//
// The debug OneHand build also accepts 'p' (spin) and 'w'; a fast double-tap of
// the spin key stops the reels early for quicker results.
//
// Usage:
//   AutoPlay.exe --action spin --count 1
//   AutoPlay.exe --seq "betminus*8,betplus*4,spin,wait1500,spin" --log C:\Windows\Temp\autoplay.log
//
// Options:
//   --action <name>        single action (spin|betplus|betminus|maxbet|collect|gameselect)
//   --count N              repeat the --action N times (default 1)
//   --delay-ms N           gap between repeated presses (default 1500)
//   --pre-delay-ms N       wait before the first press (default 1000)
//   --hold-ms N            key down->up hold time (default 60)
//   --key-mode MODE        scan (default) sends hardware scancodes; vk forces virtual keys
//   --spin-key KEY         what "spin" presses: enter (keypad Enter, retail) | p | w (debug build)
//   --fast-stop-ms N       if >0, re-press the spin key after N ms to stop reels early (faster results)
//   --seq "tokens"         comma list: action names, name*N, or waitN (ms). Overrides --action.
//   --target-process NAME  SetForegroundWindow on first top-level window owned by this process
//                          (default OneHand)
//   --target-class NAME    SetForegroundWindow on this window class first
//   --target-title TEXT    SetForegroundWindow on first visible window whose title contains TEXT
//   --list-windows         log top-level windows and exit
//   --no-focus             do not try to focus a window; inject to current foreground
//   --log PATH             append a timestamped action log
//   --dry-run              log intended actions but send no input

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Diagnostics;
using System.Threading;

internal static class AutoPlay
{
    // ---- Win32 SendInput ----
    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT
    {
        public uint type;
        public InputUnion U;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public HARDWAREINPUT hi;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HARDWAREINPUT
    {
        public uint uMsg;
        public ushort wParamL, wParamH;
    }

    private const uint INPUT_KEYBOARD = 1;
    private const uint KEYEVENTF_KEYUP = 0x0002;
    private const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    private const uint KEYEVENTF_SCANCODE = 0x0008;

    private const ushort VK_RETURN = 0x0D;
    private const ushort VK_MULTIPLY = 0x6A;
    private const ushort VK_ADD = 0x6B;
    private const ushort VK_DECIMAL = 0x6E;
    private const ushort VK_DIVIDE = 0x6F;
    private const ushort VK_NUMPAD5 = 0x65;
    private const ushort VK_P = 0x50;
    private const ushort VK_W = 0x57;

    // Set-1 hardware scan codes (what the game's input layer actually reads).
    private const ushort SC_KP_ENTER = 0x1C; // extended
    private const ushort SC_KP_MULTIPLY = 0x37;
    private const ushort SC_KP_DECIMAL = 0x53;
    private const ushort SC_KP_ADD = 0x4E;
    private const ushort SC_KP_5 = 0x4C;
    private const ushort SC_KP_DIVIDE = 0x35; // extended
    private const ushort SC_P = 0x19;
    private const ushort SC_W = 0x11;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    private static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    private sealed class KeySpec
    {
        public ushort Vk;
        public bool Extended;
        public ushort Scan;       // set-1 hardware scan code (0 = none)
        public bool PreferScan;   // send as a hardware scancode regardless of key-mode
        public KeySpec(ushort vk, bool ext, ushort scan, bool preferScan)
        {
            Vk = vk; Extended = ext; Scan = scan; PreferScan = preferScan;
        }
    }

    private static readonly Dictionary<string, KeySpec> Keys =
        new Dictionary<string, KeySpec>(StringComparer.OrdinalIgnoreCase)
        {
            // Retail button-deck keys (from Keyboard.xml). Scancode is preferred
            // because the game's Direct3D input layer ignores plain virtual keys.
            { "spin",       new KeySpec(VK_RETURN,   true,  SC_KP_ENTER,    true)  },
            { "betplus",    new KeySpec(VK_MULTIPLY, false, SC_KP_MULTIPLY, true)  },
            { "betminus",   new KeySpec(VK_DECIMAL,  false, SC_KP_DECIMAL,  true)  },
            { "maxbet",     new KeySpec(VK_ADD,      false, SC_KP_ADD,      true)  },
            { "collect",    new KeySpec(VK_NUMPAD5,  false, SC_KP_5,        true)  },
            { "gameselect", new KeySpec(VK_DIVIDE,   true,  SC_KP_DIVIDE,   true)  },
            // Debug-build keys observed on the dev OneHand: 'p' spins, 'w' also
            // reacts. A fast double-tap of the spin key stops the reels early.
            { "p",          new KeySpec(VK_P,        false, SC_P,           true)  },
            { "w",          new KeySpec(VK_W,        false, SC_W,           true)  },
        };

    private static string _logPath;
    private static bool _dryRun;
    private static int _holdMs = 60;
    private static string _keyMode = "scan";
    private static string _spinKey = "spin"; // token actually pressed for "spin"
    private static int _fastStopMs = 0;      // >0: re-press spin key after this delay

    private static void Main(string[] args)
    {
        string action = null;
        int count = 1;
        int delayMs = 1500;
        int preDelayMs = 1000;
        string seq = null;
        string targetClass = null;
        string targetTitle = null;
        string targetProcess = "OneHand";
        bool focus = true;
        bool listWindows = false;

        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            switch (a.ToLowerInvariant())
            {
                case "--action": action = Next(args, ref i); break;
                case "--count": count = int.Parse(Next(args, ref i), CultureInfo.InvariantCulture); break;
                case "--delay-ms": delayMs = int.Parse(Next(args, ref i), CultureInfo.InvariantCulture); break;
                case "--pre-delay-ms": preDelayMs = int.Parse(Next(args, ref i), CultureInfo.InvariantCulture); break;
                case "--hold-ms": _holdMs = int.Parse(Next(args, ref i), CultureInfo.InvariantCulture); break;
                case "--key-mode": _keyMode = Next(args, ref i).ToLowerInvariant(); break;
                case "--spin-key":
                {
                    string v = Next(args, ref i).ToLowerInvariant();
                    _spinKey = (v == "enter") ? "spin" : v; // "enter" = keypad-Enter spin
                    break;
                }
                case "--fast-stop-ms": _fastStopMs = int.Parse(Next(args, ref i), CultureInfo.InvariantCulture); break;
                case "--seq": seq = Next(args, ref i); break;
                case "--target-process": targetProcess = Next(args, ref i); break;
                case "--target-class": targetClass = Next(args, ref i); break;
                case "--target-title": targetTitle = Next(args, ref i); break;
                case "--list-windows": listWindows = true; break;
                case "--no-focus": focus = false; break;
                case "--log": _logPath = Next(args, ref i); break;
                case "--dry-run": _dryRun = true; break;
                default:
                    Console.Error.WriteLine("Unknown arg: " + a);
                    break;
            }
        }

        Log(string.Format("START pid={0} dryRun={1} keyMode={2} spinKey={3} fastStop={4} seq=[{5}] action={6} count={7} delay={8} pre={9} hold={10}",
            System.Diagnostics.Process.GetCurrentProcess().Id, _dryRun, _keyMode, _spinKey, _fastStopMs, seq, action, count, delayMs, preDelayMs, _holdMs));

        if (listWindows)
        {
            LogVisibleWindows();
            return;
        }

        if (focus && !_dryRun)
        {
            LogForeground("BEFORE_FOCUS");
            IntPtr h = FindTargetWindow(targetProcess, targetClass, targetTitle);
            if (h != IntPtr.Zero)
            {
                Log("FOCUS requested hwnd=0x" + h.ToInt64().ToString("X") + " " + DescribeWindow(h));
                bool ok = ForceForeground(h);
                Log("FOCUS forced ok=" + ok);
                LogForeground("AFTER_FOCUS");
            }
            else
            {
                Log("FOCUS target not found; visible windows follow");
                LogVisibleWindows();
                Log("FOCUS fallback: injecting to current foreground");
            }
        }

        if (preDelayMs > 0) Thread.Sleep(preDelayMs);

        List<KeyValuePair<string, int>> plan = BuildPlan(seq, action, count, delayMs);
        if (plan.Count == 0)
        {
            Console.Error.WriteLine("Nothing to do. Provide --action or --seq.");
            Log("ABORT nothing-to-do");
            return;
        }

        int pressed = 0;
        for (int i = 0; i < plan.Count; i++)
        {
            string token = plan[i].Key;
            int waitMs = plan[i].Value;
            if (token == "wait")
            {
                Log("WAIT " + waitMs + "ms");
                Thread.Sleep(waitMs);
                continue;
            }

            // "spin" is an alias that may be remapped to a different physical key
            // (e.g. 'p' on the debug build) and may include a fast-stop double-tap.
            string resolved = (token == "spin") ? _spinKey : token;

            if (!Keys.ContainsKey(resolved))
            {
                Log("SKIP unknown token: " + token + " (resolved=" + resolved + ")");
                continue;
            }

            if (_dryRun)
            {
                Log("DRYRUN press " + token + " (key=" + resolved + ")" +
                    (token == "spin" && _fastStopMs > 0 ? " +faststop" : ""));
            }
            else
            {
                Press(Keys[resolved]);
                Log("PRESS " + token + " (key=" + resolved + ")");
                if (token == "spin" && _fastStopMs > 0)
                {
                    Thread.Sleep(_fastStopMs);
                    Press(Keys[resolved]);
                    Log("PRESS faststop (key=" + resolved + ")");
                }
            }
            pressed++;

            if (i < plan.Count - 1 && waitMs > 0)
                Thread.Sleep(waitMs);
        }

        Log("DONE pressed=" + pressed);
        Console.WriteLine("AutoPlay done. presses=" + pressed);
    }

    // Expands --seq or the --action/--count form into an ordered plan.
    // Each entry: (token, gapMsAfter). token "wait" uses gapMsAfter as the sleep.
    private static List<KeyValuePair<string, int>> BuildPlan(string seq, string action, int count, int delayMs)
    {
        var plan = new List<KeyValuePair<string, int>>();
        if (!string.IsNullOrEmpty(seq))
        {
            foreach (string raw in seq.Split(','))
            {
                string t = raw.Trim();
                if (t.Length == 0) continue;
                if (t.StartsWith("wait", StringComparison.OrdinalIgnoreCase))
                {
                    int ms;
                    string num = t.Substring(4);
                    if (!int.TryParse(num, NumberStyles.Integer, CultureInfo.InvariantCulture, out ms)) ms = 1000;
                    plan.Add(new KeyValuePair<string, int>("wait", ms));
                    continue;
                }
                int rep = 1;
                string name = t;
                int star = t.IndexOf('*');
                if (star > 0)
                {
                    name = t.Substring(0, star).Trim();
                    int.TryParse(t.Substring(star + 1), NumberStyles.Integer, CultureInfo.InvariantCulture, out rep);
                    if (rep < 1) rep = 1;
                }
                for (int k = 0; k < rep; k++)
                    plan.Add(new KeyValuePair<string, int>(name.ToLowerInvariant(), 150));
            }
            return plan;
        }

        if (!string.IsNullOrEmpty(action))
        {
            for (int k = 0; k < Math.Max(1, count); k++)
                plan.Add(new KeyValuePair<string, int>(action.ToLowerInvariant(), delayMs));
        }
        return plan;
    }

    private static IntPtr FindTargetWindow(string targetProcess, string targetClass, string targetTitle)
    {
        if (!string.IsNullOrEmpty(targetProcess))
        {
            // A OneHand process owns several top-level windows (the SlotMachine
            // Direct3D surface, an ActiveMovie/FilterGraph video window, IME/.NET
            // helper windows). Pick the real game surface, never the video window.
            IntPtr bestScored = IntPtr.Zero;
            int bestScore = int.MinValue;
            EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
            {
                uint pid;
                GetWindowThreadProcessId(hWnd, out pid);
                string proc = "";
                try { proc = Process.GetProcessById((int)pid).ProcessName; } catch { proc = ""; }
                if (!string.Equals(proc, targetProcess, StringComparison.OrdinalIgnoreCase))
                    return true;

                string cls = GetWindowClass(hWnd);
                string title = GetWindowTitle(hWnd);

                int score = 0;
                if (cls.IndexOf("Direct3D", StringComparison.OrdinalIgnoreCase) >= 0) score += 100;
                if (title.IndexOf("SlotMachine", StringComparison.OrdinalIgnoreCase) >= 0) score += 80;
                if (cls.IndexOf("Unity", StringComparison.OrdinalIgnoreCase) >= 0) score += 60;
                if (IsWindowVisible(hWnd)) score += 20;
                // Strongly avoid the video and helper windows.
                if (cls.IndexOf("FilterGraph", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    title.IndexOf("ActiveMovie", StringComparison.OrdinalIgnoreCase) >= 0) score -= 1000;
                if (cls.IndexOf("IME", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    cls.IndexOf("MSCTFIME", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    cls.IndexOf("BroadcastEventWindow", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    cls.IndexOf("Hook Window", StringComparison.OrdinalIgnoreCase) >= 0) score -= 500;

                if (score > bestScore)
                {
                    bestScore = score;
                    bestScored = hWnd;
                }
                return true;
            }, IntPtr.Zero);
            if (bestScored != IntPtr.Zero && bestScore > 0) return bestScored;
        }

        if (!string.IsNullOrEmpty(targetClass))
        {
            IntPtr byClass = FindWindow(targetClass, null);
            if (byClass != IntPtr.Zero) return byClass;
        }

        IntPtr byTitle = IntPtr.Zero;
        if (!string.IsNullOrEmpty(targetTitle))
        {
            EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
            {
                if (!IsWindowVisible(hWnd)) return true;
                string title = GetWindowTitle(hWnd);
                if (title.IndexOf(targetTitle, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    byTitle = hWnd;
                    return false;
                }
                return true;
            }, IntPtr.Zero);
            if (byTitle != IntPtr.Zero) return byTitle;
        }

        IntPtr likely = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd)) return true;
            string d = DescribeWindow(hWnd);
            if (d.IndexOf("OneHand", StringComparison.OrdinalIgnoreCase) >= 0 ||
                d.IndexOf("Unity", StringComparison.OrdinalIgnoreCase) >= 0 ||
                d.IndexOf("GoldClub", StringComparison.OrdinalIgnoreCase) >= 0 ||
                d.IndexOf("Slot", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                likely = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return likely;
    }

    // SetForegroundWindow silently fails when the caller is not the current
    // foreground process. Attach to both the foreground and target threads, raise
    // the window, and retry a few times -- this also pushes past a transient
    // ActiveMovie/video window that grabbed focus.
    private static bool ForceForeground(IntPtr hWnd)
    {
        uint myTid = GetCurrentThreadId();
        for (int attempt = 0; attempt < 5; attempt++)
        {
            IntPtr fg = GetForegroundWindow();
            uint fgPid;
            uint fgTid = GetWindowThreadProcessId(fg, out fgPid);
            uint tgtPid;
            uint tgtTid = GetWindowThreadProcessId(hWnd, out tgtPid);

            AttachThreadInput(myTid, fgTid, true);
            AttachThreadInput(myTid, tgtTid, true);
            try
            {
                ShowWindow(hWnd, 5 /* SW_SHOW */);
                BringWindowToTop(hWnd);
                SetForegroundWindow(hWnd);
            }
            finally
            {
                AttachThreadInput(myTid, tgtTid, false);
                AttachThreadInput(myTid, fgTid, false);
            }

            Thread.Sleep(250);
            if (GetForegroundWindow() == hWnd) return true;
        }
        return GetForegroundWindow() == hWnd;
    }

    private static void LogVisibleWindows()
    {
        Log("WINDOWS top-level:");
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            Log("WINDOW hwnd=0x" + hWnd.ToInt64().ToString("X") + " visible=" + IsWindowVisible(hWnd) + " " + DescribeWindow(hWnd));
            return true;
        }, IntPtr.Zero);
    }

    private static void LogForeground(string prefix)
    {
        IntPtr h = GetForegroundWindow();
        if (h == IntPtr.Zero) Log(prefix + " foreground=<none>");
        else Log(prefix + " foreground hwnd=0x" + h.ToInt64().ToString("X") + " " + DescribeWindow(h));
    }

    private static string DescribeWindow(IntPtr hWnd)
    {
        uint pid;
        GetWindowThreadProcessId(hWnd, out pid);
        string proc = "";
        try { proc = Process.GetProcessById((int)pid).ProcessName; } catch { proc = "?"; }
        return "pid=" + pid + " proc=" + proc + " class='" + GetWindowClass(hWnd) + "' title='" + GetWindowTitle(hWnd) + "'";
    }

    private static string GetWindowTitle(IntPtr hWnd)
    {
        var sb = new StringBuilder(512);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    private static string GetWindowClass(IntPtr hWnd)
    {
        var sb = new StringBuilder(256);
        GetClassName(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    private static void Press(KeySpec ks)
    {
        bool useScan = _keyMode != "vk" && (ks.PreferScan || _keyMode == "scan") && ks.Scan != 0;
        if (useScan)
        {
            SendScan(ks.Scan, ks.Extended, false);
            Thread.Sleep(_holdMs);
            SendScan(ks.Scan, ks.Extended, true);
            return;
        }

        SendKey(ks.Vk, ks.Extended, false);
        Thread.Sleep(_holdMs);
        SendKey(ks.Vk, ks.Extended, true);
    }

    private static void SendKey(ushort vk, bool extended, bool keyUp)
    {
        uint flags = 0;
        if (extended) flags |= KEYEVENTF_EXTENDEDKEY;
        if (keyUp) flags |= KEYEVENTF_KEYUP;

        var inputs = new INPUT[1];
        inputs[0].type = INPUT_KEYBOARD;
        inputs[0].U.ki = new KEYBDINPUT
        {
            wVk = vk,
            wScan = 0,
            dwFlags = flags,
            time = 0,
            dwExtraInfo = IntPtr.Zero,
        };

        uint sent = SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != 1)
            Log("WARN SendInput failed vk=0x" + vk.ToString("X") + " err=" + Marshal.GetLastWin32Error());
    }

    private static void SendScan(ushort scan, bool extended, bool keyUp)
    {
        uint flags = KEYEVENTF_SCANCODE;
        if (extended) flags |= KEYEVENTF_EXTENDEDKEY;
        if (keyUp) flags |= KEYEVENTF_KEYUP;

        var inputs = new INPUT[1];
        inputs[0].type = INPUT_KEYBOARD;
        inputs[0].U.ki = new KEYBDINPUT
        {
            wVk = 0,
            wScan = scan,
            dwFlags = flags,
            time = 0,
            dwExtraInfo = IntPtr.Zero,
        };

        uint sent = SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != 1)
            Log("WARN SendInput failed scan=0x" + scan.ToString("X") + " err=" + Marshal.GetLastWin32Error());
    }

    private static string Next(string[] args, ref int i)
    {
        if (i + 1 >= args.Length) throw new ArgumentException("missing value after " + args[i]);
        return args[++i];
    }

    private static void Log(string msg)
    {
        string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture) + "  " + msg;
        Console.WriteLine(line);
        if (!string.IsNullOrEmpty(_logPath))
        {
            try { File.AppendAllText(_logPath, line + Environment.NewLine, Encoding.ASCII); }
            catch { /* best-effort */ }
        }
    }
}
