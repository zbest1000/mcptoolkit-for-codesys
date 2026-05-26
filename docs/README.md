# Documentation

Plain-English explanations of what each part of this project is and **why** it
exists. Start here if you want to understand how the system works, not just how
to run it.

| Doc | What it covers |
|---|---|
| [concepts.md](concepts.md) | The big picture — how Claude, the server, and CODESYS fit together, and why it's built as two programs talking through a folder. **Read this first.** |
| [dashboard.md](dashboard.md) | The HTML "watch page" — a live status screen for the running IDE, what every number on it means, and why it exists. |
| [reliability.md](reliability.md) | How the server keeps a GUI program running unattended — heartbeats, auto-recovery, IDE adoption, and the pop-up dialog guard. |
| [configuration.md](configuration.md) | Every setting (command-line flags and environment variables) explained plainly, with when you'd use each. |
| [security.md](security.md) | The safety model — what can go wrong, the trust boundary, and the guard rails around controlling a real PLC. |

### Related guides (in the project root)

- [../SETUP.md](../SETUP.md) — step-by-step install for non-technical users.
- [../REMOTE.md](../REMOTE.md) — running Claude and CODESYS on **different** machines over SSH (the "runbook").
- [../README.md](../README.md) — the full tool-by-tool reference.
- [../CHANGES.md](../CHANGES.md) — version history.
