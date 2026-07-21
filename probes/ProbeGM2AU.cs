using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.Remoting;
using System.Runtime.Remoting.Channels;
using System.Runtime.Remoting.Channels.Http;

class Program
{
    static string logPath = @"\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-17.log";
    static int initialLogLines = 0;

    static void Main(string[] args)
    {
        Console.WriteLine("=== GM2AU Probe Starting ===");
        Console.WriteLine("Timestamp: " + DateTime.Now);

        IDictionary props = new Hashtable();
        props["name"] = "http_probe";
        props["port"] = 0;
        HttpChannel channel = new HttpChannel(props, null, null);
        ChannelServices.RegisterChannel(channel, false);
        Console.WriteLine("[+] HttpChannel registered");

        string url = "http://localhost:50010/GM2AU";
        Console.WriteLine("[+] Connecting to: " + url);

        object proxy = Activator.GetObject(typeof(MarshalByRefObject), url);

        if (proxy == null)
        {
            Console.WriteLine("[-] Proxy is null - connection failed");
            return;
        }

        Console.WriteLine("[+] Proxy obtained: " + proxy.GetType().FullName);
        Console.WriteLine("[+] Proxy type: " + proxy.GetType().ToString());

        Type t = proxy.GetType();
        Console.WriteLine("\n=== ENUMERATING MEMBERS ===");

        MethodInfo[] methods = t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly);
        Console.WriteLine("\n--- METHODS (" + methods.Length + " found) ---");
        foreach (MethodInfo m in methods)
        {
            ParameterInfo[] ps = m.GetParameters();
            string[] paramStrs = new string[ps.Length];
            for (int i = 0; i < ps.Length; i++)
            {
                paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
            }
            string sig = m.Name + "(" + string.Join(", ", paramStrs) + ")";
            Console.WriteLine("  " + sig);
        }

        PropertyInfo[] propInfos = t.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly);
        Console.WriteLine("\n--- PROPERTIES (" + propInfos.Length + " found) ---");
        foreach (PropertyInfo p in propInfos)
        {
            Console.WriteLine("  " + p.PropertyType.Name + " " + p.Name);
        }

        FieldInfo[] fieldInfos = t.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly);
        Console.WriteLine("\n--- FIELDS (" + fieldInfos.Length + " found) ---");
        foreach (FieldInfo f in fieldInfos)
        {
            Console.WriteLine("  " + f.FieldType.Name + " " + f.Name);
        }

        MethodInfo[] allMethods = t.GetMethods(BindingFlags.Public | BindingFlags.Instance);
        Console.WriteLine("\n--- ALL PUBLIC METHODS (including inherited, " + allMethods.Length + " found) ---");
        foreach (MethodInfo m in allMethods)
        {
            if (m.DeclaringType != typeof(object))
            {
                ParameterInfo[] ps = m.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++)
                {
                    paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                }
                string sig = m.DeclaringType.Name + "." + m.Name + "(" + string.Join(", ", paramStrs) + ")";
                Console.WriteLine("  " + sig);
            }
        }

        string[] keywords = new string[] {
            "credit", "transfer", "award", "bonus", "meter", "cash", "commit",
            "authorize", "request", "bet", "win", "pay", "coin", "bill",
            "ticket", "voucher", "balance", "amount", "add", "set", "get",
            "update", "write", "flush", "save", "load", "init", "start",
            "stop", "clear", "reset", "game", "play", "spin", "hand",
            "session", "event", "log", "send", "receive", "process",
            "handle", "execute", "run", "perform", "apply", "change",
            "modify", "new", "create", "delete", "remove", "insert",
            "push", "pull", "fetch", "retrieve", "store", "record",
            "track", "count", "total", "sum", "calc", "compute",
            "check", "verify", "validate", "confirm", "accept", "reject",
            "deny", "allow", "enable", "disable", "open", "close",
            "lock", "unlock"
        };

        Console.WriteLine("\n=== CREDIT-RELATED METHODS ===");
        ArrayList creditMethods = new ArrayList();
        foreach (MethodInfo m in methods)
        {
            string lower = m.Name.ToLower();
            foreach (string kw in keywords)
            {
                if (lower.Contains(kw))
                {
                    creditMethods.Add(m);
                    Console.WriteLine("  [MATCH] " + m.Name);
                    break;
                }
            }
        }

        if (creditMethods.Count == 0)
        {
            Console.WriteLine("  (no keyword matches found - will try all methods)");
            foreach (MethodInfo m in methods) creditMethods.Add(m);
        }

        try
        {
            if (File.Exists(logPath))
            {
                initialLogLines = File.ReadAllLines(logPath).Length;
                Console.WriteLine("\n[+] Initial log lines: " + initialLogLines);
            }
            else
            {
                Console.WriteLine("\n[+] Log file not found: " + logPath);
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("\n[+] Could not read log: " + ex.Message);
        }

        Console.WriteLine("\n=== INVOKING METHODS ===");
        foreach (MethodInfo m in creditMethods)
        {
            ParameterInfo[] ps = m.GetParameters();
            Console.WriteLine("\n--- Calling: " + m.Name + " ---");

            try
            {
                object[] callArgs = BuildArgs(ps);
                object result = m.Invoke(proxy, callArgs);
                Console.WriteLine("  [OK] Result: " + (result != null ? result.ToString() : "null"));
                CheckLogForCashlessIn(m.Name);
            }
            catch (TargetInvocationException tie)
            {
                Exception inner = tie.InnerException != null ? tie.InnerException : tie;
                Console.WriteLine("  [FAIL] " + inner.GetType().Name + ": " + inner.Message);
                if (inner.InnerException != null)
                    Console.WriteLine("    Inner: " + inner.InnerException.Message);
            }
            catch (Exception ex)
            {
                Console.WriteLine("  [FAIL] " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        Console.WriteLine("\n=== PROBE COMPLETE ===");
    }

    static object[] BuildArgs(ParameterInfo[] ps)
    {
        object[] args = new object[ps.Length];
        for (int i = 0; i < ps.Length; i++)
        {
            Type pt = ps[i].ParameterType;
            if (pt == typeof(int) || pt == typeof(Int32)) args[i] = 100;
            else if (pt == typeof(long) || pt == typeof(Int64)) args[i] = 100L;
            else if (pt == typeof(double)) args[i] = 100.0;
            else if (pt == typeof(float)) args[i] = 100.0f;
            else if (pt == typeof(bool)) args[i] = true;
            else if (pt == typeof(string)) args[i] = "probe_test";
            else if (pt == typeof(byte)) args[i] = (byte)1;
            else if (pt == typeof(short)) args[i] = (short)1;
            else if (pt.IsEnum) args[i] = Enum.GetValues(pt).Length > 0 ? Enum.GetValues(pt).GetValue(0) : null;
            else if (pt.IsArray)
            {
                Type elem = pt.GetElementType();
                if (elem == typeof(int)) args[i] = new int[] { 1, 2, 3 };
                else if (elem == typeof(string)) args[i] = new string[] { "test" };
                else args[i] = Array.CreateInstance(elem, 0);
            }
            else
            {
                try
                {
                    args[i] = Activator.CreateInstance(pt);
                }
                catch
                {
                    args[i] = null;
                }
            }
        }
        return args;
    }

    static void CheckLogForCashlessIn(string methodName)
    {
        try
        {
            if (!File.Exists(logPath)) return;

            string[] lines = File.ReadAllLines(logPath);
            int newLines = lines.Length - initialLogLines;
            if (newLines <= 0) return;

            for (int i = initialLogLines; i < lines.Length; i++)
            {
                string lower = lines[i].ToLower();
                if (lower.Contains("cashless") || lower.Contains("credit") || lower.Contains("transfer") || lower.Contains("award") || lower.Contains("bonus"))
                {
                    Console.WriteLine("  [LOG] " + lines[i].Trim());
                }
            }
            initialLogLines = lines.Length;
        }
        catch { }
    }
}
