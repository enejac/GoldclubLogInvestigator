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
        private static extern bool ScreenToClient(IntPtr hWnd, ref POINT lpPoint);

        [DllImport("user32.dll")]
        private static extern bool SetCursorPos(int x, int y);

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        [DllImport("user32.dll")]
        private static extern IntPtr WindowFromPoint(POINT pt);

        [DllImport("user32.dll")]
        private static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        private const uint WM_MOUSEMOVE = 0x0200;
        private const uint WM_LBUTTONDOWN = 0x0201;
        private const uint WM_LBUTTONUP = 0x0202;
        private const int MK_LBUTTON = 0x0001;

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
                IntPtr hWnd = IntPtr.Zero;
                var procs = Process.GetProcessesByName(processName);
                if (procs.Length > 0 && procs[0].MainWindowHandle != IntPtr.Zero)
                    hWnd = procs[0].MainWindowHandle;
                if (hWnd == IntPtr.Zero)
                    hWnd = FindLargestVisibleWindowForProcess(processName);
                // Alegro Godot window title when process name resolve fails.
                if (hWnd == IntPtr.Zero && string.Equals(processName, "godot", StringComparison.OrdinalIgnoreCase))
                    hWnd = FindWindowTitleContains("RouletteGUI");
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

        /// <summary>
        /// Godot/ruleta often report MainWindowHandle=0; pick the largest visible top-level HWND for the PID.
        /// </summary>
        private static IntPtr FindLargestVisibleWindowForProcess(string processName)
        {
            if (string.IsNullOrWhiteSpace(processName)) return IntPtr.Zero;
            var pids = new HashSet<uint>();
            try
            {
                foreach (var p in Process.GetProcessesByName(processName))
                    pids.Add((uint)p.Id);
            }
            catch { return IntPtr.Zero; }
            if (pids.Count == 0) return IntPtr.Zero;

            IntPtr best = IntPtr.Zero;
            long bestArea = 0;
            EnumWindows((hWnd, lParam) =>
            {
                if (!IsWindowVisible(hWnd)) return true;
                uint pid;
                GetWindowThreadProcessId(hWnd, out pid);
                if (!pids.Contains(pid)) return true;
                RECT rc;
                if (!GetClientRect(hWnd, out rc)) return true;
                long area = (long)(rc.Right - rc.Left) * (rc.Bottom - rc.Top);
                if (area > bestArea)
                {
                    bestArea = area;
                    best = hWnd;
                }
                return true;
            }, IntPtr.Zero);
            return best;
        }

        private static bool TryFocusWindowTitleContains(string substring)
        {
            IntPtr found = ResolveWindowHandle(substring);
            if (found == IntPtr.Zero) return false;
            return TryFocusWindow(found);
        }

        /// <summary>
        /// Prefer process main window for known game processes (OneHand); then title substring;
        /// then largest visible HWND for the process name (Godot/ruleta).
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
            found = FindLargestVisibleWindowForProcess(titleOrProcess);
            if (found != IntPtr.Zero) return found;
            // Alegro RouletteGUI2: process is "godot" but title is RouletteGUI* and MainWindowHandle is often 0.
            if (string.Equals(titleOrProcess, "godot", StringComparison.OrdinalIgnoreCase))
            {
                found = FindWindowTitleContains("RouletteGUI");
                if (found != IntPtr.Zero) return found;
                found = FindWindowTitleContains("Roulette");
                if (found != IntPtr.Zero) return found;
            }
            return IntPtr.Zero;
        }

        private static bool TryParseWindowPercent(string spec, out IntPtr hWnd, out POINT clientPt, out string title)
        {
            hWnd = IntPtr.Zero;
            clientPt = new POINT();
            title = null;
            if (string.IsNullOrWhiteSpace(spec)) return false;

            string coords = spec;
            int at = spec.IndexOf('@');
            if (at >= 0)
            {
                title = spec.Substring(0, at).Trim();
                coords = spec.Substring(at + 1).Trim();
            }

            hWnd = string.IsNullOrEmpty(title)
                ? GetForegroundWindow()
                : ResolveWindowHandle(title);
            if (hWnd == IntPtr.Zero) return false;

            var parts = coords.Split(',');
            if (parts.Length != 2) return false;
            double xp = double.Parse(parts[0].Trim(), CultureInfo.InvariantCulture);
            double yp = double.Parse(parts[1].Trim(), CultureInfo.InvariantCulture);

            RECT rc;
            if (!GetClientRect(hWnd, out rc)) return false;
            int w = rc.Right - rc.Left;
            int h = rc.Bottom - rc.Top;
            if (w <= 0 || h <= 0) return false;

            clientPt = new POINT
            {
                X = (int)Math.Round(w * xp / 100.0),
                Y = (int)Math.Round(h * yp / 100.0),
            };
            return true;
        }

        private static bool TryClickWindowClientPercent(string spec)
        {
            IntPtr hWnd;
            POINT pt;
            string title;
            if (!TryParseWindowPercent(spec, out hWnd, out pt, out title)) return false;

            TryFocusWindow(hWnd);

            if (!ClientToScreen(hWnd, ref pt)) return false;

            // Prefer SetCursorPos + click for Godot: multi-monitor virtual desktops
            // make MOUSEEVENTF_ABSOLUTE mapping easy to get wrong (CXVIRTUAL=5760 here).
            if (string.Equals(title, "OneHand", StringComparison.OrdinalIgnoreCase))
            {
                SendInputScreenClick(pt.X, pt.Y);
                return true;
            }

            SetCursorPos(pt.X, pt.Y);
            Thread.Sleep(60);
            MouseLeftClick();
            Thread.Sleep(40);
            return true;
        }

        /// <summary>
        /// Invisible click via PostMessage (cursor does not move). Used for Godot roulette UI.
        /// Spec: "ProcessOrTitle@xPercent,yPercent" — same as click_window.
        /// </summary>
        /// <summary>
        /// Parse "[ProcessOrTitle@]x,y" where x and y are client **pixels**.
        /// </summary>
        private static bool TryParseWindowPixel(string spec, out IntPtr hWnd, out POINT clientPt, out string title)
        {
            hWnd = IntPtr.Zero;
            clientPt = new POINT();
            title = null;
            if (string.IsNullOrWhiteSpace(spec)) return false;

            string coords = spec;
            int at = spec.IndexOf('@');
            if (at >= 0)
            {
                title = spec.Substring(0, at).Trim();
                coords = spec.Substring(at + 1).Trim();
            }

            hWnd = string.IsNullOrEmpty(title) ? GetForegroundWindow() : ResolveWindowHandle(title);
            if (hWnd == IntPtr.Zero) return false;

            var parts = coords.Split(',');
            if (parts.Length != 2) return false;
            int px = int.Parse(parts[0].Trim(), CultureInfo.InvariantCulture);
            int py = int.Parse(parts[1].Trim(), CultureInfo.InvariantCulture);

            RECT rc;
            if (!GetClientRect(hWnd, out rc)) return false;
            int w = rc.Right - rc.Left;
            int h = rc.Bottom - rc.Top;
            if (w <= 0 || h <= 0) return false;
            if (px < 0 || py < 0 || px >= w || py >= h) return false;

            clientPt = new POINT { X = px, Y = py };
            return true;
        }

        /// <summary>
        /// Click an exact client pixel: "ProcessOrTitle@x,y".
        ///
        /// Percent coordinates are fine for hitting the middle of a control, but a
        /// hitbox edge test asks whether pixel (x0,y0) and pixel (x1,y1) both land
        /// inside the same control, and that question cannot survive a percentage
        /// being rounded back into pixels.
        /// </summary>
        private static bool TryClickWindowClientPixel(string spec)
        {
            IntPtr hWnd;
            POINT pt;
            string title;
            if (!TryParseWindowPixel(spec, out hWnd, out pt, out title)) return false;

            TryFocusWindow(hWnd);
            if (!ClientToScreen(hWnd, ref pt)) return false;

            SetCursorPos(pt.X, pt.Y);
            Thread.Sleep(60);
            MouseLeftClick();
            Thread.Sleep(40);
            return true;
        }

        /// <summary>
        /// Click an absolute primary-monitor pixel: "x,y".
        ///
        /// Used when the agent runs on the operator's own machine and there is no
        /// game window to measure against, so the primary monitor *is* the surface.
        /// </summary>
        private static bool TryClickPrimaryScreenPixel(string spec)
        {
            if (string.IsNullOrWhiteSpace(spec)) return false;
            var parts = spec.Split(',');
            if (parts.Length != 2) return false;
            int px = int.Parse(parts[0].Trim(), CultureInfo.InvariantCulture);
            int py = int.Parse(parts[1].Trim(), CultureInfo.InvariantCulture);
            int w = GetSystemMetrics(0);
            int h = GetSystemMetrics(1);
            if (px < 0 || py < 0 || (w > 0 && px >= w) || (h > 0 && py >= h)) return false;

            SetCursorPos(px, py);
            Thread.Sleep(60);
            MouseLeftClick();
            Thread.Sleep(40);
            return true;
        }

        private static bool TryClickWindowPostMessage(string spec, bool synchronous)
        {
            IntPtr hWnd;
            POINT clientPt;
            string title;
            if (!TryParseWindowPercent(spec, out hWnd, out clientPt, out title)) return false;

            // Prefer the child HWND under the click point (Godot often nests the real target).
            var screenPt = clientPt;
            if (!ClientToScreen(hWnd, ref screenPt)) return false;
            IntPtr target = WindowFromPoint(screenPt);
            if (target == IntPtr.Zero) target = hWnd;

            var local = screenPt;
            if (!ScreenToClient(target, ref local)) return false;

            int lp = (local.Y << 16) | (local.X & 0xFFFF);
            IntPtr lParam = (IntPtr)lp;
            IntPtr wDown = (IntPtr)MK_LBUTTON;

            if (synchronous)
            {
                SendMessage(target, WM_MOUSEMOVE, IntPtr.Zero, lParam);
                Thread.Sleep(8);
                SendMessage(target, WM_LBUTTONDOWN, wDown, lParam);
                Thread.Sleep(40);
                SendMessage(target, WM_LBUTTONUP, IntPtr.Zero, lParam);
            }
            else
            {
                PostMessage(target, WM_MOUSEMOVE, IntPtr.Zero, lParam);
                Thread.Sleep(8);
                PostMessage(target, WM_LBUTTONDOWN, wDown, lParam);
                Thread.Sleep(40);
                PostMessage(target, WM_LBUTTONUP, IntPtr.Zero, lParam);
            }
            Thread.Sleep(20);
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

        private static void MouseLeftButton(uint flag)
        {
            var evt = new INPUT
            {
                type = INPUT_MOUSE,
                U = new InputUnion
                {
                    mi = new MOUSEINPUT { dwFlags = flag, dx = 0, dy = 0, mouseData = 0, time = 0, dwExtraInfo = IntPtr.Zero }
                }
            };
            SendInput(1, new INPUT[] { evt }, Marshal.SizeOf(typeof(INPUT)));
        }

        private static void MouseLeftDown()
        {
            MouseLeftButton(MOUSEEVENTF_LEFTDOWN);
        }

        private static void MouseLeftUp()
        {
            MouseLeftButton(MOUSEEVENTF_LEFTUP);
        }

        private static void MouseLeftClick()
        {
            MouseLeftDown();
            Thread.Sleep(30);
            MouseLeftUp();
        }

        /// <summary>
        /// Press-move-release drag. Spec: "ProcessOrTitle@x1,y1&gt;x2,y2" (client percent).
        /// Godot sliders and the history bar only react to real intermediate motion, so
        /// the travel is emitted as many small SetCursorPos steps, not one jump.
        /// <paramref name="totalMs"/> is the time spent travelling.
        /// </summary>
        private static bool TryDragWindowClientPercent(string spec, int totalMs)
        {
            if (string.IsNullOrWhiteSpace(spec)) return false;
            int arrow = spec.IndexOf('>');
            if (arrow < 0) return false;

            string head = spec.Substring(0, arrow).Trim();
            string tail = spec.Substring(arrow + 1).Trim();
            if (head.Length == 0 || tail.Length == 0) return false;

            // The window is named once, before '@'; reuse it for the end point.
            string title = null;
            int at = head.IndexOf('@');
            if (at >= 0) title = head.Substring(0, at).Trim();
            string tailSpec = string.IsNullOrEmpty(title) ? tail : title + "@" + tail;

            IntPtr hFrom, hTo;
            POINT from, to;
            string t1, t2;
            if (!TryParseWindowPercent(head, out hFrom, out from, out t1)) return false;
            if (!TryParseWindowPercent(tailSpec, out hTo, out to, out t2)) return false;

            TryFocusWindow(hFrom);
            if (!ClientToScreen(hFrom, ref from)) return false;
            if (!ClientToScreen(hTo, ref to)) return false;

            const int steps = 24;
            int travelMs = totalMs > 0 ? totalMs : 600;
            int perStep = Math.Max(4, travelMs / steps);

            SetCursorPos(from.X, from.Y);
            Thread.Sleep(80);
            MouseLeftDown();
            Thread.Sleep(90);
            for (int i = 1; i <= steps; i++)
            {
                int x = from.X + (int)Math.Round((to.X - from.X) * (double)i / steps);
                int y = from.Y + (int)Math.Round((to.Y - from.Y) * (double)i / steps);
                SetCursorPos(x, y);
                Thread.Sleep(perStep);
            }
            Thread.Sleep(90);
            MouseLeftUp();
            Thread.Sleep(40);
            return true;
        }

        /// <summary>
        /// Press and hold without moving. Spec is the same as click_window. Needed for
        /// Godot TouchScreenButton nodes that act on ButtonHeld rather than on release.
        /// </summary>
        private static bool TryHoldWindowClientPercent(string spec, int holdMs)
        {
            IntPtr hWnd;
            POINT pt;
            string title;
            if (!TryParseWindowPercent(spec, out hWnd, out pt, out title)) return false;

            TryFocusWindow(hWnd);
            if (!ClientToScreen(hWnd, ref pt)) return false;

            SetCursorPos(pt.X, pt.Y);
            Thread.Sleep(80);
            MouseLeftDown();
            Thread.Sleep(Math.Max(120, holdMs));
            MouseLeftUp();
            Thread.Sleep(40);
            return true;
        }

        /// <summary>
        /// Save a screenshot to <paramref name="spec"/> = "[process@]path".
        ///
        /// With a process (or window title) the capture is that window's client
        /// area, so the pixels line up 1:1 with the client-percent coordinates the
        /// click steps use; without one it falls back to the primary screen. This
        /// runs in the interactive session, which is why it can see the desktop at
        /// all, and it is ~10x faster than driving a capture over PsExec.
        /// </summary>
        private static bool TryCaptureScreenshot(string spec)
        {
            string title = null;
            string path = spec;
            int at = spec.IndexOf('@');
            if (at >= 0)
            {
                title = spec.Substring(0, at).Trim();
                path = spec.Substring(at + 1).Trim();
            }
            if (string.IsNullOrWhiteSpace(path)) return false;

            // "screen@path" / "primary@path" asks for the whole primary monitor even
            // when a window happens to be in front, which is what a local mapping run
            // on the operator's own machine needs.
            bool forcePrimary = !string.IsNullOrEmpty(title)
                && (string.Equals(title, "screen", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(title, "primary", StringComparison.OrdinalIgnoreCase));

            IntPtr hWnd = forcePrimary
                ? IntPtr.Zero
                : (string.IsNullOrEmpty(title) ? GetForegroundWindow() : ResolveWindowHandle(title));
            int x = 0, y = 0, w = 0, h = 0;
            RECT rc;
            if (hWnd != IntPtr.Zero && GetClientRect(hWnd, out rc) && rc.Right > rc.Left && rc.Bottom > rc.Top)
            {
                var origin = new POINT { X = rc.Left, Y = rc.Top };
                if (ClientToScreen(hWnd, ref origin))
                {
                    x = origin.X;
                    y = origin.Y;
                    w = rc.Right - rc.Left;
                    h = rc.Bottom - rc.Top;
                }
            }
            if (w <= 0 || h <= 0)
            {
                w = GetSystemMetrics(0);
                h = GetSystemMetrics(1);
            }
            if (w <= 0 || h <= 0) return false;

            string dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

            using (var bmp = new System.Drawing.Bitmap(w, h, System.Drawing.Imaging.PixelFormat.Format24bppRgb))
            {
                using (var g = System.Drawing.Graphics.FromImage(bmp))
                {
                    g.CopyFromScreen(x, y, 0, 0, new System.Drawing.Size(w, h),
                        System.Drawing.CopyPixelOperation.SourceCopy);
                }
                string ext = (Path.GetExtension(path) ?? "").ToLowerInvariant();
                var format = (ext == ".jpg" || ext == ".jpeg")
                    ? System.Drawing.Imaging.ImageFormat.Jpeg
                    : System.Drawing.Imaging.ImageFormat.Png;
                bmp.Save(path, format);
            }
            return File.Exists(path);
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

                // "screenshot" writes a PNG/JPEG on the cabinet; ms settles first.
                if (t == "screenshot" || t == "shot")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (st.ms > 0) Thread.Sleep(st.ms);
                        if (!TryCaptureScreenshot(st.value.Trim()))
                            Console.Error.WriteLine("screenshot failed: " + st.value);
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

                if (t == "click_window_px")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryClickWindowClientPixel(st.value.Trim()))
                            Console.Error.WriteLine("click_window_px failed: " + st.value);
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }

                if (t == "click_screen_px")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryClickPrimaryScreenPixel(st.value.Trim()))
                            Console.Error.WriteLine("click_screen_px failed: " + st.value);
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }

                if (t == "click_window" || t == "click")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryClickWindowClientPercent(st.value.Trim()))
                            Console.Error.WriteLine("click_window failed: " + st.value);
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }

                // Invisible Godot/roulette clicks (PostMessage; cursor stays put).
                if (t == "click_post" || t == "click_pm")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryClickWindowPostMessage(st.value.Trim(), synchronous: false))
                            Console.Error.WriteLine("click_post failed: " + st.value);
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }

                if (t == "click_post_sync" || t == "click_pm_sync")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryClickWindowPostMessage(st.value.Trim(), synchronous: true))
                            Console.Error.WriteLine("click_post_sync failed: " + st.value);
                        Thread.Sleep(Math.Max(0, st.ms > 0 ? st.ms : 80));
                    }
                    continue;
                }

                // Sliders / scrollable strips: "godot@x1,y1>x2,y2", ms = travel time.
                if (t == "drag_window" || t == "drag")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryDragWindowClientPercent(st.value.Trim(), st.ms))
                            Console.Error.WriteLine("drag_window failed: " + st.value);
                        Thread.Sleep(60);
                    }
                    continue;
                }

                // ButtonHeld targets: same spec as click_window, ms = hold duration.
                if (t == "hold_window" || t == "hold")
                {
                    if (!string.IsNullOrWhiteSpace(st.value))
                    {
                        if (!TryHoldWindowClientPercent(st.value.Trim(), st.ms))
                            Console.Error.WriteLine("hold_window failed: " + st.value);
                        Thread.Sleep(60);
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

