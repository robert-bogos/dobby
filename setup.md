# Dobby Setup Guide

**Give this file to Claude** (drag it into Claude Desktop, or paste its contents) and say: _"Set up Dobby for me following this guide."_

---

## What Dobby does

Dobby is a Microsoft Teams meeting notetaker bot. It joins meetings as a participant, records audio, transcribes speech with Whisper, summarises the meeting with a local Ollama model, and saves structured notes (summary + decisions + action items) to your disk. It integrates with Claude Desktop as an MCP server, so you can ask Claude to join a meeting, check its status, or read past notes.

---

## Prerequisites

- macOS (Apple Silicon or Intel)
- Python 3.12+ installed
- Claude Desktop installed and running at least once

---

## Instructions for Claude

> Read this entire file, then execute every step in order without asking for confirmation. Only pause if a step fails. Before running any command that opens a browser window, always tell the user what is about to happen.

### Step 1 — Run the installer

```bash
curl -fsSL https://raw.githubusercontent.com/robert-bogos/dobby/main/install.sh -o /tmp/dobby-install.sh && bash /tmp/dobby-install.sh
```

Watch the output. Every line should show ✓. If the script exits asking the user to install Xcode Command Line Tools, tell the user to complete the dialog that appeared, then re-run the same command. If any other step fails, report the error to the user and stop.

### Step 2 — Create the bot account

First check if credentials are already configured:

```bash
python3 -c "
import json, pathlib
p = pathlib.Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
try:
    cfg = json.loads(p.read_text())
    env = cfg['mcpServers']['dobby']['env']
    if env.get('TEAMS_BOT_EMAIL') and env.get('TEAMS_BOT_PASSWORD'):
        print('SKIP')
    else:
        print('NEEDED')
except Exception:
    print('NEEDED')
"
```

If the output is `SKIP`, credentials already exist — tell the user Step 2 is already done and move on to the final message below.

If the output is `NEEDED`, tell the user:

_"I'll open an incognito Chrome window so you can create a free Outlook account for the bot. The script will suggest an email and password — you can use those or pick your own. Once the account is created, come back to this window and enter the credentials."_

```bash
cd ~/dobby && ~/dobby/.venv/bin/python setup_account.py
```

The script will print suggested credentials, open Chrome in incognito, then wait for the user to type the email and password they used. Once entered, it saves them automatically.

When it finishes, tell the user:

_"All done. Please quit Claude Desktop and reopen it — that's required to load the new bot config. After that, you can say 'join this Teams meeting for me: [link]' and I'll handle the rest."_

---

## You are ready

Once Claude Desktop restarts, try:

> "Join this Teams meeting for me: [paste a Teams meeting link]"
