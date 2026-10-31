"""Print simplestart's compiled submodule names (import-hidden .so/.pyd), one per line.

simplestart loads its UI components dynamically (getattr by name), so PyInstaller
cannot see them via the import graph, and collect-all does not pick up extension
submodules. Both build scripts feed this list to --hidden-import.
"""
import os
import importlib.machinery as im

import simplestart

base = os.path.dirname(simplestart.__file__)
for sub in ("ss_ui", "ss_core", "ss_modules", "ss_api", "ss_system"):
    pdir = os.path.join(base, sub)
    for fn in sorted(os.listdir(pdir)):
        if fn in ("__init__.py", "__pycache__") or fn.endswith(".c"):
            continue
        for suf in im.all_suffixes():
            if fn.endswith(suf):
                print("simplestart.%s.%s" % (sub, fn[:-len(suf)]))
                break
