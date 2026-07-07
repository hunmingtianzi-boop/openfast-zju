from __future__ import annotations

import csv
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "00_reference" / "curated_six_dof_reference" / "processed"
STARTING_MODEL_DIR = ROOT / "02_starting_model" / "best_reproducible_model"
OUT = ROOT / "05_metrics"

EXPECTED = {
    "Surge": "FD_SURGE_curated_processed.csv",
    "Sway": "FD_SWAY_curated_processed.csv",
    "Heave": "FD_HEAVE_curated_processed.csv",
    "Roll": "FD_ROLL_curated_processed.csv",
    "Pitch": "FD_PITCH_curated_processed.csv",
    "Yaw": "FD_YAW_curated_processed.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_reference_manifest() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for dof, filename in EXPECTED.items():
        path = REFERENCE_DIR / filename
        rows.append(
            {
                "dof": dof,
                "curated_processed_file": str(path),
                "exists": str(path.exists()).lower(),
                "sha256": sha256(path) if path.exists() else "",
                "bytes": str(path.stat().st_size) if path.exists() else "",
            }
        )
    return rows


def build_starting_model_manifest() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(p for p in STARTING_MODEL_DIR.rglob("*") if p.is_file()):
        rows.append(
            {
                "path": str(path.relative_to(STARTING_MODEL_DIR)),
                "sha256": sha256(path),
                "bytes": str(path.stat().st_size),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["status"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ref_rows = build_reference_manifest()
    model_rows = build_starting_model_manifest()
    write_csv(OUT / "curated_reference_usage_manifest.csv", ref_rows)
    write_csv(OUT / "starting_model_file_manifest.csv", model_rows)

    all_refs = all(row["exists"] == "true" for row in ref_rows)
    heave_ok = any(row["dof"] == "Heave" and row["exists"] == "true" for row in ref_rows)
    summary = [
        "# Clean Workspace Rescore Readiness",
        "",
        f"- reference_dir = `{REFERENCE_DIR}`",
        f"- starting_model_dir = `{STARTING_MODEL_DIR}`",
        f"- all six curated processed references found = {str(all_refs).lower()}",
        f"- curated Heave found = {str(heave_ok).lower()}",
        "- deprecated Heave processed source used = false",
        "- raw CSV used = false",
        "",
        "This helper is intentionally scoped to the clean workspace. It does not enumerate",
        "deprecated run families or external historical result directories.",
    ]
    (OUT / "clean_workspace_rescore_readiness.md").write_text("\n".join(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
