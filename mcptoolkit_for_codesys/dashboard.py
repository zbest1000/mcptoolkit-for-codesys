"""Read-only observability dashboard for the CODESYS MCP watcher.

The file-IPC + spawned-IDE design is opaque: when something hangs, you can't
see why without digging through the workdir. This serves a tiny localhost web
page that tails that workdir — watcher liveness, heartbeat (idle/busy + which
op), command/result queue depth, recent log lines, and any modal dialog
blocking the IDE.

It is strictly READ-ONLY: it issues no commands and never touches CODESYS. It
exists to *explain* state, not change it. Run it alongside the MCP server:

    python -m mcptoolkit_for_codesys.dashboard --workdir <dir> [--port 8765]

Stdlib only — no extra dependencies, so it never weighs down the server.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .watcher_manager import (
    HEARTBEAT_BUSY_GRACE_S,
    HEARTBEAT_IDLE_STALE_S,
    _pid_alive,
    describe_dialogs,
)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _classify_liveness(pid: int | None, hb: dict | None) -> str:
    """Same rules as WatcherProcess.liveness, but from raw files (the dashboard
    has no WatcherProcess handle)."""
    if not pid or not _pid_alive(int(pid)):
        return "dead"
    if hb is None:
        return "healthy"  # startup window
    now = time.time()
    ts = float(hb.get("ts") or 0)
    if (hb.get("state") or "idle") == "busy":
        started = float(hb.get("op_started_ts") or ts)
        deadline = float(hb.get("deadline_s") or 120.0)
        return "hung" if (now - started) > (deadline + HEARTBEAT_BUSY_GRACE_S) else "healthy"
    return "hung" if (now - ts) > HEARTBEAT_IDLE_STALE_S else "healthy"


def _tail_log(path: Path, n: int = 40) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-n:]:
        rec = None
        try:
            rec = json.loads(ln)
        except ValueError:
            rec = {"msg": ln, "level": "raw"}
        out.append(rec)
    return out


def build_status(workdir: Path) -> dict:
    """Gather every read-only signal about the watcher into one snapshot."""
    now = time.time()
    ready = _read_json(workdir / "watcher.ready")
    hb = _read_json(workdir / "watcher.heartbeat")
    pid = (ready or {}).get("pid")

    def _age(rec):
        ts = (rec or {}).get("ts")
        return round(now - ts, 1) if ts else None

    try:
        cmd_depth = len(list((workdir / "commands").glob("*.json")))
    except OSError:
        cmd_depth = -1
    try:
        res_depth = len(list((workdir / "results").glob("*.json")))
    except OSError:
        res_depth = -1

    liveness = _classify_liveness(pid, hb)
    dialogs = describe_dialogs(int(pid)) if pid and _pid_alive(int(pid)) else {
        "dialog_count": 0, "dialogs": [], "all_windows": [],
    }

    return {
        "now": now,
        "workdir": str(workdir),
        "liveness": liveness,
        "pid": pid,
        "pid_alive": bool(pid and _pid_alive(int(pid))),
        "ops_count": len((ready or {}).get("ops") or []),
        "ready_age_s": _age(ready),
        "heartbeat": hb,
        "heartbeat_age_s": _age(hb),
        "command_queue_depth": cmd_depth,
        "pending_results": res_depth,
        "dialogs": dialogs,
        "log_tail": _tail_log(workdir / "log" / "watcher.log"),
    }


_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>CODESYS MCP — watcher</title>
<style>
 body{font:13px/1.5 ui-monospace,Consolas,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:16px}
 h1{font-size:15px;margin:0 0 12px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:14px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px}
 .k{color:#8b949e;font-size:11px;text-transform:uppercase}
 .v{font-size:18px;margin-top:2px}
 .pill{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600}
 .healthy{background:#238636;color:#fff}.hung{background:#9e6a03;color:#fff}
 .dead{background:#da3633;color:#fff}.none{background:#30363d;color:#c9d1d9}
 .dlg{background:#9e6a03;color:#fff;padding:8px;border-radius:6px;margin:8px 0}
 pre{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px;max-height:42vh;overflow:auto;white-space:pre-wrap}
 .err{color:#ff7b72}.warn{color:#d29922}.info{color:#8b949e}
</style></head><body>
<h1>CODESYS MCP watcher <span id=ls></span></h1>
<div class=grid id=cards></div>
<div id=dlg></div>
<div class=k>log tail</div>
<pre id=log></pre>
<script>
async function tick(){
 let s; try{ s=await (await fetch('/api/status')).json() }catch(e){ document.getElementById('ls').textContent='(dashboard offline)'; return }
 document.getElementById('ls').innerHTML='<span class="pill '+s.liveness+'">'+s.liveness+'</span>';
 const hb=s.heartbeat||{};
 const cards=[
  ['pid', s.pid||'—'],
  ['state', hb.state||'—'],
  ['busy op', hb.op||'—'],
  ['heartbeat age', s.heartbeat_age_s!=null? s.heartbeat_age_s+'s':'—'],
  ['ready age', s.ready_age_s!=null? s.ready_age_s+'s':'—'],
  ['ops', s.ops_count],
  ['cmd queue', s.command_queue_depth],
  ['pending results', s.pending_results],
 ];
 document.getElementById('cards').innerHTML=cards.map(c=>'<div class=card><div class=k>'+c[0]+'</div><div class=v>'+c[1]+'</div></div>').join('');
 let d=s.dialogs||{}; let dh='';
 if(d.dialog_count>0){ dh=d.dialogs.map(x=>'<div class=dlg>⚠ modal: <b>'+(x.title||'')+'</b> — '+(x.text||'')+' &nbsp;['+(x.buttons||[]).join(', ')+']</div>').join(''); }
 document.getElementById('dlg').innerHTML=dh;
 document.getElementById('log').innerHTML=(s.log_tail||[]).map(r=>{
   let cls=r.level==='error'?'err':r.level==='warn'?'warn':'info';
   let t=r.ts? new Date(r.ts*1000).toLocaleTimeString():'';
   return '<span class="'+cls+'">'+t+'  '+(r.msg||'')+'</span>';
 }).join('\\n');
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    workdir: Path = Path(".")

    def log_message(self, *_a):  # silence per-request logging
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/api/status"):
            body = json.dumps(build_status(self.workdir), default=str).encode("utf-8")
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def _default_workdir() -> Path:
    env = os.environ.get("MCPTOOLKIT_WORKDIR")
    if env:
        return Path(env)
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    return Path(tmp) / "mcptoolkit-for-codesys"


def main() -> None:
    p = argparse.ArgumentParser(prog="mcptoolkit-for-codesys-dashboard")
    p.add_argument("--workdir", type=Path, default=_default_workdir())
    p.add_argument("--host", default="127.0.0.1", help="bind host (default localhost).")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    _Handler.workdir = Path(args.workdir).resolve()
    # The dashboard exposes watcher internals: log contents (may include
    # project paths + error detail), dialog text, window titles, queue state.
    # That's fine on localhost; binding to a routable interface publishes it
    # unauthenticated. Warn loudly so it's never an accident.
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: binding to {args.host} exposes watcher internals "
            "(logs, paths, dialog text) with NO authentication. Use 127.0.0.1 "
            "unless you have a deliberate, firewalled reason."
        )
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(
        f"CODESYS MCP dashboard on http://{args.host}:{args.port}  "
        f"(workdir: {_Handler.workdir})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard stopped.")


if __name__ == "__main__":
    main()
