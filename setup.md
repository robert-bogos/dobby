# Dobby Setup Guide

**Give this file to Claude** (drag it into Claude Desktop, or paste its contents) and say: _"Set up Dobby for me following this guide."_ Claude will execute each step, check for errors, and verify the full setup at the end.

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

## Step 1 — Install Homebrew

Check if Homebrew is installed:

```bash
which brew
```

If the command returns nothing, install it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

After installing, follow any instructions printed about adding Homebrew to your PATH (the `eval` line for Apple Silicon Macs).

---

## Step 2 — Install Python 3.12+

Check if a suitable Python version exists:

```bash
python3 --version
```

If the version is below 3.12, install via Homebrew:

```bash
brew install python@3.12
```

---

## Step 3 — Clone the repository to ~/dobby

```bash
git clone https://github.com/robert-bogos/dobby.git ~/dobby
```

All files live at `~/dobby`. This is the permanent home for the bot.

---

## Step 4 — Set up the Python virtual environment

```bash
cd ~/dobby
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install \
  patchright==1.59.1 \
  faster-whisper==1.2.1 \
  ollama==0.6.1 \
  mcp==1.27.0 \
  numpy==2.4.4 \
  scipy==1.17.1 \
  av==17.0.1 \
  python-dotenv==1.2.2 \
  httpx==0.28.1 \
  httpx-sse==0.4.3 \
  anyio==4.13.0 \
  click==8.3.2 \
  pyyaml \
  cryptography
```

---

## Step 5 — Install the Chromium browser for Patchright

Patchright (the stealth Playwright fork used by the bot) needs its own Chromium binary:

```bash
cd ~/dobby
source .venv/bin/activate
python -m patchright install chromium
```

---

## Step 6 — Install and start Ollama

Install Ollama via Homebrew:

```bash
brew install ollama
```

Start the Ollama service (runs in the background):

```bash
brew services start ollama
```

Pull the default model (this download is ~4 GB — it may take a few minutes):

```bash
ollama pull llama3
```

Verify Ollama is running:

```bash
ollama list
```

You should see `llama3` in the output.

---

## Step 7 — Create a Microsoft Outlook bot account

> **Claude:** Before running this step, tell the user:
>
> _"I'll automatically create a new Microsoft Outlook account for your bot. The script will fill in the signup form on its own. There's one thing I may need your help with: Microsoft will show a press-and-hold CAPTCHA that the script can't solve automatically. You'll need to do it for me — once you do, everything continues on its own. Ready? Just say go and I'll kick it off."_
>
> Wait for the user to confirm (e.g. "go", "do it", "ok", "yes") before running the command below.

The bot needs a dedicated Microsoft account. `setup_account.py` automates the full signup flow and saves the credentials to `~/dobby/.env`.

```bash
cd ~/dobby
source .venv/bin/activate
python setup_account.py
```

Watch the terminal output. When the CAPTCHA appears the script will print:
```
[setup] ACTION REQUIRED: solve the CAPTCHA in the browser window
[setup] The script will save credentials automatically once done.
```
Tell the user to press and hold the button in the browser window — the script will resume on its own as soon as it clears.

When it finishes successfully it will print:
```
[setup] Done — credentials saved to /Users/<you>/dobby/.env
[setup]   TEAMS_BOT_EMAIL=<generated>@outlook.com
[setup]   TEAMS_BOT_PASSWORD=<generated>
```

**Note:** If you already have a bot account, skip this step and create `~/dobby/.env` manually:

```
TEAMS_BOT_EMAIL=your-bot@outlook.com
TEAMS_BOT_PASSWORD=your-password
```

---

## Step 8 — Register the MCP server with Claude Desktop

Claude Desktop reads its MCP server list from:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

The credentials are passed directly in the MCP server config via its `env` field — Claude Desktop does not source `~/.zshrc`, so this is the correct place for them. The bot subprocess inherits these variables automatically.

Run this to collect the three values needed for the config:

```bash
# credentials saved by setup_account.py
cat ~/dobby/.env

# macOS login name (used in file paths)
whoami

# your first name from macOS system account (used in the bot display name)
id -F | awk '{print $1}'
```

Then open `~/Library/Application Support/Claude/claude_desktop_config.json` (create it if it does not exist) and add the `dobby` entry, substituting the values printed above:

```json
{
  "mcpServers": {
    "dobby": {
      "command": "/Users/<whoami>/dobby/.venv/bin/python",
      "args": ["/Users/<whoami>/dobby/mcp_server.py"],
      "env": {
        "TEAMS_BOT_EMAIL": "<email from .env>",
        "TEAMS_BOT_PASSWORD": "<password from .env>",
        "TEAMS_BOT_DISPLAY_NAME": "<first name from id -F>'s AI notetaker"
      }
    }
  }
}
```

If `claude_desktop_config.json` already has other MCP servers, add the `"dobby"` key alongside them — do not replace the whole file.

After saving, **restart Claude Desktop completely** (Quit from the menu bar icon, then reopen).

---

## Step 9 — Verification

Run this checklist to confirm everything is working before the first meeting.

### 9a — Python environment

```bash
cd ~/dobby
source .venv/bin/activate
python -c "
import faster_whisper, ollama, mcp, patchright, numpy, scipy, av, dotenv
print('All Python packages: OK')
"
```

### 9b — Chromium

```bash
cd ~/dobby
source .venv/bin/activate
python -c "
from patchright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.close()
    print('Chromium: OK')
"
```

### 9c — Ollama + llama3

```bash
ollama list | grep llama3 && echo "Ollama llama3: OK" || echo "FAIL — run: ollama pull llama3"
```

### 9d — Bot credentials in MCP config

```bash
python3 -c "
import json, pathlib
cfg_path = pathlib.Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
cfg = json.loads(cfg_path.read_text())
dobby = cfg.get('mcpServers', {}).get('dobby', {})
env = dobby.get('env', {})
email = env.get('TEAMS_BOT_EMAIL', '')
pw = env.get('TEAMS_BOT_PASSWORD', '')
display = env.get('TEAMS_BOT_DISPLAY_NAME', '')
if email and pw:
    print(f'Credentials in MCP config: OK ({email})')
    print(f'Display name: {display or \"(not set — will default to AI notetaker)\"}')
else:
    print('FAIL — TEAMS_BOT_EMAIL or TEAMS_BOT_PASSWORD missing from mcpServers.dobby.env')
"
```

### 9e — MCP config

```bash
cat ~/Library/Application\ Support/Claude/claude_desktop_config.json | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
if 'dobby' in cfg.get('mcpServers', {}):
    print('MCP config: OK')
else:
    print('FAIL — dobby not found in mcpServers')
"
```

### 10f — MCP server starts without error

```bash
cd ~/dobby
timeout 5 ~/dobby/.venv/bin/python mcp_server.py < /dev/null 2>&1 | head -20
echo "(exit — this is expected after timeout)"
```

The output should not contain any import errors or tracebacks. A line like `[dobby] Downloading bot-feed.y4m in background...` is normal on first run.

---

## You are ready

After all checks pass and Claude Desktop has been restarted, open Claude Desktop and try:

> "Join this Teams meeting for me: [paste a Teams meeting link]"

Claude will ask if you want it to post a message in the chat, then launch the bot. When the meeting ends (or you ask Claude to leave), it will transcribe and summarise automatically. Ask Claude "show me the notes from my last meeting" to read them.
