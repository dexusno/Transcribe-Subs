<#
.SYNOPSIS
    Install Transcribe_Subs — AI subtitle generation pipeline.

.DESCRIPTION
    Creates a conda environment, installs all dependencies (including CUDA),
    downloads the Whisper model, and sets up the project ready to use.

    Can be run as a one-liner from GitHub:
        irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex

    Or locally after cloning:
        .\install.ps1
#>

# ── Configuration ─────────────────────────────────────────────────────────────

$EnvName       = "transcribe_subs"
$PythonVersion = "3.11"
$WhisperModel  = "large-v3"
$ProjectDir    = "D:\Transcribe_Subs"
$RepoURL       = "https://github.com/dexusno/Transcribe-Subs.git"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [$script:StepNum] $Message" -ForegroundColor Cyan
    $script:StepNum++
}

function Write-OK {
    param([string]$Message)
    Write-Host "      $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "      $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
    Write-Host ""
}

function Test-CommandExists {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

$script:StepNum = 1

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Transcribe_Subs — Installer" -ForegroundColor White
Write-Host "   AI subtitle generation: Whisper + DeepSeek Reasoner" -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan

# ── Step 1: Check prerequisites ──────────────────────────────────────────────

Write-Step "Checking prerequisites"

# Check conda
$CondaExe = $null
if (Test-CommandExists "conda") {
    $CondaExe = (Get-Command conda).Source
    Write-OK "conda found: $CondaExe"
} else {
    # Try common locations
    $CommonPaths = @(
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "D:\anaconda3\Scripts\conda.exe",
        "C:\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
    )
    foreach ($p in $CommonPaths) {
        if (Test-Path $p) {
            $CondaExe = $p
            Write-OK "conda found: $CondaExe"
            break
        }
    }
    if (-not $CondaExe) {
        Write-Err "conda not found. Install Anaconda or Miniconda first:`n         https://www.anaconda.com/download"
        exit 1
    }
}

# Derive conda base directory
$CondaBase = Split-Path (Split-Path $CondaExe -Parent) -Parent
$CondaActivate = Join-Path $CondaBase "Scripts\activate.bat"
$EnvDir = Join-Path $CondaBase "envs\$EnvName"

# Check git
if (Test-CommandExists "git") {
    Write-OK "git found"
} else {
    Write-Err "git not found. Install Git for Windows:`n         https://git-scm.com/download/win"
    exit 1
}

# Check ffmpeg
if (Test-CommandExists "ffmpeg") {
    Write-OK "ffmpeg found"
} else {
    Write-Warn "ffmpeg not found in PATH — required at runtime"
    Write-Warn "Install: winget install ffmpeg  OR  https://ffmpeg.org/download.html"
}

# Check NVIDIA GPU
$GpuOK = $false
if (Test-CommandExists "nvidia-smi") {
    $smi = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
    if ($smi) {
        Write-OK "NVIDIA GPU: $($smi.Trim())"
        $GpuOK = $true
    }
}
if (-not $GpuOK) {
    Write-Warn "NVIDIA GPU not detected — Whisper will fall back to CPU (much slower)"
}

# ── Step 2: Clone or update repository ───────────────────────────────────────

Write-Step "Setting up project directory"

if (Test-Path (Join-Path $ProjectDir ".git")) {
    Write-OK "Repository exists at $ProjectDir — pulling latest"
    Push-Location $ProjectDir
    & git pull --ff-only 2>&1 | Out-Null
    Pop-Location
} elseif (Test-Path (Join-Path $ProjectDir "transcribe_subs.py")) {
    Write-OK "Project files found at $ProjectDir (not a git repo)"
} else {
    Write-OK "Cloning repository to $ProjectDir"
    $ParentDir = Split-Path $ProjectDir -Parent
    if (-not (Test-Path $ParentDir)) {
        New-Item -ItemType Directory -Path $ParentDir -Force | Out-Null
    }
    & git clone $RepoURL $ProjectDir 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to clone repository"
        exit 1
    }
}

# ── Step 3: Create conda environment ─────────────────────────────────────────

Write-Step "Creating conda environment '$EnvName' (Python $PythonVersion)"

$EnvExists = $false
if (Test-Path $EnvDir) {
    $EnvExists = $true
    Write-OK "Environment already exists at $EnvDir"
} else {
    Write-OK "Creating new environment ..."
    & cmd /c "call `"$CondaActivate`" && conda create -n $EnvName python=$PythonVersion -y" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to create conda environment"
        exit 1
    }
    Write-OK "Environment created"
}

# ── Step 4: Install Python dependencies ──────────────────────────────────────

Write-Step "Installing Python dependencies"

$PipExe = Join-Path $EnvDir "python.exe"
if (-not (Test-Path $PipExe)) {
    Write-Err "Python not found in conda environment at: $PipExe"
    exit 1
}

# Install packages via pip inside the conda env
$ReqFile = Join-Path $ProjectDir "requirements.txt"

Write-OK "Installing faster-whisper, requests, python-dotenv ..."
& cmd /c "call `"$CondaActivate`" && conda activate $EnvName && pip install -r `"$ReqFile`" --quiet" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip install via conda activate had issues, trying direct pip ..."
    $PipPath = Join-Path $EnvDir "Scripts\pip.exe"
    & $PipPath install -r $ReqFile --quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install Python dependencies"
        exit 1
    }
}
Write-OK "Python dependencies installed"

# ── Step 5: Pre-download Whisper model ───────────────────────────────────────

Write-Step "Pre-downloading Whisper model '$WhisperModel'"
Write-OK "This may take a few minutes on first install (~3 GB download) ..."

$downloadScript = @"
import sys
try:
    from faster_whisper.utils import download_model
    path = download_model('$WhisperModel')
    print(f'Model cached at: {path}')
except Exception as e:
    print(f'Download via utils failed ({e}), trying model load ...')
    from faster_whisper import WhisperModel
    model = WhisperModel('$WhisperModel', device='cpu', compute_type='int8')
    print('Model downloaded and verified')
"@

& $PipExe -c $downloadScript 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Model pre-download had issues — it will download on first run instead"
} else {
    Write-OK "Whisper model '$WhisperModel' cached successfully"
}

# ── Step 6: Set up .env file ─────────────────────────────────────────────────

Write-Step "Checking .env configuration"

$EnvFile = Join-Path $ProjectDir ".env"
$EnvExample = Join-Path $ProjectDir ".env.example"

if (Test-Path $EnvFile) {
    Write-OK ".env file exists"
    # Check if it has a real DeepSeek key
    $content = Get-Content $EnvFile -Raw
    if ($content -match "DEEPSEEK_API_KEY=your-key-here" -or $content -match "DEEPSEEK_API_KEY=$") {
        Write-Warn "DEEPSEEK_API_KEY is not set in .env"
        Write-Warn "Edit $EnvFile and add your key from https://platform.deepseek.com/api_keys"
        Write-Warn "Or use --skip-llm to run Whisper-only (no LLM cleanup)"
    }
} else {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-OK "Created .env from .env.example"
        Write-Warn "Edit $EnvFile and add your DeepSeek API key"
        Write-Warn "Get your key at: https://platform.deepseek.com/api_keys"
    } else {
        Write-Warn "No .env.example found — create .env manually with your API key"
    }
}

# ── Step 7: Update transcribe_subs.ps1 with correct Python path ─────────────

Write-Step "Configuring run script"

$RunScript = Join-Path $ProjectDir "transcribe_subs.ps1"
if (Test-Path $RunScript) {
    $scriptContent = Get-Content $RunScript -Raw
    # The wrapper already reads $CondaEnv and activates — just verify
    Write-OK "Run script: $RunScript"
} else {
    Write-Warn "transcribe_subs.ps1 not found — use Python directly"
}

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host "   Installation complete!" -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Project:      $ProjectDir" -ForegroundColor White
Write-Host "  Conda env:    $EnvName ($EnvDir)" -ForegroundColor White
Write-Host "  Whisper:      $WhisperModel" -ForegroundColor White
Write-Host ""
Write-Host "  Quick start:" -ForegroundColor Yellow
Write-Host "    # Activate the environment:" -ForegroundColor DarkGray
Write-Host "    conda activate $EnvName" -ForegroundColor White
Write-Host ""
Write-Host "    # Generate subtitles for a folder:" -ForegroundColor DarkGray
Write-Host "    cd $ProjectDir" -ForegroundColor White
Write-Host '    python transcribe_subs.py "D:\Movies\Some Movie"' -ForegroundColor White
Write-Host ""
Write-Host "    # Or use the wrapper script:" -ForegroundColor DarkGray
Write-Host '    .\transcribe_subs.ps1 "D:\Movies\Some Movie"' -ForegroundColor White
Write-Host ""
Write-Host "    # Whisper-only (no LLM, no API key needed):" -ForegroundColor DarkGray
Write-Host '    python transcribe_subs.py --skip-llm "D:\Movies\Some Movie"' -ForegroundColor White
Write-Host ""
Write-Host "    # Preview without processing:" -ForegroundColor DarkGray
Write-Host '    python transcribe_subs.py --dry-run "D:\Movies"' -ForegroundColor White
Write-Host ""

if (-not (Test-Path $EnvFile) -or (Get-Content $EnvFile -Raw) -match "your-key-here") {
    Write-Host "  NEXT STEP: Add your DeepSeek API key to $EnvFile" -ForegroundColor Yellow
    Write-Host "             Get your key: https://platform.deepseek.com/api_keys" -ForegroundColor Yellow
    Write-Host ""
}
