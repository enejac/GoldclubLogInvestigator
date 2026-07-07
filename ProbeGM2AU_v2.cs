using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.Remoting;
using System.Runtime.Remoting.Channels;
using System.Runtime.Remoting.Channels.Http;
using System.Runtime.Remoting.Proxies;

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

        string[] urls = new string[] {
            "http://169.254.243.18:50010/GM2AU",
            "http://169.254.243.18:50010/GM2AU.rem",
            "http://169.254.243.18:50010/GM2AU.soap",
            "http://169.254.243.18:50010/OneHandService",
            "http://169.254.243.18:50010/SlotMachine",
            "http://169.254.243.18:50010/CreditManager",
            "http://169.254.243.18:50010/GameManager",
            "http://169.254.243.18:50010/AurumService"
        };

        foreach (string url in urls)
        {
            Console.WriteLine("\n[+] Trying: " + url);
            try
            {
                object proxy = Activator.GetObject(typeof(MarshalByRefObject), url);
                if (proxy == null)
                {
                    Console.WriteLine("    [-] Proxy is null");
                    continue;
                }

                Console.WriteLine("    [+] Proxy obtained: " + proxy.GetType().FullName);

                // Try to get real proxy info
                try
                {
                    IRemotingTypeInfo rti = (IRemotingTypeInfo)RemotingServices.GetRealProxy(proxy);
                    Console.WriteLine("    [+] TypeName: " + rti.TypeName);
                }
                catch (Exception ex)
                {
                    Console.WriteLine("    [?] RealProxy info: " + ex.Message);
                }

                // Try calling some common methods
                string[] testMethods = new string[] {
                    "GetCredit", "SetCredit", "AddCredit", "GetBalance",
                    "TransferCredits", "AwardCredits", "CommitCredit",
                    "GetMeters", "UpdateMeters", "ResetMeters",
                    "GetCashlessBalance", "CashlessIn", "CashlessOut",
                    "AuthorizeCredit", "RequestCredit", "ProcessCredit",
                    "GetGameCredits", "SetGameCredits", "AddGameCredits",
                    "GetTransferAmount", "SetTransferAmount",
                    "GetBonusAmount", "SetBonusAmount", "AwardBonus",
                    "GetJackpotAmount", "SetJackpotAmount",
                    "GetWinAmount", "SetWinAmount", "GetBetAmount",
                    "GetCoinIn", "GetCoinOut", "GetBillIn",
                    "GetTicketIn", "GetTicketOut", "GetVoucherIn",
                    "GetDrop", "GetPaidOut", "GetWon",
                    "CommitBet", "CommitWin", "CommitTransfer",
                    "ProcessGameEvent", "HandleCreditEvent",
                    "SendCreditUpdate", "ReceiveCreditUpdate",
                    "InitializeCredits", "ResetCredits",
                    "GetSessionCredits", "EndSession",
                    "SaveState", "LoadState", "FlushState",
                    "GetStatus", "GetState", "GetInfo"
                };

                Type proxyType = proxy.GetType();
                foreach (string methodName in testMethods)
                {
                    try
                    {
                        MethodInfo mi = proxyType.GetMethod(methodName);
                        if (mi != null)
                        {
                            Console.WriteLine("    [FOUND] " + methodName);
                            ParameterInfo[] ps = mi.GetParameters();
                            object[] callArgs = BuildArgs(ps);
                            object result = mi.Invoke(proxy, callArgs);
                            Console.WriteLine("      [OK] Result: " + (result != null ? result.ToString() : "null"));
                            CheckLogForCashlessIn(methodName);
                        }
                        else
                        {
                            // Try invoking directly anyway
                            try
                            {
                                object result = proxyType.InvokeMember(methodName, BindingFlags.InvokeMethod, null, proxy, new object[0]);
                                Console.WriteLine("    [CALL-OK] " + methodName + " => " + (result != null ? result.ToString() : "null"));
                                CheckLogForCashlessIn(methodName);
                            }
                            catch (TargetInvocationException tie)
                            {
                                Exception inner = tie.InnerException != null ? tie.InnerException : tie;
                                if (inner.Message.Contains("not found") || inner.Message.Contains("missing") || inner.Message.Contains("does not contain"))
                                {
                                    // Method does not exist
                                }
                                else
                                {
                                    Console.WriteLine("    [CALL-FAIL] " + methodName + " => " + inner.GetType().Name + ": " + inner.Message);
                                }
                            }
                            catch
                            {
                                // Method does not exist
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine("    [ERROR] " + methodName + ": " + ex.Message);
                    }
                }

                // Also enumerate all methods on the proxy
                Console.WriteLine("\n    === ALL METHODS ON PROXY ===");
                MethodInfo[] allMethods = proxyType.GetMethods(BindingFlags.Public | BindingFlags.Instance);
                foreach (MethodInfo m in allMethods)
                {
                    if (m.DeclaringType != typeof(object) && m.DeclaringType != typeof(MarshalByRefObject))
                    {
                        ParameterInfo[] ps = m.GetParameters();
                        string[] paramStrs = new string[ps.Length];
                        for (int i = 0; i < ps.Length; i++)
                        {
                            paramStrs[i] = ps[i].ParameterType.Name + " " + ps[i].Name;
                        }
                        Console.WriteLine("      " + m.Name + "(" + string.Join(", ", paramStrs) + ")");
                    }
                }

                break; // Stop after first successful connection
            }
            catch (Exception ex)
            {
                Console.WriteLine("    [-] " + ex.GetType().Name + ": " + ex.Message);
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
                    Console.WriteLine("      [LOG] " + lines[i].Trim());
                }
            }
            initialLogLines = lines.Length;
        }
        catch { }
    }
}
