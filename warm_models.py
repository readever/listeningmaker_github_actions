"""Download the TTS model files into the ModelScope cache (~/.cache/pocket-tts-modelscope).

Run this BEFORE build.sh / build_windows.ps1 so the packaged app ships the
model and runs fully offline. Re-running is cheap: existing files are skipped.
"""
import modelscope_patch as mp  # importing also installs the ModelScope redirect

REPO_MODEL = "kyutai/pocket-tts"
REPO_VOICE = "kyutai/pocket-tts-without-voice-cloning"

VOICES = [
    "alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles", "cosette",
    "eve", "eponine", "fantine", "george", "jane", "javert", "jean", "marius",
    "mary", "michael", "paul", "peter_yearsley", "stuart_bell", "vera",
]

FILES = [
    (REPO_MODEL, "languages/english/model.safetensors"),
    (REPO_VOICE, "languages/english/tokenizer.json"),
] + [(REPO_VOICE, f"languages/english/embeddings/{v}.safetensors") for v in VOICES]

for repo, filename in FILES:
    print(f"ensuring {repo}/{filename}")
    mp._download_from_modelscope(repo, filename)

print("model cache ready")
