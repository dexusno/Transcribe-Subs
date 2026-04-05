<#
.SYNOPSIS
    Install Transcribe_Subs — AI subtitle generation pipeline.

.DESCRIPTION
    Checks all prerequisites (GPU, drivers, CUDA, ffmpeg, git, conda),
    offers to install missing dependencies, creates a conda environment,
    installs all Python packages, and pre-downloads the Whisper model.

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
    Write-Host "      [OK] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "      $Message" -ForegroundColor DarkGray
}

function Write-Warn {
    param([string]$Message)
    Write-Host "      [!] $Message" -ForegroundColor Yellow
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

function Ask-YesNo {
    param([string]$Question, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    Write-Host ""
    Write-Host "      $Question $hint " -ForegroundColor White -NoNewline
    $answer = Read-Host
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim().ToLower().StartsWith("y")
}

function Install-WithWinget {
    param([string]$PackageId, [string]$FriendlyName)
    if (-not (Test-CommandExists "winget")) {
        Write-Warn "winget not available — please install $FriendlyName manually"
        return $false
    }
    Write-Info "Installing $FriendlyName via winget ..."
    & winget install --id $PackageId --accept-source-agreements --accept-package-agreements 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "$FriendlyName installed successfully"
        # Refresh PATH for this session
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                     [System.Environment]::GetEnvironmentVariable("Path", "User")
        return $true
    } else {
        Write-Warn "winget install returned exit code $LASTEXITCODE"
        return $false
    }
}

$script:StepNum = 1
$script:GpuMode = "cpu"       # Will be set to "cuda" if GPU is usable
$script:NeedsRestart = $false # Set if PATH-changing installs happened

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Transcribe_Subs — Installer" -ForegroundColor White
Write-Host "   AI subtitle generation: Whisper + DeepSeek Reasoner" -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: GPU Detection & Driver Check
# ══════════════════════════════════════════════════════════════════════════════
#
# Philosophy: No hardcoded version numbers. Driver/CUDA versions change
# constantly. Instead we just TEST if things actually work:
#   1. Is there NVIDIA hardware? (WMI or nvidia-smi)
#   2. Does nvidia-smi run? (= drivers are installed and functional)
#   3. Does CTranslate2 see CUDA? (= the full stack works end-to-end)
# If any step fails, we offer to fix it and fall back to CPU.

Write-Step "Checking GPU and drivers"

$GpuDetected = $false
$DriversWorking = $false
$GpuName = ""
$DriverVersion = ""
$CudaVersion = ""

# --- Try nvidia-smi first (proves drivers are installed AND working) ---
if (Test-CommandExists "nvidia-smi") {
    try {
        $smiOutput = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
        if ($smiOutput -and $LASTEXITCODE -eq 0) {
            $parts = $smiOutput.Trim() -split ",\s*"
            $GpuName = $parts[0].Trim()
            $DriverVersion = $parts[1].Trim()
            $GpuDetected = $true
            $DriversWorking = $true
            Write-OK "NVIDIA GPU detected: $GpuName"
            Write-Info "VRAM: $($parts[2].Trim()), Driver: $DriverVersion"

            # Also grab CUDA version for display (informational only)
            try {
                $smiHeader = & nvidia-smi 2>$null | Select-Object -First 5
                $cudaMatch = ($smiHeader | Select-String "CUDA Version:\s+([\d.]+)").Matches
                if ($cudaMatch.Count -gt 0) {
                    $CudaVersion = $cudaMatch[0].Groups[1].Value
                    Write-Info "CUDA support: $CudaVersion"
                }
            } catch {}
        }
    } catch {
        # nvidia-smi exists but crashed — drivers are broken
    }
}

# --- If nvidia-smi didn't work, check if GPU hardware exists via WMI ---
if (-not $GpuDetected) {
    try {
        $gpus = Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue
        $nvidiaGpu = $gpus | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
        if ($nvidiaGpu) {
            $GpuName = $nvidiaGpu.Name
            $GpuDetected = $true
            Write-OK "NVIDIA GPU found: $GpuName"
            Write-Warn "nvidia-smi not working — drivers are missing or broken"
        } else {
            $allGpus = ($gpus | ForEach-Object { $_.Name }) -join ", "
            if ($allGpus) {
                Write-Warn "No NVIDIA GPU detected. Found: $allGpus"
            } else {
                Write-Warn "No GPU hardware detected"
            }
        }
    } catch {
        Write-Warn "Could not query GPU hardware"
    }
}

# --- Decision tree based on what we found ---

if (-not $GpuDetected) {
    # No NVIDIA hardware found at all
    Write-Info ""
    Write-Info "Whisper CAN run on CPU, but it will be much slower:"
    Write-Info "  GPU:  ~2-3 minutes per movie"
    Write-Info "  CPU:  ~30-60 minutes per movie"
    Write-Info ""

    if (Ask-YesNo "Do you have an NVIDIA GPU that should be detected?" $false) {
        Write-Info ""
        Write-Info "Your GPU may need drivers. Steps:"
        Write-Info "  1. Install drivers: https://www.nvidia.com/Download/index.aspx"
        Write-Info "  2. Restart your computer"
        Write-Info "  3. Run this installer again"
        Write-Info ""

        if (Ask-YesNo "Would you like to install NVIDIA drivers now via winget?" $true) {
            $installed = Install-WithWinget "Nvidia.GeForceExperience" "NVIDIA GeForce Experience (includes drivers)"
            if ($installed) {
                Write-Warn "Drivers installed — you MUST restart your computer"
                Write-Warn "After restart, run this installer again."
                Write-Host ""
                Write-Host "  Please restart your computer and run the installer again." -ForegroundColor Yellow
                Write-Host ""
                exit 0
            } else {
                Write-Info "Download manually: https://www.nvidia.com/Download/index.aspx"
            }
        }
        Write-Info "Continuing with CPU mode — re-run installer after driver setup"
    } else {
        Write-Info "Continuing with CPU mode"
    }

} elseif (-not $DriversWorking) {
    # GPU hardware exists, but nvidia-smi doesn't work (no drivers / broken drivers)
    Write-Warn "GPU hardware found but drivers are not functional"

    if (Ask-YesNo "Would you like to install/repair NVIDIA drivers now?" $true) {
        $installed = Install-WithWinget "Nvidia.GeForceExperience" "NVIDIA GeForce Experience (includes drivers)"
        if ($installed) {
            Write-Warn "Drivers installed — you MUST restart your computer"
            Write-Warn "After restart, run this installer again."
            Write-Host ""
            Write-Host "  Please restart your computer and run the installer again." -ForegroundColor Yellow
            Write-Host ""
            exit 0
        } else {
            Write-Info "Download manually: https://www.nvidia.com/Download/index.aspx"
        }
    }
    Write-Info "Continuing with CPU mode — re-run installer after driver setup"

} else {
    # GPU detected AND nvidia-smi works — drivers are functional.
    # Whether they're new enough for CUDA will be tested after pip install
    # (Step 5) by actually trying CTranslate2. No version number guessing.
    $script:GpuMode = "cuda"
    Write-OK "GPU and drivers are working — CUDA will be verified after package install"
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Check & install system dependencies
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Checking system dependencies"

# --- conda ---
$CondaExe = $null
if (Test-CommandExists "conda") {
    $CondaExe = (Get-Command conda).Source
    Write-OK "conda found: $CondaExe"
} else {
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
        Write-Warn "conda not found"
        Write-Info ""
        Write-Info "Anaconda or Miniconda is required to manage the Python environment."

        if (Ask-YesNo "Would you like to install Miniconda now?" $true) {
            $installed = Install-WithWinget "Anaconda.Miniconda3" "Miniconda3"
            if ($installed) {
                # Try to find it after install
                $postInstallPaths = @(
                    "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
                    "C:\ProgramData\miniconda3\Scripts\conda.exe"
                )
                foreach ($p in $postInstallPaths) {
                    if (Test-Path $p) {
                        $CondaExe = $p
                        break
                    }
                }
                if (-not $CondaExe) {
                    Write-Warn "Miniconda installed but conda not found in expected paths"
                    Write-Warn "You may need to restart your terminal and run the installer again"
                    exit 1
                }
                Write-OK "conda ready: $CondaExe"
            } else {
                Write-Err "conda is required. Install manually:`n         https://www.anaconda.com/download"
                exit 1
            }
        } else {
            Write-Err "conda is required. Install Anaconda or Miniconda first:`n         https://www.anaconda.com/download"
            exit 1
        }
    }
}

$CondaBase = Split-Path (Split-Path $CondaExe -Parent) -Parent
$CondaActivate = Join-Path $CondaBase "Scripts\activate.bat"
$EnvDir = Join-Path $CondaBase "envs\$EnvName"

# --- git ---
if (Test-CommandExists "git") {
    Write-OK "git found"
} else {
    Write-Warn "git not found"

    if (Ask-YesNo "Would you like to install Git now?" $true) {
        $installed = Install-WithWinget "Git.Git" "Git for Windows"
        if ($installed) {
            if (Test-CommandExists "git") {
                Write-OK "git ready"
            } else {
                Write-Warn "git installed but not in PATH yet — you may need to restart your terminal"
                $script:NeedsRestart = $true
            }
        } else {
            Write-Err "git is required. Install manually: https://git-scm.com/download/win"
            exit 1
        }
    } else {
        Write-Err "git is required. Install Git for Windows:`n         https://git-scm.com/download/win"
        exit 1
    }
}

# --- ffmpeg ---
if (Test-CommandExists "ffmpeg") {
    $ffmpegVer = & ffmpeg -version 2>&1 | Select-Object -First 1
    Write-OK "ffmpeg found: $($ffmpegVer -replace 'ffmpeg version\s+', '' -replace '\s+Copyright.*', '')"
} else {
    Write-Warn "ffmpeg not found — required for audio extraction at runtime"

    if (Ask-YesNo "Would you like to install ffmpeg now?" $true) {
        $installed = Install-WithWinget "Gyan.FFmpeg" "ffmpeg"
        if ($installed) {
            if (Test-CommandExists "ffmpeg") {
                Write-OK "ffmpeg ready"
            } else {
                Write-Warn "ffmpeg installed but not in PATH yet"
                Write-Info "You may need to restart your terminal, or add ffmpeg to PATH manually"
                $script:NeedsRestart = $true
            }
        } else {
            Write-Warn "Could not install ffmpeg via winget"
            Write-Info "Install manually: https://ffmpeg.org/download.html"
            Write-Info "Or: winget install ffmpeg"
        }
    } else {
        Write-Warn "ffmpeg must be installed before running transcribe_subs"
        Write-Info "Install: winget install ffmpeg  OR  https://ffmpeg.org/download.html"
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Clone or update repository
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Setting up project directory"

if (Test-Path (Join-Path $ProjectDir ".git")) {
    Write-OK "Repository exists at $ProjectDir"
    Write-Info "Pulling latest changes ..."
    Push-Location $ProjectDir
    & git pull --ff-only 2>&1 | Out-Null
    Pop-Location
} elseif (Test-Path (Join-Path $ProjectDir "transcribe_subs.py")) {
    Write-OK "Project files found at $ProjectDir (not a git repo — skipping pull)"
} else {
    Write-Info "Cloning repository to $ProjectDir ..."
    $ParentDir = Split-Path $ProjectDir -Parent
    if (-not (Test-Path $ParentDir)) {
        New-Item -ItemType Directory -Path $ParentDir -Force | Out-Null
    }
    & git clone $RepoURL $ProjectDir 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to clone repository"
        exit 1
    }
    Write-OK "Repository cloned"
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Create conda environment
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Creating conda environment '$EnvName' (Python $PythonVersion)"

if (Test-Path $EnvDir) {
    Write-OK "Environment already exists at $EnvDir"
} else {
    Write-Info "Creating new environment (this may take a minute) ..."
    & cmd /c "call `"$CondaActivate`" && conda create -n $EnvName python=$PythonVersion -y" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to create conda environment"
        exit 1
    }
    Write-OK "Environment created"
}

$PythonExe = Join-Path $EnvDir "python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Err "Python not found in conda environment at: $PythonExe"
    exit 1
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Install Python dependencies
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Installing Python dependencies"

$ReqFile = Join-Path $ProjectDir "requirements.txt"

Write-Info "Installing faster-whisper, requests, python-dotenv ..."
& cmd /c "call `"$CondaActivate`" && conda activate $EnvName && pip install -r `"$ReqFile`" --quiet" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip via conda had issues, trying direct ..."
    $PipPath = Join-Path $EnvDir "Scripts\pip.exe"
    & $PipPath install -r $ReqFile --quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install Python dependencies"
        exit 1
    }
}
Write-OK "Python packages installed"

# --- Verify CUDA actually works end-to-end ---
# This is the REAL test. No version number guessing — we ask CTranslate2
# to actually use CUDA. If the driver is too old, CUDA libs are missing,
# or anything else is wrong, this will catch it.
if ($script:GpuMode -eq "cuda") {
    Write-Info "Verifying CUDA works end-to-end in CTranslate2 ..."

    # This script:
    # 1. Asks CTranslate2 to actually use CUDA (the real test)
    # 2. If it fails, discovers what CUDA version ctranslate2 was built
    #    against and what the installed driver supports, so we can show
    #    the user a meaningful message with no hardcoded versions.
    $cudaCheckScript = @"
import sys
try:
    import ctranslate2

    # --- Discover versions (informational, for error messages) ---
    ct2_version = getattr(ctranslate2, '__version__', 'unknown')
    cuda_build = 'unknown'
    try:
        # ctranslate2 exposes what CUDA it was compiled for
        cuda_build = getattr(ctranslate2, 'cuda_version', None)
        if cuda_build is None:
            # Some versions expose it differently
            import importlib.metadata
            meta = importlib.metadata.metadata('ctranslate2')
            for line in (meta.get_all('Requires-Dist') or []):
                if 'nvidia' in line.lower() and 'cu' in line.lower():
                    cuda_build = line
                    break
    except Exception:
        pass

    # --- The actual test: can CTranslate2 use CUDA? ---
    supported = ctranslate2.get_supported_compute_types('cuda')
    if supported:
        print(f'CUDA_OK:supported={",".join(sorted(supported))}')
    else:
        print(f'CUDA_NONE:ct2={ct2_version},cuda_build={cuda_build}')

except RuntimeError as e:
    # RuntimeError = driver too old, CUDA unavailable, etc.
    # Include discovered info so the installer can show a helpful message
    try:
        import ctranslate2
        ct2_ver = getattr(ctranslate2, '__version__', '?')
    except Exception:
        ct2_ver = '?'
    print(f'CUDA_RUNTIME_ERROR:ct2={ct2_ver}|{e}')
except Exception as e:
    print(f'CUDA_ERROR:{e}')
"@

    $cudaResult = & $PythonExe -c $cudaCheckScript 2>&1
    $cudaResultStr = ($cudaResult | Out-String).Trim()

    if ($cudaResultStr -match "CUDA_OK") {
        Write-OK "CUDA verified — GPU acceleration is working"
        # Extract supported types for info
        if ($cudaResultStr -match "supported=(.+)$") {
            Write-Info "Supported compute types: $($Matches[1])"
        }
    } elseif ($cudaResultStr -match "CUDA_RUNTIME_ERROR:(.+)") {
        $errorDetail = $Matches[1].Trim()

        # Parse out CTranslate2 version from the error for display
        $ct2Ver = ""
        if ($errorDetail -match "ct2=([^|]+)\|(.+)") {
            $ct2Ver = $Matches[1].Trim()
            $errorMsg = $Matches[2].Trim()
        } else {
            $errorMsg = $errorDetail
        }

        Write-Warn "CUDA not available: $errorMsg"
        if ($ct2Ver) {
            Write-Info "CTranslate2 version: $ct2Ver"
        }
        if ($DriverVersion) {
            Write-Info "Installed driver: $DriverVersion"
        }
        Write-Info ""

        # Detect common failure reasons from the error message
        if ($errorMsg -match "driver|CUDA driver|insufficient|not compatible|not supported") {
            Write-Warn "Your NVIDIA driver may be too old for the installed CUDA libraries."
            Write-Info "Update your drivers: https://www.nvidia.com/Download/index.aspx"
            Write-Info ""
            if (Ask-YesNo "Would you like to update NVIDIA drivers now via winget?" $true) {
                $installed = Install-WithWinget "Nvidia.GeForceExperience" "NVIDIA GeForce Experience (includes drivers)"
                if ($installed) {
                    Write-Warn "Drivers updated — a restart may be needed"
                    Write-Info "Re-run this installer after restarting to verify CUDA"
                    $script:NeedsRestart = $true
                }
            }
            $script:GpuMode = "cpu"
        } else {
            # Generic CUDA failure — try installing runtime libs
            if (Ask-YesNo "Would you like to try installing CUDA runtime libraries via conda?" $true) {
                Write-Info "Installing CUDA toolkit (this may take a few minutes) ..."
                & cmd /c "call `"$CondaActivate`" && conda activate $EnvName && conda install -c nvidia cuda-toolkit -y" 2>&1
                if ($LASTEXITCODE -eq 0) {
                    $recheck = & $PythonExe -c $cudaCheckScript 2>&1
                    $recheckStr = ($recheck | Out-String).Trim()
                    if ($recheckStr -match "CUDA_OK") {
                        Write-OK "CUDA now working after toolkit install"
                    } else {
                        Write-Warn "CUDA still not available — falling back to CPU"
                        $script:GpuMode = "cpu"
                    }
                } else {
                    Write-Warn "Toolkit install failed — falling back to CPU"
                    $script:GpuMode = "cpu"
                }
            } else {
                $script:GpuMode = "cpu"
            }
        }
    } elseif ($cudaResultStr -match "CUDA_NONE") {
        Write-Warn "CTranslate2 reports no CUDA compute types available"
        if ($cudaResultStr -match "ct2=([^,]+)") {
            Write-Info "CTranslate2 version: $($Matches[1])"
        }
        Write-Info "This may mean the installed package doesn't include CUDA support."
        Write-Info ""

        if (Ask-YesNo "Would you like to try installing CUDA runtime libraries via conda?" $true) {
            Write-Info "Installing CUDA toolkit ..."
            & cmd /c "call `"$CondaActivate`" && conda activate $EnvName && conda install -c nvidia cuda-toolkit -y" 2>&1
            $recheck = & $PythonExe -c $cudaCheckScript 2>&1
            $recheckStr = ($recheck | Out-String).Trim()
            if ($recheckStr -match "CUDA_OK") {
                Write-OK "CUDA now working"
            } else {
                Write-Warn "CUDA still not available — falling back to CPU"
                $script:GpuMode = "cpu"
            }
        } else {
            $script:GpuMode = "cpu"
        }
    } else {
        Write-Warn "Could not verify CUDA: $cudaResultStr"
        Write-Info "Whisper will attempt GPU on first run and fall back to CPU if needed"
    }
}

# --- Update llm_config.json with detected GPU mode ---
$ConfigFile = Join-Path $ProjectDir "llm_config.json"
if ($script:GpuMode -eq "cpu" -and (Test-Path $ConfigFile)) {
    try {
        $configJson = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($configJson.whisper.device -ne "cpu") {
            Write-Info "Updating llm_config.json: whisper device → cpu"
            $configContent = Get-Content $ConfigFile -Raw
            $configContent = $configContent -replace '"device":\s*"cuda"', '"device": "cpu"'
            $configContent = $configContent -replace '"compute_type":\s*"int8"', '"compute_type": "float32"'
            Set-Content $ConfigFile $configContent -Encoding UTF8
            Write-OK "Config updated for CPU mode"
        }
    } catch {
        Write-Warn "Could not update config for CPU mode — edit llm_config.json manually"
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 6: Pre-download Whisper model
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Pre-downloading Whisper model '$WhisperModel'"
Write-Info "This may take a few minutes on first install (~3 GB download) ..."

$downloadScript = @"
import sys
try:
    from faster_whisper.utils import download_model
    path = download_model('$WhisperModel')
    print(f'DOWNLOAD_OK:{path}')
except Exception as e:
    print(f'DOWNLOAD_FALLBACK:{e}')
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel('$WhisperModel', device='cpu', compute_type='float32')
        del model
        print('DOWNLOAD_OK:model loaded and cached')
    except Exception as e2:
        print(f'DOWNLOAD_FAIL:{e2}')
"@

$dlResult = & $PythonExe -c $downloadScript 2>&1
$dlResultStr = ($dlResult | Out-String).Trim()

if ($dlResultStr -match "DOWNLOAD_OK") {
    Write-OK "Whisper model '$WhisperModel' cached successfully"
} else {
    Write-Warn "Model pre-download had issues — it will download on first run instead"
    Write-Info "Details: $dlResultStr"
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 7: Set up .env file
# ══════════════════════════════════════════════════════════════════════════════

Write-Step "Checking .env configuration"

$EnvFile = Join-Path $ProjectDir ".env"
$EnvExample = Join-Path $ProjectDir ".env.example"

if (Test-Path $EnvFile) {
    Write-OK ".env file exists"
    $content = Get-Content $EnvFile -Raw
    if ($content -match "DEEPSEEK_API_KEY=your-key-here" -or $content -match "DEEPSEEK_API_KEY=\s*$") {
        Write-Warn "DEEPSEEK_API_KEY is not set in .env"
        Write-Info "Edit $EnvFile and add your key from https://platform.deepseek.com/api_keys"
        Write-Info "Or use --skip-llm for Whisper-only mode (free, no API key needed)"
    } else {
        Write-OK "DeepSeek API key is configured"
    }
} else {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-OK "Created .env from .env.example"
        Write-Warn "Edit $EnvFile and add your DeepSeek API key"
        Write-Info "Get your key at: https://platform.deepseek.com/api_keys"
    } else {
        Write-Warn "No .env.example found — create .env manually with your API key"
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════════════════

$deviceDisplay = if ($script:GpuMode -eq "cuda") {
    $extra = if ($CudaVersion) { " (CUDA $CudaVersion)" } else { "" }
    "$GpuName$extra"
} else { "CPU" }

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host "   Installation complete!" -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Project:     $ProjectDir" -ForegroundColor White
Write-Host "  Conda env:   $EnvName" -ForegroundColor White
Write-Host "  Whisper:     $WhisperModel" -ForegroundColor White
Write-Host "  Device:      $deviceDisplay" -ForegroundColor White
Write-Host ""

if ($script:NeedsRestart) {
    Write-Host "  WARNING: Some installations require a terminal restart." -ForegroundColor Yellow
    Write-Host "  Close and reopen PowerShell before running transcribe_subs." -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "  Quick start:" -ForegroundColor Yellow
Write-Host ""
Write-Host "    # Using the wrapper (auto-activates conda):" -ForegroundColor DarkGray
Write-Host "    cd $ProjectDir" -ForegroundColor White
Write-Host '    .\transcribe_subs.ps1 "D:\Movies\Some Movie"' -ForegroundColor White
Write-Host ""
Write-Host "    # Or activate manually:" -ForegroundColor DarkGray
Write-Host "    conda activate $EnvName" -ForegroundColor White
Write-Host '    python transcribe_subs.py "D:\Movies\Some Movie"' -ForegroundColor White
Write-Host ""
Write-Host "    # Whisper-only (free, no API key needed):" -ForegroundColor DarkGray
Write-Host '    .\transcribe_subs.ps1 "D:\Movies\Some Movie" -SkipLLM' -ForegroundColor White
Write-Host ""
Write-Host "    # Preview what would be processed:" -ForegroundColor DarkGray
Write-Host '    .\transcribe_subs.ps1 "D:\Movies" -DryRun' -ForegroundColor White
Write-Host ""

if (-not (Test-Path $EnvFile) -or (Get-Content $EnvFile -Raw) -match "your-key-here") {
    Write-Host "  NEXT STEP: Add your DeepSeek API key to .env" -ForegroundColor Yellow
    Write-Host "             Get your key: https://platform.deepseek.com/api_keys" -ForegroundColor Yellow
    Write-Host ""
}
