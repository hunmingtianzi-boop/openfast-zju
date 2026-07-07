param(
    [string]$RepoUrl = "https://github.com/hunmingtianzi-boop/openfast-zju.git",
    [string]$CloneDir = "D:\OpenFast\openfast-zju-backup",
    [string]$SourceRoot = "",
    [switch]$Push
)

$ErrorActionPreference = "Stop"

if (-not $SourceRoot) {
    $SourceRoot = Split-Path -Parent $PSScriptRoot
}
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path

function Run-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Ensure-Clone {
    $cloneParent = Split-Path -Parent $CloneDir
    New-Item -ItemType Directory -Path $cloneParent -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $CloneDir ".git"))) {
        Run-Git -GitArgs @("clone", $RepoUrl, $CloneDir)
    }
    Run-Git -GitArgs @("-C", $CloneDir, "remote", "set-url", "origin", $RepoUrl)
    Run-Git -GitArgs @("-C", $CloneDir, "config", "core.longpaths", "true")
    $userName = (& git -C $CloneDir config --get user.name) 2>$null
    if (-not $userName) {
        Run-Git -GitArgs @("-C", $CloneDir, "config", "user.name", "Codex Backup Bot")
    }
    $userEmail = (& git -C $CloneDir config --get user.email) 2>$null
    if (-not $userEmail) {
        Run-Git -GitArgs @("-C", $CloneDir, "config", "user.email", "codex-backup@local")
    }
    Run-Git -GitArgs @("-C", $CloneDir, "checkout", "-B", "main")
}

function Copy-Rel {
    param(
        [string]$Rel,
        [string]$DestRoot,
        [switch]$Optional
    )
    $from = Join-Path $SourceRoot $Rel
    if (-not (Test-Path -LiteralPath $from)) {
        if ($Optional) {
            return $false
        }
        throw "Missing backup source: $Rel"
    }
    $to = Join-Path $DestRoot $Rel
    $parent = Split-Path -Parent $to
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
    return $true
}

Ensure-Clone

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$backupRoot = Join-Path $CloneDir "active_physics_workflow"
$snapshotRoot = Join-Path $backupRoot "snapshots"
$snapshotDir = Join-Path $snapshotRoot $stamp
$packageDir = Join-Path $backupRoot "packages"
New-Item -ItemType Directory -Path $snapshotDir, $packageDir -Force | Out-Null

$packageZip = Get-ChildItem -LiteralPath (Join-Path $SourceRoot "99_loop_packages") -Filter "global_6dof_loop_workflow_latest_*.zip" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $packageZip) {
    $packageZip = Get-ChildItem -LiteralPath (Join-Path $SourceRoot "99_loop_packages") -Filter "global_6dof_loop_workflow_*.zip" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}
if (-not $packageZip) {
    throw "No workflow package zip found under 99_loop_packages."
}
$packageDest = Join-Path $packageDir $packageZip.Name
Copy-Item -LiteralPath $packageZip.FullName -Destination $packageDest -Force

$relPaths = @(
    "config.yaml",
    "03_scripts\20_global_calibration_loop.py",
    "03_scripts\25_plot_composite_sixdof.py",
    "03_scripts\90_backup_to_openfast_zju.ps1",
    "03_scripts\global_loop",
    "05_registry",
    "10_global_memory\global_state.json",
    "10_global_memory\diagnostic_queue.json",
    "10_global_memory\epochs\oracle_da77e9aa1d9c593c\global_state.json",
    "10_global_memory\epochs\oracle_da77e9aa1d9c593c\coupling_memory.json",
    "10_global_memory\epochs\oracle_da77e9aa1d9c593c\diagnostic_queue.json",
    "10_global_memory\epochs\oracle_da77e9aa1d9c593c\proposal_pool.json",
    "10_global_memory\epochs\oracle_da77e9aa1d9c593c\reports"
)

$copied = @()
foreach ($rel in $relPaths) {
    if (Copy-Rel -Rel $rel -DestRoot $snapshotDir -Optional) {
        $copied += $rel
    }
}

$epochRoot = Join-Path $SourceRoot "10_global_memory\epochs\oracle_da77e9aa1d9c593c"
$epochStatePath = Join-Path $epochRoot "global_state.json"
if (Test-Path -LiteralPath $epochStatePath) {
    $epochState = Get-Content -LiteralPath $epochStatePath -Raw | ConvertFrom-Json
    $shortRunCardDir = Join-Path $snapshotDir "runcards"
    New-Item -ItemType Directory -Path $shortRunCardDir -Force | Out-Null
    $runCardCopies = @(
        @{ id = [string]$epochState.current_best_run_id; name = "current_best_runcard.json" },
        @{ id = [string]$epochState.last_run_id; name = "latest_step_runcard.json" }
    )
    foreach ($copy in $runCardCopies) {
        if (-not $copy.id) {
            continue
        }
        $sourceCard = Join-Path $epochRoot ("runcards\" + $copy.id + ".json")
        if (Test-Path -LiteralPath $sourceCard) {
            Copy-Item -LiteralPath $sourceCard -Destination (Join-Path $shortRunCardDir $copy.name) -Force
            $copied += "runcards\$($copy.name)"
        }
    }
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    repo_url = $RepoUrl
    source_root = $SourceRoot
    snapshot = $stamp
    package_zip = "active_physics_workflow/packages/$($packageZip.Name)"
    copied_paths = $copied
    excludes = @(
        "04_current_runs/runs full OpenFAST outputs",
        ".codegraph cache",
        "API keys",
        "legacy work-zx files"
    )
}
$manifestPath = Join-Path $snapshotDir "SNAPSHOT_MANIFEST.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $backupRoot "LATEST_BACKUP.json") -Encoding UTF8

Run-Git -GitArgs @("-C", $CloneDir, "add", "active_physics_workflow")
$null = & git -C $CloneDir diff --cached --quiet
$diffExit = $LASTEXITCODE
if ($diffExit -eq 1) {
    Run-Git -GitArgs @("-C", $CloneDir, "commit", "-m", "Backup active OpenFAST workflow $stamp")
    if ($Push) {
        Run-Git -GitArgs @("-C", $CloneDir, "push", "-u", "origin", "main")
    }
    $status = if ($Push) { "committed_and_pushed" } else { "committed_local_only" }
} elseif ($diffExit -eq 0) {
    $status = "no_staged_changes"
} else {
    throw "git diff --cached --quiet failed with exit code $diffExit"
}

[ordered]@{
    status = $status
    clone_dir = $CloneDir
    snapshot_dir = $snapshotDir
    package_zip = $packageDest
    pushed = [bool]$Push
} | ConvertTo-Json -Depth 4
