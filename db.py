"""SQLite storage for NeuroVoxel v3.1 (Case Explorer). One new database file; the v2 database is not touched.

Tables
  meta                  dataset name, label/channel mapping that was used, index time, size thresholds
  cases                 one row per MSD case: index columns (volumes, dominant sub-region, hemisphere) + calculation status
  validation_reports    pass/warn/fail report (stage 'index' = quick, 'full' = all four channels, run at calculation)
  region_stats, normalisation, feature_values, sensitivity_runs, sensitivity_values, provenance, jobs
  feature_values        long format (sequence, region, family, name, value); sequence 'mask' = mask-only shape features
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, image_rel TEXT NOT NULL, label_rel TEXT NOT NULL, indexed_at TEXT NOT NULL,
    shape TEXT, spacing TEXT, n_edema INTEGER, n_net INTEGER, n_enh INTEGER,
    vol_whole_ml REAL, vol_core_ml REAL, vol_enh_ml REAL, vol_edema_ml REAL, vol_net_ml REAL,
    enh_pct REAL, edema_pct REAL, net_pct REAL, dominant TEXT, hemisphere TEXT, right_fraction REAL,
    cx REAL, cy REAL, cz REAL, index_status TEXT NOT NULL DEFAULT 'pass', index_note TEXT NOT NULL DEFAULT '',
    calc_status TEXT NOT NULL DEFAULT 'none', calculated_at TEXT, app_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_reports (
    case_id TEXT PRIMARY KEY REFERENCES cases(case_id) ON DELETE CASCADE,
    status TEXT NOT NULL, report TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'full', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS region_stats (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    region TEXT NOT NULL, voxels INTEGER NOT NULL, volume_mm3 REAL NOT NULL, included INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, region)
);
CREATE TABLE IF NOT EXISTS normalisation (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    sequence TEXT NOT NULL, brain_voxels INTEGER NOT NULL, mean REAL NOT NULL, sd REAL NOT NULL,
    PRIMARY KEY (case_id, sequence)
);
CREATE TABLE IF NOT EXISTS feature_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    sequence TEXT NOT NULL, region TEXT NOT NULL, family TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL,
    UNIQUE (case_id, sequence, region, family, name)
);
CREATE TABLE IF NOT EXISTS sensitivity_runs (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    sequence TEXT NOT NULL, region TEXT NOT NULL, perturbation TEXT NOT NULL,
    voxels INTEGER NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, sequence, region, perturbation)
);
CREATE TABLE IF NOT EXISTS sensitivity_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    sequence TEXT NOT NULL, region TEXT NOT NULL, perturbation TEXT NOT NULL, family TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL,
    UNIQUE (case_id, sequence, region, perturbation, family, name)
);
CREATE TABLE IF NOT EXISTS provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE, key TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    case_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '', error TEXT, started_at TEXT, finished_at TEXT,
    PRIMARY KEY (case_id, kind)
);
CREATE TABLE IF NOT EXISTS uploaded_cases (
    upload_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, sequences_json TEXT NOT NULL,
    validation_json TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploaded_feature_values (
    upload_id TEXT NOT NULL REFERENCES uploaded_cases(upload_id) ON DELETE CASCADE,
    sequence TEXT NOT NULL, region TEXT NOT NULL, family TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (upload_id, sequence, region, family, name)
);
CREATE INDEX IF NOT EXISTS idx_features_case ON feature_values(case_id, sequence, region);
CREATE INDEX IF NOT EXISTS idx_sens_case ON sensitivity_values(case_id, sequence, region, perturbation);
CREATE INDEX IF NOT EXISTS idx_upload_features_case ON uploaded_feature_values(upload_id, sequence, region);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_uploaded_case(result: dict[str, Any]) -> None:
    """Persist a private Pipeline A result without adding it to the reference cohort."""
    upload_id = str(result["upload_id"])
    features: list[tuple] = []
    for sequence, regions in result.get("features", {}).items():
        for region, families in regions.items():
            for family, values in families.items():
                features.extend((upload_id, sequence, region, family, name, float(value)) for name, value in values.items())
    for region, values in result.get("shape", {}).items():
        features.extend((upload_id, "mask", region, "shape", name, float(value)) for name, value in values.items())
    with connect() as con:
        con.execute("""INSERT INTO uploaded_cases VALUES (?,?,?,?,?)
                    ON CONFLICT(upload_id) DO UPDATE SET sequences_json=excluded.sequences_json,
                    validation_json=excluded.validation_json, result_json=excluded.result_json""",
                    (upload_id, now(), json.dumps(result.get("sequences", [])),
                     json.dumps(result.get("validation", {})), json.dumps(result, allow_nan=False)))
        con.execute("DELETE FROM uploaded_feature_values WHERE upload_id=?", (upload_id,))
        con.executemany("INSERT INTO uploaded_feature_values VALUES (?,?,?,?,?,?)", features)


def load_uploaded_case(upload_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT result_json FROM uploaded_cases WHERE upload_id=?", (upload_id,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def list_uploaded_cases(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT upload_id, created_at, result_json FROM uploaded_cases ORDER BY created_at DESC LIMIT ?",
                           (max(1, min(int(limit), 100)),)).fetchall()
    result = []
    for row in rows:
        data = json.loads(row["result_json"])
        region = data.get("regions", {}).get("whole_tumour", {})
        result.append({"upload_id": row["upload_id"], "created_at": row["created_at"],
                       "sequences": data.get("sequences", []), "voxels": region.get("voxels", 0),
                       "volume_ml": round(float(region.get("volume_mm3", 0)) / 1000, 3)})
    return result


def delete_uploaded_case(upload_id: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM uploaded_cases WHERE upload_id=?", (upload_id,))


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(C.DATABASE, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db() -> None:
    C.UPLOADS.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        con.execute("UPDATE jobs SET state='interrupted', message='Server restarted before this job finished.' WHERE state='running'")
        con.execute("UPDATE cases SET calc_status='none' WHERE calc_status='running'")
    # Bring forward completed uploads created before upload results were added
    # to SQLite. Their source files and result.json already live in C.UPLOADS.
    for result_path in C.UPLOADS.glob("upload-*/result.json"):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("upload_id") and result.get("features"):
                save_uploaded_case(result)
        except (OSError, ValueError, TypeError, KeyError):
            continue


# ------------------------------------------------------------------ meta + index
def set_meta(key: str, value: Any) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))


def get_meta(key: str, default: Any = None) -> Any:
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


INDEX_COLS = ("case_id", "image_rel", "label_rel", "shape", "spacing", "n_edema", "n_net", "n_enh", "vol_whole_ml", "vol_core_ml",
              "vol_enh_ml", "vol_edema_ml", "vol_net_ml", "enh_pct", "edema_pct", "net_pct", "dominant", "hemisphere", "right_fraction",
              "cx", "cy", "cz", "index_status", "index_note")


def upsert_index(rows: list[dict[str, Any]]) -> None:
    """Insert or refresh index columns; calculation status and results of an existing case are kept."""
    with connect() as con:
        for r in rows:
            vals = [r.get(c) for c in INDEX_COLS]
            names = ", ".join(INDEX_COLS)
            marks = ", ".join("?" * len(INDEX_COLS))
            updates = ", ".join(f"{c}=excluded.{c}" for c in INDEX_COLS[1:])
            con.execute(f"INSERT INTO cases ({names}, indexed_at, app_version) VALUES ({marks}, ?, ?) "
                        f"ON CONFLICT(case_id) DO UPDATE SET {updates}, indexed_at=excluded.indexed_at",
                        (*vals, now(), C.APP_VERSION))


def indexed_ids() -> set[str]:
    with connect() as con:
        return {r[0] for r in con.execute("SELECT case_id FROM cases")}


def compute_thresholds() -> dict[str, float]:
    """Size tertiles of whole-tumour volume over all indexed cases that have a tumour (used by the 'size' filter)."""
    with connect() as con:
        v = sorted(r[0] for r in con.execute("SELECT vol_whole_ml FROM cases WHERE vol_whole_ml > 0"))
    if len(v) < 3:
        return {"small_max": 0.0, "medium_max": 0.0}
    t = {"small_max": v[len(v) // 3], "medium_max": v[2 * len(v) // 3]}
    set_meta("size_thresholds", t)
    return t


SORTABLE = {"case_id", "vol_whole_ml", "vol_core_ml", "vol_enh_ml", "vol_edema_ml", "enh_pct", "edema_pct", "net_pct", "calculated_at"}


def query_cases(f: dict[str, Any], limit: int = 50, offset: int = 0, sort: str = "case_id", desc: bool = False) -> dict[str, Any]:
    where, args = ["1=1"], []
    t = get_meta("size_thresholds") or {"small_max": 0, "medium_max": 0}
    if f.get("q"):
        where.append("case_id LIKE ?"); args.append(f"%{f['q']}%")
    for col, key in (("vol_whole_ml", "whole"), ("vol_enh_ml", "enh"), ("vol_core_ml", "core"), ("vol_edema_ml", "edema")):
        if f.get(f"min_{key}") is not None:
            where.append(f"{col} >= ?"); args.append(float(f[f"min_{key}"]))
        if f.get(f"max_{key}") is not None:
            where.append(f"{col} <= ?"); args.append(float(f[f"max_{key}"]))
    if f.get("dominant"):
        where.append("dominant = ?"); args.append(f["dominant"])
    if f.get("hemisphere"):
        where.append("hemisphere = ?"); args.append(f["hemisphere"])
    if f.get("size") == "small":
        where.append("vol_whole_ml <= ?"); args.append(t["small_max"])
    elif f.get("size") == "medium":
        where.append("vol_whole_ml > ? AND vol_whole_ml <= ?"); args += [t["small_max"], t["medium_max"]]
    elif f.get("size") == "large":
        where.append("vol_whole_ml > ?"); args.append(t["medium_max"])
    if f.get("has_enhancing") == "yes":
        where.append("n_enh >= ?"); args.append(C.MIN_REGION_VOXELS)
    elif f.get("has_enhancing") == "no":
        where.append("n_enh < ?"); args.append(C.MIN_REGION_VOXELS)
    if f.get("validation"):
        where.append("index_status = ?"); args.append(f["validation"])
    if f.get("calculated") == "yes":
        where.append("calc_status = 'done'")
    elif f.get("calculated") == "no":
        where.append("calc_status != 'done'")
    order = sort if sort in SORTABLE else "case_id"
    clause = " AND ".join(where)
    with connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM cases WHERE {clause}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM cases WHERE {clause} ORDER BY {order} {'DESC' if desc else 'ASC'}, case_id LIMIT ? OFFSET ?",
                           (*args, limit, offset)).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows], "size_thresholds": t}


def matching_ids(f: dict[str, Any], only_uncalculated: bool = True, cap: int | None = None) -> list[str]:
    f = dict(f)
    if only_uncalculated:
        f["calculated"] = "no"
    return [r["case_id"] for r in query_cases(f, limit=cap or 100000)["rows"]]


def dataset_summary() -> dict[str, Any]:
    with connect() as con:
        n = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        done = con.execute("SELECT COUNT(*) FROM cases WHERE calc_status='done'").fetchone()[0]
        bad = con.execute("SELECT index_status, COUNT(*) c FROM cases GROUP BY index_status").fetchall()
        dom = con.execute("SELECT dominant, COUNT(*) c FROM cases GROUP BY dominant").fetchall()
        hemi = con.execute("SELECT hemisphere, COUNT(*) c FROM cases GROUP BY hemisphere").fetchall()
        stats = con.execute("SELECT MIN(vol_whole_ml), MAX(vol_whole_ml), AVG(vol_whole_ml) FROM cases WHERE vol_whole_ml>0").fetchone()
    return {"cases": n, "calculated": done, "validation": {r[0]: r[1] for r in bad}, "dominant": {str(r[0]): r[1] for r in dom},
            "hemisphere": {str(r[0]): r[1] for r in hemi}, "volume_ml": {"min": stats[0], "max": stats[1], "mean": stats[2]},
            "size_thresholds": get_meta("size_thresholds"), "labels": get_meta("label_mapping"), "channels": get_meta("channel_order"),
            "dataset": get_meta("dataset_name"), "indexed_at": get_meta("indexed_at")}


def get_case(token: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM cases WHERE case_id=?", (token,)).fetchone()
    return dict(row) if row else None


def set_status(token: str, status: str) -> None:
    with connect() as con:
        con.execute("UPDATE cases SET calc_status=?, calculated_at=CASE WHEN ?='done' THEN ? ELSE calculated_at END WHERE case_id=?",
                    (status, status, now(), token))


def clear_results(token: str) -> None:
    """Delete calculated results of one case (keeps the index row)."""
    with connect() as con:
        for table in ("feature_values", "normalisation", "provenance", "sensitivity_values", "sensitivity_runs", "region_stats", "validation_reports"):
            con.execute(f"DELETE FROM {table} WHERE case_id=?", (token,))
        con.execute("DELETE FROM jobs WHERE case_id=?", (token,))
        con.execute("UPDATE cases SET calc_status='none', calculated_at=NULL WHERE case_id=?", (token,))


def count_cases() -> int:
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM cases WHERE calc_status='done'").fetchone()[0]


def calculated_ids() -> list[str]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT case_id FROM cases WHERE calc_status='done' ORDER BY case_id")]


def cohort_frame(ids: list[str]):
    """Wide table: one row per case, one column per 'sequence|region|family_name'. Used for percentiles and group statistics."""
    import pandas as pd
    if not ids:
        return pd.DataFrame()
    marks = ",".join("?" * len(ids))
    with connect() as con:
        rows = con.execute(f"SELECT case_id, sequence, region, family, name, value FROM feature_values WHERE case_id IN ({marks})", ids).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["case_id", "sequence", "region", "family", "name", "value"])
    if df.empty:
        return df
    df["col"] = df["sequence"] + "|" + df["region"] + "|" + df["family"] + "_" + df["name"]
    return df.pivot(index="case_id", columns="col", values="value")


def save_report(token: str, report: dict[str, Any], stage: str = "full") -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO validation_reports VALUES (?,?,?,?,?)", (token, report["status"], json.dumps(report), stage, now()))
        for region, r in report.get("regions", {}).items():
            con.execute("INSERT OR REPLACE INTO region_stats VALUES (?,?,?,?,?,?)",
                        (token, region, r["voxels"], r["volume_mm3"], int(r["available"]),
                         "" if r["available"] else f"fewer than {C.MIN_REGION_VOXELS} voxels"))


def get_report(token: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT report FROM validation_reports WHERE case_id=?", (token,)).fetchone()
    return json.loads(row["report"]) if row else None


# ------------------------------------------------------------------ results
def save_baseline(token: str, out: dict[str, Any], provenance: dict[str, str]) -> None:
    with connect() as con:
        con.execute("DELETE FROM feature_values WHERE case_id=?", (token,))
        con.execute("DELETE FROM normalisation WHERE case_id=?", (token,))
        con.execute("DELETE FROM provenance WHERE case_id=?", (token,))
        con.executemany("INSERT INTO feature_values (case_id, sequence, region, family, name, value) VALUES (?,?,?,?,?,?)",
                        [(token, seq, region, family, name, value) for seq, region, _tag, family, name, value in out["rows"]])
        con.executemany("INSERT INTO normalisation VALUES (?,?,?,?,?)",
                        [(token, s, n["brain_voxels"], n["mean"], n["sd"]) for s, n in out["normalisation"].items()])
        con.executemany("INSERT INTO provenance (case_id, key, value) VALUES (?,?,?)", [(token, k, v) for k, v in provenance.items()])


def load_features(token: str) -> dict[str, Any]:
    with connect() as con:
        rows = con.execute("SELECT sequence, region, family, name, value FROM feature_values WHERE case_id=? ORDER BY sequence, region, family, name", (token,)).fetchall()
        norm = con.execute("SELECT sequence, brain_voxels, mean, sd FROM normalisation WHERE case_id=?", (token,)).fetchall()
        regions = con.execute("SELECT region, voxels, volume_mm3, included, note FROM region_stats WHERE case_id=?", (token,)).fetchall()
        prov = con.execute("SELECT key, value FROM provenance WHERE case_id=? ORDER BY id", (token,)).fetchall()
    features: dict[str, Any] = {}
    shape: dict[str, Any] = {}
    for r in rows:
        if r["sequence"] == "mask":
            shape.setdefault(r["region"], {}).setdefault(r["family"], {})[r["name"]] = r["value"]
        else:
            features.setdefault(r["sequence"], {}).setdefault(r["region"], {}).setdefault(r["family"], {})[r["name"]] = r["value"]
    return {"features": features, "shape": {k: v.get("shape", {}) for k, v in shape.items()},
            "normalisation": {r["sequence"]: {"brain_voxels": r["brain_voxels"], "mean": r["mean"], "sd": r["sd"]} for r in norm},
            "regions": {r["region"]: {"voxels": r["voxels"], "volume_mm3": r["volume_mm3"], "volume_ml": r["volume_mm3"] / 1000.0,
                                      "included": bool(r["included"]), "note": r["note"]} for r in regions},
            "provenance": {r["key"]: r["value"] for r in prov}}


def long_rows(token: str) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute("SELECT sequence, region, family, name, value FROM feature_values WHERE case_id=? ORDER BY region, sequence, family, name", (token,)).fetchall()


def save_sensitivity(token: str, out: dict[str, Any]) -> None:
    with connect() as con:
        con.execute("DELETE FROM sensitivity_values WHERE case_id=?", (token,))
        con.execute("DELETE FROM sensitivity_runs WHERE case_id=?", (token,))
        con.executemany("INSERT INTO sensitivity_values (case_id, sequence, region, perturbation, family, name, value) VALUES (?,?,?,?,?,?,?)",
                        [(token, seq, region, tag, family, name, value) for seq, region, tag, family, name, value in out["rows"]])
        con.executemany("INSERT OR REPLACE INTO sensitivity_runs VALUES (?,?,?,?,?,?,?)",
                        [(token, seq, region, tag, voxels, status, note) for seq, region, tag, voxels, status, note in out["runs"]])


def load_sensitivity(token: str) -> dict[str, Any] | None:
    with connect() as con:
        runs = con.execute("SELECT sequence, region, perturbation, voxels, status, note FROM sensitivity_runs WHERE case_id=?", (token,)).fetchall()
        if not runs:
            return None
        rows = con.execute("SELECT sequence, region, perturbation, family, name, value FROM sensitivity_values WHERE case_id=?", (token,)).fetchall()
    values: dict[str, Any] = {}
    for r in rows:
        values.setdefault(r["sequence"], {}).setdefault(r["region"], {}).setdefault(r["perturbation"], {})[f"{r['family']}_{r['name']}"] = r["value"]
    return {"perturbations": list(C.PERTURBATIONS), "runs": [dict(r) for r in runs], "values": values}


# ------------------------------------------------------------------ jobs
def upsert_job(token: str, kind: str, **fields: Any) -> None:
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO jobs (case_id, kind, state) VALUES (?,?,?)", (token, kind, fields.get("state", "running")))
        cols = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE jobs SET {cols} WHERE case_id=? AND kind=?", (*fields.values(), token, kind))


def get_job(token: str, kind: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT state, progress, message, error FROM jobs WHERE case_id=? AND kind=?", (token, kind)).fetchone()
    return dict(row) if row else None


def purge_all_results() -> None:
    with connect() as con:
        for table in ("feature_values", "normalisation", "provenance", "sensitivity_values", "sensitivity_runs", "region_stats", "validation_reports", "jobs"):
            con.execute(f"DELETE FROM {table}")
        con.execute("UPDATE cases SET calc_status='none', calculated_at=NULL")
