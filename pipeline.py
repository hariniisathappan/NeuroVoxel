"""Long-running operations: dataset indexing, per-case calculation, batch calculation, clean-up of staged files."""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import config as C
import db
import engine
import jobs
import msd
from validation import validate_case

Progress = Callable[[float, str], None]
DATASET = "__dataset__"


# ------------------------------------------------------------------ index
def index_dataset(progress: Progress, force: bool = False) -> None:
    meta = msd.load_meta()
    entries = msd.list_cases(meta)
    if not entries:
        raise RuntimeError(f"No training cases found under {C.DATASET_DIR}.")
    db.set_meta("dataset_name", f"MSD Task01 {meta['raw'].get('name', 'BrainTumour')}")
    db.set_meta("label_mapping", meta["labels"])
    db.set_meta("channel_order", meta["channels"])
    have = set() if force else db.indexed_ids()
    todo = [e for e in entries if e["case_id"] not in have]
    progress(0.02, f"{len(entries)} cases in dataset.json, {len(todo)} to index")
    batch: list[dict] = []
    with ThreadPoolExecutor(max_workers=C.MAX_INDEX_THREADS) as pool:
        futures = [pool.submit(msd.index_case, e, meta) for e in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            if jobs.cancelled(DATASET, "index"):
                for f in futures:
                    f.cancel()
                raise RuntimeError("Indexing cancelled. Cases indexed so far are kept.")
            batch.append(fut.result())
            if len(batch) >= 20:
                db.upsert_index(batch); batch = []
            progress(0.02 + 0.96 * n / max(len(todo), 1), f"Indexed {n}/{len(todo)}")
    if batch:
        db.upsert_index(batch)
    db.set_meta("indexed_at", db.now())
    db.compute_thresholds()


# ------------------------------------------------------------------ calculation
def _add_check(report: dict, cid: str, title: str, status: str, detail: str) -> None:
    report["checks"].append({"id": cid, "title": title, "status": status, "detail": detail})
    report["summary"] = {s: sum(1 for c in report["checks"] if c["status"] == s) for s in ("pass", "warn", "fail")}
    report["status"] = "fail" if report["summary"]["fail"] else "warn" if report["summary"]["warn"] else "pass"
    report["can_analyze"] = report["status"] != "fail"


def _provenance(out: dict, meta: dict) -> dict[str, str]:
    prov = {f"software_{k}": v for k, v in engine.versions().items()}
    prov.update({"normalisation": C.NORMALISATION, "radiomics_settings": str(C.RADIOMICS_SETTINGS), "bin_width_z": str(C.BIN_WIDTH),
                 "feature_classes": ", ".join(C.FEATURE_CLASSES), "min_region_voxels": str(C.MIN_REGION_VOXELS),
                 "msd_label_mapping": str(meta["labels"]["map"]), "msd_label_mapping_source": meta["labels"]["source"],
                 "msd_channel_order": str(meta["channels"]["order"]), "msd_channel_order_source": meta["channels"]["source"]})
    for tid, d in out["diagnostics"].items():
        seq, region, _ = tid.split("|")
        if region == "whole_tumour" and "diagnostics_Image-original_Hash" in d:
            prov[f"normalised_image_hash_{seq}"] = d["diagnostics_Image-original_Hash"]
    for tid, message in out["errors"].items():
        prov[f"task_error_{tid.replace('|', '_')}"] = message
    return prov


def stage_for_viewing(case_id: str) -> None:
    """Make sure the staged volumes exist (re-created from the read-only dataset if they were cleaned up)."""
    if msd.is_staged(case_id):
        return
    case = db.get_case(case_id)
    if not case:
        raise KeyError(case_id)
    msd.stage_case(case_id, msd.load_meta(), {"case_id": case_id, "image": case["image_rel"], "label": case["label_rel"]})


def calculate_case(case_id: str, progress: Progress) -> None:
    """Stage -> validate fully -> extract 4 sequences x 3 regions -> store. Sets the case status itself."""
    case = db.get_case(case_id)
    if not case:
        raise KeyError(case_id)
    meta = msd.load_meta()
    db.set_status(case_id, "running")
    try:
        progress(0.02, "Reading the case from the dataset folder")
        info = msd.stage_case(case_id, meta, {"case_id": case_id, "image": case["image_rel"], "label": case["label_rel"]})
        progress(0.06, "Running the full validation")
        report = validate_case(engine.case_paths(case_id))
        if info["unexpected"]:
            _add_check(report, "raw_labels", "Raw label values are the dataset's tumour labels", "fail",
                       f"Unexpected values in the label file: {info['unexpected']}.")
        db.save_report(case_id, report, "full")
        if not report["can_analyze"]:
            fails = "; ".join(c["detail"] for c in report["checks"] if c["status"] == "fail")
            raise RuntimeError("Validation failed, no features were calculated. " + fails)
        regions = [r for r, v in report["regions"].items() if v["available"]]
        out = engine.extract_baseline(case_id, regions, lambda v, m: progress(0.08 + 0.9 * v, m))
        if not out["rows"]:
            raise RuntimeError("PyRadiomics returned no features. " + "; ".join(list(out["errors"].values())[:2]))
        db.save_baseline(case_id, out, _provenance(out, meta))
        db.set_status(case_id, "done")
    except Exception:
        db.set_status(case_id, "error")
        raise
    finally:
        cleanup_staged()


def run_sensitivity(case_id: str, progress: Progress) -> None:
    stage_for_viewing(case_id)
    regions = [r for r, v in (db.get_report(case_id) or {}).get("regions", {}).items() if v["available"]]
    db.save_sensitivity(case_id, engine.extract_sensitivity(case_id, regions, progress))


def batch_calculate(ids: list[str], progress: Progress) -> None:
    failed: list[str] = []
    for n, cid in enumerate(ids):
        if jobs.cancelled(DATASET, "batch"):
            raise RuntimeError(f"Stopped by you after {n} of {len(ids)} cases. Finished cases are kept.")
        try:
            calculate_case(cid, lambda v, m, n=n, cid=cid: progress((n + v) / len(ids), f"Case {n + 1}/{len(ids)} {cid}: {m}"))
        except Exception as error:  # noqa: BLE001 - one failing case must not stop the batch
            failed.append(f"{cid}: {str(error)[:120]}")
    if failed:
        raise RuntimeError(f"{len(ids) - len(failed)} of {len(ids)} cases finished. Failed: " + " | ".join(failed[:5]))


# ------------------------------------------------------------------ clean-up
def staged_cases() -> list[tuple[str, float, int]]:
    out = []
    if C.UPLOADS.exists():
        for d in C.UPLOADS.iterdir():
            if d.is_dir():
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                out.append((d.name, d.stat().st_mtime, size))
    return sorted(out, key=lambda x: -x[1])


def cleanup_staged(keep: int = 6, everything: bool = False) -> dict[str, int]:
    """Staged files are re-creatable copies. Keep only the most recent few (or none)."""
    removed = freed = 0
    eligible = 0
    for name, _mtime, size in staged_cases():
        # Pipeline A uploads are user-owned analysis sessions, not disposable staged dataset cases.
        if name.startswith("upload-"):
            continue
        case = db.get_case(name)
        if case and case["calc_status"] == "running":
            continue
        if everything or eligible >= keep:
            shutil.rmtree(C.UPLOADS / name, ignore_errors=True)
            removed += 1
            freed += size
        eligible += 1
    return {"removed": removed, "freed_mb": round(freed / 2**20, 1)}
