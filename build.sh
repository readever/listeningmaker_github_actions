#!/bin/bash
# Build ListeningMaker as a native macOS .app (torch prototype, arm64).
# Run from this directory:  bash build.sh
set -e

cd "$(dirname "$0")"

# CI build: PyPI simplestart (Cython-protected .so) is installed by the workflow beforehand.
PY=python

# Bundle the TTS model (mirrors ~/.cache/pocket-tts-modelscope layout) so the
# packaged app runs fully offline. warm_models.py in the workflow pre-populates it.
MODEL_ARGS=()
MODEL_SRC="$HOME/.cache/pocket-tts-modelscope"
if [ -d "$MODEL_SRC" ]; then
  MODEL_ARGS+=(--add-data "$MODEL_SRC:models")
else
  echo "WARNING: model cache not found at $MODEL_SRC — app will download the model on first run"
fi

# Optional bundled clone voices (voices/*.wav) — options hide themselves when absent.
VOICE_ARGS=()
if [ -d voices ]; then VOICE_ARGS+=(--add-data "voices:voices"); fi

# simplestart's compiled subpackages are loaded dynamically and invisible to
# PyInstaller's import graph — list them via the shared helper script.
SUBMODS=$($PY list_submodules.py)
HI_ARGS=()
for m in $SUBMODS; do HI_ARGS+=(--hidden-import "$m"); done
echo ">>> explicit hidden-imports for simplestart subpackages: ${#HI_ARGS[@]}"

$PY build_local.py \
  --name ListeningMaker \
  --onedir \
  --windowed \
  --noconfirm \
  --add-data "samples:samples" \
  --add-data "modelscope_patch.py:." \
  --add-data "app.py:." \
  "${MODEL_ARGS[@]}" \
  "${VOICE_ARGS[@]}" \
  --collect-all pocket_tts \
  --collect-all simplestart \
  --collect-all webview \
  --collect-data simplestart \
  --collect-all easydict \
  --collect-all itsdangerous \
  --collect-all python_multipart \
  --collect-all multipart \
  --collect-all starlette \
  --collect-all anyio \
  --collect-all PIL \
  --collect-all typing_extensions \
  "${HI_ARGS[@]}" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import watchdog \
  --hidden-import watchdog.observers \
  --hidden-import watchdog.events \
  --hidden-import yaml \
  launcher.py

echo
echo "=== Build complete ==="
echo "App bundle: $(pwd)/dist/ListeningMaker.app"
du -sh "$(pwd)/dist/ListeningMaker.app"
