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
#   pip install pytesseract Pillow
#   winget install UB-Mannheim.TesseractOCR   (tick Korean under language data,
#     or download it after: see README "OCR" section for the tessdata URL)
#   Put your Claude key in a .env file in the repo root: ANTHROPIC_API_KEY=sk-ant-...
#
# (If Windows Update lets you install the Korean OCR language pack — Settings >
#  Time & Language > Language & region > Add a language > 한국어 — you can set
#  $OcrBackend below to "auto" or "windows" for the built-in engine instead,
#  which is more accurate. It failed with error 0x80070102 on some machines;
#  Tesseract is the default here because it always works.)
#
# IMPORTANT: keep the caption overlay browser window OUTSIDE the watched region
# (e.g. above the video, or on another monitor) so the OCR never reads it.

$ErrorActionPreference = "Stop"

# ============================= settings =============================
$Lang           = "ko"                       # language of the burned-in subs
# Exact pixels "left,top,width,height" around JUST the subtitle text — a tight
# region is far more accurate than the "bottom"/"bottom:N" percentage presets.
# Find yours interactively (drag a box over the subtitle band):
#   python -m plextranslator ocr --select-region
$Region         = "7,837,947,186"
$Interval       = 0.4                        # seconds between screen checks
$StableFrames   = 3                          # frames a line must persist (fewer
                                             # transition-frame misreads than 2)
# Claude Haiku is fast, cheap, and plenty for subtitle lines — snappier captions.
# For maximum translation quality use "claude-opus-4-8" (a beat slower per line).
$AnthropicModel = "claude-haiku-4-5-20251001"
$Port           = 8765
# OCR engine: "auto" (Windows built-in, else Tesseract), "windows", "tesseract".
$OcrBackend     = "tesseract"
# Tesseract page-segmentation mode: 7 = single line, best for a tight strip
# like $Region above. Use 6 ("block") if you widen the region to 2+ lines.
$Psm            = 7
# Only needed for Tesseract when it isn't on PATH (the Windows installer doesn't
# add it). Leave "" to auto-detect the usual install locations.
$TesseractCmd   = ""
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
$ocrArgs = @(
    "-m", "plextranslator", "ocr",
    "--source-language", $Lang,
    "--region", $Region,
    "--interval", $Interval,
    "--stable-frames", $StableFrames,
    "--anthropic-model", $AnthropicModel,
    "--ocr-backend", $OcrBackend,
    "--psm", $Psm,
    "--port", $Port
)
if ($TesseractCmd -ne "") { $ocrArgs += @("--tesseract-cmd", $TesseractCmd) }
python @ocrArgs
