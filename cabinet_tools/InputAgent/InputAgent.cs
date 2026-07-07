using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Threading;
using System.IO;

namespace GoldClub.InputAgent
{
    [DataContract]
    internal sealed class Script
    {
        [DataMember] public List<Step> steps = new List<Step>();
        [DataMember] public int defaultKeyDelayMs = 35;
    }

    [DataContract]
    internal sealed class Step
    {
        [DataMember] public string type = "";
        [DataMember] public string value = null;
        [DataMember] public int ms = 0;
    }

    internal static class Program
    {
        // ---- Win32 ---------------------------------------------------------
        [DllImport("user32.dll")]
        private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        [DllImport("user32.dll")]
        private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

        private const int SW_RESTORE = 9;

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

        private const uint INPUT_KEYBOARD = 1;
        private const uint INPUT_MOUSE = 0;
        private const uint KEYEVENTF_KEYUP = 0x0002;
        private const uint MOUSEEVENTF_MOVE = 0x0001;
        private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        private const uint MOUSEEVENTF_LEFTUP = 0x0004;
        private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MOUSEINPUT
        {
            public int dx;
            public int dy;
            public uint mouseData;
            public uint dwFlags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [DllImport("user32.dll")]
        private static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        private static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint);

        [DllImport("user32.dll")]
        private static extern bool SetCursorPos(int x, int y);

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        // ---- CLI -----------------------------------------------------------
        private static int Main(string[] args)
        {
            try
            {
                string scriptJson = null;
                string scriptPath = null;
                string focusProcess = null;

                for (int i = 0; i < args.Length; i++)
                {
                    var a = args[i];
                    if (a == "--scriptJson" && i + 1 < args.Length) { scriptJson = args[++i]; continue; }
                    if (a == "--scriptPath" && i + 1 < args.Length) { scriptPath = args[++i]; continue; }
                    if (a == "--focusProcess" && i + 1 < args.Length) { focusProcess = args[++i]; continue; }
                }

                if (scriptJson == null && scriptPath == null)
                {
                    Console.Error.WriteLine("Usage: InputAgent.exe (--scriptJson <json> | --scriptPath <file>) [--focusProcess OneHand]");
                    return 2;
                }

                if (focusProcess != null)
                {
                    TryFocusProcessMainWindow(focusProcess);
                }

                Script script = scriptJson != null
                    ? DeserializeScript(scriptJson)
                    : DeserializeScript(File.ReadAllText(scriptPath, Encoding.UTF8));

                RunScript(script);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.ToString());
                return 1;
            }
        }

        private static bool TryFocusWindow(IntPtr hWnd)
        {
            if (hWnd == IntPtr.Zero) return false;
            try
            {
                IntPtr fore = GetForegroundWindow();
                uint pid;
                uint foreThread = GetWindowThreadProcessId(fore, out pid);
                uint thisThread = GetCurrentThreadId();
                bool attached = false;
                if (foreThread != 0 && foreThread != thisThread)
                {
                    attached = AttachThreadInput(thisThread, foreThread, true);
                }
                ShowWindow(hWnd, SW_RESTORE);
                bool ok = SetForegroundWindow(hWnd);
                if (attached) AttachThreadInput(thisThread, foreThread, false);
                Thread.Sleep(200);
                return ok;
            }
            catch { return false; }
        }

        private static void TryFocusProcessMainWindow(string processName)
        {
            try
            {
                var procs = Process.GetProcessesByName(processName);
                if (procs.Length == 0) return;
                IntPtr hWnd = procs[0].MainWindowHandle;
                TryFocusWindow(hWnd);
            }
            catch { }
        }

        private static IntPtr FindWindowTitleContains(string substring)
        {
            if (string.IsNullOrWhiteSpace(substring)) return IntPtr.Zero;
            IntPtr found = IntPtr.Zero;
            EnumWindows((hWnd, lParam) =>
            {
                if (!IsWindowVisible(hWnd)) return true;
                var sb = new StringBuilder(512);
                if (GetWindowText(hWnd, sb, sb.Capacity) <= 0) return true;
                if (sb.ToString().IndexOf(substring, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    found = hWnd;
                    return false;
                }
                return true;
            }, IntPtr.Zero);
            return found;
        }

        private static bool TryFocusWindowTitleContains(string substring)
        {
            IntPtr found = ResolveWindowHandle(substring);
            if (found == IntPtr.Zero) return false;
            return TryFocusWindow(found);
        }

        /// <summary>
        /// Prefer process main window for known game processes (OneHand); then title substring.
        /// </summary>
        private static IntPtr ResolveWindowHandle(string titleOrProcess)
        {
            if (string.IsNullOrWhiteSpace(titleOrProcess)) return IntPtr.Zero;
            if (string.Equals(titleOrProcess, "OneHand", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    var procs = Process.GetProcessesByName(titleOrProcess);
                    if (procs.Length > 0 && procs[0].MainWindowHandle != IntPtr.Zero)
                        return procs[0].MainWindowHandle;
                }
                catch { }
            }
            IntPtr found = FindWindowTitleContains(titleOrProcess);
            if (found != IntPtr.Zero) return found;
            try
            {
                var procs = Process.GetProcessesByName(titleOrProcess);
                if (procs.Length > 0 && procs[0].MainWindowHandle != IntPtr.Zero)
                    return procs[0].MainWindowHandle;
            }
            catch { }
            return IntPtr.Zero;
        }

        private static bool TryClickWindowClientPercent(string spec)
        {
            if (string.IsNullOrWhiteSpace(spec)) return false;
            string title = null;
            string coords = spec;
            int at = spec.IndexOf('@');
            if (at >= 0)
            {
                title = spec.Substring(0, at).Trim();
                coords = spec.Substring(at + 1).Trim();
            }

            IntPtr hWnd = string.IsNullOrEmpty(title)
                ? GetForegroundWindow()
                : ResolveWindowHandle(title);
            if (hWnd == IntPtr.Zero) return false;

            TryFocusWindow(hWnd);

            var parts = coords.Split(',');
            if (parts.Length != 2) return false;
            double xp = double.Parse(parts[0].Trim(), CultureInfo.InvariantCulture);
            double yp = double.Parse(parts[1].Trim(), CultureInfo.InvariantCulture);

            RECT rc;
            if (!GetClientRect(hWnd, out rc)) return false;
            int w = rc.Right - rc.Left;
            int h = rc.Bottom - rc.Top;
            if (w <= 0 || h <= 0) return false;

            var pt = new POINT
            {
                X = (int)Math.Round(w * xp / 100.0),
                Y = (int)Math.Round(h * yp / 100.0),
            };

            // Fullscreen OneHand: use SendInput absolute screen coords (cursor injection).
            if (string.Equals(title, "OneHand", StringComparison.OrdinalIgnoreCase))
            {
                if (!ClientToScreen(hWnd, ref pt)) return false;
                SendInputScreenClick(pt.X, pt.Y);
                return true;
            }

            if (!ClientToScreen(hWnd, ref pt)) return false;

            SetCursorPos(pt.X, pt.Y);
            Thread.Sleep(60);
            MouseLeftClick();
            Thread.Sleep(40);
            return true;
        }

        private static void SendInputScreenClick(int screenX, int screenY)
        {
            int screenW = GetSystemMetrics(0);
            int screenH = GetSystemMetrics(1);
            int ax = (int)Math.Round(screenX * 65535.0 / Math.Max(1, screenW - 1));
            int ay = (int)Math.Round(screenY * 65535.0 / Math.Max(1, screenH - 1));
            var move = new INPUT
            {
                type = INPUT_MOUSE,
                U = new InputUnion
                {
                    mi = new MOUSEINPUT
                    {
                        dx = ax,
                        dy = ay,
                        dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                        mouseData = 0,
                        time = 0,
                        dwExtraInfo = IntPtr.Zero,
                    },
                },
            };
            SendInput(1, new INPUT[] { move }, Marshal.SizeOf(typeof(INPUT)));
            Thread.Sleep(80);
            MouseLeftClick();
            Thread.Sleep(40);
        }

        private static void MouseLeftClick()
        {
            var down = new INPUT
            {
                type = INPUT_MOUSE,
                U = new InputUnion
                {
                    mi = new MOUSEINPUT { dwFlags = MOUSEEVENTF_LEFTDOWN, dx = 0, dy = 0, mouseData = 0, time = 0, dwExtraInfo = IntPtr.Zero }
                }
            };
            var up = new INPUT
            {
                type = INPUT_MOUSE,
                U = new InputUnion
                {
                    mi = new MOUSEINPUT { dwFlags = MOUSEEVENTF_LEFTUP, dx = 0, dy = 0, mouseData = 0, time = 0, dwExtraInfo = IntPtr.Zero }
                }
            };
            SendInput(1, new INPUT[] { down }, Marshal.SizeOf(typeof(INPUT)));
            Thread.Sleep(30);
            SendInput(1, new INPUT[] { up }, Marshal.SizeOf(typeof(INPUT)));
        }

        private static Script DeserializeScript(string json)
        {
            var ser = new DataContractJsonSerializer(typeof(Script));
            using (var ms = new MemoryStream(Encoding.UTF8.GetBytes(json ?? "")))
            {
                var obj = ser.ReadObject(ms) as Script;
                return obj ?? new Script();
            }
        }

        private static void RunScript(Script script)
        {
            int delay = script.defaultKeyDelayMs <= 0 ? 35 : script.defaultKeyDelayMs;
            for (int i = 0; i < script.steps.Count; i++)
            {
                var st = script.steps[i];
                var t = (st.type ?? "").Trim().ToLowerInvariant();
                Emit(i, t, st.value, st.ms);

                if (t == "sleep")
                {
                    Thread.Sleep(Math.Max(0, st.ms));
                    continue;
                }

                if (t == "key")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        SendKey(st.value.Trim());
                        Thread.Sleep(delay);
                    }
                    continue;
                }

                if (t == "text")
                {
                    if (!string.IsNullOrEmpty(st.value))
                    {
                        foreach (char ch in st.value)
                        {
                            SendChar(ch);
                            Thread.Sleep(5);
                        }
                        Thread.Sleep(delay);
                    }
                    continue;
                }

                if (t == "focus_window" || t == "focus")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        TryFocusWindowTitleContains(st.value.Trim());
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 200));
                    }
                    continue;
                }

                if (t == "focus_process")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        TryFocusProcessMainWindow(st.value.Trim());
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 150));
                    }
                    continue;
                }

                if (t == "click_window" || t == "click")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        TryClickWindowClientPercent(st.value.Trim());
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }
            }
            Emit(script.steps.Count, "done", null, 0);
        }

        private static void Emit(int idx, string type, string value, int ms)
        {
            // Manual JSON to avoid System.Text.Json dependency on older .NET.
            var sb = new StringBuilder();
            sb.Append("{");
            sb.Append("\"ts\":\"").Append(Escape(DateTimeOffset.Now.ToString("o"))).Append("\",");
            sb.Append("\"idx\":").Append(idx).Append(",");
            sb.Append("\"type\":\"").Append(Escape(type ?? "")).Append("\",");
            sb.Append("\"value\":").Append(value == null ? "null" : "\"" + Escape(value) + "\"").Append(",");
            sb.Append("\"ms\":").Append(ms.ToString());
            sb.Append("}");
            Console.WriteLine(sb.ToString());
        }

        private static string Escape(string s)
        {
            if (s == null) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n");
        }

        private static void SendKey(string keyName)
        {
            if (keyName.StartsWith("^") && keyName.Length == 2)
            {
                ushort mod = 0x11; // VK_CONTROL
                ushort vk = VkFromName(keyName.Substring(1));
                if (vk == 0) return;
                KeyDown(mod);
                KeyDown(vk);
                KeyUp(vk);
                KeyUp(mod);
                return;
            }

            ushort vkOnly = VkFromName(keyName);
            if (vkOnly == 0) return;
            KeyDown(vkOnly);
            Thread.Sleep(15);
            KeyUp(vkOnly);
        }

        private static void SendChar(char ch)
        {
            short vkAndShift = VkKeyScan(ch);
            if (vkAndShift == -1) return;
            ushort vk = (ushort)(vkAndShift & 0xff);
            bool shift = ((vkAndShift >> 8) & 1) == 1;
            if (shift) KeyDown(0x10); // VK_SHIFT
            KeyDown(vk);
            KeyUp(vk);
            if (shift) KeyUp(0x10);
        }

        [DllImport("user32.dll")]
        private static extern short VkKeyScan(char ch);

        private static void KeyDown(ushort vk)
        {
            SendInput(1, new INPUT[] { new INPUT { type = INPUT_KEYBOARD, U = new InputUnion { ki = new KEYBDINPUT { wVk = vk, wScan = 0, dwFlags = 0, time = 0, dwExtraInfo = IntPtr.Zero } } } }, Marshal.SizeOf(typeof(INPUT)));
        }

        private static void KeyUp(ushort vk)
        {
            SendInput(1, new INPUT[] { new INPUT { type = INPUT_KEYBOARD, U = new InputUnion { ki = new KEYBDINPUT { wVk = vk, wScan = 0, dwFlags = KEYEVENTF_KEYUP, time = 0, dwExtraInfo = IntPtr.Zero } } } }, Marshal.SizeOf(typeof(INPUT)));
        }

        private static ushort VkFromName(string name)
        {
            name = name.Trim().ToUpperInvariant();
            switch (name)
            {
                case "ENTER": return 0x0D;
                case "TAB": return 0x09;
                case "HOME": return 0x24;
                case "END": return 0x25;
                case "ESC":
                case "ESCAPE": return 0x1B;
                case "SPACE": return 0x20;
                case "LEFT": return 0x25;
                case "UP": return 0x26;
                case "RIGHT": return 0x27;
                case "DOWN": return 0x28;
                case "F1": return 0x70;
                case "F2": return 0x71;
                case "F3": return 0x72;
                case "F4": return 0x73;
                case "F5": return 0x74;
                case "F6": return 0x75;
                case "F7": return 0x76;
                case "F8": return 0x77;
                case "F9": return 0x78;
                case "F10": return 0x79;
                case "F11": return 0x7A;
                case "F12": return 0x7B;
                case "BACKSPACE":
                case "BS": return 0x08;
                case "DELETE":
                case "DEL": return 0x2E;
                default: return 0;
            }
        }
    }
}

