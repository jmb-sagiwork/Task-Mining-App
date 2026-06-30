$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

py -3 -c "import PIL, pynput, psutil" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing FlowLens dependencies..." -ForegroundColor Cyan
    py -3 -m pip install -r requirements.txt
}

py -3 TM.py
