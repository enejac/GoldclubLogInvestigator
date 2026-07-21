/*
 * IlDump.cs -- minimal IL disassembler used to inspect Aurum handler methods
 * (e.g. WATmanager.RequestTransferPosted) without running any static ctors.
 *
 * Usage:
 *   IlDump.exe <TypeFullName> <MethodName>
 *   IlDump.exe GoldClub.Aurum.WATmanager RequestTransferPosted
 */

using System;
using System.IO;
using System.Reflection;
using System.Reflection.Emit;

internal static class IlDump
{
    private static readonly string ExeDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    private static readonly string AurumLibDir = @"C:\Goldclub\services\aurum\bin\lib";
    private static readonly string AurumBinDir = @"C:\Goldclub\services\aurum\bin";

    private static readonly OpCode[] OneByte = new OpCode[256];
    private static readonly OpCode[] TwoByte = new OpCode[256];

    private static int Main(string[] args)
    {
        AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        BuildOpcodeTables();

        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: IlDump.exe <TypeFullName> <MethodName>");
            return 2;
        }

        try
        {
            string primary = args.Length > 2 ? args[2] : "GoldClub.Aurum.Engine.dll";
            var asm = Assembly.LoadFrom(Path.Combine(ExeDir, primary));

            if (args[0] == "::find")
            {
                string kw = args[1].ToLowerInvariant();
                bool all = (kw == "::all");
                foreach (var t in SafeTypes(asm))
                {
                    foreach (var mm in t.GetMethods(BindingFlags.Instance | BindingFlags.Static |
                                                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                    {
                        if (all || t.FullName.ToLowerInvariant().Contains(kw) || mm.Name.ToLowerInvariant().Contains(kw))
                            Console.WriteLine(t.FullName + " :: " + mm.Name);
                    }
                }
                return 0;
            }

            var type = asm.GetType(args[0], false);
            if (type == null)
            {
                Console.Error.WriteLine("type not found: " + args[0]);
                return 1;
            }

            if (args[1] == "::typeinfo")
            {
                Console.WriteLine("TYPE " + type.FullName);
                var bt = type;
                while (bt != null) { Console.WriteLine("  BASE " + bt.FullName + (bt.IsMarshalByRef ? " [MarshalByRef]" : "") + (bt.IsSerializable ? " [Serializable]" : "")); bt = bt.BaseType; }
                foreach (var f in type.GetFields(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))
                    Console.WriteLine("  FIELD " + f.FieldType.FullName + " " + f.Name);
                foreach (var p in type.GetProperties(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))
                    Console.WriteLine("  PROP " + p.PropertyType.FullName + " " + p.Name +
                                      " get=" + (p.GetGetMethod(true) != null) +
                                      " set=" + (p.GetSetMethod(true) != null));
                foreach (var c in type.GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
                    Console.WriteLine("  CTOR (" + string.Join(", ", Array.ConvertAll(c.GetParameters(), p => p.ParameterType.Name + " " + p.Name)) + ")");
                return 0;
            }

            var methods = type.GetMethods(BindingFlags.Instance | BindingFlags.Static |
                                          BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            int dumped = 0;
            foreach (var m in methods)
            {
                if (!string.Equals(m.Name, args[1], StringComparison.Ordinal)) continue;
                DumpMethod(m, asm.ManifestModule);
                dumped++;
            }
            if (dumped == 0)
            {
                Console.Error.WriteLine("method not found: " + args[1]);
                Console.Error.WriteLine("available methods:");
                foreach (var m in methods) Console.Error.WriteLine("  " + m.Name);
                return 1;
            }
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("FATAL: " + ex);
            return 1;
        }
    }

    private static void DumpMethod(MethodInfo m, Module module)
    {
        Console.WriteLine("=== " + m.DeclaringType.FullName + "." + m.Name + " ===");
        var body = m.GetMethodBody();
        if (body == null) { Console.WriteLine("(no body)"); return; }

        foreach (var lv in body.LocalVariables)
            Console.WriteLine("  local " + lv.LocalIndex + ": " + lv.LocalType);

        byte[] il = body.GetILAsByteArray();
        var genTypeArgs = m.DeclaringType.IsGenericType ? m.DeclaringType.GetGenericArguments() : null;
        var genMethArgs = m.IsGenericMethodDefinition ? m.GetGenericArguments() : null;

        int pos = 0;
        while (pos < il.Length)
        {
            int start = pos;
            OpCode op;
            ushort code = il[pos++];
            if (code == 0xFE) op = TwoByte[il[pos++]];
            else op = OneByte[code];

            string operand = "";
            switch (op.OperandType)
            {
                case OperandType.InlineNone:
                    break;
                case OperandType.ShortInlineBrTarget:
                    operand = "-> " + (pos + 1 + (sbyte)il[pos]); pos += 1; break;
                case OperandType.InlineBrTarget:
                    operand = "-> " + (pos + 4 + BitConverter.ToInt32(il, pos)); pos += 4; break;
                case OperandType.ShortInlineI:
                case OperandType.ShortInlineVar:
                    operand = il[pos].ToString(); pos += 1; break;
                case OperandType.InlineVar:
                    operand = BitConverter.ToInt16(il, pos).ToString(); pos += 2; break;
                case OperandType.InlineI:
                    operand = BitConverter.ToInt32(il, pos).ToString(); pos += 4; break;
                case OperandType.InlineI8:
                    operand = BitConverter.ToInt64(il, pos).ToString(); pos += 8; break;
                case OperandType.ShortInlineR:
                    operand = BitConverter.ToSingle(il, pos).ToString(); pos += 4; break;
                case OperandType.InlineR:
                    operand = BitConverter.ToDouble(il, pos).ToString(); pos += 8; break;
                case OperandType.InlineString:
                    operand = "\"" + Safe(module.ResolveString(BitConverter.ToInt32(il, pos))) + "\""; pos += 4; break;
                case OperandType.InlineField:
                case OperandType.InlineMethod:
                case OperandType.InlineTok:
                case OperandType.InlineType:
                    operand = ResolveToken(module, BitConverter.ToInt32(il, pos), genTypeArgs, genMethArgs); pos += 4; break;
                case OperandType.InlineSwitch:
                    int n = BitConverter.ToInt32(il, pos); pos += 4 + 4 * n; operand = "switch[" + n + "]"; break;
                default:
                    pos = il.Length; break;
            }
            Console.WriteLine(string.Format("  IL_{0:X4}: {1,-12} {2}", start, op.Name, operand));
        }
    }

    private static string ResolveToken(Module module, int token, Type[] gt, Type[] gm)
    {
        try
        {
            var mb = module.ResolveMember(token, gt, gm);
            var fi = mb as FieldInfo;
            if (fi != null) return "FIELD " + fi.DeclaringType.Name + "." + fi.Name + " : " + fi.FieldType.Name;
            var mi = mb as MethodBase;
            if (mi != null) return "CALL  " + mi.DeclaringType.Name + "." + mi.Name;
            var ty = mb as Type;
            if (ty != null) return "TYPE  " + ty.FullName;
            return mb.Name;
        }
        catch (Exception ex) { return "token:" + token.ToString("X8") + " (" + ex.GetType().Name + ")"; }
    }

    private static string Safe(string s)
    {
        if (s == null) return "";
        return s.Replace("\r", "\\r").Replace("\n", "\\n");
    }

    private static void BuildOpcodeTables()
    {
        foreach (var f in typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static))
        {
            var oc = (OpCode)f.GetValue(null);
            if (oc.Size == 1) OneByte[oc.Value & 0xFF] = oc;
            else TwoByte[oc.Value & 0xFF] = oc;
        }
    }

    private static Type[] SafeTypes(Assembly asm)
    {
        try { return asm.GetTypes(); }
        catch (ReflectionTypeLoadException ex)
        {
            var list = new System.Collections.Generic.List<Type>();
            foreach (var t in ex.Types) if (t != null) list.Add(t);
            return list.ToArray();
        }
    }

    private static Assembly Resolve(object sender, ResolveEventArgs args)
    {
        string file = new AssemblyName(args.Name).Name + ".dll";
        foreach (string dir in new[] { ExeDir, AurumLibDir, AurumBinDir })
        {
            string path = Path.Combine(dir, file);
            if (File.Exists(path)) return Assembly.LoadFrom(path);
        }
        return null;
    }
}
