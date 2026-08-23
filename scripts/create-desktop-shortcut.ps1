# create-desktop-shortcut.ps1
# One-time setup: creates a "Korean OCR Subtitles" shortcut on your Desktop that
# double-clicks straight into watch-korean-ocr.bat, so you never need to open
# this folder again. Safe to re-run (just recreates the shortcut).
#
# Run it once:
#   .\scripts\create-desktop-shortcut.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Target   = Join-Path $RepoRoot "scripts\watch-korean-ocr.bat"
$Desktop  = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Korean OCR Subtitles.lnk"

if (-not (Test-Path $Target)) {
    Write-Host "Can't find $Target — run this from inside the plextranslator repo." -ForegroundColor Red
    exit 1
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:ComSpec"                      # cmd.exe, so the console window is visible
$Shortcut.Arguments  = "/c `"$Target`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.WindowStyle = 1                                   # normal window
$Shortcut.Description = "Live English captions for Korean shows (screen OCR)"
# A speech-bubble icon bundled with Windows (imageres.dll), so it's recognizable
# at a glance instead of the generic .bat icon.
$Shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,18"
$Shortcut.Save()

Write-Host "Created shortcut: $ShortcutPath" -ForegroundColor Green
Write-Host "Double-click it on your Desktop to start Korean OCR subtitles." -ForegroundColor Green
