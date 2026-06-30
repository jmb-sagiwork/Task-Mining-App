$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

py -3 -c "import PIL, pynput, psutil" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Missing developer dependencies." -ForegroundColor Yellow
    Write-Host "Use download\FlowLens-Task-Mining.exe for the packaged app." -ForegroundColor Cyan
    Write-Host "Only developers running from source should install requirements.txt." -ForegroundColor Cyan
    exit 1
}

py -3 TM.py
