# examples/remote-ssh

Templates for running the server on one PC and Claude on another, over SSH.
Full walkthrough: [`../../REMOTE.md`](../../REMOTE.md).

| File | Where it runs | What it does |
|---|---|---|
| `codesys-mcp-stdio.cmd` | CODESYS PC | What the remote `ssh` command runs; starts the MCP server over stdio. |
| `start-codesys-visible.py` / `.cmd` | CODESYS PC | Run first if you want the IDE visible on that PC's screen; the SSH-launched server adopts it. |

Copy these to the CODESYS PC (e.g. `C:\mcp\`) and edit the paths at the top of
each `.cmd` (and the workdir, which must match between the two). Then add the SSH
config block from `REMOTE.md` to the Claude PC.
