from __future__ import annotations

import argparse
import csv
import ctypes
import io
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


cfgmod = _load_script("00_config_loader.py")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("_") or "suite"


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc)}


def _run_dirs_for(candidate: str, dof: str) -> list[Path]:
    runs_root = cfgmod.workspace_root() / "04_current_runs" / "runs"
    if not runs_root.exists():
        return []
    suffix = f"_{dof}_{candidate}"
    return sorted(
        [path for path in runs_root.iterdir() if path.is_dir() and path.name.endswith(suffix)],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _read_child_status(candidate: str, dof: str, suite_id: str, known_run_id: str = "") -> dict[str, object]:
    run_dirs: list[Path] = []
    if known_run_id:
        run_dirs.append(cfgmod.workspace_root() / "04_current_runs" / "runs" / known_run_id)
    run_dirs.extend(path for path in _run_dirs_for(candidate, dof) if path.name != known_run_id)
    for run_dir in run_dirs:
        manifest = _read_json(run_dir / "manifest.json")
        if not manifest:
            continue
        if manifest.get("candidate_id") != candidate or manifest.get("dof") != dof:
            continue
        manifest_suite = str(manifest.get("suite_id") or "")
        if suite_id and manifest_suite and manifest_suite != suite_id:
            continue
        live = _read_json(run_dir / "live_status.json") or _read_json(run_dir / "logs" / "live_status.json")
        if not live:
            live = {
                "run_id": manifest.get("run_id", run_dir.name),
                "candidate_id": candidate,
                "dof": dof,
                "status": "CREATED",
                "updated": manifest.get("created", ""),
                "elapsed_sec": "",
                "notes": "run directory created; live status not written yet",
            }
        live.setdefault("run_id", manifest.get("run_id", run_dir.name))
        live.setdefault("candidate_id", candidate)
        live.setdefault("dof", dof)
        live["run_dir"] = run_dir.relative_to(cfgmod.workspace_root()).as_posix()
        live["manifest_path"] = (run_dir / "manifest.json").relative_to(cfgmod.workspace_root()).as_posix()
        artifact_map = run_dir / "artifact_map.json"
        if artifact_map.exists():
            live["artifact_map_path"] = artifact_map.relative_to(cfgmod.workspace_root()).as_posix()
        return live
    return {}


def _write_suite_status(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_suite_event(log_path: Path, event: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("[suite-event] " + json.dumps(event, ensure_ascii=False) + "\n")


def _pid_running(pid: object) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, value)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def _iso_age_seconds(value: str) -> float:
    from datetime import datetime, timezone

    try:
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
    except Exception:
        return 1e99


def _suite_lock_path() -> Path:
    return cfgmod.workspace_root() / "08_logs" / "openfast_suite.lock"


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    # Suite locks are acquired with O_EXCL, so never replace the lock path while
    # it is held. Replacing creates a short window where another suite can enter.
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _acquire_suite_lock(suite_id: str, candidate: str, dofs: list[str], suite_status_path: Path, latest_status_path: Path, log_path: Path) -> str:
    cfg = cfgmod.load_config()
    run_cfg = cfg.get("run", {})
    timeout = int(run_cfg.get("resource_wait_sec", max(int(run_cfg.get("timeout_sec", 900)) * 2, 1800)))
    stale_sec = int(run_cfg.get("suite_lock_stale_sec", timeout))
    deadline = time.time() + timeout
    path = _suite_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "suite_id": suite_id,
        "candidate_id": candidate,
        "dofs": dofs,
        "owner_pid": os.getpid(),
        "acquired_at": cfgmod.utc_now(),
        "updated_at": cfgmod.utc_now(),
    }
    last_wait_log_at = 0.0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            return token
        except FileExistsError:
            current = _read_json(path)
            if current.get("_read_error"):
                time.sleep(0.2)
                continue
            owner_alive = _pid_running(current.get("owner_pid"))
            stale = _iso_age_seconds(str(current.get("updated_at") or current.get("acquired_at") or "")) > stale_sec
            if not owner_alive or stale:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            status = {
                "suite_id": suite_id,
                "candidate_id": candidate,
                "dofs": dofs,
                "status": "WAITING_FOR_SUITE_LOCK",
                "updated": cfgmod.utc_now(),
                "held_by_suite_id": current.get("suite_id", ""),
                "held_by_pid": current.get("owner_pid", ""),
                "suite_lock_path": path.relative_to(cfgmod.workspace_root()).as_posix(),
                "suite_status_path": suite_status_path.relative_to(cfgmod.workspace_root()).as_posix(),
            }
            _write_suite_status(suite_status_path, status)
            _write_suite_status(latest_status_path, status)
            now = time.time()
            if now - last_wait_log_at >= 30:
                last_wait_log_at = now
                _append_suite_event(log_path, status)
            if time.time() > deadline:
                raise SystemExit(f"Timed out waiting for OpenFAST suite lock held by {current.get('suite_id', '')}")
            time.sleep(5)


def _release_suite_lock(token: str) -> None:
    if not token:
        return
    path = _suite_lock_path()
    current = _read_json(path)
    if current.get("_read_error"):
        return
    if current.get("token") == token:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _openfast_pids() -> list[str]:
    try:
        raw = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq openfast_x64.exe", "/FO", "CSV", "/NH"],
            text=True,
            errors="ignore",
        )
    except Exception:
        return []
    out: list[str] = []
    for row in csv.reader(io.StringIO(raw)):
        if len(row) >= 2 and row[0].lower() == "openfast_x64.exe":
            out.append(row[1])
    return out


def _wait_for_openfast_quiescence(suite_lock_token: str, suite_id: str, candidate: str, dofs: list[str], suite_status_path: Path, latest_status_path: Path, log_path: Path) -> None:
    cfg = cfgmod.load_config()
    run_cfg = cfg.get("run", {})
    if not bool(run_cfg.get("suite_wait_for_existing_openfast", True)):
        return
    timeout = int(run_cfg.get("resource_wait_sec", max(int(run_cfg.get("timeout_sec", 900)) * 2, 1800)))
    deadline = time.time() + timeout
    last_wait_log_at = 0.0
    last_wait_pids: tuple[str, ...] = ()
    while True:
        pids = _openfast_pids()
        if not pids:
            if last_wait_log_at:
                _append_suite_event(log_path, {
                    "suite_id": suite_id,
                    "candidate_id": candidate,
                    "dofs": dofs,
                    "status": "OPENFAST_QUIESCENT",
                    "updated": cfgmod.utc_now(),
                })
            return
        status = {
            "suite_id": suite_id,
            "candidate_id": candidate,
            "dofs": dofs,
            "status": "WAITING_FOR_OPENFAST_QUIESCENCE",
            "updated": cfgmod.utc_now(),
            "openfast_pids": pids,
            "suite_status_path": suite_status_path.relative_to(cfgmod.workspace_root()).as_posix(),
        }
        _touch_suite_lock(suite_lock_token)
        _write_suite_status(suite_status_path, status)
        _write_suite_status(latest_status_path, status)
        now = time.time()
        pid_tuple = tuple(pids)
        if now - last_wait_log_at >= 30 or pid_tuple != last_wait_pids:
            last_wait_log_at = now
            last_wait_pids = pid_tuple
            _append_suite_event(log_path, status)
        if time.time() > deadline:
            raise SystemExit(f"Timed out waiting for existing OpenFAST processes to finish: {','.join(pids)}")
        time.sleep(5)


def _touch_suite_lock(token: str) -> None:
    if not token:
        return
    path = _suite_lock_path()
    current = _read_json(path)
    if current.get("_read_error"):
        return
    if current.get("token") == token:
        current["updated_at"] = cfgmod.utc_now()
        _write_json_atomic(path, current)


def run_step(args: list[str], log_path: Path, suite_status_path: Path, latest_status_path: Path, suite_lock_token: str, suite_id: str, candidate: str, dof: str, phase: str) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(args)}\n")
        log.flush()
        proc = subprocess.Popen(args, cwd=cfgmod.workspace_root(), text=True, stdout=log, stderr=subprocess.STDOUT)
        heartbeat_sec = int(cfgmod.load_config().get("run", {}).get("heartbeat_sec", 30))
        last_heartbeat = -1.0
        start = time.time()
        child_run_id = ""
        while proc.poll() is None:
            elapsed = time.time() - start
            if elapsed - last_heartbeat >= heartbeat_sec:
                last_heartbeat = elapsed
                live = _read_child_status(candidate, dof, suite_id, child_run_id) if phase == "executor" else {}
                child_run_id = str(live.get("run_id") or child_run_id)
                status = {
                    "suite_id": suite_id,
                    "candidate_id": candidate,
                    "dof": dof,
                    "phase": phase,
                    "status": "RUNNING",
                    "updated": cfgmod.utc_now(),
                    "elapsed_sec": f"{elapsed:.1f}",
                    "child_live_status": live,
                    "log_path": log_path.relative_to(cfgmod.workspace_root()).as_posix(),
                }
                _touch_suite_lock(suite_lock_token)
                _write_suite_status(suite_status_path, status)
                _write_suite_status(latest_status_path, status)
                line = (
                    f"[suite-heartbeat] suite={suite_id} dof={dof} phase={phase} "
                    f"elapsed={elapsed:.1f}s child_status={live.get('status', '')} "
                    f"child_run={live.get('run_id', '')}\n"
                )
                log.write(line)
                log.flush()
                print(line.strip(), flush=True)
            time.sleep(1)
        log.write(f"[exit {proc.returncode}]\n")
        live = _read_child_status(candidate, dof, suite_id, child_run_id) if phase == "executor" else {}
        final_step_status = {
            "suite_id": suite_id,
            "candidate_id": candidate,
            "dof": dof,
            "phase": phase,
            "status": "DONE" if proc.returncode == 0 else "FAILED",
            "updated": cfgmod.utc_now(),
            "elapsed_sec": f"{time.time() - start:.1f}",
            "return_code": proc.returncode,
            "child_live_status": live,
            "log_path": log_path.relative_to(cfgmod.workspace_root()).as_posix(),
        }
        _write_suite_status(suite_status_path, final_step_status)
        _write_suite_status(latest_status_path, final_step_status)
        return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--dofs", default="")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--suite-id", default="")
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    dofs = [item.strip() for item in args.dofs.split(",") if item.strip()] or list(cfg["dofs"])
    suite_id = args.suite_id or cfgmod.utc_now().replace("-", "").replace(":", "").replace("+", "Z")
    safe_suite = _safe_name(suite_id)
    log_path = cfgmod.workspace_root() / "08_logs" / f"sixdof_runner_{args.candidate}_{safe_suite}.log"
    suite_status_path = cfgmod.workspace_root() / "08_logs" / f"sixdof_runner_{args.candidate}_{safe_suite}.status.json"
    latest_status_path = cfgmod.workspace_root() / "08_logs" / f"sixdof_runner_{args.candidate}_latest.json"
    _write_suite_status(suite_status_path, {
        "suite_id": suite_id,
        "candidate_id": args.candidate,
        "dofs": dofs,
        "status": "STARTED",
        "updated": cfgmod.utc_now(),
        "log_path": log_path.relative_to(cfgmod.workspace_root()).as_posix(),
    })
    _write_suite_status(latest_status_path, {
        "suite_id": suite_id,
        "candidate_id": args.candidate,
        "status": "STARTED",
        "updated": cfgmod.utc_now(),
        "log_path": log_path.relative_to(cfgmod.workspace_root()).as_posix(),
        "suite_status_path": suite_status_path.relative_to(cfgmod.workspace_root()).as_posix(),
    })
    failures: list[str] = []

    suite_lock_token = _acquire_suite_lock(suite_id, args.candidate, dofs, suite_status_path, latest_status_path, log_path)
    try:
        _wait_for_openfast_quiescence(suite_lock_token, suite_id, args.candidate, dofs, suite_status_path, latest_status_path, log_path)
        for dof in dofs:
            rc = run_step([
                sys.executable,
                str(Path("03_scripts") / "04_run_executor.py"),
                "--candidate",
                args.candidate,
                "--dof",
                dof,
                "--suite-id",
                suite_id,
            ], log_path, suite_status_path, latest_status_path, suite_lock_token, suite_id, args.candidate, dof, "executor")
            if rc != 0:
                failures.append(f"{dof}:executor")
                if not args.continue_on_failure:
                    break
                continue
            rc = run_step([
                sys.executable,
                str(Path("03_scripts") / "05_evaluate_metrics.py"),
                "--candidate",
                args.candidate,
                "--dof",
                dof,
            ], log_path, suite_status_path, latest_status_path, suite_lock_token, suite_id, args.candidate, dof, "metrics")
            if rc != 0:
                failures.append(f"{dof}:metrics")
                if not args.continue_on_failure:
                    break

        run_step([sys.executable, str(Path("03_scripts") / "06_select_candidates.py")], log_path, suite_status_path, latest_status_path, suite_lock_token, suite_id, args.candidate, "ALL", "selector")
    finally:
        _release_suite_lock(suite_lock_token)
    final_status = {
        "suite_id": suite_id,
        "candidate_id": args.candidate,
        "dofs": dofs,
        "status": "FAILED" if failures else "SUCCESS",
        "updated": cfgmod.utc_now(),
        "failures": failures,
        "log_path": log_path.relative_to(cfgmod.workspace_root()).as_posix(),
        "suite_status_path": suite_status_path.relative_to(cfgmod.workspace_root()).as_posix(),
    }
    _write_suite_status(suite_status_path, final_status)
    _write_suite_status(latest_status_path, final_status)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n[suite-complete] " + json.dumps(final_status, ensure_ascii=False) + "\n")
    if failures:
        print(f"SixDOF runner completed with failures: {','.join(failures)} log={log_path}", flush=True)
        return 1
    print(f"SixDOF runner complete suite={suite_id} log={log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
