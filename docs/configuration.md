# Configuration

Every setting, in plain language, and when you'd use it. Most people need none of
these — the defaults work. You set them either as **command-line arguments** in
your Claude Desktop config, or as **environment variables**.

## Command-line arguments

These go in the `args` list of your Claude Desktop config (see the
[main README](../README.md#configure-claude-desktop)).

| Argument | What it does | When you'd use it |
|---|---|---|
| `--workdir <folder>` | Sets the shared folder the server and CODESYS use to exchange messages (the "mailbox"). | To pin a specific location, or to make a remote setup match (see [../REMOTE.md](../REMOTE.md)). Default: a folder under your temp directory. |
| `--sp <number>` | Picks which CODESYS service pack to drive, e.g. `--sp 22`. | When you have more than one CODESYS version installed and want a specific one. |
| `--headless` | Starts CODESYS with no visible window. | When you don't need to see the IDE (e.g. a server with no screen). Otherwise CODESYS opens visibly. |
| `--log-level <level>` | How much detail the server logs: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. | `DEBUG` when troubleshooting; `INFO` is the default. |

## Environment variables

Set these in the `env` block of your Claude Desktop config, or in your shell.
Each `MCPTOOLKIT_*` variable mirrors an argument above; the argument wins if both
are set.

| Variable | What it does |
|---|---|
| `MCPTOOLKIT_WORKDIR` | Same as `--workdir`. The mailbox folder. |
| `MCPTOOLKIT_SP` | Same as `--sp`. Prefer this CODESYS service pack. |
| `MCPTOOLKIT_HEADLESS` | Set to `1` for the same effect as `--headless`. |
| `MCPTOOLKIT_LOG` | Default log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). |
| `MCPTOOLKIT_DEV` | Set to `1` to turn on developer-only diagnostics inside CODESYS. **Leave this off** in normal use — it enables running arbitrary code inside the IDE. See [security.md](security.md). |

## Telling the server where CODESYS is

Normally the server **finds CODESYS automatically** (it asks the CODESYS
installer which versions are present). You only need these if auto-detection
doesn't find your install, or you want to force a specific one:

| Variable | What it does |
|---|---|
| `CODESYS_EXE` | Full path to `CODESYS.exe`. Overrides auto-detection. |
| `CODESYS_PROFILE` | Required when `CODESYS_EXE` is set — the full path to the matching `.profile.xml`. |

> **Why a profile path is also required:** CODESYS is launched with a "profile"
> that tells it which version/configuration to run as. When you let the server
> auto-detect, it learns both the program path *and* its profile together. If you
> override the program path by hand, the server can no longer infer the matching
> profile (it lives in a different sub-folder and varies by version), so you must
> supply it too.

## A note for developers

When running the test suite, a few extra `MCPTOOLKIT_*` variables control the
**live** tests (which drive a real CODESYS): `MCPTOOLKIT_LIVE=1` turns them on,
and `MCPTOOLKIT_TEMPLATES` / `MCPTOOLKIT_STARTUP_TIMEOUT` tune them. These have
no effect on normal use. See the [main README](../README.md).
