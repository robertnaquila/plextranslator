# watch-korean.ps1
# One-click live English captions for KOREAN shows, using the best Whisper model
# and Claude (LLM) refinement. Run this, then play the show; captions appear in
# the browser overlay. No Plex token / NAS needed — it listens to system audio.
#
# Run it by double-clicking watch-korean.bat, or from PowerShell:
#   .\scripts\watch-korean.ps1
#
# Prereqs (one time):
#   pip install -e ".[run,llm,monitor]"
#   a loopback capture device enabled (e.g. Stereo Mix), and either Windows
#   "Listen to this device -> your speakers" OR --monitor-device (see below).
#   Put your Claude key in a .env file in the repo root:  ANTHROPIC_API_KEY=sk-ant-...

$ErrorActionPreference = "Stop"

# ============================= settings =============================
$Lang          = "ko"                                              # Korean
$Model         = "large-v3"                                        # best quality
$AnthropicModel = "claude-opus-4-8"                                # best Claude model
$AudioFormat   = "dshow"
$AudioDevice   = "audio=Stereo Mix (Realtek High Definition Audio)" # <-- your capture device
$WindowSeconds = 8                                                 # bigger = more context, more delay
$Port          = 8765
$UseLlm        = $true
# Optional: play captured audio out to a device so you can hear it WITHOUT using
# Windows "Listen". Leave as "" if you use Windows "Listen to this device".
# Example: "15" (a device index from:  python -m plextranslator capture --list-monitor-devices)
$MonitorDevice = ""
# ====================================================================

# Move to the repo root (parent of this script's folder) so .env is found.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Load .env (KEY=VALUE lines) into this process so ANTHROPIC_API_KEY is available.
$envFile = Join-Path $RepoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

# Build the argument list.
$cmd = @(
    "-m", "plextranslator", "capture",
    "--source-language", $Lang,
    "--model", $Model,
    "--audio-format", $AudioFormat,
    "--audio-device", $AudioDevice,
    "--window-seconds", "$WindowSeconds",
    "--port", "$Port"
)

if ($UseLlm) {
    if (-not $env:ANTHROPIC_API_KEY) {
        Write-Host "WARNING: ANTHROPIC_API_KEY not set (put it in $envFile)." -ForegroundColor Yellow
        Write-Host "         Running WITHOUT LLM refinement." -ForegroundColor Yellow
    } else {
        python -c "import anthropic" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Installing anthropic (one-time)..." -ForegroundColor Cyan
            python -m pip install anthropic
        }
        $cmd += @("--use-llm", "--anthropic-model", $AnthropicModel)
    }
}

if ($MonitorDevice -ne "") {
    $cmd += @("--monitor-device", $MonitorDevice)
}

# Open the overlay; it auto-reconnects until the server is up.
Start-Process "http://127.0.0.1:$Port/"

Write-Host "Korean live captions starting (model=$Model, llm=$UseLlm)..." -ForegroundColor Green
Write-Host "Overlay: http://127.0.0.1:$Port/   (Ctrl+C to stop)" -ForegroundColor Green
python @cmd
