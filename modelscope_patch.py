"""Redirect pocket-tts downloads from Hugging Face to ModelScope (anonymous, no token).
Usage: import modelscope_patch  BEFORE  TTSModel.load_model().
Falls back to the original Hugging Face download if ModelScope is unreachable.
"""
import os
import sys
import time
from pathlib import Path

import requests

_CACHE = Path(os.environ.get("POCKET_TTS_MS_CACHE",
                             Path.home() / ".cache" / "pocket-tts-modelscope"))
_BASE_URL = "https://modelscope.cn/models/{repo_id}/resolve/master/{filename}"

def _download_from_modelscope(repo_id, filename, retries=3):
    dest = _CACHE / repo_id / filename
    if dest.is_file():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(dest) + ".part"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(_BASE_URL.format(repo_id=repo_id, filename=filename),
                              stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            os.replace(tmp, dest)
            return str(dest)
        except requests.RequestException as e:
            # ModelScope CDN occasionally drops the connection mid-stream
            # (ChunkedEncodingError: Response ended prematurely); retry from scratch.
            last_err = e
            print(f"[modelscope] {repo_id}/{filename} attempt {attempt}/{retries} "
                  f"failed: {e}", file=sys.stderr)
            time.sleep(5 * attempt)
    raise last_err

def _bundled_model_path(repo_id, filename):
    """Model files shipped inside the .app (build.sh adds ~/.cache/pocket-tts-modelscope
    as 'models'). Checked first so the packaged app runs fully offline."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    p = Path(base) / "models" / repo_id / filename
    return str(p) if p.is_file() else None

def _patched_hf_hub_download(repo_id, filename, revision=None, **kwargs):
    bundled = _bundled_model_path(repo_id, filename)
    if bundled:
        return bundled
    try:
        return _download_from_modelscope(repo_id, filename)
    except Exception:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(repo_id=repo_id, filename=filename,
                               revision=revision, **kwargs)

def use_modelscope():
    import pocket_tts.utils.utils as _utils
    _utils.hf_hub_download = _patched_hf_hub_download

use_modelscope()
