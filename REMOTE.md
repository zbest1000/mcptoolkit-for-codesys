# Remote setup — Claude and CODESYS on different machines (SSH)

This covers the case where **Claude runs on one PC ("PC-A")** and **CODESYS plus
this server run on another PC ("PC-B")** on the same network. You drive CODESYS
on PC-B from Claude on PC-A over SSH — and, if you're sitting at PC-B, you can
**watch the IDE on its screen** while Claude operates it.

If everything runs on one machine, you don't need this — see [SETUP.md](SETUP.md).

## How it works

MCP's stdio transport means the client *launches the server as a subprocess* and
talks to it over that process's stdin/stdout. By setting the client's launch
command to `ssh`, that subprocess becomes an SSH session that runs the server on
PC-B and pipes the JSON-RPC stream back to PC-A:

```
  PC-A (Claude)                         PC-B (CODESYS machine)
  ┌─────────────┐   ssh (stdin/stdout)  ┌──────────────────────────────┐
  │ Claude      │──────────────────────▶│ launcher .cmd = MCP server    │
  │  client     │   MCP JSON-RPC        │        │ file IPC              │
  └─────────────┘                       │        ▼                      │
                                        │ CODESYS.exe + watcher.py      │
                                        └──────────────────────────────┘
```

Nothing about the server changes — it's the same stdio server, just reached
through SSH. SSH also supplies the authentication and encryption that the local
file-IPC channel deliberately does not have (see [Security](#security)).

## The one thing to know: seeing the IDE

On Windows, a process started by the SSH service runs in a **non-interactive
session**, so a CODESYS that the SSH-launched server *spawns* will **not** appear
on PC-B's physical screen.

So if you want to watch the IDE on PC-B while Claude drives it:

1. **Start CODESYS on PC-B yourself first**, in your normal logged-in desktop
   (use the `start-codesys-visible` helper below). It opens visibly.
2. When Claude connects over SSH, the server **adopts** that already-running
   instance (they share a workdir) instead of spawning a hidden one. You see
   every action happen live, and you're there to answer any IDE prompt.
3. Closing Claude leaves your IDE running — an adopted watcher is not stopped on
   disconnect (v0.2.1+). Close CODESYS yourself when you're done.

Don't need to see the IDE? Skip step 1 and add `--headless` to the launcher; the
server will run CODESYS UI-less inside the SSH session.

## Setup

### On PC-B (the CODESYS machine)

1. **Install the server** following [SETUP.md](SETUP.md). Confirm
   `mcptoolkit-for-codesys --help` works.
2. **Enable an SSH server.** On Windows, install OpenSSH Server (elevated
   PowerShell):
   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Set-Service sshd -StartupType Automatic
   Start-Service sshd
   ```
3. **Set up key authentication** (required — see [Authentication](#authentication)).
4. **Copy the launchers** from [`examples/remote-ssh/`](examples/remote-ssh/) to a
   folder on PC-B (e.g. `C:\mcp\`) and edit the paths at the top of each to match
   your install:
   - `codesys-mcp-stdio.cmd` — what SSH runs; starts the server over stdio.
   - `start-codesys-visible.cmd` (+ `.py`) — run this first if you want the IDE
     visible on PC-B's screen.

   Both the launcher and the visible-start helper must use the **same**
   `--workdir`, or the server won't find the instance to adopt.

### On PC-A (the Claude machine)

Add this to your client config (for Claude Desktop,
`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "codesys": {
      "command": "ssh",
      "args": ["<user>@<pc-b-host>", "C:\\mcp\\codesys-mcp-stdio.cmd"]
    }
  }
}
```

- `<pc-b-host>` is PC-B's hostname or IP (or its VPN address — see below).
- **Do not** add `-t`/`-tt`; a forced TTY corrupts the JSON-RPC stream.
- To run CODESYS UI-less, append `"--headless"` as a final entry in `args`
  (the launcher forwards extra arguments to the server).

## Daily use (IDE visible on PC-B)

1. **PC-B:** run `start-codesys-visible.cmd`. CODESYS opens on screen (~60–90 s).
2. **PC-A:** open Claude and use a CODESYS tool. The server adopts the visible
   instance; you watch it work on PC-B.
3. **Done:** quitting Claude leaves CODESYS running. Close it yourself, or use
   the `codesys.shutdown`-style stop, when finished.

## Authentication

Use an **SSH key**, not a password. The client launches `ssh` as a headless
subprocess with no terminal, so a password prompt can't be answered (and there's
no GUI pop-up). Set up key auth once and every connection is silent:

- Generate a key on PC-A: `ssh-keygen -t ed25519`.
- Install the **public** key on PC-B. For a Windows **admin** account this goes
  in `C:\ProgramData\ssh\administrators_authorized_keys` with restricted ACLs:
  ```powershell
  icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
  ```
  A non-admin account uses `C:\Users\<user>\.ssh\authorized_keys`.
- **Accept the host key once** from a normal terminal on PC-A so the first
  connection doesn't block the subprocess:
  ```
  ssh <user>@<pc-b-host> whoami
  ```
  It should print the username with no password prompt.

## Over a VPN

You can connect over a mesh VPN (e.g. Tailscale, WireGuard) by using PC-B's VPN
address as `<pc-b-host>`. The VPN keeps SSH off the public internet — recommended
for anything controlling a PLC. The SSH server itself is still OpenSSH; on a
**Windows** PC-B the built-in `tailscale ssh` server is not available, so run
OpenSSH and connect to the VPN IP.

## Security

- SSH (key auth) provides the authentication and encryption the file-IPC channel
  lacks. Keep the connection on a private network or VPN.
- This exposes **live PLC control** (`online.start/stop/reset/write/force`) to a
  remote machine. Treat access accordingly: behind a VPN/firewall, trusted users
  only. Physical-impact ops still require `confirm: true`.
- Leave the development gate off on PC-B: no `MCPTOOLKIT_DEV=1`, no `dev.flag` in
  the workdir (those enable arbitrary IronPython execution inside CODESYS).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| ssh asks for a password / hangs | key auth not set up | Redo [Authentication](#authentication); admin accounts need `administrators_authorized_keys` + the `icacls` ACL |
| First connection blocks | host key not yet accepted | Run `ssh <user>@<pc-b-host> whoami` once from a terminal |
| MCP connects but no IDE on PC-B's screen | server *spawned* CODESYS inside the SSH session (invisible) | Run `start-codesys-visible` on PC-B **before** connecting, so the server adopts it |
| Garbled / no JSON over SSH | shell noise on stdout, or a forced TTY | Keep the launcher's stdout clean (no echo/print); remove any `-t`/`-tt` from the config |
| `initialize` times out | watcher spawn >60 s, or the IDE is on a modal | First call is slow by design; the dialog guard auto-confirms safe prompts; check the visible IDE for an unexpected dialog |
