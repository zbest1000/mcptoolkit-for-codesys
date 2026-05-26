# Keeping it running unattended

## The problem

CODESYS is a graphical program, driven on its main window thread. Two things can
freeze it:

1. A **pop-up dialog** (e.g. "Upgrade the project's storage format?") — CODESYS
   waits for someone to click, but with an AI driving it, nobody is there.
2. A **wedged operation** — something that should take seconds but hangs.

When Claude is operating CODESYS, often without a person watching, the system has
to detect and handle these by itself. Here's how, and why each piece exists.

## Heartbeat and liveness

The watcher writes a tiny "I'm alive" record (a **heartbeat**) to the workdir
every couple of seconds while idle, and at the start/end of each operation while
busy. The server reads it and classifies the watcher as:

- **healthy** — alive and responding.
- **hung** — alive but stuck: silent for more than ~30 seconds while idle, or a
  single operation has run more than ~30 seconds past its own deadline.
- **dead** — the process is gone.

*Why:* this is the difference between "patiently waiting on a long build" and
"frozen and never coming back." Without it, the server couldn't tell them apart.

## Automatic recovery

If the watcher is found **hung**, the server kills it and starts a fresh one on
the next tool call. *Why:* the system heals itself instead of needing you to
restart anything.

## IDE adoption (no duplicate IDEs)

When the server needs CODESYS, it first checks whether one is **already running**
for that workdir. If so, it **attaches to the existing one** instead of opening a
second.

*Why this matters:*

- Two CODESYS instances sharing one mailbox folder would read each other's
  messages and corrupt results. Only ever one per workdir is allowed.
- It makes restarts cheap — a restarted server adopts the running IDE instead of
  paying the 60–90 second startup again.
- It enables the remote "watch the IDE on screen" trick: you start CODESYS
  visibly yourself, and the server adopts it (see [../REMOTE.md](../REMOTE.md)).

An **adopted** IDE (one the server didn't start itself) is **left running** when
the server disconnects — the server only shuts down IDEs it launched. So a remote
client closing never closes an IDE you opened.

## The dialog guard

A background task on the server side watches for pop-up windows on CODESYS and
**auto-clicks the safe ones**:

- It clicks **Yes / OK / Continue / Save** on the routine prompts that scripted
  operations legitimately trigger (like the storage-format upgrade).
- It **never** clicks No/Cancel, and it refuses to touch any dialog whose text
  looks destructive (contains words like *delete, overwrite, erase, reset*).
- A dialog it doesn't recognize is **left alone** and surfaced to you instead
  (you'll see it on the [watch page](dashboard.md)).

*Why:* without this, a single "click Yes to continue" pop-up would freeze a
scripted job forever. The guard handles the safe, expected ones automatically
while deliberately not making risky decisions on your behalf.

## The hang-proof health checks

Two tools answer **instantly even when everything else is stuck**, because they
read only the server-side signals (process ID, heartbeat, the IDE's visible
windows) and never wait on the watcher:

- **`codesys.diagnose`** — the first thing to call when something looks frozen.
  It reports liveness and, crucially, **names the pop-up dialog** that's blocking
  the IDE, if any.
- **`codesys.health`** — a fuller snapshot of both the server and watcher sides.

*Why:* if a health check had to go *through* the stuck watcher to report status,
it would hang too. These deliberately don't, so you can always find out what's
wrong.
