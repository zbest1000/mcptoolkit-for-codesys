# Setup Guide

A step-by-step walkthrough to get the CODESYS MCP server running with Claude
Desktop. No prior programming experience needed — just follow each step in
order. It takes about 15 minutes.

When you're done, you'll be able to ask Claude things like *"open my CODESYS
project and build it"* and watch it happen in CODESYS.

---

## Before you start

You need three things installed on the **same Windows PC**:

1. **CODESYS V3.5 SP22** — the PLC programming software this server controls.
   Install it from the [CODESYS Installer](https://store.codesys.com/). (Other
   versions from SP19 up may work, but SP22 is the tested one.)
2. **Python 3.11 or newer** — the language the server is written in.
   Download it from [python.org/downloads](https://www.python.org/downloads/).
   **Important:** on the first installer screen, tick the box that says
   *"Add Python to PATH"* before clicking Install.
3. **Claude Desktop** — from [claude.ai/download](https://claude.ai/download).

> **What's a "terminal"?** A few steps below use one. To open it: press the
> Windows key, type **PowerShell**, and press Enter. A blue/black window opens
> where you can paste commands. To paste, right-click in the window.

---

## Step 1 — Download the server

If you have **Git** installed, open PowerShell and run (replace the URL with
your repository's address):

```
cd $env:USERPROFILE
git clone https://github.com/YOUR-USERNAME/mcptoolkit-for-codesys.git
```

No Git? On the GitHub page click the green **Code** button → **Download ZIP**,
then right-click the downloaded file → **Extract All**. Remember where you put
it (e.g. `C:\Users\YourName\mcptoolkit-for-codesys`).

---

## Step 2 — Install it

In PowerShell, go into the folder you just downloaded and run these three lines
one at a time (replace the path with your actual folder):

```
cd C:\Users\YourName\mcptoolkit-for-codesys
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

The last line downloads what the server needs and sets it up. When it finishes
without red error text, you're installed.

**Quick check** — run this; it should print a help screen, not an error:

```
.venv\Scripts\mcptoolkit-for-codesys.exe --help
```

Note the **full path** to that `mcptoolkit-for-codesys.exe` file — you'll paste
it into the next step. It's your folder + `\.venv\Scripts\mcptoolkit-for-codesys.exe`.

---

## Step 3 — Connect it to Claude Desktop

Claude Desktop reads a settings file. You'll add this server to it.

1. Open the file location: press Windows key, type **Run**, press Enter, then
   type this and click OK:
   ```
   %APPDATA%\Claude
   ```
2. Open **claude_desktop_config.json** in Notepad (right-click → Open with →
   Notepad). If the file doesn't exist, create an empty text file with that
   exact name.
3. Paste in the following. **Change the `command` path** to your exe from Step 2
   (keep the double backslashes `\\`):

   ```json
   {
     "mcpServers": {
       "codesys": {
         "command": "C:\\Users\\YourName\\mcptoolkit-for-codesys\\.venv\\Scripts\\mcptoolkit-for-codesys.exe",
         "args": ["--sp", "22"]
       }
     }
   }
   ```

   If the file already had content, just add the `"codesys": { ... }` block
   inside the existing `"mcpServers"` section.
4. Save the file and close Notepad.

---

## Step 4 — Restart and test

1. Fully quit Claude Desktop (right-click its icon in the system tray near the
   clock → Quit), then open it again.
2. In a new chat, ask: **"Use the codesys ping tool."**
3. The **first** request opens CODESYS and can take a minute or two — that's
   normal, it only happens once per session. After that it's quick.

If Claude replies with a successful "pong" and a CODESYS version, everything
works. You can now ask it to open projects, create program blocks, build, and
(carefully) control a live PLC.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| `py` or `python` "not recognized" | Python isn't on PATH. Re-run the Python installer, choose **Modify**, and enable *"Add Python to PATH"*. |
| Claude doesn't show a codesys tool | Re-check the `command` path in the config (real exe path, double backslashes), then fully quit and reopen Claude Desktop. |
| First request seems stuck | CODESYS is starting — give it up to two minutes. If a pop-up window appeared in CODESYS, the server usually answers it automatically; if not, click it. |
| "No CODESYS installations found" | CODESYS SP22 isn't installed where the server can find it. Install it via the CODESYS Installer, or see `CODESYS_EXE`/`CODESYS_PROFILE` in the [README](README.md#environment-variables). |

For the full list of tools, settings, and the security model, see the
[README](README.md).

---

## Updating later

To get the newest version:

```
cd C:\Users\YourName\mcptoolkit-for-codesys
git pull
.venv\Scripts\python.exe -m pip install -e .
```

(If you downloaded the ZIP instead of using Git, download the new ZIP and repeat
Step 2.)
