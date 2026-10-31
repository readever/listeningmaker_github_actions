"""Packaged app entry point. Starts the SimpleStart server in-process on a free port and opens the browser."""
import os
import socket
import sys
import threading
import time
import webbrowser

if sys.platform == "win32":
    _base = os.environ.get("APPDATA") or os.path.expanduser("~")
    DATA_DIR = os.environ.get("LM_HOME") or os.path.join(_base, "ListeningMaker")
else:
    DATA_DIR = os.environ.get("LM_HOME") or os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", "ListeningMaker")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ["LM_HOME"] = DATA_DIR  # app.py reads this for outputs/uploads


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# when frozen, app.py lives inside the bundle alongside this launcher
here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app_py = os.path.join(here, "app.py")

port = free_port()

# simplestart.launch.start() reads sys.argv to discover the user script + port.
# It also calls uvicorn.run(...) which blocks, so run it on a worker thread.
sys.argv = ["ss", app_py, "--port", str(port)]


def run_server():
    from simplestart.launch import start
    start()


threading.Thread(target=run_server, daemon=True).start()

# wait for the server to come up (first launch also loads the model — allow up to 120s)
for _ in range(150):
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        break
    except OSError:
        time.sleep(1)
else:
    sys.exit("Server failed to start within 150s")

url = f"http://127.0.0.1:{port}"

# Prefer a native app window (WKWebView via pywebview); fall back to the default browser.
try:
    import webview
    webview.create_window("ListeningMaker", url, width=1200, height=860,
                          min_size=(900, 620))
    webview.start()  # blocks until the window is closed
    sys.exit(0)      # window closed → quit the app
except Exception:
    webbrowser.open(url)
    # keep the main thread alive for the lifetime of the server
    threading.Event().wait()
