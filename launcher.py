"""Packaged app entry point. Starts the SimpleStart server in-process on a free port and opens the window.

Everything that can fail is logged to DATA_DIR/listeningmaker.log — with a --windowed build
there is no console, so this file is the only way to see startup crashes remotely.
"""
import faulthandler
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser

if sys.platform == "win32":
    # frozen Windows apps must init multiprocessing before anything spawns subprocesses (torch etc.)
    import multiprocessing
    multiprocessing.freeze_support()
    _base = os.environ.get("APPDATA") or os.path.expanduser("~")
    DATA_DIR = os.environ.get("LM_HOME") or os.path.join(_base, "ListeningMaker")
else:
    DATA_DIR = os.environ.get("LM_HOME") or os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", "ListeningMaker")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ["LM_HOME"] = DATA_DIR  # app.py reads this for outputs/uploads

LOG_PATH = os.path.join(DATA_DIR, "listeningmaker.log")
LOG = open(LOG_PATH, "a", encoding="utf-8")  # append across runs — history helps diagnosis


def log(msg):
    LOG.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    LOG.flush()


def fatal(msg):
    """Last resort: surface the error even without a console (message box on Windows)."""
    log(msg)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"{msg}\n\n日志：{LOG_PATH}", "ListeningMaker 启动失败", 0x10)


# catch native crashes (DLL load failures etc.) that kill the process without a traceback
try:
    faulthandler.enable(LOG)
except Exception:
    pass


def thread_excepthook(args):
    fatal("线程崩溃: " + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


threading.excepthook = thread_excepthook  # the server runs on a daemon thread — land its crashes in the log


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


log(f"=== ListeningMaker 启动 | python {sys.version.split()[0]} | {'frozen' if getattr(sys, 'frozen', False) else 'source'} ===")

# when frozen, app.py lives inside the bundle alongside this launcher
here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app_py = os.path.join(here, "app.py")
log(f"app: {app_py} | data dir: {DATA_DIR}")

port = free_port()

# simplestart.launch.start() reads sys.argv to discover the user script + port.
# It also calls uvicorn.run(...) which blocks, so run it on a worker thread.
sys.argv = ["ss", app_py, "--port", str(port)]


def run_server():
    from simplestart.launch import start
    start()


threading.Thread(target=run_server, daemon=True).start()
log(f"server thread started, waiting for port {port}")

# wait for the server to come up (first launch also loads the model — allow up to 120s)
for i in range(150):
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        log(f"server is up after {i + 1}s")
        break
    except OSError:
        time.sleep(1)
else:
    fatal(f"服务器 150 秒内未能启动（详见日志 {LOG_PATH}）")
    sys.exit(1)

url = f"http://127.0.0.1:{port}"

# Prefer a native app window (WKWebView/WebView2 via pywebview); fall back to the default browser.
try:
    import webview
    log("opening webview window...")
    webview.create_window("ListeningMaker", url, width=1200, height=860,
                          min_size=(900, 620))
    webview.start()  # blocks until the window is closed
    log("window closed, exiting")
    sys.exit(0)      # window closed → quit the app
except Exception:
    fatal("窗口创建失败，改用浏览器打开。原因: " + traceback.format_exc())
    webbrowser.open(url)
    # keep the main thread alive for the lifetime of the server
    threading.Event().wait()
