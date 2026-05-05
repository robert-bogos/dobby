# Dobby Setup Guide

**Give this file to Claude** (drag it into Claude Desktop, or paste its contents) and say: _"Set up Dobby for me following this guide."_ Claude will execute each step, check for errors, and verify the full setup at the end.

This guide is safe to re-run at any time. Each step checks whether it has already been completed and skips it if so — nothing will be reinstalled, overwritten, or re-created unnecessarily.

---

## What Dobby does

Dobby is a Microsoft Teams meeting notetaker bot. It joins meetings as a participant, records audio, transcribes speech with Whisper, summarises the meeting with a local Ollama model, and saves structured notes (summary + decisions + action items) to your disk. It integrates with Claude Desktop as an MCP server, so you can ask Claude to join a meeting, check its status, or read past notes.

---

## Prerequisites

- macOS (Apple Silicon or Intel)
- Admin access (for Homebrew installs)
- Claude Desktop installed and running at least once (so its config directory exists)
- An internet connection

---

## Step 0 — Pre-flight checks

Run this block first. It checks every system-level tool that later steps depend on. Fix any FAILs before continuing.

```bash
echo "=== Pre-flight checks ==="

xcode-select -p > /dev/null 2>&1 \
  && echo "Xcode CLT:  OK ($(xcode-select -p))" \
  || echo "Xcode CLT:  MISSING — run: xcode-select --install  (then re-run this check)"

which brew > /dev/null 2>&1 \
  && echo "Homebrew:   OK ($(brew --version | head -1))" \
  || echo "Homebrew:   MISSING — see Step 1"

git --version > /dev/null 2>&1 \
  && echo "git:        OK ($(git --version))" \
  || echo "git:        MISSING — install Xcode CLT or run: brew install git"

curl --version > /dev/null 2>&1 \
  && echo "curl:       OK" \
  || echo "curl:       MISSING — run: brew install curl"

python3 -c "
import sys
v = sys.version_info
if v >= (3, 12):
    print(f'Python:     OK ({v.major}.{v.minor}.{v.micro} at {sys.executable})')
else:
    print(f'Python:     FAIL — need 3.12+, found {v.major}.{v.minor} — see Step 2')
" 2>/dev/null || echo "Python:     MISSING — see Step 2"

python3 -m pip --version > /dev/null 2>&1 \
  && echo "pip:        OK ($(python3 -m pip --version))" \
  || echo "pip:        MISSING — run: python3 -m ensurepip --upgrade"

echo "=== Done ==="
```

Fix every FAIL before moving on. Do not skip this step.

---

## Step 1 — Install Homebrew

```bash
if which brew > /dev/null 2>&1; then
  echo "Homebrew already installed ($(brew --version | head -1)) — skipping."
else
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Apple Silicon: add Homebrew to PATH for this session
  [ -f /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  brew --version && echo "Homebrew installed OK."
fi
```

---

## Step 2 — Install Python 3.12+

```bash
python3 -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null \
  && echo "Python $(python3 --version) already present — skipping install." \
  || (echo "Installing Python 3.12..." && brew install python@3.12)

# Ensure pip is available
python3 -m pip --version > /dev/null 2>&1 \
  && echo "pip OK." \
  || (echo "Bootstrapping pip..." && python3 -m ensurepip --upgrade && python3 -m pip install --upgrade pip)
```

---

## Step 3 — Clone or update the repository

If `~/dobby` already exists and is the correct git repo, this pulls the latest changes instead of cloning.

```bash
if [ -d ~/dobby/.git ] && [ -f ~/dobby/bot.py ]; then
  echo "Repo already present — pulling latest changes..."
  git -C ~/dobby pull --ff-only && echo "Repo up to date."
else
  echo "Cloning repo..."
  git clone https://github.com/robert-bogos/dobby.git ~/dobby
fi

ls ~/dobby/bot.py ~/dobby/mcp_server.py ~/dobby/setup_account.py > /dev/null \
  && echo "Repo check: OK" \
  || echo "FAIL — expected files missing from ~/dobby"
```

---

## Step 4 — Set up the Python virtual environment and install packages

```bash
# Create venv only if it doesn't already exist
if [ -f ~/dobby/.venv/bin/python ]; then
  echo "Virtual environment already exists — skipping creation."
else
  echo "Creating virtual environment..."
  python3 -m venv ~/dobby/.venv
fi

# Check which packages are missing and only install those
echo "Checking packages..."
~/dobby/.venv/bin/python -c "
import subprocess, sys

required = {
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

missing_installs = []
for mod, pkg in required.items():
    try:
        __import__(mod)
        print(f'  OK  {pkg}')
    except ImportError:
        print(f'  MISSING  {pkg}')
        missing_installs.append(pkg)

if missing_installs:
    print(f'\nInstalling {len(missing_installs)} missing package(s)...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
    subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing_installs)
    print('Install complete.')
else:
    print('\nAll packages already installed — skipping pip install.')
"
```

Verify all imports pass after the above:

```bash
~/dobby/.venv/bin/python -c "
failed = []
for mod in ['patchright','faster_whisper','ollama','mcp','numpy','scipy','av','dotenv','httpx','anyio','click','yaml','cryptography']:
    try: __import__(mod); print(f'  OK  {mod}')
    except ImportError: print(f'  FAIL {mod}'); failed.append(mod)
if failed:
    print(f'\nStill missing: {failed} — re-run Step 4')
else:
    print('\nAll packages: OK')
"
```

---

## Step 5 — Install the Chromium browser for Patchright

Patchright ships its own patched Chromium binary. The `pip install` in Step 4 does **not** include it — it must be downloaded separately. This step checks whether the binary is already present before downloading.

```bash
~/dobby/.venv/bin/python -c "
from patchright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        v = b.version
        b.close()
    print(f'Chromium already installed (version {v}) — skipping download.')
except Exception:
    print('Chromium not found — downloading now (~200 MB)...')
    import subprocess, sys
    subprocess.check_call([sys.executable, '-m', 'patchright', 'install', 'chromium'])
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        v = b.version
        b.close()
    print(f'Chromium installed OK (version {v}).')
"
```

---

## Step 6 — Install and start Ollama

Each part (install, service, model) is checked independently.

```bash
# Install Ollama if not present
if which ollama > /dev/null 2>&1; then
  echo "Ollama binary already installed — skipping brew install."
else
  echo "Installing Ollama..."
  brew install ollama
fi

# Start the service if not already running
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  echo "Ollama service already running — skipping start."
else
  echo "Starting Ollama service..."
  brew services start ollama
  echo "Waiting for service to come up..."
  for i in $(seq 1 10); do
    curl -s http://localhost:11434/api/tags > /dev/null 2>&1 && break
    sleep 1
  done
  curl -s http://localhost:11434/api/tags > /dev/null 2>&1 \
    && echo "Ollama service: OK" \
    || echo "FAIL — Ollama did not start. Try: brew services restart ollama"
fi

# Pull llama3 model if not already present (~4 GB, may take several minutes)
if ollama list 2>/dev/null | grep -q llama3; then
  echo "llama3 model already present — skipping pull."
else
  echo "Pulling llama3 model (~4 GB)..."
  ollama pull llama3
fi
```

---

## Step 7 — Create a Microsoft Outlook bot account

This step checks whether credentials already exist — either in `~/dobby/.env` or in the Claude Desktop MCP config — before opening a browser. If valid credentials are found in either place, the account creation is skipped entirely.

```bash
python3 -c "
import json, pathlib, re

# Check ~/dobby/.env
env_file = pathlib.Path.home() / 'dobby/.env'
env_email = ''
if env_file.exists():
    text = env_file.read_text()
    m = re.search(r'TEAMS_BOT_EMAIL=(.+)', text)
    if m: env_email = m.group(1).strip()

# Check Claude Desktop MCP config
cfg_email = ''
cfg_path = pathlib.Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text())
        cfg_email = cfg.get('mcpServers', {}).get('dobby', {}).get('env', {}).get('TEAMS_BOT_EMAIL', '')
    except Exception:
        pass

if env_email:
    print(f'Credentials found in ~/dobby/.env ({env_email}) — skipping account creation.')
elif cfg_email:
    print(f'Credentials found in Claude Desktop config ({cfg_email}) — skipping account creation.')
else:
    print('NO_CREDENTIALS')
" | grep -q "NO_CREDENTIALS" && NEED_ACCOUNT=true || NEED_ACCOUNT=false

if [ "$NEED_ACCOUNT" = "true" ]; then
  echo "No credentials found — account creation needed. See prompt below."
else
  echo "Credentials already set — Step 7 complete, nothing to do."
fi
```

> **Claude:** Only proceed with account creation if the check above printed `No credentials found`. If it printed that credentials were found, tell the user Step 7 is already done and move on to Step 8.
>
> If account creation is needed, tell the user:
>
> _"I'm about to open a browser window and automatically create a new Microsoft Outlook account for your bot. The script fills in the entire signup form on its own. There's one thing I may need your help with: Microsoft sometimes shows a press-and-hold CAPTCHA that the script can't always solve automatically. If that happens I'll tell you — you just solve it in the browser window and the script will continue on its own from there. Ready? Just say go and I'll kick it off."_
>
> Wait for the user to confirm (e.g. "go", "do it", "ok", "yes") before running the command below.

```bash
cd ~/dobby
~/dobby/.venv/bin/python setup_account.py
```

Watch the output. If the auto-solve fails, the script will print:

```
[setup] -------------------------------------------------------
[setup] Auto-solve failed. Please solve the CAPTCHA manually.
[setup] The script will continue automatically once done.
[setup] -------------------------------------------------------
```

Tell the user to press and hold the button in the browser window — the script resumes automatically once it clears.

When it finishes it prints:

```
[setup] Done — credentials saved to /Users/<you>/dobby/.env
[setup]   TEAMS_BOT_EMAIL=<generated>@outlook.com
[setup]   TEAMS_BOT_PASSWORD=<generated>
```

---

## Step 8 — Register the MCP server with Claude Desktop

This step checks the existing config before making any changes. If a valid `dobby` entry is already present with matching credentials and a working Python path, no edits are needed.

```bash
python3 -c "
import json, pathlib

cfg_path = pathlib.Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
venv_python = pathlib.Path.home() / 'dobby/.venv/bin/python'
mcp_script  = pathlib.Path.home() / 'dobby/mcp_server.py'

# Load or create config
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception as e:
        print(f'FAIL — existing config is invalid JSON: {e}')
        raise SystemExit(1)
else:
    cfg = {}

dobby = cfg.get('mcpServers', {}).get('dobby', {})
env   = dobby.get('env', {})
cmd   = dobby.get('command', '')

issues = []
if not env.get('TEAMS_BOT_EMAIL'):  issues.append('TEAMS_BOT_EMAIL missing')
if not env.get('TEAMS_BOT_PASSWORD'): issues.append('TEAMS_BOT_PASSWORD missing')
if cmd != str(venv_python):         issues.append(f'command should be {venv_python}')
if not pathlib.Path(cmd).exists():  issues.append(f'python binary not found at: {cmd}')

if not issues:
    print(f'MCP config already complete — skipping.')
    print(f'  email:   {env[\"TEAMS_BOT_EMAIL\"]}')
    print(f'  display: {env.get(\"TEAMS_BOT_DISPLAY_NAME\", \"(not set)\")}')
    raise SystemExit(0)

print('Issues found — updating config:')
for i in issues: print(f'  - {i}')

# Read credentials from .env
import re, os
env_file = pathlib.Path.home() / 'dobby/.env'
creds = {}
if env_file.exists():
    for line in env_file.read_text().splitlines():
        m = re.match(r'(TEAMS_BOT_EMAIL|TEAMS_BOT_PASSWORD)=(.+)', line)
        if m: creds[m.group(1)] = m.group(2).strip()

if not creds.get('TEAMS_BOT_EMAIL') or not creds.get('TEAMS_BOT_PASSWORD'):
    print('FAIL — ~/dobby/.env missing or incomplete. Run Step 7 first.')
    raise SystemExit(1)

import subprocess
first_name = subprocess.check_output(['id', '-F']).decode().split()[0]

cfg.setdefault('mcpServers', {})['dobby'] = {
    'command': str(venv_python),
    'args':    [str(mcp_script)],
    'env': {
        'TEAMS_BOT_EMAIL':        creds['TEAMS_BOT_EMAIL'],
        'TEAMS_BOT_PASSWORD':     creds['TEAMS_BOT_PASSWORD'],
        'TEAMS_BOT_DISPLAY_NAME': f\"{first_name}'s AI notetaker\",
    }
}

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(json.dumps(cfg, indent=2))
print(f'Config updated OK.')
print(f'  email:   {creds[\"TEAMS_BOT_EMAIL\"]}')
print(f'  display: {first_name}'\''s AI notetaker')
print()
print('RESTART REQUIRED: Quit Claude Desktop and reopen it to load the new config.')
"
```

---

## Step 9 — Full verification

Run every check in sequence. All must pass before the bot is ready to use.

```bash
echo ""
echo "=== Dobby Full Verification ==="
echo ""

echo "[1/5] Python packages"
~/dobby/.venv/bin/python -c "
failed = []
for mod in ['patchright','faster_whisper','ollama','mcp','numpy','scipy','av','dotenv','httpx','anyio','click','yaml','cryptography']:
    try: __import__(mod); print(f'  OK  {mod}')
    except ImportError: print(f'  FAIL {mod}'); failed.append(mod)
if failed: print(f'\n  Re-run Step 4 for: {failed}')
else: print('  All packages OK')
"

echo ""
echo "[2/5] Chromium browser"
~/dobby/.venv/bin/python -c "
from patchright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); v = b.version; b.close()
    print(f'  OK  Chromium {v}')
except Exception as e:
    print(f'  FAIL: {e}')
    print('  Fix: ~/dobby/.venv/bin/python -m patchright install chromium')
"

echo ""
echo "[3/5] Ollama"
curl -s http://localhost:11434/api/tags > /dev/null 2>&1 \
  && echo "  OK  service running" \
  || echo "  FAIL not running — fix: brew services restart ollama"
ollama list 2>/dev/null | grep -q llama3 \
  && echo "  OK  llama3 present" \
  || echo "  FAIL llama3 missing — fix: ollama pull llama3"

echo ""
echo "[4/5] Bot credentials"
~/dobby/.venv/bin/python -c "
import pathlib
env = pathlib.Path.home() / 'dobby/.env'
if not env.exists():
    print('  FAIL .env not found — run Step 7'); exit()
text = env.read_text()
for key in ('TEAMS_BOT_EMAIL', 'TEAMS_BOT_PASSWORD'):
    print(f'  OK  {key}' if key in text else f'  FAIL {key} missing')
"

echo ""
echo "[5/5] MCP config and server startup"
python3 -c "
import json, pathlib, subprocess, os
p = pathlib.Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
try:
    cfg = json.loads(p.read_text())
    dobby = cfg['mcpServers']['dobby']
    cmd = dobby['command']
    env = dobby.get('env', {})
    assert env.get('TEAMS_BOT_EMAIL'), 'TEAMS_BOT_EMAIL missing'
    assert env.get('TEAMS_BOT_PASSWORD'), 'TEAMS_BOT_PASSWORD missing'
    assert pathlib.Path(cmd).exists(), f'python not found: {cmd}'
    print('  OK  claude_desktop_config.json')
except Exception as e:
    print(f'  FAIL {e}'); exit(1)
result = subprocess.run(
    [cmd, str(pathlib.Path.home() / 'dobby/mcp_server.py')],
    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6,
    env={**os.environ, **env}
)
output = result.stdout + result.stderr
if any(x in output for x in ['Traceback', 'ImportError', 'ModuleNotFoundError', 'SyntaxError']):
    print('  FAIL MCP server crashed:'); print(output[:800])
else:
    print('  OK  MCP server starts cleanly')
    if 'Downloading' in output:
        print('  INFO bot-feed.y4m downloading in background (normal on first run)')
" 2>/dev/null

echo ""
echo "=== Verification complete ==="
```

---

## You are ready

After all checks pass and Claude Desktop has been restarted, open Claude Desktop and try:

> "Join this Teams meeting for me: [paste a Teams meeting link]"

Claude will ask if you want it to post a message in the chat, then launch the bot. When the meeting ends (or you ask Claude to leave), it will transcribe and summarise automatically. Ask Claude "show me the notes from my last meeting" to read them.
