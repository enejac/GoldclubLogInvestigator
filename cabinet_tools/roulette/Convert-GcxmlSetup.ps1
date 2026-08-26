<#
.SYNOPSIS
    Decrypt / re-encrypt GoldClub gcxml setup.xml (ruleta) using GoldClub.Settings.dll.

.DESCRIPTION
    Live ruleta setup.xml uses content-type="gcxml". Names and values are mangled with
    GoldClub.Settings.Manglers.EncodingType0 and keyword "settings".

    Decrypt writes an editable plain XML (node/@name = logical setting path).
    Encrypt rebuilds a gcxml file GoldClub can load (encode is salted; output bytes
    will differ from the previous ciphertext even when values are unchanged).

.EXAMPLE
    .\Convert-GcxmlSetup.ps1 -Decrypt
    .\Convert-GcxmlSetup.ps1 -Encrypt -PlainPath D:\ConfigScanner\setup.plain.xml
#>
[CmdletBinding()]
param(
    [switch] $Decrypt,
    [switch] $Encrypt,
    [string] $EncryptedPath = 'C:\Goldclub\config\etc\application\ruleta\setup.xml',
    [string] $PlainPath = 'D:\ConfigScanner\setup.plain.xml',
    [string] $SettingsDll = 'C:\Goldclub\bin\lib\GoldClub.Settings.dll',
    [string] $Keyword = 'settings',
    [switch] $WhatIf,
    # Decrypt to stdout only (no PlainPath write) — used by LogInvestigator AI Helper.
    [switch] $Stdout
)

$ErrorActionPreference = 'Stop'

if (-not $Decrypt -and -not $Encrypt) {
    Write-Host 'Specify -Decrypt and/or -Encrypt.' -ForegroundColor Yellow
    exit 2
}

if (-not (Test-Path -LiteralPath $SettingsDll)) {
    throw "Missing GoldClub.Settings.dll: $SettingsDll"
}

[void][Reflection.Assembly]::LoadFrom((Resolve-Path -LiteralPath $SettingsDll).Path)
$asm = [AppDomain]::CurrentDomain.GetAssemblies() |
    Where-Object { $_.GetName().Name -eq 'GoldClub.Settings' } |
    Select-Object -First 1
if (-not $asm) { throw 'Failed to load GoldClub.Settings' }

$encType = $asm.GetType('GoldClub.Settings.Manglers.EncodingType0')
$xmlType = $asm.GetType('GoldClub.Settings.XML')
$create = $encType.GetMethod('Create')
$script:Mangler = $create.Invoke($null, @([string]$Keyword, $true))

function Decode-GcToken([string] $Token) {
    if ([string]::IsNullOrEmpty($Token)) { return $Token }
    return [string]$script:Mangler.DecodeText($Token, $Token)
}

function Encode-GcToken([string] $Plain) {
    return [string]$script:Mangler.EncodeText([string]$Plain)
}

function ConvertTo-XmlLocalName([string] $EncodedName) {
    if ([string]::IsNullOrEmpty($EncodedName)) { return 'node' }
    if ($EncodedName[0] -match '[0-9]') {
        $code = [int][char]$EncodedName[0]
        return ('_x{0:X4}_{1}' -f $code, $EncodedName.Substring(1))
    }
    return $EncodedName
}

function ConvertFrom-XmlLocalName([string] $LocalName) {
    if ($LocalName -match '^_x([0-9A-Fa-f]{4})_(.+)$') {
        return ([char][Convert]::ToInt32($Matches[1], 16)).ToString() + $Matches[2]
    }
    return $LocalName
}

function Escape-XmlText([string] $Text) {
    if ($null -eq $Text) { return '' }
    return [System.Security.SecurityElement]::Escape($Text)
}

function Write-PlainTree {
    param($XmlSettings, [string] $Path, [System.Text.StringBuilder] $Sb, [int] $Depth)

    $keys = @($XmlSettings.getSubkeys($Path))
    $indent = '  ' * $Depth
    if ($keys.Count -eq 0) {
        $leafToken = ($Path -split '/')[-1]
        $leafName = Decode-GcToken $leafToken
        $rawVal = [string]$XmlSettings.getString($Path)
        $plainVal = Decode-GcToken $rawVal
        [void]$Sb.AppendLine(
            ('{0}<node name="{1}">{2}</node>' -f $indent, (Escape-XmlText $leafName), (Escape-XmlText $plainVal))
        )
        return
    }

    if ($Path) {
        $section = Decode-GcToken (($Path -split '/')[-1])
        [void]$Sb.AppendLine(('{0}<node name="{1}">' -f $indent, (Escape-XmlText $section)))
        $childDepth = $Depth + 1
    } else {
        [void]$Sb.AppendLine('<?xml version="1.0" encoding="utf-8"?>')
        [void]$Sb.AppendLine(
            ('<config content-type="gcxml-plain" manglerKeyword="{0}" sourceEncrypted="{1}">' -f `
                (Escape-XmlText $Keyword), (Escape-XmlText $EncryptedPath))
        )
        $childDepth = 1
    }

    foreach ($key in $keys) {
        $child = if ($Path) { "$Path/$key" } else { $key }
        Write-PlainTree -XmlSettings $XmlSettings -Path $child -Sb $Sb -Depth $childDepth
    }

    if ($Path) {
        [void]$Sb.AppendLine(('{0}</node>' -f $indent))
    } else {
        [void]$Sb.AppendLine('</config>')
    }
}

function Invoke-Decrypt {
    if (-not (Test-Path -LiteralPath $EncryptedPath)) {
        throw "Encrypted file not found: $EncryptedPath"
    }
    $xml = [Activator]::CreateInstance($xmlType)
    if (-not $xml.Open($EncryptedPath)) {
        throw "GoldClub.Settings.XML.Open failed: $EncryptedPath"
    }
    try {
        $sb = New-Object System.Text.StringBuilder
        Write-PlainTree -XmlSettings $xml -Path '' -Sb $sb -Depth 0
        $text = $sb.ToString()
        if ($Stdout) {
            # No disk write — AI Helper / callers capture stdout only.
            [Console]::Out.Write($text)
            return
        }
        $dir = Split-Path -Parent $PlainPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        if ($WhatIf) {
            Write-Host ("WhatIf: would write {0} ({1} chars)" -f $PlainPath, $text.Length) -ForegroundColor Yellow
            return
        }
        [System.IO.File]::WriteAllText($PlainPath, $text, [System.Text.UTF8Encoding]::new($false))
        Write-Host ("Decrypted -> {0} ({1} bytes)" -f $PlainPath, (Get-Item -LiteralPath $PlainPath).Length) -ForegroundColor Green
        if ($text -match 'keepPaytableUserSelection') {
            Write-Host 'Contains keepPaytableUserSelection (editable).' -ForegroundColor DarkGray
        }
    } finally {
        try { [void]$xml.Close() } catch { }
        try { $xml.Dispose() } catch { }
    }
}

function Write-EncryptedNodes {
    param(
        [System.Xml.XmlElement] $Element,
        [System.Text.StringBuilder] $Sb,
        [int] $Depth
    )
    $indent = '  ' * $Depth
    foreach ($child in @($Element.ChildNodes | Where-Object { $_ -is [System.Xml.XmlElement] })) {
        $plainName = [string]$child.GetAttribute('name')
        if (-not $plainName) { $plainName = $child.LocalName }
        $encName = Encode-GcToken $plainName
        $local = ConvertTo-XmlLocalName $encName
        $nested = @($child.ChildNodes | Where-Object { $_ -is [System.Xml.XmlElement] })
        if ($nested.Count -gt 0) {
            [void]$Sb.AppendLine(
                ('{0}<{1} name="{2}">' -f $indent, $local, (Escape-XmlText $encName))
            )
            Write-EncryptedNodes -Element $child -Sb $Sb -Depth ($Depth + 1)
            [void]$Sb.AppendLine(('{0}</{1}>' -f $indent, $local))
        } else {
            $plainVal = [string]$child.InnerText
            $encVal = Encode-GcToken $plainVal
            [void]$Sb.AppendLine(
                ('{0}<{1} name="{2}">{3}</{1}>' -f `
                    $indent, $local, (Escape-XmlText $encName), (Escape-XmlText $encVal))
            )
        }
    }
}

function Invoke-Encrypt {
    if (-not (Test-Path -LiteralPath $PlainPath)) {
        throw "Plain file not found: $PlainPath (run -Decrypt first)"
    }
    $doc = New-Object System.Xml.XmlDocument
    $doc.PreserveWhitespace = $false
    $doc.Load($PlainPath)
    $root = $doc.DocumentElement
    if (-not $root) { throw "Invalid plain XML: $PlainPath" }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('<?xml version="1.0" encoding="utf-8"?>')
    [void]$sb.AppendLine(
        '<config generator="GoldClub.Settings.XML, GoldClub.Settings, Version=2.5.9684.30007, Culture=neutral, PublicKeyToken=null" content-type="gcxml" xmlns="config">'
    )
    Write-EncryptedNodes -Element $root -Sb $sb -Depth 1
    [void]$sb.AppendLine('</config>')
    $text = $sb.ToString()

    if ($WhatIf) {
        Write-Host ("WhatIf: would write {0} ({1} chars)" -f $EncryptedPath, $text.Length) -ForegroundColor Yellow
        return
    }

    $dir = Split-Path -Parent $EncryptedPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $bak = $EncryptedPath + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    if (Test-Path -LiteralPath $EncryptedPath) {
        Copy-Item -LiteralPath $EncryptedPath -Destination $bak -Force
        Write-Host ("Backup: {0}" -f $bak) -ForegroundColor DarkGray
    }
    [System.IO.File]::WriteAllText($EncryptedPath, $text, [System.Text.UTF8Encoding]::new($false))
    Write-Host ("Encrypted -> {0} ({1} bytes)" -f $EncryptedPath, (Get-Item -LiteralPath $EncryptedPath).Length) -ForegroundColor Green
}

if ($Decrypt) { Invoke-Decrypt }
if ($Encrypt) { Invoke-Encrypt }
