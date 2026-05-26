# The watch page (dashboard)

## What it is

A small **web page that shows the live status** of the running CODESYS watcher.
You open it in a browser and it refreshes itself every 2 seconds. It is
**read-only**: it only *shows* you what's happening — it never sends commands or
changes anything in CODESYS.

## Why it exists

The design from [concepts.md](concepts.md) — two programs passing messages
through a folder, with CODESYS running in the background — is **opaque**. When
something seems stuck, you can't tell why just by looking at Claude. Is CODESYS
still alive? Is it busy on a long build, or truly frozen? Did a pop-up window
appear that's blocking everything?

The watch page answers all of that at a glance, so you don't have to dig through
files in the workdir to understand what's going on.

## How to run it

Run it alongside the server, pointing at the **same workdir**:

```
mcptoolkit-for-codesys-dashboard --workdir <same dir the server uses>
```

Then open **http://127.0.0.1:8765** in any browser. (Change the port with
`--port`.) It uses only Python's built-in libraries — no extra installs — so it
never slows the server down.

## What everything on the page means

### The status pill (top)
A colored badge summarizing the watcher's health:

| Pill | Meaning |
|---|---|
| **healthy** (green) | CODESYS is alive and responding normally. |
| **hung** (amber) | CODESYS is alive but stuck — silent too long while idle, or a single operation has run well past its deadline. Usually a pop-up dialog is blocking it. |
| **dead** (red) | The CODESYS process is gone. |
| **none** (grey) | No watcher has started yet. |

### The cards (the row of boxes)

| Card | What it tells you | A "bad" value means… |
|---|---|---|
| **pid** | The CODESYS process ID. | "—" means CODESYS isn't running. |
| **state** | `idle` (waiting for work) or `busy` (running an operation). | — |
| **busy op** | Which operation is currently running (e.g. `build.build`). | If it sits on the same op for a long time, that op may be stuck. |
| **heartbeat age** | Seconds since the watcher last "checked in". | A large and growing number means it's frozen. |
| **ready age** | Seconds since CODESYS finished starting up. | — |
| **ops** | How many operations the watcher knows how to do. | `0` means the watcher didn't load properly. |
| **cmd queue** | Requests waiting to be processed. | Growing and not draining → the watcher is stuck. |
| **pending results** | Finished answers not yet collected. | — |

### The dialog banner
If CODESYS has popped up a modal window (which would block everything until
someone responds), an amber banner shows its **title, message, and buttons** —
i.e. exactly what CODESYS is asking. This is usually the reason for a "hung"
status. (Routine, safe pop-ups are auto-answered by the dialog guard — see
[reliability.md](reliability.md) — so anything you see here is something it
didn't recognize as safe.)

### The log tail
The last 40 lines of the watcher's log, colored by severity (errors in red,
warnings in amber). Good for seeing what just happened.

## A note on safety

The page is bound to **localhost (127.0.0.1)** by default, meaning only your own
machine can open it. That's deliberate: the page reveals internal detail (log
contents, project paths, dialog text). If you bind it to a network address with
`--host`, the program prints a loud warning — doing so publishes that
information with no password. Don't, unless you've put a firewall in front of it.
