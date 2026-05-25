# Comprehensive end-to-end integration test of the CODESYS MCP server.
#
# Builds a real project from scratch that exercises:
#   - Project lifecycle (create_standard, set_info, save, tree)
#   - Folder hierarchy
#   - Data types (struct + enum)
#   - GVLs
#   - Function Blocks with methods and properties
#   - Programs with ST logic that references everything
#   - Library inventory (list_project, diagnose, find_on_disk)
#   - Device repository discovery (EtherNet/IP filter)
#   - Build pipeline (with structured compile error capture)
#   - Mirror export (git-friendly source dump)
#   - Health probe
#
# Requires a running watcher at the configured workdir (dev.flag for diagnostics).
# Run from a PowerShell that has CODESYS spawned via the standard pattern.

param(
    [string]$Workdir = "$env:TEMP\mcptoolkit-for-codesys",
    [string]$ProjectPath = "$env:TEMP\mcptoolkit-for-codesys-fulltest.project",
    [string]$MirrorDir = "$env:TEMP\mcptoolkit-for-codesys-fulltest-mirror",
    [string]$TemplatesDir = "C:\Program Files\CODESYS 3.5.22.10\CODESYS\Templates"
)

$ErrorActionPreference = "Stop"

function Invoke-Op([string]$op, [hashtable]$opArgs, [int]$timeout = 120) {
    $id = "int-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $payload = @{ id = $id; op = $op; args = $opArgs; deadline_s = ($timeout * 1.0) }
    $json = $payload | ConvertTo-Json -Depth 12 -Compress
    $cmdfile = "$Workdir\commands\$id.json"; $tmp = "$cmdfile.tmp"
    $json | Out-File -FilePath $tmp -Encoding utf8 -NoNewline
    Move-Item $tmp $cmdfile -Force
    $rf = "$Workdir\results\$id.json"; $deadline = (Get-Date).AddSeconds($timeout)
    while (-not (Test-Path $rf) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    if (Test-Path $rf) {
        $r = Get-Content $rf -Raw
        Remove-Item $rf
        return $r | ConvertFrom-Json
    }
    return @{ status = "TIMEOUT" }
}

function Step([string]$title, $r, $maxData = 400) {
    $status = $r.status
    $color = if ($status -eq "ok") { "Green" } elseif ($status -eq "error") { "Red" } else { "Yellow" }
    $line = "[{0,-4}]  {1,-40} {2}ms" -f $status.ToUpper(), $title, $r.elapsed_ms
    Write-Host $line -ForegroundColor $color
    if ($r.error) {
        $lastLine = ($r.error -split "`n" | Select-Object -Last 1)
        Write-Host ("        ERR: " + $lastLine.Substring(0, [Math]::Min(180, $lastLine.Length))) -ForegroundColor Red
    }
    if ($r.data -and $maxData -gt 0) {
        $j = $r.data | ConvertTo-Json -Compress -Depth 6
        $snippet = $j.Substring(0, [Math]::Min($maxData, $j.Length))
        Write-Host ("        " + $snippet) -ForegroundColor DarkGray
    }
}

# Clean slate
Remove-Item $ProjectPath -Force -ErrorAction SilentlyContinue
Remove-Item $MirrorDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  CODESYS MCP — full project integration test" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
Write-Host "`n[1] Project bootstrap" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "project.create_standard" (Invoke-Op "project.create_standard" @{
    path = $ProjectPath; overwrite = $true; templates_dir = $TemplatesDir
} 120)
Step "project.set_info" (Invoke-Op "project.set_info" @{
    title = "MCP Full Integration Test"
    version = "0.1.0"
    author = "mcptoolkit-for-codesys"
    company = "Integration"
    description = "Built end-to-end via MCP tools to exercise the full surface."
})
Step "health (post-create)" (Invoke-Op "health" @{}) 600

# ---------------------------------------------------------------------------
Write-Host "`n[2] Folder hierarchy" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "create_folder POUs" (Invoke-Op "pou.create_folder" @{ name = "POUs" })
Step "create_folder DataTypes" (Invoke-Op "pou.create_folder" @{ name = "DataTypes" })
Step "create_folder GVLs" (Invoke-Op "pou.create_folder" @{ name = "GVLs" })
Step "create_folder Devices" (Invoke-Op "pou.create_folder" @{ name = "Devices" })

# ---------------------------------------------------------------------------
Write-Host "`n[3] Data types (struct + enum)" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "create_dut E_PumpState (enum)" (Invoke-Op "pou.create_dut" @{
    name = "E_PumpState"; kind = "enumeration"; parent = "DataTypes"
    declaration = "TYPE E_PumpState :`n(`n    Idle := 0,`n    Starting := 1,`n    Running := 2,`n    Stopping := 3,`n    Faulted := 99`n);`nEND_TYPE"
})
Step "create_dut ST_SensorReading (struct)" (Invoke-Op "pou.create_dut" @{
    name = "ST_SensorReading"; kind = "structure"; parent = "DataTypes"
    declaration = "TYPE ST_SensorReading :`nSTRUCT`n    timestamp : DT;`n    valuePsi : REAL;`n    valid : BOOL;`nEND_STRUCT`nEND_TYPE"
})

# ---------------------------------------------------------------------------
Write-Host "`n[4] Global variable list" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "create_gvl GVL_Config" (Invoke-Op "pou.create_gvl" @{
    name = "GVL_Config"; parent = "GVLs"
    declaration = "VAR_GLOBAL`n    cMaxPumps : INT := 4;`n    cFaultPressurePsi : REAL := 250.0;`n    cScanCycleMs : TIME := T#100MS;`nEND_VAR"
})

# ---------------------------------------------------------------------------
Write-Host "`n[5] Function blocks with methods + properties" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "create FB_Pump" (Invoke-Op "pou.create" @{
    name = "FB_Pump"; pou_type = "function_block"; language = "st"; parent = "POUs"
    declaration = @"
FUNCTION_BLOCK FB_Pump
VAR_INPUT
    enable : BOOL;
    pressureSetpoint : REAL;
END_VAR
VAR_OUTPUT
    running : BOOL;
    fault : BOOL;
END_VAR
VAR
    state : E_PumpState := E_PumpState.Idle;
    lastReading : ST_SensorReading;
    cycleCount : DINT;
END_VAR
"@
    implementation = @"
CASE state OF
    E_PumpState.Idle:
        IF enable THEN
            state := E_PumpState.Starting;
        END_IF;
    E_PumpState.Starting:
        state := E_PumpState.Running;
    E_PumpState.Running:
        cycleCount := cycleCount + 1;
        running := TRUE;
        IF lastReading.valuePsi > GVL_Config.cFaultPressurePsi THEN
            state := E_PumpState.Faulted;
        END_IF;
        IF NOT enable THEN
            state := E_PumpState.Stopping;
        END_IF;
    E_PumpState.Stopping:
        running := FALSE;
        state := E_PumpState.Idle;
    E_PumpState.Faulted:
        fault := TRUE;
        running := FALSE;
END_CASE
"@
})
Step "create_method Reset on FB_Pump" (Invoke-Op "pou.create_method" @{
    name = "Reset"; parent = "FB_Pump"
    declaration = "METHOD Reset"
    implementation = "state := E_PumpState.Idle;`ncycleCount := 0;`nfault := FALSE;"
})
Step "create_method UpdateReading" (Invoke-Op "pou.create_method" @{
    name = "UpdateReading"; parent = "FB_Pump"
    declaration = "METHOD UpdateReading`nVAR_INPUT`n    reading : ST_SensorReading;`nEND_VAR"
    implementation = "lastReading := reading;"
})
Step "create_property CycleCount on FB_Pump" (Invoke-Op "pou.create_property" @{
    name = "CycleCount"; parent = "FB_Pump"; return_type = "DINT"
})
Step "set Get accessor body" (Invoke-Op "pou.set_text" @{
    target = "FB_Pump/CycleCount/Get"
    implementation = "CycleCount := cycleCount;"
})
Step "create FB_Conveyor" (Invoke-Op "pou.create" @{
    name = "FB_Conveyor"; pou_type = "function_block"; language = "st"; parent = "POUs"
    declaration = "FUNCTION_BLOCK FB_Conveyor`nVAR_INPUT`n    run : BOOL;`nEND_VAR`nVAR_OUTPUT`n    speed : REAL;`nEND_VAR`nVAR`n    rampStep : REAL := 0.1;`nEND_VAR"
    implementation = "IF run THEN`n    IF speed < 1.0 THEN speed := speed + rampStep; END_IF;`nELSE`n    IF speed > 0.0 THEN speed := speed - rampStep; END_IF;`nEND_IF"
})

# ---------------------------------------------------------------------------
Write-Host "`n[6] Wire it all up in PLC_PRG" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "set_text PLC_PRG (orchestrator)" (Invoke-Op "pou.set_text" @{
    target = "PLC_PRG"
    declaration = "PROGRAM PLC_PRG`nVAR`n    pumps : ARRAY[1..4] OF FB_Pump;`n    conveyor : FB_Conveyor;`n    runRequest : BOOL;`n    totalCycles : DINT;`n    i : INT;`nEND_VAR"
    implementation = @"
conveyor(run := runRequest);
totalCycles := 0;
FOR i := 1 TO GVL_Config.cMaxPumps DO
    pumps[i](enable := runRequest, pressureSetpoint := 100.0);
    totalCycles := totalCycles + pumps[i].CycleCount;
END_FOR
"@
})

# ---------------------------------------------------------------------------
Write-Host "`n[7] Inventory + diagnostics" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "project.tree (depth=4)" (Invoke-Op "project.tree" @{ max_depth = 4 }) 0
Step "project.info" (Invoke-Op "project.info" @{}) 500
Step "library.list_project" (Invoke-Op "library.list_project" @{}) 400
Step "library.diagnose" (Invoke-Op "library.diagnose" @{}) 400

# Device repo: how many EtherNet/IP devices do we have?
Step "device.list_installed (EtherNet/IP)" (Invoke-Op "device.list_installed" @{
    name = "ethernet/ip"; limit = 6; resolve_categories = $true
}) 600

# ---------------------------------------------------------------------------
Write-Host "`n[8] Compile" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
$buildResult = Invoke-Op "build.build" @{} 180
Step "build.build" $buildResult 200
if ($buildResult.data) {
    Write-Host ("        => errors={0}, warnings={1}, messages={2}" -f `
        $buildResult.data.errors, $buildResult.data.warnings, $buildResult.data.messages.Count) -ForegroundColor Cyan
    # Print first PLC_PRG-related error if any
    foreach ($m in $buildResult.data.messages) {
        if ($m.severity -eq "error" -and $m.source -and ($m.source -eq "PLC_PRG" -or $m.source -like "FB_*")) {
            Write-Host ("        ! " + $m.severity_raw + " in " + $m.source + " @ " + $m.position + ": " + $m.text) -ForegroundColor Red
            break
        }
    }
}

# ---------------------------------------------------------------------------
Write-Host "`n[9] Mirror export (git-friendly source dump)" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
$mirror = Invoke-Op "project.mirror_export" @{ out_dir = $MirrorDir; clean = $true } 120
Step "project.mirror_export" $mirror 400
if (Test-Path $MirrorDir) {
    $files = Get-ChildItem $MirrorDir -Recurse -File
    Write-Host ("        => " + $files.Count + " files, " + ($files | Measure-Object -Property Length -Sum).Sum + " bytes total") -ForegroundColor Cyan
    foreach ($f in $files | Sort-Object FullName) {
        $rel = $f.FullName.Substring($MirrorDir.Length + 1)
        Write-Host ("          " + ("{0,-50} {1} bytes" -f $rel, $f.Length)) -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
Write-Host "`n[10] Save + close cycle" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "project.save" (Invoke-Op "project.save" @{}) 200
Step "project.close" (Invoke-Op "project.close" @{}) 200
Step "project.open (re-test kwarg fix)" (Invoke-Op "project.open" @{ path = $ProjectPath } 60) 600

# ---------------------------------------------------------------------------
Write-Host "`n[11] Final health + cleanup" -ForegroundColor Yellow
# ---------------------------------------------------------------------------
Step "health (final)" (Invoke-Op "health" @{}) 600

Write-Host "`n================================================================" -ForegroundColor Cyan
Write-Host "  Integration test complete." -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
