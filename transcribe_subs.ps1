# transcribe_subs.ps1 — Windows PowerShell wrapper for transcribe_subs.py
#
# Usage:
#   .\transcribe_subs.ps1 "D:\Movies\Some Movie"
#   .\transcribe_subs.ps1 "D:\Movies\Some Movie" --skip-llm
#   .\transcribe_subs.ps1 "D:\Movies\Some Movie" --dry-run
#   .\transcribe_subs.ps1 "\\NAS\Media\Movies" --profile deepseek --parallel 4

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Folder,

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)

# ── Configuration ─────────────────────────────────────────────────────────────

# Edit this to match your Python installation
$PythonExe = "D:\anaconda3\python.exe"

# Script directory (where this .ps1 lives)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "transcribe_subs.py"
$EnvFile = Join-Path $ScriptDir ".env"

# ── Validation ────────────────────────────────────────────────────────────────

if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python not found at: $PythonExe" -ForegroundColor Red
    Write-Host "Edit `$PythonExe in this script to point to your Python installation."
    exit 1
}

if (-not (Test-Path $PythonScript)) {
    Write-Host "ERROR: transcribe_subs.py not found at: $PythonScript" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    Write-Host "WARNING: .env file not found at: $EnvFile" -ForegroundColor Yellow
    Write-Host "Copy .env.example to .env and fill in your API key(s)."
    Write-Host ""
}

# Resolve folder path (support UNC paths)
if ($Folder.StartsWith("\\")) {
    $ResolvedFolder = $Folder
} else {
    $ResolvedFolder = (Resolve-Path $Folder -ErrorAction SilentlyContinue).Path
    if (-not $ResolvedFolder) {
        Write-Host "ERROR: Folder not found: $Folder" -ForegroundColor Red
        exit 1
    }
}

# ── Show configuration ────────────────────────────────────────────────────────

Write-Host "======================================================================"
Write-Host "  transcribe_subs — Subtitle Generation Pipeline"
Write-Host "======================================================================"
Write-Host "  Python:  $PythonExe"
Write-Host "  Script:  $PythonScript"
Write-Host "  Folder:  $ResolvedFolder"
if ($ExtraArgs) {
    Write-Host "  Args:    $($ExtraArgs -join ' ')"
}
Write-Host "----------------------------------------------------------------------"
Write-Host ""

# ── Run ───────────────────────────────────────────────────────────────────────

$AllArgs = @($PythonScript, "`"$ResolvedFolder`"") + $ExtraArgs

& $PythonExe @AllArgs

$ExitCode = $LASTEXITCODE
if ($ExitCode -eq 0) {
    Write-Host ""
    Write-Host "Done." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Finished with errors (exit code $ExitCode)." -ForegroundColor Yellow
}

exit $ExitCode
