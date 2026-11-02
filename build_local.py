#!/usr/bin/env python
"""Like `python -m PyInstaller <args>`, but passes an explicit pyi_config with the
binary cache redirected into the workspace. TRAE's sandbox denies writes to
~/Library/Application Support/pyinstaller, which kills the COLLECT stage locally;
on GitHub CI this wrapper behaves identically to plain PyInstaller.

Usage: python build_local.py <same args as python -m PyInstaller>
"""
import os
import sys

from PyInstaller import __main__ as pyi_main
from PyInstaller.configure import get_config

cfg = get_config()
cfg['cachedir'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build', 'pyi-bincache')
pyi_main.run(pyi_args=sys.argv[1:], pyi_config=cfg)
