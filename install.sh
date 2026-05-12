#!/usr/bin/env bash
set -euo pipefail

DOBBY="$HOME/dobby"
VENV="$DOBBY/.venv"
PY="$VENV/bin/python"

ok()   { echo "  ✓  $*"; }
fail() { echo "  ✗  $*"; }
info() { echo ""; echo "── $* ──────────────────────────────────"; }

# ── Xcode CLT (provides git, make, clang) ────────────────────────────────────
info "Xcode Command Line Tools"
if xcode-select -p > /dev/null 2>&1; then
  ok "already installed"
else
  echo "  Not found — triggering install dialog..."
  xcode-select --install 2>/dev/null || true
  echo ""
  echo "  A dialog appeared asking you to install the Command Line Tools."
  echo "  Click Install, wait for it to finish, then re-run this script."
  exit 1
fi

# ── Homebrew ──────────────────────────────────────────────────────────────────
info "Homebrew"
if which brew > /dev/null 2>&1; then
  ok "already installed"
else
  echo "  Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [ -f /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  ok "installed"
fi

# Ensure brew is on PATH for the rest of this script (Apple Silicon)
[ -f /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)" || true

# ── Python 3.12+ ──────────────────────────────────────────────────────────────
info "Python"
if python3 -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
  ok "$(python3 --version)"
else
  echo "  Installing Python 3.12..."
  brew install python@3.12
  ok "$(python3 --version)"
fi

# ── Repository ────────────────────────────────────────────────────────────────
info "Repository"
if [ -d "$DOBBY/.git" ]; then
  git -C "$DOBBY" pull --ff-only && ok "up to date"
else
  git clone https://github.com/robert-bogos/dobby.git "$DOBBY" && ok "cloned"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
info "Virtual environment"
if [ -f "$PY" ]; then
  ok "already exists"
else
  python3 -m venv "$VENV" && ok "created"
fi

# ── Python packages ───────────────────────────────────────────────────────────
info "Python packages"
"$PY" - <<'PYEOF'
import importlib.util, subprocess, sys

required = {
    'playwright':     'playwright',
    'patchright':     'patchright==1.59.1',
    'faster_whisper': 'faster-whisper==1.2.1',
    'ollama':         'ollama==0.6.1',
    'mcp':            'mcp==1.27.0',
    'numpy':          'numpy==2.4.4',
    'scipy':          'scipy==1.17.1',
    'dotenv':         'python-dotenv==1.2.2',
    'httpx':          'httpx==0.28.1',
    'httpx_sse':      'httpx-sse==0.4.3',
    'anyio':          'anyio==4.13.0',
    'click':          'click==8.3.2',
    'yaml':           'pyyaml',
    'cryptography':   'cryptography',
}

missing = [pkg for mod, pkg in required.items() if not importlib.util.find_spec(mod)]

if missing:
    print(f"  Installing {len(missing)} package(s)...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet'] + missing)
    print("  ✓  done")
else:
    print("  ✓  all packages already installed")
PYEOF

# ── Clear Chromium caches (ensure clean install) ──────────────────────────────
info "Clearing Chromium caches"
rm -rf ~/Library/Caches/ms-playwright
rm -rf ~/Library/Caches/ms-patchright
ok "done"

# ── Chromium (playwright) ─────────────────────────────────────────────────────
info "Chromium (playwright)"
"$PY" - <<'PYEOF'
import subprocess, sys
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); v = b.version; b.close()
    print(f"  ✓  already installed (Chromium {v})")
except Exception:
    print("  Downloading (~200 MB)...")
    subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); v = b.version; b.close()
    print(f"  ✓  installed (Chromium {v})")
PYEOF

# ── Chromium (patchright) ─────────────────────────────────────────────────────
info "Chromium (patchright)"
"$PY" - <<'PYEOF'
import subprocess, sys
from patchright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); v = b.version; b.close()
    print(f"  ✓  already installed (Chromium {v})")
except Exception:
    print("  Downloading (~200 MB)...")
    subprocess.check_call([sys.executable, '-m', 'patchright', 'install', 'chromium'])
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); v = b.version; b.close()
    print(f"  ✓  installed (Chromium {v})")
PYEOF

# ── Ollama ────────────────────────────────────────────────────────────────────
info "Ollama"
if which ollama > /dev/null 2>&1; then
  ok "already installed"
else
  brew install ollama && ok "installed"
fi

if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  ok "service already running"
else
  brew services start ollama
  for i in $(seq 1 10); do
    curl -s http://localhost:11434/api/tags > /dev/null 2>&1 && break; sleep 1
  done
  curl -s http://localhost:11434/api/tags > /dev/null 2>&1 \
    && ok "service started" \
    || { fail "service failed to start — try: brew services restart ollama"; exit 1; }
fi

if ollama list 2>/dev/null | grep -q "gemma4:e4b"; then
  ok "gemma4:e4b already present"
else
  echo "  Pulling gemma4:e4b (may take several minutes)..."
  ollama pull gemma4:e4b && ok "gemma4:e4b ready"
fi

# ── Verification ──────────────────────────────────────────────────────────────
info "Verification"
"$PY" - <<'PYEOF'
import importlib.util
failed = []
for mod in ['playwright','patchright','faster_whisper','ollama','mcp','numpy','scipy','dotenv','httpx','anyio','click','yaml','cryptography']:
    if importlib.util.find_spec(mod):
        print(f"  ✓  {mod}")
    else:
        print(f"  ✗  {mod} MISSING"); failed.append(mod)
if failed:
    raise SystemExit(1)
PYEOF

echo ""
echo "════════════════════════════════════════════════"
echo "  Install complete."
echo ""
echo "  Next: run setup_account.py to create the bot"
echo "  account, then restart Claude Desktop."
echo "════════════════════════════════════════════════"
