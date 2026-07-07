import os
import subprocess

# Read C# probe (may be UTF-16 from Write tool)
cs_path = r'C:\Users\Ezbogar\GoldclubLogInvestigator\ProbeSASControler1.cs'
with open(cs_path, 'rb') as f:
    raw = f.read()
# Check for UTF-16 BOM and decode accordingly
if raw[:2] == b'\xff\xfe':
    cs_code = raw.decode('utf-16-le')
elif raw[:2] == b'\xfe\xff':
    cs_code = raw.decode('utf-16-be')
else:
    cs_code = raw.decode('utf-8-sig')
# Strip any null bytes that might remain
cs_code = cs_code.replace('\x00', '')
print(f'C# probe read OK, length: {len(cs_code)} chars')

# Write UTF-8 version for remote copy
remote_cs = r'\\10.0.0.90\c$\Temp\ProbeSASControler1.cs'
with open(remote_cs, 'w', encoding='utf-8') as f:
    f.write(cs_code)
print('C# probe copied to remote with UTF-8 encoding')

# Build PowerShell script content
ps_lines = []
ps_lines.append('$cred = New-Object System.Management.Automation.PSCredential("GOLD-CLUB\\test", (ConvertTo-SecureString "test" -AsPlainText -Force))')
ps_lines.append('$session = New-PSSession -ComputerName 10.0.0.90 -Credential $cred')
ps_lines.append('Invoke-Command -Session $session -ScriptBlock {')
ps_lines.append("    $csharpCode = @'")
ps_lines.append(cs_code)
ps_lines.append("'@")
ps_lines.append('    Add-Type -TypeDefinition $csharpCode -Language CSharp -ReferencedAssemblies System.Runtime.Remoting')
ps_lines.append('    [Probe]::Main(@())')
ps_lines.append('}')
ps_lines.append('Remove-PSSession $session')
ps_lines.append('')

# Write PS1 with UTF-8 (no BOM)
ps1_path = r'C:\Users\Ezbogar\GoldclubLogInvestigator\RunProbeSASControler1.ps1'
ps_content = '\n'.join(ps_lines)
with open(ps1_path, 'wb') as f:
    f.write(ps_content.encode('utf-8'))
print('PowerShell script written with UTF-8 encoding (no BOM)')

# Verify encoding
with open(ps1_path, 'rb') as f:
    check = f.read(20)
print(f'First 20 bytes of PS1: {check[:20]}')

# Run the probe
print('Running probe...')
result = subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1_path], capture_output=True, text=True, timeout=300)
print('STDOUT:')
print(result.stdout)
print('STDERR:')
print(result.stderr)
print(f'Return code: {result.returncode}')
