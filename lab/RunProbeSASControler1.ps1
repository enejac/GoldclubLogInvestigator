$cred = New-Object System.Management.Automation.PSCredential("GOLD-CLUB\test", (ConvertTo-SecureString "test" -AsPlainText -Force))
$session = New-PSSession -ComputerName 10.0.0.90 -Credential $cred
Invoke-Command -Session $session -ScriptBlock {
    $csharpCode = @'
using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.Remoting;
using System.Runtime.Remoting.Channels;
using System.Runtime.Remoting.Channels.Http;
using System.Text;

public class Probe
{
    static StringBuilder log = new StringBuilder();

    static void Log(string msg)
    {
        Console.WriteLine(msg);
        log.AppendLine(msg);
    }

    static public void Main(string[] args)
    {
        try
        {
            // Step 1: Register HttpChannel
            Log("=== Step 1: Registering HttpChannel ===");
            Hashtable props = new Hashtable();
            props["name"] = "probeHttp";
            HttpChannel channel = new HttpChannel(props, null, null);
            ChannelServices.RegisterChannel(channel, false);
            Log("HttpChannel registered OK");

            // Step 2: Connect to remote object
            Log("");
            Log("=== Step 2: Connecting to http://localhost:50011/SASControler1 ===");
            object obj = Activator.GetObject(typeof(MarshalByRefObject), "http://localhost:50011/SASControler1");
            if (obj == null)
            {
                Log("ERROR: Got null object reference");
                return;
            }
            Log("Object type: " + obj.GetType().FullName);
            Log("Is transparent proxy: " + RemotingServices.IsTransparentProxy(obj));

            // Step 3: Enumerate ALL methods and properties
            Log("");
            Log("=== Step 3: Enumerating methods/properties ===");
            Type type = obj.GetType();
            MethodInfo[] methods = type.GetMethods(BindingFlags.Public | BindingFlags.Instance);
            Log("Total methods found: " + methods.Length);
            foreach (MethodInfo m in methods)
            {
                ParameterInfo[] ps = m.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++)
                {
                    paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                }
                string paramStr = string.Join(", ", paramStrs);
                Log("  " + m.ReturnType.Name + " " + m.Name + "(" + paramStr + ")");
            }

            PropertyInfo[] props_info = type.GetProperties(BindingFlags.Public | BindingFlags.Instance);
            Log("");
            Log("Total properties found: " + props_info.Length);
            foreach (PropertyInfo p in props_info)
            {
                string getter = p.CanRead ? "get; " : "";
                string setter = p.CanWrite ? "set; " : "";
                Log("  " + p.PropertyType.Name + " " + p.Name + " { " + getter + setter + "}");
            }

            // Step 4: Try to identify WAT-related members
            Log("");
            Log("=== Step 4: Looking for WAT/SAS/WideAreaProgressive members ===");
            foreach (MethodInfo m in methods)
            {
                string lower = m.Name.ToLower();
                if (lower.Contains("wat") || lower.Contains("sas") || lower.Contains("transfer") ||
                    lower.Contains("transaction") || lower.Contains("command") || lower.Contains("authorize") ||
                    lower.Contains("commit") || lower.Contains("request"))
                {
                    ParameterInfo[] ps = m.GetParameters();
                    string[] paramStrs = new string[ps.Length];
                    for (int i = 0; i < ps.Length; i++)
                    {
                        paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                    }
                    string paramStr = string.Join(", ", paramStrs);
                    Log("  [RELEVANT] " + m.ReturnType.Name + " " + m.Name + "(" + paramStr + ")");
                }
            }

            // Step 5: Try GetNewCommand with WAT device class
            Log("");
            Log("=== Step 5: Try GetNewCommand(deviceClassBase.WAT) ===");
            MethodInfo getNewCommand = type.GetMethod("GetNewCommand");
            if (getNewCommand != null)
            {
                ParameterInfo[] ps = getNewCommand.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++)
                {
                    paramStrs[i] = ps[i].ParameterType.FullName + " " + ps[i].Name;
                }
                Log("  GetNewCommand params: " + string.Join(", ", paramStrs));

                // Try to find deviceClassBase enum/type
                Type deviceClassType = null;
                foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try
                    {
                        foreach (Type t in asm.GetTypes())
                        {
                            if (t.Name.Contains("deviceClassBase") || t.Name.Contains("DeviceClass"))
                            {
                                Log("  Found type in " + asm.GetName().Name + ": " + t.FullName);
                                if (t.IsEnum)
                                {
                                    foreach (string val in Enum.GetNames(t))
                                    {
                                        Log("    Enum value: " + val + " = " + (int)Enum.Parse(t, val));
                                    }
                                    deviceClassType = t;
                                }
                            }
                        }
                    }
                    catch { }
                }

                if (deviceClassType != null && deviceClassType.IsEnum)
                {
                    try
                    {
                        object watVal = Enum.Parse(deviceClassType, "WAT");
                        Log("  deviceClassBase.WAT = " + watVal + " (" + (int)watVal + ")");
                        object cmd = getNewCommand.Invoke(obj, new object[] { watVal });
                        Log("  GetNewCommand(WAT) returned: " + (cmd != null ? cmd.GetType().FullName : "null"));
                        if (cmd != null) DumpObject(cmd, "WAT Command");
                    }
                    catch (Exception ex)
                    {
                        Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                        Log("  GetNewCommand(WAT) failed: " + inner.Message);
                    }
                }
                else
                {
                    Log("  Could not resolve deviceClassBase type - trying numeric values");
                    for (int i = 0; i <= 10; i++)
                    {
                        try
                        {
                            object cmd = getNewCommand.Invoke(obj, new object[] { i });
                            Log("  GetNewCommand(" + i + ") returned: " + (cmd != null ? cmd.GetType().FullName : "null"));
                            if (cmd != null) DumpObject(cmd, "Command(" + i + ")");
                        }
                        catch (Exception ex)
                        {
                            Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                            Log("  GetNewCommand(" + i + ") failed: " + inner.Message);
                        }
                    }
                }
            }
            else
            {
                Log("  GetNewCommand method not found");
            }

            // Step 6: KEY VECTOR - Pre-create AurumTransaction before requestTransfer
            Log("");
            Log("=== Step 6: Pre-create AurumTransaction vector ===");

            // Look for transaction-related methods
            ArrayList txMethods = new ArrayList();
            foreach (MethodInfo m in methods)
            {
                string lower = m.Name.ToLower();
                if (lower.Contains("transaction") || lower.Contains("create") || lower.Contains("new"))
                {
                    txMethods.Add(m);
                }
            }

            if (txMethods.Count > 0)
            {
                Log("  Transaction-related methods:");
                foreach (MethodInfo m in txMethods)
                {
                    ParameterInfo[] ps = m.GetParameters();
                    string[] paramStrs = new string[ps.Length];
                    for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                    Log("    " + m.Name + "(" + string.Join(", ", paramStrs) + ")");
                }
            }

            // Try to find AurumTransaction type
            Type aurumTxType = null;
            foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    foreach (Type t in asm.GetTypes())
                    {
                        if (t.Name.Contains("AurumTransaction"))
                        {
                            Log("  Found AurumTransaction type: " + t.FullName + " in " + asm.GetName().Name);
                            aurumTxType = t;
                        }
                    }
                }
                catch { }
            }

            object preCreatedTx = null;
            if (aurumTxType != null)
            {
                Log("  Attempting to create " + aurumTxType.FullName + "...");
                try
                {
                    ConstructorInfo ctor = aurumTxType.GetConstructor(Type.EmptyTypes);
                    if (ctor != null)
                    {
                        preCreatedTx = ctor.Invoke(null);
                        Log("  Created transaction: " + preCreatedTx);

                        // Try to set properties
                        foreach (PropertyInfo p in aurumTxType.GetProperties(BindingFlags.Public | BindingFlags.Instance))
                        {
                            if (p.CanWrite)
                            {
                                try
                                {
                                    if (p.PropertyType == typeof(string)) p.SetValue(preCreatedTx, "test");
                                    else if (p.PropertyType == typeof(int) || p.PropertyType == typeof(long) || p.PropertyType == typeof(decimal))
                                        p.SetValue(preCreatedTx, Convert.ChangeType(100, p.PropertyType));
                                }
                                catch (Exception ex) { Log("    Failed to set " + p.Name + ": " + ex.Message); }
                            }
                        }

                        // Now try requestTransfer with pre-created transaction
                        MethodInfo requestTransfer = type.GetMethod("requestTransfer");
                        if (requestTransfer != null)
                        {
                            ParameterInfo[] ps = requestTransfer.GetParameters();
                            string[] paramStrs = new string[ps.Length];
                            for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                            Log("  requestTransfer params: " + string.Join(", ", paramStrs));

                            try
                            {
                                object result = requestTransfer.Invoke(obj, new object[] { preCreatedTx });
                                Log("  requestTransfer(preCreatedTx) returned: " + (result != null ? result.GetType().FullName : "null"));
                                if (result != null) DumpObject(result, "requestTransfer result");
                                CheckLogForCashlessIn("requestTransfer(preCreatedTx)");
                            }
                            catch (Exception ex)
                            {
                                Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                                Log("  requestTransfer(preCreatedTx) failed: " + inner.Message);
                            }
                        }
                    }
                    else
                    {
                        Log("  No parameterless constructor for " + aurumTxType.FullName);
                        // Try factory methods
                        foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                        {
                            try
                            {
                                foreach (Type t in asm.GetTypes())
                                {
                                    foreach (MethodInfo m in t.GetMethods(BindingFlags.Public | BindingFlags.Static))
                                    {
                                        if (m.Name.Contains("Create") && m.ReturnType.Name.Contains("Transaction"))
                                        {
                                            Log("  Found factory: " + t.FullName + "." + m.Name + "()");
                                            try
                                            {
                                                preCreatedTx = m.Invoke(null, null);
                                                Log("  Created via factory: " + preCreatedTx);
                                            }
                                            catch (Exception ex)
                                            {
                                                Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                                                Log("  Factory failed: " + inner.Message);
                                            }
                                        }
                                    }
                                }
                            }
                            catch { }
                        }
                    }
                }
                catch (Exception ex)
                {
                    Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                    Log("  Transaction creation failed: " + inner.Message);
                }
            }
            else
            {
                Log("  Could not find AurumTransaction type in loaded assemblies");
            }

            // Step 7: Try direct authorizeTransfer and commitTransfer
            Log("");
            Log("=== Step 7: Direct authorizeTransfer/commitTransfer calls ===");

            MethodInfo authorizeTransfer = type.GetMethod("authorizeTransfer");
            if (authorizeTransfer != null)
            {
                ParameterInfo[] ps = authorizeTransfer.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                Log("  authorizeTransfer params: " + string.Join(", ", paramStrs));

                try
                {
                    object[] callArgs = BuildArgs(ps);
                    object result = authorizeTransfer.Invoke(obj, callArgs);
                    Log("  authorizeTransfer(defaults) returned: " + (result != null ? result.GetType().FullName : "null"));
                    if (result != null) DumpObject(result, "authorizeTransfer result");
                    CheckLogForCashlessIn("authorizeTransfer");
                }
                catch (Exception ex)
                {
                    Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                    Log("  authorizeTransfer(defaults) failed: " + inner.Message);
                }
            }
            else
            {
                Log("  authorizeTransfer method not found");
            }

            MethodInfo commitTransfer = type.GetMethod("commitTransfer");
            if (commitTransfer != null)
            {
                ParameterInfo[] ps = commitTransfer.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                Log("  commitTransfer params: " + string.Join(", ", paramStrs));

                try
                {
                    object[] callArgs = BuildArgs(ps);
                    object result = commitTransfer.Invoke(obj, callArgs);
                    Log("  commitTransfer(defaults) returned: " + (result != null ? result.GetType().FullName : "null"));
                    if (result != null) DumpObject(result, "commitTransfer result");
                    CheckLogForCashlessIn("commitTransfer");
                }
                catch (Exception ex)
                {
                    Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                    Log("  commitTransfer(defaults) failed: " + inner.Message);
                }
            }
            else
            {
                Log("  commitTransfer method not found");
            }

            // Step 8: Try requestTransfer with various approaches
            Log("");
            Log("=== Step 8: requestTransfer variations ===");
            MethodInfo reqTransfer2 = type.GetMethod("requestTransfer");
            if (reqTransfer2 != null)
            {
                ParameterInfo[] ps = reqTransfer2.GetParameters();
                string[] paramStrs = new string[ps.Length];
                for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                Log("  requestTransfer params: " + string.Join(", ", paramStrs));

                // Try with defaults
                try
                {
                    object[] callArgs = BuildArgs(ps);
                    object result = reqTransfer2.Invoke(obj, callArgs);
                    Log("  requestTransfer(defaults) returned: " + (result != null ? result.GetType().FullName : "null"));
                    if (result != null) DumpObject(result, "requestTransfer result");
                    CheckLogForCashlessIn("requestTransfer(defaults)");
                }
                catch (Exception ex)
                {
                    Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                    Log("  requestTransfer(defaults) failed: " + inner.Message);
                }

                // Try with pre-created transaction if available
                if (preCreatedTx != null)
                {
                    try
                    {
                        object result = reqTransfer2.Invoke(obj, new object[] { preCreatedTx });
                        Log("  requestTransfer(preCreatedTx) returned: " + (result != null ? result.GetType().FullName : "null"));
                        if (result != null) DumpObject(result, "requestTransfer(preCreatedTx) result");
                        CheckLogForCashlessIn("requestTransfer(preCreatedTx)");
                    }
                    catch (Exception ex)
                    {
                        Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                        Log("  requestTransfer(preCreatedTx) failed: " + inner.Message);
                    }
                }
            }
            else
            {
                Log("  requestTransfer method not found");
            }

            // Step 9: Dump all fields of the remote object
            Log("");
            Log("=== Step 9: Remote object fields ===");
            try
            {
                FieldInfo[] fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                Log("  Fields: " + fields.Length);
                foreach (FieldInfo f in fields)
                {
                    try
                    {
                        object val = f.GetValue(obj);
                        Log("    " + f.FieldType.Name + " " + f.Name + " = " + val);
                    }
                    catch { }
                }
            }
            catch (Exception ex)
            {
                Exception inner = ex.InnerException != null ? ex.InnerException : ex;
                Log("  ERROR: " + inner.Message);
            }

            // Step 10: Check for other relevant methods
            Log("");
            Log("=== Step 10: All remaining methods ===");
            foreach (MethodInfo m in methods)
            {
                if (m.Name.StartsWith("get_") || m.Name.StartsWith("set_") || m.Name.StartsWith("add_") || m.Name.StartsWith("remove_"))
                    continue;
                bool alreadyLogged = false;
                string logStr = log.ToString();
                string[] logLines = logStr.Split('\n');
                foreach (string line in logLines)
                {
                    if (line.Contains(" " + m.Name + "("))
                    {
                        alreadyLogged = true;
                        break;
                    }
                }
                if (!alreadyLogged)
                {
                    ParameterInfo[] ps = m.GetParameters();
                    string[] paramStrs = new string[ps.Length];
                    for (int i = 0; i < ps.Length; i++) paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                    Log("  " + m.ReturnType.Name + " " + m.Name + "(" + string.Join(", ", paramStrs) + ")");
                }
            }
        }
        catch (Exception ex)
        {
            Log("");
            Log("FATAL ERROR: " + ex.GetType().FullName);
            Log("Message: " + ex.Message);
            Exception inner = ex.InnerException;
            Log("Inner: " + (inner != null ? inner.Message : "none"));
            Log("Stack: " + ex.StackTrace);
        }

        // Save full log to file for review
        File.WriteAllText(@"C:\Temp\probe_sascontroler1_output.txt", log.ToString());
        Log("");
        Log(@"=== Full log saved to C:\Temp\probe_sascontroler1_output.txt ===");
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
                try { args[i] = Activator.CreateInstance(pt); }
                catch { args[i] = null; }
            }
        }
        return args;
    }

    static void DumpObject(object obj, string label)
    {
        if (obj == null) return;
        Log("  --- Dumping " + label + " ---");
        Type t = obj.GetType();
        Log("  Type: " + t.FullName);

        foreach (PropertyInfo p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            try
            {
                if (p.CanRead)
                {
                    object val = p.GetValue(obj);
                    Log("    " + p.PropertyType.Name + " " + p.Name + " = " + val);
                }
            }
            catch (Exception ex)
            {
                Log("    " + p.Name + " = <error: " + ex.Message + ">");
            }
        }

        foreach (FieldInfo f in t.GetFields(BindingFlags.Public | BindingFlags.Instance))
        {
            try
            {
                object val = f.GetValue(obj);
                Log("    field " + f.FieldType.Name + " " + f.Name + " = " + val);
            }
            catch { }
        }
    }

    static void CheckLogForCashlessIn(string methodName)
    {
        try
        {
            string logPath = @"\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-17.log";
            if (!File.Exists(logPath)) return;

            string[] lines = File.ReadAllLines(logPath);
            foreach (string line in lines)
            {
                string lower = line.ToLower();
                if (lower.Contains("cashless") || lower.Contains("credit") || lower.Contains("transfer") || lower.Contains("award") || lower.Contains("bonus"))
                {
                    Log("  [LOG] " + line.Trim());
                }
            }
        }
        catch { }
    }
}

'@
    Add-Type -TypeDefinition $csharpCode -Language CSharp -ReferencedAssemblies System.Runtime.Remoting
    [Probe]::Main(@())
}
Remove-PSSession $session
