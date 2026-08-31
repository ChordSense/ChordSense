$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Model = Join-Path $Backend "models\chord-cnn-lstm-model"
$Frontend = Join-Path $Root "frontend-tauri\frontend-tauri"
$Patch = Join-Path $Backend "patches\chord-model-compat.patch"

Write-Host "=== ChordSense setup ==="

# 1. Initialize submodules
Write-Host "`n[1/6] Initializing submodules..."
git -C $Root submodule update --init --recursive

# 2. Backend virtual environment
Write-Host "`n[2/6] Setting up backend Python environment..."

$BackendPython = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path $BackendPython)) {
    py -3.12 -m venv (Join-Path $Backend ".venv")
}

& $BackendPython -m pip install --upgrade pip
& $BackendPython -m pip install -r (Join-Path $Backend "requirements.txt")

# 3. Model virtual environment
Write-Host "`n[3/6] Setting up model Python environment..."

$ModelPython = Join-Path $Model "venv\Scripts\python.exe"

if (-not (Test-Path $ModelPython)) {
    py -3.12 -m venv (Join-Path $Model "venv")
}

& $ModelPython -m pip install --upgrade pip
& $ModelPython -m pip install -r (Join-Path $Model "requirements.txt")

# 4. Apply ChordSense compatibility patch
Write-Host "`n[4/6] Applying model compatibility patch..."

if (-not (Test-Path $Patch)) {
    throw "Missing compatibility patch: $Patch"
}

# Check whether the patch can be applied normally.
git -C $Model apply --check $Patch 2>$null

if ($LASTEXITCODE -eq 0) {
    git -C $Model apply $Patch

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply model compatibility patch."
    }

    Write-Host "Compatibility patch applied."
}
else {
    # If reverse-check succeeds, the patch is already applied.
    git -C $Model apply --reverse --check $Patch 2>$null

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Compatibility patch is already applied."
    }
    else {
        throw "Compatibility patch does not apply cleanly to the pinned model commit."
    }
}

# 5. Frontend dependencies
Write-Host "`n[5/6] Installing frontend dependencies..."

Push-Location $Frontend
try {
    npm install

    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed."
    }
}
finally {
    Pop-Location
}

# 6. Done
Write-Host "`n[6/6] Setup complete."
Write-Host ""
Write-Host "Start backend:"
Write-Host "  cd backend"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python app.py"
Write-Host ""
Write-Host "Start frontend in another terminal:"
Write-Host "  cd frontend-tauri\frontend-tauri"
Write-Host "  npm run tauri dev"
