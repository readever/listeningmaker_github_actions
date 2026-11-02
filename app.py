import os, re, shutil, subprocess, sys, time, wave
from contextlib import suppress

import numpy as np
import torch

import modelscope_patch  # download model weights from ModelScope (CN-friendly, no token)

import simplestart as ss
from pocket_tts import TTSModel

FROZEN = getattr(sys, "frozen", False)
# bundled data (samples/) lives next to the source in dev, in sys._MEIPASS when packaged
ASSET_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# writable data: when packaged, the .app bundle is read-only — use Application Support
DATA_DIR = os.environ.get("LM_HOME") or (
    os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ListeningMaker")
    if FROZEN else ASSET_DIR)
OUT_DIR = os.path.join(DATA_DIR, "outputs")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
KEEP = 100  # max files to keep in each dir (outputs + uploads), older ones get pruned
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# bundled clone samples (voices/ ships with the app) — better quality than the stock voices
CLONE_SAMPLES = {  # dropdown name -> (male/female, wav file in voices/)
    "口语男声": ("male", "speak_m01.wav"),
    "播音男声": ("male", "news-male-01.wav"),
    "口语女声": ("female", "speak_f01.wav"),
    "播音女声": ("female", "news-female-01.wav"),
}
# options appear only when the sample file actually ships with the app
BUILTIN_CLONES = {n: os.path.join(ASSET_DIR, "voices", f) for n, (_, f) in CLONE_SAMPLES.items()
                  if os.path.isfile(os.path.join(ASSET_DIR, "voices", f))}
MALE_VOICES = ["George", "Paul", "Jean"]
FEMALE_VOICES = ["Mary", "Anna", "Alba"]
for _name, (_slot, _) in CLONE_SAMPLES.items():
    if _name in BUILTIN_CLONES:
        (FEMALE_VOICES if _slot == "female" else MALE_VOICES).append(_name)
CLONE_M, CLONE_F = "克隆男声", "克隆女声"  # extra dropdown options: use the uploaded sample for that slot
PAUSES = ["0.5 秒", "1 秒", "2 秒"]

# one ready-to-try example per tab, loaded from the samples/ folder
def load_sample(n):
    with open(os.path.join(ASSET_DIR, "samples", f"sample{n}.txt"), encoding="utf-8") as f:
        return f.read().strip()
EXAMPLES = [load_sample(i) for i in (1, 2, 3)]  # 1=短对话, 2=长对话, 3=短文
EX_NEWS = load_sample(4)  # 双主播新闻 demo: fills the passage tab and auto-picks the broadcast clone voices
EX_SLOW = load_sample(5)  # 慢速朗读 demo: auto-sets 0.8x speed

model = TTSModel.load_model(language="english")  # loaded once when a user first opens the page
SR = model.config.mimi.sample_rate
S = ss.session
ss.config.title = "英语听力音频生成器"  # browser tab title
ss.config.upload_path = UPLOAD_DIR  # uploads go to a writable dir (the app bundle is read-only when packaged)
for _k in ("short", "long", "passage"):  # per-tab status, so switching tabs never shows another tab's message
    setattr(S, f"status_{_k}", getattr(S, f"status_{_k}", None) or "粘贴听力原文，点击「生成音频」。")

S.clone_hint = S.clone_hint or ""
S.save_msg = S.save_msg or ""

_states = {}  # voice token (built-in name or clone sample path) -> cached model state
clone_paths = {"male": "", "female": ""}  # slot -> uploaded clone sample path; each client runs its own copy of this page, so no cross-user clash


def parse_dialogue(text):
    """Split 'Name: sentence' lines into (speaker, sentence); lines without a name get None."""
    segs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z][\w .'-]{0,19})\s*[:：]\s*(.+)$", line)
        segs.append((m.group(1), m.group(2).strip()) if m else (None, line))
    return segs


MAX_SEG = 300  # chars per TTS call — beyond this, quality gets unstable, so overlong paragraphs are chunked
SENT_SPLIT = re.compile(r"(?<=[.!?。！？；])\s*")  # sentence boundaries, ASCII + CJK punctuation


def parse_passage(text):
    """One paragraph (line) per TTS call keeps the flow natural; overlong paragraphs are chunked at sentence boundaries."""
    segs = []
    for line in text.strip().splitlines():  # line-first split, so marker lines like [Repeat] and headers like "Questions" stay separate
        line = line.strip()
        if not line:
            continue
        if len(line) <= MAX_SEG:
            segs.append((None, line))
            continue
        parts = []
        for p in SENT_SPLIT.split(line):  # split into sentences first...
            while len(p) > MAX_SEG:  # ...a punctuation-less run longer than MAX_SEG gets hard-split on word gaps
                cut = p.rfind(" ", 0, MAX_SEG)
                parts.append(p[:cut if cut > 0 else MAX_SEG])
                p = p[cut if cut > 0 else MAX_SEG:].strip()
            parts.append(p)
        chunk = ""
        for p in parts:  # greedy-pack sentences into chunks of at most MAX_SEG chars
            if chunk and len(chunk) + len(p) + 1 > MAX_SEG:
                segs.append((None, chunk))
                chunk = p
            else:
                chunk = f"{chunk} {p}".strip()
        if chunk:
            segs.append((None, chunk))
    return segs


PAUSE = "@pause"  # sentinel speaker for silence segments


def expand_pauses(segs):
    """A marker line like [停5秒] / 【停3秒】 becomes a silence segment instead of speech."""
    out = []
    for speaker, text in segs:
        m = re.fullmatch(r"[\[【]\s*停\s*(\d+(?:\.\d+)?)\s*秒?\s*[\]】]", text.strip())
        out.append((PAUSE, m.group(1)) if m else (speaker, text))
    return out


def time_stretch(x, speed):
    """WSOLA time-stretch, pitch unchanged: speed 0.8 -> audio 1.25x longer."""
    x = np.asarray(x, dtype=np.float32)
    frame = 2048                      # ~85 ms at 24 kHz
    hop_out = frame // 2              # synthesis hop (50% overlap, Hann window)
    hop_in = max(1, round(hop_out * speed))  # smaller analysis hop = stretched output
    tol = frame // 8                  # frame-alignment search range (+-256 samples)
    win = np.hanning(frame).astype(np.float32)
    y = np.zeros(int(np.ceil(len(x) / speed)) + frame, dtype=np.float32)
    wsum = np.zeros_like(y)
    pos_in = pos_out = 0
    while pos_in + frame < len(x) and pos_out + frame < len(y):
        if pos_out:  # slide the frame start within +-tol so its head best continues the audio already written
            ref = y[pos_out:pos_out + hop_out]
            lo, hi = max(0, pos_in - tol), min(len(x) - frame, pos_in + tol)
            if hi > lo:
                segs = np.lib.stride_tricks.sliding_window_view(x[lo:hi + hop_out], hop_out)
                scores = (segs @ ref) / (np.linalg.norm(segs, axis=1) + 1e-8)
                pos_in = lo + int(np.argmax(scores))
        y[pos_out:pos_out + frame] += x[pos_in:pos_in + frame] * win
        wsum[pos_out:pos_out + frame] += win
        pos_in += hop_in
        pos_out += hop_out
    tail = min(len(x) - pos_in, len(y) - pos_out)  # leftover shorter than one frame: append as-is
    if tail > 0:
        y[pos_out:pos_out + tail] += x[pos_in:pos_in + tail]
        wsum[pos_out:pos_out + tail] += 1.0
    mask = wsum > 1e-6
    y[mask] /= wsum[mask]
    return y[:int(np.ceil(len(x) / speed))]


def synth(segs, gap_s, speed=1.0, on_progress=None):
    """Generate each (voice, sentence) segment, join with silence gaps. voice == PAUSE means pure silence."""
    gap = torch.zeros(int(SR * gap_s))
    parts = []
    for i, (voice, sentence) in enumerate(segs, 1):
        if voice == PAUSE:
            audio = torch.zeros(int(SR * float(sentence)))
        else:
            if voice not in _states:  # setdefault would still CALL get_state_for_audio_prompt every time — only run it once per voice
                v = BUILTIN_CLONES.get(voice) or (voice if os.path.isfile(voice) else voice.lower())  # bundled/uploaded clones are file paths — never lowercase those
                _states[voice] = model.get_state_for_audio_prompt(v)
            audio = torch.cat([c.reshape(-1) for c in model.generate_audio_stream(model_state=_states[voice], text_to_generate=sentence)])
            if speed < 0.99:  # slow down speech only — pause segments and gaps keep their real length
                audio = torch.from_numpy(time_stretch(audio.numpy(), speed))
        if audio.numel():
            if parts:
                parts.append(gap.clone())
            parts.append(audio)
        if on_progress:
            on_progress(i, len(segs))
    return torch.cat(parts).numpy() if parts else None


def write_wav(path, audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


@ss.thread  # run in the background, so the page stays responsive while generating
def run_gen(kind, text, male, female, gap, speed, key):
    def note(msg):  # write this tab's own status and push it
        setattr(S, f"status_{key}", msg)
        ss.refresh_page()
    note("⏳ 正在生成，请耐心等待…")
    t0 = time.time()
    try:
        segs = parse_passage(text) if kind == "passage" else parse_dialogue(text)
        segs = expand_pauses(segs)
        if not segs:
            raise ValueError("请先粘贴听力原文")
        first = next((s.upper() for s, _ in segs if s and s.upper() in ("M", "MAN", "W", "WOMAN")), "M")  # who speaks first
        first_voice = female if first in ("W", "WOMAN") else male  # Q: and unprefixed dialogue lines follow the first speaker
        marks = {"M": male, "MAN": male, "W": female, "WOMAN": female, "Q": first_voice, "QUESTION": first_voice}  # standard exam markers
        pool, roles, i = [male, female], {}, 0
        cur = female if kind == "passage" else first_voice  # default voice for unprefixed lines; a [M]/[W]/[男声]/[女声] line switches it mid-text
        resolved = []
        repeat_at = None
        for speaker, text in segs:
            d = None if speaker == PAUSE else re.fullmatch(r"[\[【]\s*(m|w|男声|女声)\s*[\]】]", text.strip(), re.I)
            if speaker != PAUSE and re.fullmatch(r"[\[【]\s*repeat\s*[\]】]", text.strip(), re.I):
                repeat_at = len(resolved)  # [Repeat]: replay everything so far, second pass read by the other voice
            elif d:
                cur = male if d.group(1).lower() in ("m", "男声") else female
            elif speaker is None or speaker == PAUSE:
                resolved.append((cur if speaker is None else PAUSE, text))
            else:
                spk = speaker.upper()
                if spk in marks:
                    v = marks[spk]
                else:  # custom names still work: alternate male/female by first appearance
                    if speaker not in roles:
                        roles[speaker] = pool[i % 2]; i += 1
                    v = roles[speaker]
                resolved.append((v, text))
        if repeat_at is not None:  # exam convention: the body plays twice; directions and questions only once
            direction = re.compile(r"based on the following|seconds? to read", re.I)  # both halves of the standard directions line
            other = {male: female, female: male}
            body = [(other.get(v, v), t) for v, t in resolved[:repeat_at] if not direction.search(t)]
            resolved = resolved[:repeat_at] + body + resolved[repeat_at:]
        audio = synth(resolved, gap, speed, lambda i, n: note(f"⏳ 正在生成 {i}/{n} 段，请耐心等待…"))
        if audio is None:
            raise ValueError("没有可生成的内容")
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, f"listening_{int(time.time() * 1000)}.wav")
        write_wav(out, audio)
        with wave.open(out) as w:
            dur = w.getnframes() / w.getframerate()
        S.last_out = out  # remembered for the desktop save button (webview can't do browser downloads)
        note(f"✅ 生成完成：{dur:.0f} 秒音频，用时 {time.time() - t0:.1f} 秒。" + ("点播放器右上角下载图标可保存到下载文件夹。" if FROZEN else "可在线试听或下载。"))
        for d in (OUT_DIR, UPLOAD_DIR):  # cap both outputs and uploads at KEEP files each
            if os.path.isdir(d):
                files = sorted((os.path.join(d, f) for f in os.listdir(d)), key=os.path.getmtime)
                for f in files[:-KEEP]:
                    with suppress(OSError): os.remove(f)
        with suppress(Exception):  # if the user refreshed meanwhile, `player` is stale — just skip playing
            player.src = out
            player.play()
    except Exception as e:
        note(f"❌ {e}")
    finally:
        ss.refresh_page()


def gen(kind, text, key):
    male = clone_paths["male"] if male_sel.value == CLONE_M else male_sel.value  # a clone option resolves to the uploaded sample path
    female = clone_paths["female"] if female_sel.value == CLONE_F else female_sel.value
    if not male or not female:
        setattr(S, f"status_{key}", "❌ 选择了克隆音色，请先在下方上传克隆样本")
        ss.refresh_page()
        return
    run_gen(kind, text, male, female, float(gap_sel.value.split()[0]), float(speed_sel.value), key)


def save_audio(e):
    """Copy the last generated wav to ~/Downloads and reveal it in Finder/Explorer.

    The packaged app runs in a webview (WKWebView / WebView2): the player's built-in
    download button is a browser <a download> link, which WKWebView turns into an
    in-page navigation (QuickTime takes over) and WebView2 silently ignores.
    """
    out = getattr(S, "last_out", "")
    if not out or not os.path.isfile(out):
        S.save_msg = "请先生成音频"
        ss.refresh_page()
        return
    dest_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    if not os.path.isdir(dest_dir):
        dest_dir = DATA_DIR
    dest = os.path.join(dest_dir, os.path.basename(out))
    shutil.copy2(out, dest)
    S.save_msg = f"✅ 已保存：{dest}"
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", dest])  # reveal in Finder
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(dest)])  # reveal in Explorer
    ss.refresh_page()


def on_male_sel(e):  # picked 克隆男声 with no sample yet → reopen the clone area and say what to do
    if e.value == CLONE_M and not clone_paths["male"]:
        clone_sheet.visible = True
        S.clone_hint = "请在「克隆男声」页上传样本：安静环境录制 5 秒以上清晰人声（wav / mp3）"
        ss.refresh_page()


def on_female_sel(e):
    if e.value == CLONE_F and not clone_paths["female"]:
        clone_sheet.visible = True
        S.clone_hint = "请在「克隆女声」页上传样本：安静环境录制 5 秒以上清晰人声（wav / mp3）"
        ss.refresh_page()


def close_clone(e):
    clone_sheet.visible = False
    ss.refresh_page()


def up_ok(slot, sel, name, e):
    src = (e.value or {}).get("real_path") or ""  # e.value = {"real_path": ..., "name": ...} — info about the uploaded file
    if os.path.isfile(src):
        try:
            _states[src] = model.get_state_for_audio_prompt(src)  # validate right now; the cached state also speeds up the first generation
            clone_paths[slot] = src
            sel.value = CLONE_M if slot == "male" else CLONE_F  # confirm the dropdown on the cloned voice
            S.clone_hint = f"✅ {name}已切换为克隆声音"
        except Exception:
            S.clone_hint = f"❌ 这个文件不能作为克隆样本，请上传 wav / mp3 人声录音（模型权重等 .safetensors 文件不适用）"
    else:
        S.clone_hint = "❌ 上传失败，请重试"
    ss.refresh_page()


def up_reset(slot, sel, e):  # user clicked ✕ on the uploader: drop the clone, delete the sample file, restore the built-in voice
    old = clone_paths[slot]
    clone_paths[slot] = ""
    if old and os.path.isfile(old):
        with suppress(OSError): os.remove(old)  # scheme A: instant cleanup when user clicks ✕
    sel.value = MALE_VOICES[0] if slot == "male" else FEMALE_VOICES[0]
    S.clone_hint = "已恢复内置音色；重新选择克隆选项可再次上传"
    ss.refresh_page()


def fill(ta, text, male=None, female=None, speed=None):
    ta.value = text  # component value syncs to the page automatically
    if male and male in MALE_VOICES:  # presets apply only when that voice option exists (bundled sample shipped)
        male_sel.value = male
    if female and female in FEMALE_VOICES:
        female_sel.value = female
    if speed:
        speed_sel.value = speed
    else:
        speed_sel.value = 1.0  # any example click pins a known speed; the user's own manual picks are untouched


def example_row(ta, example, note="", **preset):
    with ss.row(align="center", no_gutters=True, style="gap:4px; margin:0 0 8px -8px;"):  # -8px cancels the text button's own padding so it left-aligns with the line above
        ss.button("示例", variant="text", size="x-small", color="primary", onclick=lambda e: fill(ta, example, **preset))
        if note:
            ss.text(note, size="small", type="grey-darken-1")


# ── UI ───────────────────────────────────────────────────────
with ss.sidebar(position="top", shared=True, style="background-color:#f8fafc; border-bottom:1px solid #e5e7eb; padding:0 16px;"):
    with ss.row(align="center", justify="space-between", no_gutters=True, style="height:56px; flex-wrap:nowrap;"):
        ss.text("🎧 英语听力音频生成器", tag="b")
with ss.row(justify="center"):
    ss.text("粘贴听力原文，自动生成可下载的英语听力音频。免费、不限量、无需注册。", size="small")
ss.space()
with ss.row(justify="center"):
    with ss.panel(width="760px", padding=16):
        with ss.row(align="center"):
            male_sel = ss.selectbox([("👨 " + v, v) for v in MALE_VOICES] + [("🎙️ " + CLONE_M + "…", CLONE_M)], value=MALE_VOICES[0], label="男声", onchange=on_male_sel)  # "…" marks the option that needs an upload first
            female_sel = ss.selectbox([("👩 " + v, v) for v in FEMALE_VOICES] + [("🎙️ " + CLONE_F + "…", CLONE_F)], value=FEMALE_VOICES[0], label="女声", onchange=on_female_sel)
            gap_sel = ss.selectbox(PAUSES, value=PAUSES[0], label="句间停顿")
            speed_sel = ss.selectbox([("正常 1.0倍", 1.0), ("稍慢 0.9倍", 0.9), ("慢速 0.8倍", 0.8), ("很慢 0.7倍", 0.7)], value=1.0, label="语速")
        ss.space(16)
        clone_sheet = ss.sheet(border=True, rounded="lg", padding=12, width="100%", color="#f5f5f5", style="margin-top:4px;", visible=False)  # hidden until a 克隆 option is picked
        with clone_sheet:
            with ss.row(justify="end", no_gutters=True, style="width:100%;"):  # sheet content is a centering flex container — force full width so justify="end" reaches the corner
                ss.button("✕", size="x-small", variant="text", onclick=close_clone)
            t_cm, t_cf = ss.tabs([CLONE_M, CLONE_F])
            with t_cm:
                up_male = ss.audio(fileType="file", accept="audio/*", dragMode=True, minimal=False, downloadable=False, closable=True, block=True, color="primary", label="上传男声克隆样本（5 秒以上清晰人声，wav / mp3）", onsuccess=lambda e: up_ok("male", male_sel, "男声", e), onreset=lambda e: up_reset("male", male_sel, e))
            with t_cf:
                up_female = ss.audio(fileType="file", accept="audio/*", dragMode=True, minimal=False, downloadable=False, closable=True, block=True, color="#FF6D00", label="上传女声克隆样本（5 秒以上清晰人声，wav / mp3）", onsuccess=lambda e: up_ok("female", female_sel, "女声", e), onreset=lambda e: up_reset("female", female_sel, e))
            ss.text("@@clone_hint", size="small", type="grey-darken-1")
        ss.space(12)
        t_short, t_long, t_passage = ss.tabs(["短对话", "长对话", "短文"])
        with t_short:
            ta_short = ss.textarea(rows=6, maxlength=2000, show_word_limit=True,
                                   placeholder="每行一句，标准格式：M: / W: / Q:\n\nM: The museum closes at 5 p.m. today.\nW: Then we'd better hurry.\nQ: What does the woman mean?")
            ss.text("M: 男声，W: 女声；单独一行 [停5秒] 可插入停顿", size="small", type="grey-darken-1")
            example_row(ta_short, EXAMPLES[0], "（上海高考卷格式）")
            ss.button("🎧 生成音频", type="primary", block=True, onclick=lambda e: gen("dialogue", ta_short.value, "short"))
            ss.text("@@status_short", size="small", type="grey-darken-1")
        with t_long:
            ta_long = ss.textarea(rows=12, maxlength=2000, show_word_limit=True,
                                  placeholder="粘贴整段长对话，每行一句：\n\nW: Good evening, Dr. Smith. Thank you for coming.\nM: Good evening. It's my pleasure to be here.\nW: Could you tell us about your research?")
            ss.text("M: 男声，W: 女声；[M] / [W] 切换音色，[停5秒] 插入停顿，[Repeat] 再读一遍", size="small", type="grey-darken-1")
            example_row(ta_long, EXAMPLES[1], "（上海高考卷格式）")
            ss.button("🎧 生成音频", type="primary", block=True, onclick=lambda e: gen("dialogue", ta_long.value, "long"))
            ss.text("@@status_long", size="small", type="grey-darken-1")
        with t_passage:
            ta_passage = ss.textarea(rows=10, maxlength=2000, show_word_limit=True,
                                     placeholder="粘贴短文（可含考试指令和 Questions 部分）。例如：\n\nQuestions 11 through 13 are based on the following passage. ...")
            ss.text("默认女声朗读，[M]/[W] 可中途换声；[Repeat] 换声再读一遍；[停5秒] 插入停顿", size="small", type="grey-darken-1")
            example_row(ta_passage, EXAMPLES[2], "（上海高考卷格式）")
            example_row(ta_passage, EX_NEWS, "（双主播新闻，自动选播音音色）", male="播音男声", female="播音女声")
            example_row(ta_passage, EX_SLOW, "（慢速朗读，自动选播音女声、0.7 倍）", female="播音女声", speed=0.7)
            ss.button("🎧 生成音频", type="primary", block=True, onclick=lambda e: gen("passage", ta_passage.value, "passage"))
            ss.text("@@status_passage", size="small", type="grey-darken-1")
ss.space(16)
with ss.row(justify="center"):
    with ss.panel(width="760px", padding=16):
        with ss.row(no_gutters=True):
            if FROZEN:  # ondownload intercepts the player's native download click → save_audio (copy to ~/Downloads + reveal); button/UI is the web player's own, no CSS hack
                player = ss.audio(downloadable=True, ondownload=save_audio, block=True, color="#FF6D00")
            else:
                player = ss.audio(downloadable=True, block=True, color="#FF6D00")
        if FROZEN:
            ss.space(8)
            ss.text("@@save_msg", size="small", type="grey-darken-1")
ss.space(16)
if not FROZEN:  # 单源：本文件同时服务网页版（sys.frozen=False）与 CI 打包版（=True），CI 仓的 app.py 是本文件的逐字节拷贝
    with ss.row(justify="center"):
        with ss.panel(width="760px", padding=16):
            with ss.row(align="center", justify="space-between", no_gutters=True, style="width:100%; flex-wrap:wrap; gap:8px;"):
                ss.text("💾 桌面版 ListeningMaker（本地运行 · 不限量 · 纯离线）", tag="b")
                ss.link("安装包发布页", href="https://modelscope.cn/models/simplestart/listeningmaker", target="_blank", tag_style=True)
            ss.text("与网页版同功能，语音模型已内置安装包，下载即用，课文内容不上传。macOS 首次打开需在「系统设置 → 隐私与安全性」中放行一次。", size="small", type="grey-darken-1")
            with ss.row(gap=12):
                ss.link("🍎 macOS 下载（DMG · 532 MB）", icon="bi-download", href="https://modelscope.cn/models/simplestart/listeningmaker/resolve/master/ListeningMaker-0.1.0-mac-arm64.dmg", target="_blank", variant="flat", type="primary", style="font-size:15px;")
                ss.link("🪟 Windows 下载（ZIP · 500 MB）", icon="bi-download", href="https://modelscope.cn/models/simplestart/listeningmaker/resolve/master/ListeningMaker-0.1.0-windows-x64.zip", target="_blank", variant="flat", type="primary", style="font-size:15px;")
with ss.row(justify="center", no_gutters=True, style="gap:0;"):
    ss.text("本工具由", size="small", style="margin-right:0"); ss.link("SimpleStart", href="https://www.simplestart.cc/zh", size="small", style="padding:4px")
    ss.text("构建 · 语音合成", size="small", style="margin-right:0"); ss.link("Kyutai Pocket TTS", href="https://github.com/kyutai-labs/pocket-tts", size="small", style="padding:4px")
    ss.text("· 仅供学习交流", size="small", style="margin-right:0")
