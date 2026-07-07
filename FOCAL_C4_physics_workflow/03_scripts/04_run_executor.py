from __future__ import annotations

import argparse
import csv
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()

FIELDS = [
    "run_id",
    "candidate_id",
    "dof",
    "input_dir",
    "output_dir",
    "fst_file",
    "status",
    "pid",
    "start_time",
    "end_time",
    "elapsed_sec",
    "timeout_sec",
    "return_code",
    "stdout_path",
    "stderr_path",
    "input_sha256",
    "output_sha256",
    "notes",
]

PTFM_KEY = {
    "Surge": "PtfmSurge",
    "Sway": "PtfmSway",
    "Heave": "PtfmHeave",
    "Roll": "PtfmRoll",
    "Pitch": "PtfmPitch",
    "Yaw": "PtfmYaw",
}


def _candidate(candidate_id: str) -> dict[str, str]:
    rows = cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv"))
    for row in rows:
        if row.get("candidate_id") == candidate_id:
            return row
    raise SystemExit(f"Candidate not found: {candidate_id}")


def _dof_package_spec(cand: dict[str, str], vector: dict[str, Any], dof: str, cfg: dict[str, Any]) -> tuple[str, str, str]:
    meta = vector.get("decision_meta") or {}
    packages = meta.get("dof_packages") or {}
    spec = packages.get(dof) or {}
    return (
        spec.get("model_input_dir") or cand["model_input_dir"],
        spec.get("openfast_subdir") or cand.get("openfast_subdir") or cfg["model"].get("openfast_subdir", "OpenFAST_input_files"),
        spec.get("fst_file") or cand.get("fst_file") or cfg["model"].get("fst_file", "IEA-15-240-RWT-UMaineSemi.fst"),
    )


def _openfast_exe_for(vector: dict[str, Any], dof: str, cfg: dict[str, Any]) -> Path | None:
    meta = vector.get("decision_meta") or {}
    packages = meta.get("dof_packages") or {}
    spec = packages.get(dof) or {}
    value = spec.get("openfast_exe") or meta.get("openfast_exe")
    if value:
        exe = cfgmod.resolve_workspace_path(value)
        if not cfgmod.is_within(exe):
            raise SystemExit(f"candidate openfast_exe must be inside workspace: {value}")
        return exe
    return cfgmod.openfast_exe(cfg)


def _next_run_id(candidate_id: str, dof: str) -> str:
    safe = f"{dof}_{candidate_id}"
    existing = [p.name for p in (cfgmod.workspace_root() / "04_current_runs" / "runs").glob(f"RUN_*_{safe}")]
    return f"RUN_{len(existing) + 1:04d}_{safe}"


def _split_value_key(line: str) -> tuple[str | None, str | None]:
    parts = line.split()
    if len(parts) < 2:
        return None, None
    return parts[0].strip('"'), parts[1]


def _set_val(lines: list[str], key: str, newval: Any) -> None:
    for idx, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            comment = ""
            key_pos = line.find(key)
            if key_pos >= 0:
                after_key = line[key_pos + len(key) :]
                dash_pos = after_key.find("-")
                if dash_pos >= 0:
                    comment = " " + after_key[dash_pos:]
            lines[idx] = f"{newval:<16} {key}{comment}"
            return
    raise KeyError(f"key not found: {key}")


def _block_start(lines: list[str], tag: str) -> int:
    for i, line in enumerate(lines):
        if tag in line and "Additional" in line:
            nums = 0
            for token in line.split():
                try:
                    float(token)
                except ValueError:
                    break
                nums += 1
            return i if nums >= 6 else i + 1
    if tag == "AddBQuad":
        blin = _block_start(lines, "AddBLin")
        if blin + 11 < len(lines):
            return blin + 6
    raise ValueError(f"block not found: {tag}")


def _read_matrix(lines: list[str], start: int) -> list[list[float]]:
    rows: list[list[float]] = []
    for r in range(6):
        rows.append([float(x) for x in lines[start + r].split()[:6]])
    return rows


def _fmt_matrix(mat: list[list[float]]) -> list[str]:
    return [" ".join(f"{v: .9e}" for v in row) for row in mat]


def _apply_addf0_edits(lines: list[str], edits: list[dict[str, Any]]) -> list[str]:
    out = list(lines)
    start = next(
        (i for i, line in enumerate(out) if "AddF0" in line and "Additional" in line),
        None,
    )
    if start is None:
        raise ValueError("AddF0 block not found")
    for edit in edits:
        idx = int(edit["i"])
        if idx < 0 or idx >= 6:
            raise ValueError(f"AddF0 index out of range: {idx}")
        line_idx = start + idx
        val = float(edit["value"])
        if idx == 0:
            key_pos = out[line_idx].find("AddF0")
            suffix = out[line_idx][key_pos:] if key_pos >= 0 else ""
            out[line_idx] = f"{val: .9e}   {suffix}".rstrip()
        else:
            out[line_idx] = f"{val: .9e}"
    return out


def _apply_hydrodyn_edits(lines: list[str], edits: list[dict[str, Any]]) -> list[str]:
    out = list(lines)
    tagmap = {"CLin": "AddCLin", "BLin": "AddBLin", "BQuad": "AddBQuad"}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        grouped.setdefault(edit["block"], []).append(edit)
    # AddBQuad may be located relative to the AddBLin label in compact HydroDyn
    # files. Apply BQuad before BLin, because rewriting a matrix row drops labels.
    block_order = ["F0", "AddF0", "BQuad", "CLin", "BLin"]
    ordered_blocks = [b for b in block_order if b in grouped]
    ordered_blocks.extend(b for b in grouped if b not in ordered_blocks)
    for block in ordered_blocks:
        block_edits = grouped[block]
        if block in {"F0", "AddF0"}:
            out = _apply_addf0_edits(out, block_edits)
            continue
        tag = tagmap.get(block, block)
        start = _block_start(out, tag)
        mat = _read_matrix(out, start)
        for edit in block_edits:
            i = int(edit["i"])
            j = int(edit["j"])
            val = float(edit["value"])
            mat[i][j] = val
            if i != j and edit.get("symmetric", True):
                mat[j][i] = val
        out[start : start + 6] = _fmt_matrix(mat)
    return out


def _md_header(lines: list[str], keyword: str) -> int:
    target = keyword.upper()
    for i, line in enumerate(lines):
        section = line.strip().upper().strip("-").strip()
        if section == target:
            return i
    raise ValueError(f"MoorDyn section not found: {keyword}")


def _md_rows(lines: list[str], hdr: int) -> list[int]:
    rows: list[int] = []
    for i in range(hdr + 3, len(lines)):
        s = lines[i].strip()
        if not s or s.startswith("---") or s.upper().startswith("END"):
            break
        rows.append(i)
    return rows


def _scale_md_col(lines: list[str], section: str, col: int, factor: float) -> None:
    for i in _md_rows(lines, _md_header(lines, section)):
        parts = lines[i].split()
        if len(parts) > col:
            parts[col] = f"{float(parts[col]) * factor:.9g}"
            lines[i] = " ".join(parts)


def _find_ref(fst_path: Path, key: str) -> Path | None:
    for line in fst_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        value, found_key = _split_value_key(line)
        if found_key == key and value and value.lower() not in {"none", "unused"}:
            return (fst_path.parent / value).resolve()
    return None


def _set_fst_ref(lines: list[str], key: str, value: str) -> None:
    for idx, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            lines[idx] = f'"{value}"    {key}'
            return
    raise KeyError(f"FST key not found: {key}")


def _initial_conditions_for_dof(vector: dict[str, Any], dof: str) -> dict[str, Any]:
    conditions = dict(vector.get("initial_conditions") or {})
    meta = vector.get("decision_meta") or {}
    dof_conditions = meta.get("dof_initial_conditions") or {}
    if dof in dof_conditions:
        conditions.update(dof_conditions[dof] or {})
    return conditions


def _materialize_vector(fst_file: Path, vector: dict[str, Any], dof: str) -> list[str]:
    notes: list[str] = []
    fst_lines = fst_file.read_text(encoding="utf-8", errors="ignore").splitlines()

    if vector.get("tmax"):
        _set_val(fst_lines, "TMax", vector["tmax"])
        notes.append(f"TMax={vector['tmax']}")

    ed_path = _find_ref(fst_file, "EDFile")
    initial_conditions = _initial_conditions_for_dof(vector, dof)
    if ed_path and initial_conditions:
        lines = ed_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for ic_name, key in PTFM_KEY.items():
            try:
                _set_val(lines, key, 0)
            except KeyError:
                pass
        for ic_dof, value in initial_conditions.items():
            _set_val(lines, PTFM_KEY[ic_dof], value)
        ed_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        notes.append(f"ElastoDyn initial_conditions[{dof}]")

    if ed_path and vector.get("ed_edits"):
        lines = ed_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for edit in vector["ed_edits"]:
            _set_val(lines, str(edit["key"]), edit["value"])
        ed_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        notes.append(f"ElastoDyn edits={len(vector['ed_edits'])}")

    hd_path = _find_ref(fst_file, "HydroFile")
    if hd_path and vector.get("hydrodyn_edits"):
        lines = hd_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        lines = _apply_hydrodyn_edits(lines, vector["hydrodyn_edits"])
        hd_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        notes.append(f"HydroDyn edits={len(vector['hydrodyn_edits'])}")

    md_path = _find_ref(fst_file, "MooringFile")
    moor = vector.get("moordyn") or {}
    if md_path and moor:
        lines = md_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if moor.get("EA_scale") is not None:
            _scale_md_col(lines, "LINE TYPES", 3, float(moor["EA_scale"]))
            notes.append(f"MoorDyn EA_scale={moor['EA_scale']}")
        if moor.get("UnstrLen_scale") is not None:
            _scale_md_col(lines, "LINES", 4, float(moor["UnstrLen_scale"]))
            notes.append(f"MoorDyn UnstrLen_scale={moor['UnstrLen_scale']}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fst_file.write_text("\n".join(fst_lines) + "\n", encoding="utf-8")
    return notes


def _write_live(data: dict[str, Any], run_dir: Path | None = None, update_global: bool = True) -> None:
    if run_dir is not None:
        (run_dir / "live_status.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logs_dir = run_dir / "logs"
        if logs_dir.exists():
            (logs_dir / "live_status.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if not update_global:
        return
    cfgmod.write_json("08_logs/live_progress_status.json", data)
    cfgmod.write_report("08_logs/live_progress_status.md", "Live Progress Status", [
        f"- Run ID: {data.get('run_id')}",
        f"- Candidate: {data.get('candidate_id')}",
        f"- DOF: {data.get('dof')}",
        f"- Status: {data.get('status')}",
        f"- Updated: {data.get('updated')}",
        f"- Elapsed: {data.get('elapsed_sec', '')}",
        f"- Notes: {data.get('notes', '')}",
    ])


def _append_process_log(row: dict[str, Any]) -> None:
    path = cfgmod.workspace_root() / "08_logs" / "openfast_process_log.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["time", "run_id", "candidate_id", "dof", "event", "pid", "status", "notes"])
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _pid_running(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
    except (OSError, TypeError, ValueError):
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, value)
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
    try:
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
    except Exception:
        return 1e99


def _lock_path() -> Path:
    return cfgmod.workspace_root() / "08_logs" / "openfast_resource.lock"


def _suite_lock_path() -> Path:
    return cfgmod.workspace_root() / "08_logs" / "openfast_suite.lock"


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc)}


def _write_lock(path: Path, payload: dict[str, Any]) -> None:
    # Do not replace the lock file while it is held. A replace briefly removes
    # the path and lets another process acquire the same file lock via O_EXCL.
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _wait_for_suite_lock(run_id: str, candidate_id: str, dof: str, suite_id: str, cfg: dict[str, Any], timeout: int, run_dir: Path | None) -> None:
    path = _suite_lock_path()
    run_cfg = cfg.get("run", {})
    stale_sec = int(run_cfg.get("suite_lock_stale_sec", max(timeout * 2, 1800)))
    wait_sec = int(run_cfg.get("resource_wait_sec", max(timeout * 2, 1800)))
    deadline = time.time() + wait_sec
    while path.exists():
        current = _read_lock(path)
        if current.get("_read_error"):
            time.sleep(0.2)
            continue
        if suite_id and current.get("suite_id") == suite_id:
            return
        owner_alive = _pid_running(current.get("owner_pid"))
        stale = _iso_age_seconds(str(current.get("updated_at") or current.get("acquired_at") or "")) > stale_sec
        if not owner_alive or stale:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        notes = f"waiting for OpenFAST suite lock held_by={current.get('suite_id', '')} pid={current.get('owner_pid', '')}"
        _write_live({
            "run_id": run_id,
            "candidate_id": candidate_id,
            "dof": dof,
            "status": "WAITING_FOR_SUITE_LOCK",
            "updated": cfgmod.utc_now(),
            "elapsed_sec": "",
            "notes": notes,
        }, run_dir=run_dir, update_global=False)
        if time.time() > deadline:
            raise SystemExit(f"Timed out waiting for OpenFAST suite lock: {notes}")
        time.sleep(5)


@contextmanager
def _openfast_resource_lock(run_id: str, candidate_id: str, dof: str, cfg: dict[str, Any], timeout: int, run_dir: Path | None = None):
    run_cfg = cfg.get("run", {})
    suite_id = ""
    manifest_path = run_dir / "manifest.json" if run_dir is not None else None
    if manifest_path is not None:
        suite_id = str(_read_lock(manifest_path).get("suite_id") or "")
    _wait_for_suite_lock(run_id, candidate_id, dof, suite_id, cfg, timeout, run_dir)
    if int(run_cfg.get("max_parallel_openfast", 1)) > 1:
        yield ""
        return
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stale_sec = int(run_cfg.get("resource_lock_stale_sec", max(timeout * 2, 1800)))
    wait_sec = int(run_cfg.get("resource_wait_sec", max(timeout * 2, 1800)))
    token = uuid.uuid4().hex
    deadline = time.time() + wait_sec
    payload = {
        "token": token,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "dof": dof,
        "owner_pid": os.getpid(),
        "openfast_pid": "",
        "acquired_at": cfgmod.utc_now(),
        "updated_at": cfgmod.utc_now(),
    }
    acquired = False
    while not acquired:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            acquired = True
            _append_process_log({
                "time": cfgmod.utc_now(),
                "run_id": run_id,
                "candidate_id": candidate_id,
                "dof": dof,
                "event": "resource_lock_acquired",
                "pid": os.getpid(),
                "status": "LOCKED",
                "notes": str(path.relative_to(cfgmod.workspace_root())),
            })
            break
        except FileExistsError:
            current = _read_lock(path)
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
            notes = f"waiting for OpenFAST lock held_by={current.get('run_id', '')} pid={current.get('owner_pid', '')}"
            _write_live({
                "run_id": run_id,
                "candidate_id": candidate_id,
                "dof": dof,
                "status": "WAITING_FOR_RESOURCE_LOCK",
                "updated": cfgmod.utc_now(),
                "elapsed_sec": "",
                "notes": notes,
            }, run_dir=run_dir, update_global=False)
            if time.time() > deadline:
                raise SystemExit(f"Timed out waiting for OpenFAST resource lock: {notes}")
            time.sleep(5)
    try:
        yield token
    finally:
        current = _read_lock(path)
        if current.get("token") == token:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            _append_process_log({
                "time": cfgmod.utc_now(),
                "run_id": run_id,
                "candidate_id": candidate_id,
                "dof": dof,
                "event": "resource_lock_released",
                "pid": os.getpid(),
                "status": "UNLOCKED",
                "notes": str(path.relative_to(cfgmod.workspace_root())),
            })


def _update_lock_openfast_pid(token: str, pid: str) -> None:
    if not token:
        return
    path = _lock_path()
    current = _read_lock(path)
    if current.get("token") == token:
        current["openfast_pid"] = pid
        current["updated_at"] = cfgmod.utc_now()
        _write_lock(path, current)


def _touch_lock(token: str) -> None:
    if not token:
        return
    path = _lock_path()
    current = _read_lock(path)
    if current.get("token") == token:
        current["updated_at"] = cfgmod.utc_now()
        _write_lock(path, current)


def _collect_openfast_outputs(openfast_dir: Path, output_dir: Path, fst_stem: str) -> list[str]:
    copied: list[str] = []
    suffixes = {".out", ".outb", ".sum", ".ech", ".log", ".yaml", ".yml"}
    for path in sorted(openfast_dir.glob(f"{fst_stem}*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        target = output_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        copied.append(target.relative_to(cfgmod.workspace_root()).as_posix())
    return copied


def _write_run_artifact_map(run_dir: Path, row: dict[str, Any], manifest: dict[str, Any], collected_outputs: list[str]) -> Path:
    paths = [
        row.get("input_dir", ""),
        row.get("output_dir", ""),
        row.get("fst_file", ""),
        row.get("stdout_path", ""),
        row.get("stderr_path", ""),
        run_dir.relative_to(cfgmod.workspace_root()).as_posix(),
        (run_dir / "logs").relative_to(cfgmod.workspace_root()).as_posix(),
        (run_dir / "manifest.json").relative_to(cfgmod.workspace_root()).as_posix(),
        *collected_outputs,
    ]
    entries = []
    for rel in sorted({str(p).replace("\\", "/") for p in paths if p}):
        path = cfgmod.workspace_root() / rel
        entries.append({
            "path": rel,
            "exists": path.exists(),
            "kind": "dir" if path.is_dir() else "file" if path.is_file() else "missing",
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        })
    artifact_map = {
        "created_at": cfgmod.utc_now(),
        "run_id": row.get("run_id", ""),
        "candidate_id": row.get("candidate_id", ""),
        "dof": row.get("dof", ""),
        "status": row.get("status", ""),
        "manifest": manifest,
        "artifact_paths": entries,
    }
    path = run_dir / "artifact_map.json"
    path.write_text(json.dumps(artifact_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="CAND_TEST")
    parser.add_argument("--dof", default="FD_ROLL")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--suite-id", default="")
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    cand = _candidate(args.candidate)
    vector = json.loads(cand.get("parameter_vector_json") or "{}")
    source_rel, openfast_subdir, fst_name = _dof_package_spec(cand, vector, args.dof, cfg)
    source_package = cfgmod.resolve_workspace_path(source_rel)
    if not cfgmod.is_within(source_package) or not source_package.exists():
        raise SystemExit(f"Candidate package directory is invalid: {source_package}")

    run_id = _next_run_id(args.candidate, args.dof)
    run_dir = cfgmod.workspace_root() / "04_current_runs" / "runs" / run_id
    input_dir = run_dir / "input"
    output_dir = run_dir / "output"
    logs_dir = run_dir / "logs"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    if not any(input_dir.iterdir()):
        shutil.copytree(source_package, input_dir, dirs_exist_ok=True)

    openfast_dir = input_dir / openfast_subdir
    fst_file = openfast_dir / fst_name
    if not fst_file.exists():
        raise SystemExit(f"FST file missing in candidate run input: {fst_file}")

    materialize_notes = _materialize_vector(fst_file, vector, args.dof)

    stdout_path = logs_dir / "stdout.txt"
    stderr_path = logs_dir / "stderr.txt"
    run_log = logs_dir / "run.log"
    timeout = int(cfg["run"]["timeout_sec"])
    start = cfgmod.utc_now()
    status = "DRY_RUN" if args.dry_run else "RUNNING"
    exe = _openfast_exe_for(vector, args.dof, cfg)
    exe_available = bool(exe and exe.exists())
    command = [str(exe), fst_file.name] if exe_available else []
    manifest = {
        "run_id": run_id,
        "candidate_id": args.candidate,
        "dof": args.dof,
        "source_package": str(source_package),
        "run_dir": str(run_dir),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "logs_dir": str(logs_dir),
        "openfast_dir": str(openfast_dir),
        "fst_file": str(fst_file),
        "dry_run": args.dry_run,
        "command": command,
        "materialized": materialize_notes,
        "suite_id": args.suite_id,
        "created": start,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_live({"run_id": run_id, "candidate_id": args.candidate, "dof": args.dof, "status": status, "updated": start, "elapsed_sec": "0.0", "notes": "created run directory"}, run_dir=run_dir)
    _append_process_log({"time": start, "run_id": run_id, "candidate_id": args.candidate, "dof": args.dof, "event": "start", "pid": "", "status": status, "notes": "dry-run" if args.dry_run else ""})

    return_code = ""
    pid = ""
    notes = "; ".join(materialize_notes)
    t0 = time.time()
    if args.dry_run:
        stdout_path.write_text("DRY RUN\n" + " ".join(command) + "\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        run_log.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        end = cfgmod.utc_now()
    elif not exe_available:
        status = "SKIPPED"
        notes = (notes + "; " if notes else "") + "openfast_exe missing or unavailable"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(notes + "\n", encoding="utf-8")
        end = cfgmod.utc_now()
    else:
        with _openfast_resource_lock(run_id, args.candidate, args.dof, cfg, timeout, run_dir=run_dir) as lock_token:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                proc = subprocess.Popen(command, cwd=openfast_dir, stdout=out, stderr=err)
                pid = str(proc.pid)
                _update_lock_openfast_pid(lock_token, pid)
                last_heartbeat = -1.0
                while proc.poll() is None:
                    elapsed = time.time() - t0
                    if elapsed - last_heartbeat >= int(cfg["run"]["heartbeat_sec"]):
                        last_heartbeat = elapsed
                        hb = {
                            "run_id": run_id,
                            "candidate_id": args.candidate,
                            "dof": args.dof,
                            "status": "RUNNING",
                            "pid": pid,
                            "updated": cfgmod.utc_now(),
                            "elapsed_sec": f"{elapsed:.1f}",
                            "notes": "openfast process active",
                        }
                        _write_live(hb, run_dir=run_dir)
                        _touch_lock(lock_token)
                        _append_process_log({
                            "time": hb["updated"],
                            "run_id": run_id,
                            "candidate_id": args.candidate,
                            "dof": args.dof,
                            "event": "heartbeat",
                            "pid": pid,
                            "status": "RUNNING",
                            "notes": hb["elapsed_sec"],
                        })
                        with run_log.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(hb, ensure_ascii=False) + "\n")
                    if elapsed > timeout:
                        proc.kill()
                        proc.wait(timeout=10)
                        status = "TIMEOUT"
                        notes = (notes + "; " if notes else "") + f"timeout after {timeout} sec"
                        break
                    time.sleep(1)
                if status == "RUNNING":
                    return_code = str(proc.returncode)
                    status = "SUCCESS" if proc.returncode == 0 else "FAILED"
                else:
                    return_code = str(proc.returncode)
                end = cfgmod.utc_now()

    elapsed_sec = f"{time.time() - t0:.3f}"
    collected_outputs = _collect_openfast_outputs(openfast_dir, output_dir, fst_file.stem)
    input_sha = cfgmod.sha256_tree(input_dir)
    output_sha = cfgmod.sha256_tree(output_dir) if any(output_dir.iterdir()) else ""
    row = {
        "run_id": run_id,
        "candidate_id": args.candidate,
        "dof": args.dof,
        "input_dir": input_dir.relative_to(cfgmod.workspace_root()).as_posix(),
        "output_dir": output_dir.relative_to(cfgmod.workspace_root()).as_posix(),
        "fst_file": fst_file.relative_to(cfgmod.workspace_root()).as_posix(),
        "status": status,
        "pid": pid,
        "start_time": start,
        "end_time": end,
        "elapsed_sec": elapsed_sec,
        "timeout_sec": timeout,
        "return_code": return_code,
        "stdout_path": stdout_path.relative_to(cfgmod.workspace_root()).as_posix(),
        "stderr_path": stderr_path.relative_to(cfgmod.workspace_root()).as_posix(),
        "input_sha256": input_sha,
        "output_sha256": output_sha,
        "notes": notes,
    }
    artifact_map_path = _write_run_artifact_map(run_dir, row, manifest, collected_outputs)
    cfgmod.upsert_csv(cfgmod.registry_path("run_registry.csv"), FIELDS, "run_id", row)
    _write_live({
        "run_id": run_id,
        "candidate_id": args.candidate,
        "dof": args.dof,
        "status": status,
        "updated": end,
        "elapsed_sec": elapsed_sec,
        "notes": notes,
        "artifact_map_path": artifact_map_path.relative_to(cfgmod.workspace_root()).as_posix(),
    }, run_dir=run_dir)
    _append_process_log({"time": end, "run_id": run_id, "candidate_id": args.candidate, "dof": args.dof, "event": "end", "pid": pid, "status": status, "notes": notes})
    cfgmod.append_log("08_logs/harness_build_log.md", f"run_executor run_id={run_id} status={status}")
    print(f"Run Executor: {status} {run_id}")
    return 0 if status in {"DRY_RUN", "SUCCESS", "SKIPPED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
