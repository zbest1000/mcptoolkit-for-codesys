@echo off
REM ---------------------------------------------------------------------------
REM Start CODESYS + watcher VISIBLY on THIS machine's desktop, so you can watch
REM the IDE while a remote Claude drives it over SSH (the SSH-launched server
REM adopts this instance instead of spawning an invisible one). See REMOTE.md.
REM
REM Run this FIRST, then connect from the remote Claude. Closing this window
REM leaves CODESYS running.
REM
REM EDIT the path below to your venv's python.exe.
REM ---------------------------------------------------------------------------
"C:\path\to\mcptoolkit-for-codesys\.venv\Scripts\python.exe" "%~dp0start-codesys-visible.py"
echo.
pause
