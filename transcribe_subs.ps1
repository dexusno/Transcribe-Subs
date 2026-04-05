<#
.SYNOPSIS
    Generate subtitles for videos using Whisper + LLM cleanup.

.DESCRIPTION
    PowerShell wrapper for transcribe_subs.py. Activates the conda environment,
    scans a folder for videos without subtitles, and generates .srt files using
    local Whisper (faster-whisper) and LLM-based cleanup (DeepSeek Reasoner).

.EXAMPLE
    .\transcribe_subs.ps1 "D:\Movies\Inception (2010)"
    .\transcribe_subs.ps1 "D:\TvSeries\Breaking Bad" -DryRun
    .\transcribe_subs.ps1 "D:\Movies" -SkipLLM
    .\transcribe_subs.ps1 "\\NAS\Media\Movies" -Profile deepseek -Parallel 4
    .\transcribe_subs.ps1 "D:\Movies" -Language en -WhisperModel medium
#>
param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$Folder,

    [Parameter(Mandatory = $false)]
    [string]$Profile = "",

    [Parameter(Mandatory = $false)]
    [int]$BatchSize = 0,

    [Parameter(Mandatory = $false)]
    [int]$Parallel = 0,

    [Parameter(Mandatory = $false)]
    [string]$WhisperModel = "",

    [Parameter(Mandatory = $false)]
    [string]$Language = "",

    [Parameter(Mandatory = $false)]
    [int]$Limit = 0,

    [Parameter(Mandatory = $false)]
    [switch]$Force,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun,

    [Parameter(Mandatory = $false)]
    [switch]$SkipLLM,

    [Parameter(Mandatory = $false)]
    [string]$LogFile = ""
)

# ── Configuration ─────────────────────────────────────────────────────────────

$CondaEnv     = "transcribe_subs"
$ScriptDir    = $PSScriptRoot
$PythonScript = Join-Path $ScriptDir "transcribe_subs.py"

# ── Find conda ───────────────────────────────────────────────────────────────

$CondaBase = $null

# 1. CONDA_EXE env var (set by 'conda init' — most reliable)
if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {
    $CondaBase = Split-Path (Split-Path $env:CONDA_EXE -Parent) -Parent
}

# 2. 'conda' on PATH
if (-not $CondaBase) {
    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        $CondaBase = Split-Path (Split-Path $condaCmd.Source -Parent) -Parent
    }
}

# 3. where.exe fallback
if (-not $CondaBase) {
    try {
        $whereResult = & where.exe conda 2>$null | Select-Object -First 1
        if ($whereResult -and (Test-Path $whereResult)) {
            $CondaBase = Split-Path (Split-Path $whereResult -Parent) -Parent
        }
    } catch {}
}

if (-not $CondaBase) {
    Write-Host ""
    Write-Host "  [ERROR] conda not found. Run install.ps1 first." -ForegroundColor Red
    exit 1
}

$CondaActivate = Join-Path $CondaBase "Scripts\activate.bat"
$EnvDir = Join-Path $CondaBase "envs\$CondaEnv"
$PythonExe = Join-Path $EnvDir "python.exe"

# ── Validation ────────────────────────────────────────────────────────────────

function Exit-WithError {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies\Inception (2010)"'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -DryRun'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -SkipLLM'
    Write-Host '    .\transcribe_subs.ps1 "D:\TvSeries\Show" -Language en'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -WhisperModel medium'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -Profile openai -Parallel 4'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -Force -Limit 5'
    Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -LogFile "C:\logs\transcribe.log"'
    Write-Host ""
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Folder)) {
    Exit-WithError "No folder specified."
}

# Check conda env exists
if (-not (Test-Path $PythonExe)) {
    Write-Host ""
    Write-Host "  [ERROR] Conda environment '$CondaEnv' not found at $EnvDir" -ForegroundColor Red
    Write-Host "          Run install.ps1 first to set up the environment." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# Resolve folder path (support UNC paths)
try {
    if ($Folder -match '^\\\\') {
        $ResolvedFolder = $Folder.TrimEnd('\', '/')
        if (-not (Test-Path -LiteralPath $ResolvedFolder -PathType Container)) {
            Exit-WithError "UNC path not accessible: $Folder"
        }
    } else {
        $ResolvedFolder = (Resolve-Path -LiteralPath $Folder -ErrorAction Stop).Path
    }
} catch {
    Exit-WithError "Folder not found: $Folder"
}

if (-not (Test-Path -LiteralPath $ResolvedFolder -PathType Container)) {
    Exit-WithError "Path is not a folder: $ResolvedFolder"
}

if (-not (Test-Path -LiteralPath $PythonScript)) {
    Exit-WithError "transcribe_subs.py not found at: $PythonScript"
}

# Warn if .env missing (unless --skip-llm or --dry-run)
$EnvFile = Join-Path $ScriptDir ".env"
if (-not $SkipLLM -and -not $DryRun -and -not (Test-Path -LiteralPath $EnvFile)) {
    Write-Host "  [WARNING] .env not found — LLM cleanup requires an API key" -ForegroundColor Yellow
    Write-Host "            Use -SkipLLM for Whisper-only mode, or create .env" -ForegroundColor Yellow
    Write-Host ""
}

# ── Build arguments ──────────────────────────────────────────────────────────

$pyArgs = @($PythonScript)

if ($Profile -ne "")       { $pyArgs += @("--profile", $Profile) }
if ($BatchSize -gt 0)      { $pyArgs += @("--batch-size", $BatchSize) }
if ($Parallel -gt 0)       { $pyArgs += @("--parallel", $Parallel) }
if ($WhisperModel -ne "")  { $pyArgs += @("--whisper-model", $WhisperModel) }
if ($Language -ne "")      { $pyArgs += @("--language", $Language) }
if ($Limit -gt 0)          { $pyArgs += @("--limit", $Limit) }
if ($Force)                { $pyArgs += "--force" }
if ($DryRun)               { $pyArgs += "--dry-run" }
if ($SkipLLM)              { $pyArgs += "--skip-llm" }
if ($LogFile -ne "")       { $pyArgs += @("--log-file", $LogFile) }

$pyArgs += $ResolvedFolder

# ── Display configuration ────────────────────────────────────────────────────

$ProfileDisplay = if ($Profile -ne "") { $Profile } else { "(config default)" }
$ModelDisplay   = if ($WhisperModel -ne "") { $WhisperModel } else { "(config default)" }
$LangDisplay    = if ($Language -ne "") { $Language } else { "auto-detect" }

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Conda env:  $CondaEnv" -ForegroundColor DarkGray
Write-Host "  Python:     $PythonExe" -ForegroundColor DarkGray
Write-Host "  Folder:     $ResolvedFolder" -ForegroundColor DarkGray
Write-Host "  Whisper:    $ModelDisplay" -ForegroundColor DarkGray
Write-Host "  Language:   $LangDisplay" -ForegroundColor DarkGray
Write-Host "  Profile:    $ProfileDisplay" -ForegroundColor DarkGray
if ($Limit -gt 0)   { Write-Host "  Limit:      $Limit files" -ForegroundColor DarkGray }
if ($Force)          { Write-Host "  Force:      ON" -ForegroundColor Yellow }
if ($SkipLLM)        { Write-Host "  LLM:        SKIP (Whisper-only)" -ForegroundColor Yellow }
if ($DryRun)         { Write-Host "  Mode:       DRY-RUN" -ForegroundColor Yellow }
if ($LogFile -ne "") { Write-Host "  Log file:   $LogFile" -ForegroundColor DarkGray }
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# ── Run with conda environment activated ─────────────────────────────────────

# Use cmd to activate conda and run Python (conda activate only works in cmd/bash)
$cmdArgs = $pyArgs | ForEach-Object {
    if ($_ -match '\s') { "`"$_`"" } else { $_ }
}
$cmdLine = $cmdArgs -join " "

& cmd /c "call `"$CondaActivate`" && conda activate $CondaEnv && `"$PythonExe`" $cmdLine"

$ExitCode = $LASTEXITCODE
if ($ExitCode -eq 0) {
    Write-Host ""
    Write-Host "  Done." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  Finished with errors (exit code $ExitCode)." -ForegroundColor Yellow
}

exit $ExitCode
