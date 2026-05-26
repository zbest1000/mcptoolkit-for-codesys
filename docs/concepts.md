# How it works (in plain English)

This page explains the whole system from the top down, and the reasoning behind
each part. No code — just the ideas.

## What this project is

CODESYS is professional software for programming industrial controllers (PLCs).
This project lets an AI assistant — **Claude** — *operate* CODESYS for you: open
a project, write program blocks, compile, log into a real controller, read and
write live values, and so on. You talk to Claude in plain language; Claude uses
this server to do the actual clicking and typing inside CODESYS.

## What "MCP" means

**MCP (Model Context Protocol)** is a standard way for an AI assistant to use
external **tools**. A program that offers tools is called an *MCP server*. This
project is an MCP server whose tools all drive CODESYS (there are 82 of them —
"open project", "build", "start the PLC", etc.).

When you connect this server to Claude, Claude can see those 82 tools and call
them on your behalf. You never call them directly; you just ask Claude.

## The key idea: two programs, two Pythons

This is the one thing worth understanding, because everything else follows from
it. The system is **two separate programs**:

```
   You ── chat ──▶ Claude ── MCP ──▶  THE SERVER  ── messages ──▶  THE WATCHER
                                     (modern Python)              (inside CODESYS)
                                                                       │
                                                                       ▼
                                                                   CODESYS does
                                                                   the real work
```

1. **The server** — a normal, modern Python program. Its job is to talk to
   Claude (receive tool calls, send back results). It does **not** know how to
   operate CODESYS itself.
2. **The watcher** — a small script that runs *inside* CODESYS, using the old
   Python engine (IronPython 2.7) that CODESYS has built in. Its job is to
   actually perform operations in CODESYS.

**Why split it in two?** CODESYS can only be automated from inside its own
built-in Python, which is an old version (IronPython 2.7) that can't speak to
modern AI tooling. So we use a modern Python "server" to handle Claude, and a
small "watcher" inside CODESYS to do the work. They hand messages back and forth.

## How the two halves talk: a shared folder

The server and the watcher communicate through an ordinary folder on disk,
called the **workdir**. It has two mailboxes inside it:

- `commands/` — the server drops a request here ("open this project").
- `results/` — the watcher writes the answer back here.

The watcher constantly checks `commands/` for new files, does what each one says,
and writes the outcome to `results/`. The server then reads the result and hands
it back to Claude.

**Why a folder instead of a network connection?** Two reasons:

- The old Python inside CODESYS has fragile networking, and the CODESYS window
  can freeze (e.g. on a pop-up) — a network connection would break or hang. A
  shared folder keeps working through all of that.
- It's simple and robust. Each message is written to a temporary name first and
  then renamed, so the other side never reads a half-written file.

This folder is also the **trust boundary** — anyone who can write into it can
control CODESYS. That's why it must be a private, per-user folder. See
[security.md](security.md).

## Starting and stopping CODESYS

- **CODESYS starts on the first tool call, not when the server launches.**
  Opening CODESYS takes 30–90 seconds. If the server tried to do that at
  startup, Claude's connection would time out (it only waits ~60 seconds to
  connect). So the server connects instantly and only fires up CODESYS the first
  time Claude actually uses a tool. The first tool call is therefore slow; the
  rest are fast.
- **One CODESYS per workdir.** If a CODESYS+watcher is already running for that
  folder, the server attaches to it instead of opening a second one (this is
  called *adoption* — see [reliability.md](reliability.md)). Two IDEs sharing one
  mailbox would corrupt each other's messages, so this is strictly enforced.
- **Stopping** is done politely (a "STOP" signal the watcher notices) and, if
  that's ignored, by force.

## Where to go next

- See the live status of all this on the [watch page](dashboard.md).
- Understand the unattended-operation safeguards in [reliability.md](reliability.md).
- Change behavior with [configuration.md](configuration.md).
