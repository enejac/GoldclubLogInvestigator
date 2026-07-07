<#
.SYNOPSIS
    Start the local Venice proxy that fixes Cursor Agent + Venice integration.

.DESCRIPTION
    Venice crashes on OpenAI tool-role messages (list object has no element 0).
    Point Cursor Override OpenAI Base URL to http://127.0.0.1:8791/v1 while this runs.

    Cursor setup:
      1. Settings -> Models -> Override OpenAI Base URL: http://127.0.0.1:8791/v1
      2. OpenAI API Key: your Venice inference key
      3. Model picker -> Add Models -> qwen-3-6-plus (vision) or venice-uncensored-1-2 (text)

.EXAMPLE
    $env:VENICE_API_KEY = 'VENICE_INFERENCE_KEY_...'
    .\Start-VeniceCursorProxy.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not $env:VENICE_API_KEY) {
    Write-Host 'Set VENICE_API_KEY first, e.g.:' -ForegroundColor Yellow
    Write-Host '  $env:VENICE_API_KEY = ''VENICE_INFERENCE_KEY_...''' -ForegroundColor DarkGray
    Write-Host 'Or paste the key into Cursor Settings; the proxy reads Authorization from each request.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'TWO SETUPS (pick one):' -ForegroundColor Cyan
Write-Host ''
Write-Host 'A) Ask/Chat (local proxy - this script):' -ForegroundColor Green
Write-Host '  Override OpenAI Base URL: http://127.0.0.1:8791/v1' -ForegroundColor Green
Write-Host '  Fixes Venice tool-message crashes. Proxy terminal must show POST lines.' -ForegroundColor DarkGray
Write-Host ''
Write-Host 'B) Agent mode (NO localhost - cloud blocks 127.0.0.1):' -ForegroundColor Yellow
Write-Host '  Override OpenAI Base URL: https://api.venice.ai/api/v1' -ForegroundColor Yellow
Write-Host '  Model: qwen-3-6-plus (vision). Do NOT use 127.0.0.1 in Agent mode.' -ForegroundColor Yellow
Write-Host '  Error if you use localhost in Agent: Access to private networks is forbidden' -ForegroundColor Yellow
Write-Host ''
Write-Host 'Models:' -ForegroundColor Cyan
Write-Host '  Text: venice-uncensored-1-2, olafangensan-glm-4.7-flash-heretic' -ForegroundColor Green
Write-Host '  Vision: qwen-3-6-plus' -ForegroundColor Green
Write-Host ''
Write-Host 'DIAGNOSTIC: when you send a Cursor message, proxy should log:' -ForegroundColor Yellow
Write-Host '  POST /v1/chat/completions ... 200' -ForegroundColor DarkGray
Write-Host 'If no POST lines appear, Cursor is not using this proxy.' -ForegroundColor Yellow
Write-Host ''

python "$PSScriptRoot\tools\venice_cursor_proxy.py"
