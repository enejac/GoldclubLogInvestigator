using System;
using System.Reflection;
using System.Runtime.Remoting;
using System.Runtime.Remoting.Channels;
using System.Runtime.Remoting.Channels.Http;

class Hop3Probe {
    static void Main() {
        Console.WriteLine("=== HOP 3 SPOOF PROBE ===");
        
        // Register HTTP channel
        var channel = new HttpChannel();
        ChannelServices.RegisterChannel(channel, false);
        
        // Probe HOST endpoint (:50011)
        ProbeEndpoint("http://localhost:50011/SASControler1", "HOST");
        
        // Probe EGM endpoint (:50010)
        ProbeEndpoint("http://localhost:50010/GM2AU", "EGM");
        
        Console.WriteLine("=== PROBE COMPLETE ===");
    }
    
    static void ProbeEndpoint(string url, string label) {
        Console.WriteLine($"\n--- Probing {label} ({url}) ---");
        try {
            var proxy = Activator.GetObject(typeof(MarshalByRefObject), url);
            Console.WriteLine($"[+] Proxy obtained: {proxy.GetType().FullName}");
            
            // Enumerate ALL methods
            var methods = proxy.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance);
            Console.WriteLine($"[*] {methods.Length} public methods:");
            foreach (var m in methods) {
                var parms = string.Join(", ", m.GetParameters());
                Console.WriteLine($"    {m.ReturnType.Name} {m.Name}({parms})");
            }
            
            // Enumerate ALL properties
            var props = proxy.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance);
            Console.WriteLine($"[*] {props.Length} public properties:");
            foreach (var p in props) {
                try {
                    var val = p.GetValue(proxy, null);
                    Console.WriteLine($"    {p.PropertyType.Name} {p.Name} = {val}");
                } catch {
                    Console.WriteLine($"    {p.PropertyType.Name} {p.Name} = <error>");
                }
            }
            
            // Try to find credit-related methods
            Console.WriteLine("[*] Credit-related methods:");
            foreach (var m in methods) {
                var name = m.Name.ToLower();
                if (name.Contains("transfer") || name.Contains("credit") || 
                    name.Contains("wat") || name.Contains("aft") || 
                    name.Contains("bonus") || name.Contains("award") ||
                    name.Contains("cash") || name.Contains("meter") ||
                    name.Contains("commit") || name.Contains("authorize") ||
                    name.Contains("request")) {
                    var parms = string.Join(", ", m.GetParameters());
                    Console.WriteLine($"    ** {m.ReturnType.Name} {m.Name}({parms})");
                }
            }
            
            // Try GetNewCommand if it exists
            var getNewCmd = proxy.GetType().GetMethod("GetNewCommand");
            if (getNewCmd != null) {
                Console.WriteLine("[+] GetNewCommand found, trying to get WAT template...");
                try {
                    // Try to get deviceClassBase.WAT enum value
                    var dcField = proxy.GetType().GetField("deviceClassBase", 
                        BindingFlags.NonPublic | BindingFlags.Instance);
                    if (dcField != null) {
                        var dcVal = dcField.GetValue(proxy);
                        Console.WriteLine($"[*] deviceClassBase = {dcVal}");
                    }
                    
                    // Try calling GetNewCommand with various device classes
                    // DeviceClassBase.WAT is likely an enum, try common values
                    var result = getNewCmd.Invoke(proxy, new object[] { 3 }); // 3 might be WAT
                    if (result != null) {
                        Console.WriteLine($"[+] GetNewCommand(3) returned: {result.GetType().FullName}");
                        // Enumerate methods on the returned object
                        var watMethods = result.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance);
                        Console.WriteLine($"[*] WAT object has {watMethods.Length} methods:");
                        foreach (var wm in watMethods) {
                            var wp = string.Join(", ", wm.GetParameters());
                            Console.WriteLine($"    {wm.ReturnType.Name} {wm.Name}({wp})");
                        }
                    }
                } catch (Exception ex) {
                    Console.WriteLine($"[!] GetNewCommand failed: {ex.Message}");
                }
            }
            
        } catch (Exception ex) {
            Console.WriteLine($"[!] {label} probe failed: {ex.Message}");
        }
    }
}
