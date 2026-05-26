@echo off
REM ---------------------------------------------------------------------------
REM Start the CODESYS MCP server over stdio, for a Claude client on ANOTHER PC
REM that reaches this machine via SSH. See REMOTE.md.
REM
REM On the remote (Claude) PC, point the client config at this file:
REM   "command": "ssh",
REM   "args": ["<user>@<this-host>", "C:\\mcp\\codesys-mcp-stdio.cmd"]
REM
REM EDIT the path below to your installed server exe (from your venv's Scripts).
REM The --workdir here must MATCH start-codesys-visible.py so the server can
REM adopt a visible IDE instead of spawning a hidden one.
REM
REM stdout carries ONLY the MCP JSON-RPC stream. Do NOT add echo/print to stdout
REM in this file or it will corrupt the protocol. Append --headless to run
REM CODESYS UI-less.
REM ---------------------------------------------------------------------------
"C:\path\to\mcptoolkit-for-codesys\.venv\Scripts\mcptoolkit-for-codesys.exe" --sp 22 --workdir "%LOCALAPPDATA%\mcptoolkit-for-codesys" %*
