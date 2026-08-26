// Find CANCELAR TODO x% that clears PlayerDataBets (not BORRADOR drag-eraser).
// Usage: CancelClearCalibrate.exe <outJson> <placeXpct> <placeYpct> <chipXpct> <chipYpct>
//   [yPct=90.5] [x0=76] [x1=90] [step=1]
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class CancelClearCalibrate
{
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint);
    [DllImport("user32.dll")] private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int nIndex);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    private const int SW_RESTORE = 9;
    private const uint INPUT_MOUSE = 0;
    private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    private const uint MOUSEEVENTF_LEFTUP = 0x0004;
    private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    private const uint MOUSEEVENTF_MOVE = 0x0001;
    private const int SM_CXSCREEN = 0, SM_CYSCREEN = 1;

    [StructLayout(LayoutKind.Sequential)] private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] private struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)] private struct InputUnion { [FieldOffset(0)] public MOUSEINPUT mi; }
    [StructLayout(LayoutKind.Sequential)] private struct INPUT { public uint type; public InputUnion U; }

    private static int Main(string[] args)
    {
        if (args.Length < 5)
        {
            Console.Error.WriteLine("usage: CancelClearCalibrate.exe outJson placeX placeY chipX chipY [yPct] [x0] [x1] [step]");
            return 2;
        }
        string outJson = args[0];
        double placeX = D(args[1]), placeY = D(args[2]);
        double chipX = D(args[3]), chipY = D(args[4]);
        double yPct = args.Length >= 6 ? D(args[5]) : 90.5;
        double x0 = args.Length >= 7 ? D(args[6]) : 76.0;
        double x1 = args.Length >= 8 ? D(args[7]) : 90.0;
        double step = args.Length >= 9 ? D(args[8]) : 1.0;

        IntPtr hwnd = FindWindowTitleContains("RouletteGUI");
        if (hwnd == IntPtr.Zero) { Console.Error.WriteLine("WINDOW_NOT_FOUND"); return 3; }
        ShowWindow(hwnd, SW_RESTORE);
        SetForegroundWindow(hwnd);
        Thread.Sleep(200);
        RECT cr; GetClientRect(hwnd, out cr);
        int cw = cr.Right - cr.Left, ch = cr.Bottom - cr.Top;
        POINT origin = new POINT();
        ClientToScreen(hwnd, ref origin);

        var rows = new List<string>();
        double bestX = -1;
        for (double cx = x0; cx <= x1 + 0.001; cx += step)
        {
            // Place a chip somewhere that usually accepts (known Fields/1 center-ish).
            ClickPct(origin, cw, ch, chipX, chipY);
            Thread.Sleep(80);
            ClickPct(origin, cw, ch, placeX, placeY);
            Thread.Sleep(120);
            string before = Fetch();
            string bt, bi; int bc = ParseBets(before, out bt, out bi);
            if (bc <= 0)
            {
                rows.Add(string.Format(CultureInfo.InvariantCulture,
                    "{{\"cancel_x_pct\":{0:0.###},\"placed\":false,\"cleared\":false,\"note\":\"no bet placed\"}}", cx));
                Console.WriteLine("NO_PLACE cancelX=" + cx);
                continue;
            }
            ClickPct(origin, cw, ch, cx, yPct);
            Thread.Sleep(150);
            string after = Fetch();
            string at, ai; int ac = ParseBets(after, out at, out ai);
            bool cleared = ac == 0;
            if (cleared && bestX < 0) { bestX = cx; }
            rows.Add(string.Format(CultureInfo.InvariantCulture,
                "{{\"cancel_x_pct\":{0:0.###},\"y_pct\":{1:0.###},\"placed\":true,\"before_bets\":{2},\"before\":\"{3}/{4}\",\"after_bets\":{5},\"after\":\"{6}/{7}\",\"cleared\":{8}}}",
                cx, yPct, bc, Esc(bt), Esc(bi), ac, Esc(at), Esc(ai), cleared ? "true" : "false"));
            Console.WriteLine((cleared ? "CLEAR " : "KEEP  ") + "x=" + cx + " before=" + bt + "/" + bi + " after_bets=" + ac);
            // If not cleared, try far-right cancel once so next trial starts clean-ish.
            if (!cleared)
            {
                ClickPct(origin, cw, ch, 84.0, yPct);
                Thread.Sleep(120);
                ClickPct(origin, cw, ch, 86.0, yPct);
                Thread.Sleep(120);
            }
        }

        var sb = new StringBuilder();
        sb.Append("{\n");
        sb.Append("  \"best_cancel_x_pct\": ").Append(bestX.ToString("0.###", CultureInfo.InvariantCulture)).Append(",\n");
        sb.Append("  \"y_pct\": ").Append(yPct.ToString("0.###", CultureInfo.InvariantCulture)).Append(",\n");
        sb.Append("  \"trials\": [\n");
        for (int i = 0; i < rows.Count; i++)
        {
            sb.Append("    ").Append(rows[i]);
            if (i + 1 < rows.Count) sb.Append(",");
            sb.Append("\n");
        }
        sb.Append("  ]\n}\n");
        File.WriteAllText(outJson, sb.ToString(), new UTF8Encoding(false));
        Console.WriteLine("BEST " + bestX);
        return bestX >= 0 ? 0 : 1;
    }

    private static void ClickPct(POINT origin, int cw, int ch, double xp, double yp)
    {
        int x = (int)Math.Round(xp / 100.0 * cw);
        int y = (int)Math.Round(yp / 100.0 * ch);
        ClickClient(origin, x, y);
    }

    private static void ClickClient(POINT origin, int x, int y)
    {
        int sx = origin.X + x, sy = origin.Y + y;
        SetCursorPos(sx, sy);
        Thread.Sleep(15);
        int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
        int ax = (int)Math.Round(sx * 65535.0 / Math.Max(1, sw - 1));
        int ay = (int)Math.Round(sy * 65535.0 / Math.Max(1, sh - 1));
        INPUT[] inputs = new INPUT[3];
        inputs[0].type = INPUT_MOUSE;
        inputs[0].U.mi.dx = ax; inputs[0].U.mi.dy = ay;
        inputs[0].U.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
        inputs[1].type = INPUT_MOUSE;
        inputs[1].U.mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
        inputs[2].type = INPUT_MOUSE;
        inputs[2].U.mi.dwFlags = MOUSEEVENTF_LEFTUP;
        SendInput(3, inputs, Marshal.SizeOf(typeof(INPUT)));
    }

    private static string Fetch()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8090/api/data/0");
            req.Timeout = 2000; req.ReadWriteTimeout = 2000;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream()))
                return sr.ReadToEnd();
        }
        catch (Exception ex) { return "ERROR:" + ex.Message; }
    }

    private static int ParseBets(string body, out string betType, out string betId)
    {
        betType = ""; betId = "";
        if (string.IsNullOrEmpty(body) || body.StartsWith("ERROR:")) return 0;
        int i = body.IndexOf("\"Bets\"", StringComparison.Ordinal);
        if (i < 0) return 0;
        int lb = body.IndexOf('[', i); if (lb < 0) return 0;
        int rb = body.IndexOf(']', lb); if (rb < 0) return 0;
        string arr = body.Substring(lb, rb - lb + 1);
        if (arr == "[]" || arr.Replace(" ", "") == "[]") return 0;
        betId = ExtractStr(arr, "\"Id\"");
        betType = ExtractStr(arr, "\"BetType\"");
        int count = 0, pos = 0;
        while ((pos = arr.IndexOf("\"BetType\"", pos, StringComparison.Ordinal)) >= 0) { count++; pos += 8; }
        if (count == 0 && arr.IndexOf('{') >= 0) count = 1;
        return count;
    }

    private static string ExtractStr(string s, string key)
    {
        int i = s.IndexOf(key, StringComparison.Ordinal); if (i < 0) return "";
        int c = s.IndexOf(':', i); if (c < 0) return "";
        int q1 = s.IndexOf('"', c + 1); if (q1 < 0) return "";
        int q2 = s.IndexOf('"', q1 + 1); if (q2 < 0) return "";
        return s.Substring(q1 + 1, q2 - q1 - 1);
    }

    private static IntPtr FindWindowTitleContains(string part)
    {
        IntPtr found = IntPtr.Zero; int best = 0;
        EnumWindows((h, l) =>
        {
            if (!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(512);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString().IndexOf(part, StringComparison.OrdinalIgnoreCase) < 0) return true;
            RECT r; GetClientRect(h, out r);
            int area = Math.Max(0, r.Right - r.Left) * Math.Max(0, r.Bottom - r.Top);
            if (area > best) { best = area; found = h; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    private static double D(string s)
    {
        return double.Parse(s, CultureInfo.InvariantCulture);
    }

    private static string Esc(string s)
    {
        return (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
    }
}
