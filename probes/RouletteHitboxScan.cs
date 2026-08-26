// RouletteHitboxScan.exe - fast pixel sweep of a cloth ROI on Alegro Godot.
// Runs ON the cabinet. After each click, GETs http://127.0.0.1:8090/api/data/0
// and records whether middleware accepted the expected bet (e.g. Fields Id=1).
//
// Clear / chip select (docs/action-endpoint-commands.md):
//   PUT /api/action/0  CancelAllBets  + UI CANCELAR TODO click (not BORRADOR)
//   PUT /api/action/0  SetChip "0"    + UI chip click (Godot needs visual arm)
// Cloth placement is still a real pixel click (bot needs hitbox coords).
//
// Usage:
//   RouletteHitboxScan.exe <outJson> <expectType> <expectId>
//     <chipXpct> <chipYpct> <cancelXpct> <cancelYpct>
//     <roiX0pct> <roiY0pct> <roiX1pct> <roiY1pct> <stepPx>
//     [maxPoints=200] [processName=godot] [durationMs=16000]
//
// cancelX/Y must be CANCELAR TODO (~85%,90.5) — BORRADOR (~74%) does not clear.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class RouletteHitboxScan
{
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    private static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint);
    [DllImport("user32.dll")]
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    private const int SM_CXSCREEN = 0;
    private const int SM_CYSCREEN = 1;

    private const int SW_RESTORE = 9;
    private const uint INPUT_MOUSE = 0;
    private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    private const uint MOUSEEVENTF_LEFTUP = 0x0004;
    private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    private const uint MOUSEEVENTF_MOVE = 0x0001;

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT
    {
        public uint type;
        public InputUnion U;
    }

    private static int Main(string[] args)
    {
        if (args.Length < 12)
        {
            Console.Error.WriteLine("usage: RouletteHitboxScan.exe outJson expectType expectId chipX chipY cancelX cancelY x0 y0 x1 y1 stepPx [maxPoints] [process]");
            return 2;
        }
        string outJson = args[0];
        string expectType = args[1];
        string expectId = args[2];
        double chipX = ParseD(args[3]), chipY = ParseD(args[4]);
        double cancelX = ParseD(args[5]), cancelY = ParseD(args[6]);
        double x0 = ParseD(args[7]), y0 = ParseD(args[8]), x1 = ParseD(args[9]), y1 = ParseD(args[10]);
        int stepPx = Math.Max(1, ParseI(args[11], 4));
        int maxPoints = args.Length >= 13 ? ParseI(args[12], 220) : 220;
        string procName = args.Length >= 14 ? args[13] : "godot";
        int durationMs = args.Length >= 15 ? ParseI(args[14], 16000) : 16000;

        IntPtr hwnd = FindProcessWindow(procName);
        if (hwnd == IntPtr.Zero)
        {
            Console.Error.WriteLine("WINDOW_NOT_FOUND process=" + procName);
            return 3;
        }
        ShowWindow(hwnd, SW_RESTORE);
        SetForegroundWindow(hwnd);
        Thread.Sleep(200);

        RECT cr;
        if (!GetClientRect(hwnd, out cr))
        {
            Console.Error.WriteLine("GetClientRect failed");
            return 4;
        }
        int cw = cr.Right - cr.Left;
        int ch = cr.Bottom - cr.Top;
        if (cw < 100 || ch < 100)
        {
            Console.Error.WriteLine("bad client size " + cw + "x" + ch);
            return 5;
        }
        POINT origin = new POINT();
        origin.X = 0; origin.Y = 0;
        ClientToScreen(hwnd, ref origin);

        Console.WriteLine("CLIENT " + cw + "x" + ch + " screen=" + origin.X + "," + origin.Y);
        Console.WriteLine("EXPECT " + expectType + " id=" + expectId);
        Console.WriteLine("ROI_PCT " + x0 + "," + y0 + " .. " + x1 + "," + y1 + " step=" + stepPx);
        Console.WriteLine("CLEAR_MODE api CancelAllBets + UI CANCELAR " + cancelX + "," + cancelY);

        // Clear board (API + UI) and arm chip.
        PutAction(0, "CancelAllBets", null);
        Thread.Sleep(40);
        ClickPct(origin, cw, ch, cancelX, cancelY);
        Thread.Sleep(80);
        PutAction(0, "SetChip", "0");
        Thread.Sleep(40);
        ClickPct(origin, cw, ch, chipX, chipY);
        Thread.Sleep(80);

        int rx0 = Clamp((int)Math.Round(x0 / 100.0 * cw), 0, cw - 1);
        int ry0 = Clamp((int)Math.Round(y0 / 100.0 * ch), 0, ch - 1);
        int rx1 = Clamp((int)Math.Round(x1 / 100.0 * cw), 0, cw - 1);
        int ry1 = Clamp((int)Math.Round(y1 / 100.0 * ch), 0, ch - 1);
        if (rx1 < rx0) { int t = rx0; rx0 = rx1; rx1 = t; }
        if (ry1 < ry0) { int t = ry0; ry0 = ry1; ry1 = t; }

        var hits = new List<string>();
        var wrong = new List<string>();
        var misses = new List<string>();
        int probed = 0;
        var sw = Stopwatch.StartNew();

        for (int y = ry0; y <= ry1 && probed < maxPoints; y += stepPx)
        {
            for (int x = rx0; x <= rx1 && probed < maxPoints; x += stepPx)
            {
                if (sw.ElapsedMilliseconds > durationMs)
                {
                    Console.WriteLine("TIME_BUDGET_DONE probed=" + probed);
                    goto DoneScan;
                }
                probed++;
                // Clear: API CancelAllBets + UI CANCELAR TODO (never BORRADOR).
                int clearCount = 0;
                string clearType = "", clearId = "";
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    PutAction(0, "CancelAllBets", null);
                    Thread.Sleep(30);
                    ClickPct(origin, cw, ch, cancelX, cancelY);
                    Thread.Sleep(60);
                    string clearBody = FetchData(0);
                    clearCount = ParseBets(clearBody, out clearType, out clearId);
                    if (clearCount == 0) { break; }
                }
                if (clearCount > 0)
                {
                    Console.WriteLine("CANCEL_STUCK " + x + "," + y + " bets=" + clearCount + " " + clearType + "/" + clearId);
                    continue;
                }
                PutAction(0, "SetChip", "0");
                Thread.Sleep(20);
                ClickPct(origin, cw, ch, chipX, chipY);
                Thread.Sleep(35);
                ClickClient(origin, x, y);
                Thread.Sleep(70);

                string body = FetchData(0);
                string betType, betId;
                int betsCount = ParseBets(body, out betType, out betId);
                // Alegro names straight-up as BetType "Fields". Accept "Straight" as alias.
                bool typeOk = betType.IndexOf(expectType, StringComparison.OrdinalIgnoreCase) >= 0
                    || (expectType.Equals("Straight", StringComparison.OrdinalIgnoreCase)
                        && betType.IndexOf("Fields", StringComparison.OrdinalIgnoreCase) >= 0)
                    || (expectType.Equals("Fields", StringComparison.OrdinalIgnoreCase)
                        && betType.IndexOf("Straight", StringComparison.OrdinalIgnoreCase) >= 0);
                // Exact single-bet match only.
                bool match = betsCount == 1 && typeOk && betId == expectId;

                string row = string.Format(CultureInfo.InvariantCulture,
                    "{{\"x\":{0},\"y\":{1},\"x_pct\":{2:0.###},\"y_pct\":{3:0.###},\"bets\":{4},\"betType\":\"{5}\",\"betId\":\"{6}\"}}",
                    x, y, 100.0 * x / cw, 100.0 * y / ch, betsCount,
                    Esc(betType), Esc(betId));

                if (match) { hits.Add(row); Console.WriteLine("HIT " + x + "," + y + " " + betType + "/" + betId); }
                else if (betsCount > 0) { wrong.Add(row); Console.WriteLine("WRONG " + x + "," + y + " " + betType + "/" + betId); }
                else { misses.Add(row); }

                PutAction(0, "CancelAllBets", null);
                ClickPct(origin, cw, ch, cancelX, cancelY);
                Thread.Sleep(40);
            }
        }
    DoneScan:

        // Bounding box from hits (client pixels).
        int bx = 0, by = 0, bw = 0, bh = 0;
        if (hits.Count > 0)
        {
            int minX = int.MaxValue, minY = int.MaxValue, maxX = int.MinValue, maxY = int.MinValue;
            foreach (string h in hits)
            {
                // crude parse x/y from JSON snippet
                int xi = ExtractInt(h, "\"x\":");
                int yi = ExtractInt(h, "\"y\":");
                if (xi < minX) minX = xi;
                if (yi < minY) minY = yi;
                if (xi > maxX) maxX = xi;
                if (yi > maxY) maxY = yi;
            }
            // Expand by half step so bbox covers the sample cell.
            int pad = stepPx / 2;
            bx = Math.Max(0, minX - pad);
            by = Math.Max(0, minY - pad);
            int xRight = Math.Min(cw - 1, maxX + pad);
            int yBot = Math.Min(ch - 1, maxY + pad);
            bw = Math.Max(1, xRight - bx + 1);
            bh = Math.Max(1, yBot - by + 1);
        }

        string buttonId = "straightup_" + expectId;
        if (expectId == "1") { buttonId = "straightup_red_1"; }

        var sb = new StringBuilder();
        sb.Append("{\n");
        sb.Append("  \"layout_id\": \"layout1\",\n");
        sb.Append("  \"chip\": \"chip_1\",\n");
        sb.Append("  \"expect\": {\"BetType\":\"").Append(Esc(expectType)).Append("\",\"Id\":\"").Append(Esc(expectId)).Append("\"},\n");
        sb.Append("  \"client\": {\"width\":").Append(cw).Append(",\"height\":").Append(ch).Append("},\n");
        sb.Append("  \"roi_pct\": {\"x0\":").Append(F(x0)).Append(",\"y0\":").Append(F(y0))
          .Append(",\"x1\":").Append(F(x1)).Append(",\"y1\":").Append(F(y1)).Append("},\n");
        sb.Append("  \"step_px\": ").Append(stepPx).Append(",\n");
        sb.Append("  \"probed\": ").Append(probed).Append(",\n");
        sb.Append("  \"elapsed_ms\": ").Append(sw.ElapsedMilliseconds).Append(",\n");
        sb.Append("  \"button\": {\n");
        sb.Append("    \"id\": \"").Append(buttonId).Append("\",\n");
        sb.Append("    \"x\": ").Append(bx).Append(",\n");
        sb.Append("    \"y\": ").Append(by).Append(",\n");
        sb.Append("    \"width\": ").Append(bw).Append(",\n");
        sb.Append("    \"height\": ").Append(bh).Append("\n");
        sb.Append("  },\n");
        sb.Append("  \"hit_count\": ").Append(hits.Count).Append(",\n");
        sb.Append("  \"wrong_count\": ").Append(wrong.Count).Append(",\n");
        sb.Append("  \"miss_count\": ").Append(misses.Count).Append(",\n");
        sb.Append("  \"hits\": [").Append(string.Join(",", hits.ToArray())).Append("],\n");
        sb.Append("  \"wrong\": [").Append(string.Join(",", wrong.ToArray())).Append("]\n");
        sb.Append("}\n");

        File.WriteAllText(outJson, sb.ToString(), new UTF8Encoding(false));
        Console.WriteLine("WROTE " + outJson);
        Console.WriteLine("BUTTON x=" + bx + " y=" + by + " w=" + bw + " h=" + bh + " hits=" + hits.Count);
        return hits.Count > 0 ? 0 : 1;
    }

    private static void ClickPct(POINT origin, int cw, int ch, double xp, double yp)
    {
        int x = Clamp((int)Math.Round(xp / 100.0 * cw), 0, cw - 1);
        int y = Clamp((int)Math.Round(yp / 100.0 * ch), 0, ch - 1);
        ClickClient(origin, x, y);
    }

    private static void ClickClient(POINT origin, int cx, int cy)
    {
        int sx = origin.X + cx;
        int sy = origin.Y + cy;
        // Absolute SendInput in 0..65535 screen space.
        int sw = Math.Max(1, GetSystemMetrics(SM_CXSCREEN));
        int sh = Math.Max(1, GetSystemMetrics(SM_CYSCREEN));
        int vx = (int)Math.Round(sx * 65535.0 / sw);
        int vy = (int)Math.Round(sy * 65535.0 / sh);
        SetCursorPos(sx, sy);
        Thread.Sleep(8);
        INPUT[] down = new INPUT[1];
        down[0].type = INPUT_MOUSE;
        down[0].U.mi.dx = vx; down[0].U.mi.dy = vy;
        down[0].U.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN;
        INPUT[] up = new INPUT[1];
        up[0].type = INPUT_MOUSE;
        up[0].U.mi.dx = vx; up[0].U.mi.dy = vy;
        up[0].U.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP;
        SendInput(1, down, Marshal.SizeOf(typeof(INPUT)));
        Thread.Sleep(12);
        SendInput(1, up, Marshal.SizeOf(typeof(INPUT)));
    }

    private static string FetchData(int player)
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8090/api/data/" + player);
            req.Method = "GET";
            req.Timeout = 1500;
            req.ReadWriteTimeout = 1500;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return sr.ReadToEnd();
            }
        }
        catch (Exception ex)
        {
            return "ERROR:" + ex.Message;
        }
    }

    /// <summary>
    /// PUT /api/action/{player}  body { "Type": type, "Data": dataOrNull }.
    /// Data for SetChip is a JSON string value (e.g. "0"); CancelAllBets omits Data.
    /// </summary>
    private static bool PutAction(int player, string type, string dataStringOrNull)
    {
        try
        {
            string body;
            if (dataStringOrNull == null)
                body = "{\"Type\":\"" + Esc(type) + "\"}";
            else
                body = "{\"Type\":\"" + Esc(type) + "\",\"Data\":\"" + Esc(dataStringOrNull) + "\"}";
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8090/api/action/" + player);
            req.Method = "PUT";
            req.ContentType = "application/json";
            req.Timeout = 1500;
            req.ReadWriteTimeout = 1500;
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            req.ContentLength = bytes.Length;
            using (var s = req.GetRequestStream()) { s.Write(bytes, 0, bytes.Length); }
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                string respBody = sr.ReadToEnd();
                return respBody.IndexOf("\"Success\":true", StringComparison.OrdinalIgnoreCase) >= 0
                    || respBody.IndexOf("\"Success\": true", StringComparison.OrdinalIgnoreCase) >= 0;
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("PUT_ACTION_ERR " + type + " " + ex.Message);
            return false;
        }
    }

    private static int ParseBets(string body, out string betType, out string betId)
    {
        betType = "";
        betId = "";
        if (string.IsNullOrEmpty(body) || body.StartsWith("ERROR:")) { return 0; }
        // Find PlayerDataBets block roughly: "Bets":[ {...} ]
        int i = body.IndexOf("\"Bets\"", StringComparison.Ordinal);
        if (i < 0) { return 0; }
        int lb = body.IndexOf('[', i);
        if (lb < 0) { return 0; }
        int rb = body.IndexOf(']', lb);
        if (rb < 0) { return 0; }
        string arr = body.Substring(lb, rb - lb + 1);
        if (arr == "[]" || arr.Replace(" ", "") == "[]") { return 0; }
        // First object Id / BetType
        betId = ExtractStr(arr, "\"Id\"");
        betType = ExtractStr(arr, "\"BetType\"");
        // Count objects crudely
        int count = 0;
        int pos = 0;
        while ((pos = arr.IndexOf("\"BetType\"", pos, StringComparison.Ordinal)) >= 0)
        {
            count++;
            pos += 8;
        }
        if (count == 0 && arr.IndexOf('{') >= 0) { count = 1; }
        return count;
    }

    private static string ExtractStr(string s, string key)
    {
        int i = s.IndexOf(key, StringComparison.Ordinal);
        if (i < 0) { return ""; }
        int c = s.IndexOf(':', i);
        if (c < 0) { return ""; }
        int q1 = s.IndexOf('"', c + 1);
        if (q1 < 0) { return ""; }
        int q2 = s.IndexOf('"', q1 + 1);
        if (q2 < 0) { return ""; }
        return s.Substring(q1 + 1, q2 - q1 - 1);
    }

    private static int ExtractInt(string s, string key)
    {
        int i = s.IndexOf(key, StringComparison.Ordinal);
        if (i < 0) { return 0; }
        i += key.Length;
        int j = i;
        while (j < s.Length && (char.IsDigit(s[j]) || s[j] == '-')) { j++; }
        int v;
        return int.TryParse(s.Substring(i, j - i), out v) ? v : 0;
    }

    private static IntPtr FindProcessWindow(string processName)
    {
        // Alegro Godot often has MainWindowHandle=0 — use title + largest visible HWND.
        IntPtr byTitle = FindWindowTitleContains("RouletteGUI");
        if (byTitle != IntPtr.Zero) { return byTitle; }

        var pids = new Dictionary<uint, bool>();
        try
        {
            foreach (Process p in Process.GetProcessesByName(processName))
            {
                pids[(uint)p.Id] = true;
                if (p.MainWindowHandle != IntPtr.Zero) { return p.MainWindowHandle; }
            }
        }
        catch { }

        IntPtr best = IntPtr.Zero;
        long bestArea = 0;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd)) { return true; }
            uint pid;
            GetWindowThreadProcessId(hWnd, out pid);
            if (!pids.ContainsKey(pid)) { return true; }
            RECT rc;
            if (!GetClientRect(hWnd, out rc)) { return true; }
            long area = (long)(rc.Right - rc.Left) * (rc.Bottom - rc.Top);
            if (area > bestArea) { bestArea = area; best = hWnd; }
            return true;
        }, IntPtr.Zero);
        return best;
    }

    private static IntPtr FindWindowTitleContains(string substring)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd)) { return true; }
            StringBuilder sb = new StringBuilder(512);
            if (GetWindowText(hWnd, sb, sb.Capacity) <= 0) { return true; }
            if (sb.ToString().IndexOf(substring, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    private static int Clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
    private static double ParseD(string s) { double v; return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : 0; }
    private static int ParseI(string s, int d) { int v; return int.TryParse(s, out v) ? v : d; }
    private static string Esc(string s) { return (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\""); }
    private static string F(double d) { return d.ToString("0.###", CultureInfo.InvariantCulture); }
}
