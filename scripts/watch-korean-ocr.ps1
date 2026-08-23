# watch-korean-ocr.ps1
# One-click LOW-LATENCY subtitles for Korean shows that have BURNED-IN Korean
# subtitles on screen (variety shows, hardsubbed content, shows on someone
# else's Plex server). Instead of transcribing audio, this watches the bottom
# of the screen, OCRs each Korean subtitle line the moment it appears, and
# translates it with Claude — typically 1-2s behind the on-screen text.
# No Whisper model, no audio routing, no Stereo Mix needed.
#
# Run it by double-clicking watch-korean-ocr.bat, or from PowerShell:
#   .\scripts\watch-korean-ocr.ps1
#
# One-time setup:
#   pip install -e ".[ocr]"
#   pip install winsdk        (Windows built-in OCR — recommended)
#   Windows Settings > Time & Language > Language & region > Add a language
#     > 한국어 (Korean)   — installs the Korean OCR pack; keep English as display.
#   Put your Claude key in a .env file in the repo root: ANTHROPIC_API_KEY=sk-ant-...
#
# IMPORTANT: keep the caption overlay browser window OUTSIDE the watched region
# (e.g. above the video, or on another monitor) so the OCR never reads it.

$ErrorActionPreference = "Stop"

# ============================= settings =============================
$Lang           = "ko"                       # language of the burned-in subs
$Region         = "bottom"                   # bottom 30% of the screen; or "bottom:40",
                                             # or exact pixels "left,top,width,height".
                                             # Find yours interactively:
                                             #   python -m plextranslator ocr --select-region
$Interval       = 0.4                        # seconds between screen checks
$StableFrames   = 2                          # frames a line must persist (1 = fastest)
# Claude Haiku is fast, cheap, and plenty for subtitle lines — snappier captions.
# For maximum translation quality use "claude-opus-4-8" (a beat slower per line).
$AnthropicModel = "claude-haiku-4-5-20251001"
$Port           = 8765
# ====================================================================

# Move to the repo root (parent of this script's folder) so .env is found.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Load .env (KEY=VALUE lines) into this process.
$envFile = Join-Path $RepoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "WARNING: ANTHROPIC_API_KEY not set (put it in $envFile)." -ForegroundColor Yellow
    Write-Host "         Captions will show the ORIGINAL Korean, untranslated." -ForegroundColor Yellow
}

# Install missing pieces on first run.
python -c "import mss, PIL, anthropic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing OCR dependencies (one-time)..." -ForegroundColor Cyan
    python -m pip install mss Pillow anthropic
}
python -c "import winsdk" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing winsdk for Windows OCR (one-time)..." -ForegroundColor Cyan
    python -m pip install winsdk
}

# Open the overlay; it auto-reconnects until the server is up.
Start-Process "http://127.0.0.1:$Port/"

Write-Host "Korean screen-OCR subtitles starting (region=$Region, model=$AnthropicModel)..." -ForegroundColor Green
Write-Host "Overlay: http://127.0.0.1:$Port/  -- keep it OUTSIDE the watched region. Ctrl+C to stop." -ForegroundColor Green
python -m plextranslator ocr `
    --source-language $Lang `
    --region $Region `
    --interval $Interval `
    --stable-frames $StableFrames `
    --anthropic-model $AnthropicModel `
    --port $Port
