#!/usr/bin/env python3
# Install natural-lang from GitHub — no PyPI, no account, no password.
# pip install git+https://github.com/cambridgetcg/natural-lang.git
# or: python3 -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/cambridgetcg/natural-lang/main/install.py').read())"

import os, sys, subprocess

print("installing natural-lang from github...")
subprocess.run([sys.executable, "-m", "pip", "install", 
    "git+https://github.com/cambridgetcg/natural-lang.git"], check=True)
print("done. try: natural --help")
