# Build ListeningMaker for Windows (x64, CPU torch).
# Run inside a clean Python 3.10 env with PyPI simplestart installed:
#   pip install simplestart==0.0.1.66 pocket_tts pywebview pyinstaller
#   python warm_models.py        # optional: bundle the TTS model for offline use
#   powershell -File build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Bundle the TTS model (mirrors ~/.cache/pocket-tts-modelscope) when present.
$ModelArgs = @()
$ModelSrc = Join-Path $env:USERPROFILE ".cache\pocket-tts-modelscope"
if (Test-Path $ModelSrc) {
  $ModelArgs += "--add-data", "$ModelSrc;models"
} else {
  Write-Warning "model cache not found - the packaged app will download the model on first run"
}

# Optional bundled clone voices (voices/clone_male_1.wav, voices/clone_female_1.wav).
$VoiceArgs = @()
if (Test-Path "voices") { $VoiceArgs += "--add-data", "voices;voices" }

# simplestart's compiled subpackages are invisible to PyInstaller's import graph.
$HiArgs = @()
python list_submodules.py | ForEach-Object { $HiArgs += "--hidden-import", $_ }
Write-Host ">>> explicit hidden-imports for simplestart subpackages: $($HiArgs.Count / 2)"

python -m PyInstaller `
  --name ListeningMaker `
  --onedir `
  --windowed `
  --noconfirm `
  --add-data "samples;samples" `
  --add-data "modelscope_patch.py;." `
  --add-data "app.py;." `
  @ModelArgs `
  @VoiceArgs `
  --collect-all pocket_tts `
  --collect-all simplestart `
  --collect-all webview `
  --collect-data simplestart `
  --collect-all easydict `
  --collect-all itsdangerous `
  --collect-all python_multipart `
  --collect-all multipart `
  --collect-all starlette `
  --collect-all anyio `
  --collect-all PIL `
  --collect-all typing_extensions `
  @HiArgs `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan.on `
  --hidden-import watchdog `
  --hidden-import watchdog.observers `
  --hidden-import watchdog.events `
  --hidden-import yaml `
  launcher.py

Write-Host ""
Write-Host "=== Build complete ==="
Write-Host "App folder: $(Get-Location)\dist\ListeningMaker"
