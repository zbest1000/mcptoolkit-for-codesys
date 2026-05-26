# Safety and security

This server can command an industrial controller — start and stop a PLC, change
live values, move real equipment. So it's built with guard rails, and there are a
few things **you** need to keep right. This page explains them plainly.

## The most important rule: protect the workdir

The server and CODESYS talk through a shared folder (the **workdir** — see
[concepts.md](concepts.md)). That channel has **no password by design**:
anything that can write a file into `workdir/commands/` can make CODESYS do
anything the tools allow.

What protects it is ordinary **folder permissions**. So:

- Keep the workdir on a **private, per-user folder** on a local drive.
- **Never** put it on a shared drive, a network folder, or any location other
  people (or other machines) can write to.

The default location (a per-user temp folder) is already private. If you change
it, keep that property.

## The developer gate (`_eval` / `_introspect`) — off by default

There are two hidden developer operations that can run **arbitrary code inside
CODESYS**. They exist for debugging the integration, and they are **off unless
you deliberately turn them on** (by setting `MCPTOOLKIT_DEV=1` or placing a file
named `dev.flag` in the workdir).

- They are **not** exposed as normal tools — Claude can't call them.
- Leave them **off** in any shared or production setting. Don't ship a `dev.flag`.

*Why they exist at all:* when adapting to a new CODESYS version, a developer
sometimes needs to poke at the live scripting API. That's the only use.

## Confirmation for anything that moves equipment

Four operations can have a physical effect on a running machine —
**start**, **reset**, **write** (a live value), and **force** (override a value).
These refuse to run unless the call explicitly includes `confirm: true`.

*Why:* it stops an AI from starting a motor or changing an output "helpfully" by
accident. A human-meant action has to be unmistakably intended.

## Credentials are yours, and kept out of the chat log

Logging into a controller needs a username and password. The server **never
invents or stores** them — you supply them. Better still, you can pass the
*names* of environment variables that hold them (`username_env` / `password_env`)
instead of the secrets themselves, so the actual password never appears in the
conversation transcript.

(The password does pass through a command file in the workdir for a moment before
use, then is removed — another reason the workdir must be private.)

## The dialog guard won't make risky choices

The automatic pop-up handler (see [reliability.md](reliability.md)) only clicks
safe, routine buttons (Yes/OK on things like "save?" or "upgrade format?"). It
refuses to touch any dialog whose wording looks destructive (delete, overwrite,
erase, reset…), leaving those for a human to decide.

## The watch page is look-only and local

The [dashboard](dashboard.md) shows status but sends no commands, and it listens
only on your own machine (localhost) by default. It will warn you loudly if you
try to expose it on the network, because it reveals internal detail (logs, paths,
dialog text) without any password.

## Running over SSH (remote)

If you drive CODESYS on another machine, do it over **SSH** (see
[../REMOTE.md](../REMOTE.md)). SSH provides the encryption and login the local
file channel deliberately lacks, and keeps PLC control off the open network. Use
key-based login and keep it on a private network or VPN.

## Under the hood (for the curious)

- The server launches programs (CODESYS, the installer) using explicit argument
  lists, never by handing a string to a shell — so input can't sneak in extra
  commands.
- File names for results are validated, so a crafted request can't write outside
  the results folder.
- Project paths and similar inputs are checked (no `..`, no null bytes, correct
  extension) before they reach CODESYS.
