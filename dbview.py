"""Read-only database viewer: see what NeuroVoxel has stored (cases, validation reports, feature values, jobs ...).

Open http://127.0.0.1:8000/db . The database is opened read-only, nothing can be changed from this page.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

import config as C

router = APIRouter()
HIDDEN = {"users", "sessions", "auth_events"}       # account tables (hashed passwords) are never shown


def _con() -> sqlite3.Connection:
    if not C.DATABASE.exists():
        raise HTTPException(404, "The database file does not exist yet. Open the Dataset page and index the dataset first.")
    con = sqlite3.connect(f"file:{C.DATABASE.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r["name"] for r in rows if r["name"] not in HIDDEN]


def _columns(con: sqlite3.Connection, table: str) -> list[dict]:
    return [{"name": r["name"], "type": r["type"], "key": bool(r["pk"])} for r in con.execute(f'PRAGMA table_info("{table}")')]


def _cell(v):
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, str) and len(v) > 400:
        return v[:400] + f"… ({len(v):,} characters)"
    return v


def _table(name: str, con: sqlite3.Connection) -> str:
    if not re.match(r"^[A-Za-z0-9_]+$", name) or name not in _tables(con):
        raise HTTPException(404, "Unknown table.")
    return name


@router.get("/db", include_in_schema=False)
def db_page() -> FileResponse:
    return FileResponse(C.STATIC / "dbview.html", headers={"Cache-Control": "no-store"})


@router.get("/api/db/overview")
def overview() -> dict:
    con = _con()
    try:
        tables = []
        for t in _tables(con):
            tables.append({"name": t, "rows": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0], "columns": _columns(con, t)})
        size = C.DATABASE.stat().st_size
        return {"file": C.DATABASE.name, "location": str(C.DATABASE), "size_mb": round(size / 2**20, 2), "tables": tables, "read_only": True,
                "sqlite_version": sqlite3.sqlite_version}
    finally:
        con.close()


@router.get("/api/db/table/{name}")
def table_rows(name: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), q: str = "", sort: str = "", desc: bool = False) -> dict:
    con = _con()
    try:
        t = _table(name, con)
        cols = _columns(con, t)
        names = [c["name"] for c in cols]
        where, args = "", []
        if q.strip():
            where = " WHERE " + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in names)
            args = [f"%{q.strip()}%"] * len(names)
        order = f' ORDER BY "{sort}" {"DESC" if desc else "ASC"}' if sort in names else ""
        total = con.execute(f'SELECT COUNT(*) FROM "{t}"{where}', args).fetchone()[0]
        rows = con.execute(f'SELECT * FROM "{t}"{where}{order} LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {"table": t, "columns": cols, "total": total, "limit": limit, "offset": offset, "rows": [[_cell(v) for v in r] for r in rows]}
    finally:
        con.close()


@router.get("/api/db/table/{name}/export.csv")
def table_csv(name: str, q: str = "") -> Response:
    con = _con()
    try:
        t = _table(name, con)
        names = [c["name"] for c in _columns(con, t)]
        where, args = "", []
        if q.strip():
            where = " WHERE " + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in names)
            args = [f"%{q.strip()}%"] * len(names)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(names)
        for r in con.execute(f'SELECT * FROM "{t}"{where}', args):
            w.writerow([f"<{len(v)} bytes>" if isinstance(v, bytes) else v for v in r])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="neurovoxel_{t}.csv"', "Cache-Control": "no-store"})
    finally:
        con.close()


# ---------------------------------------------------------------- reproducibility record (works with the old config/db)
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_CODE = ("app.py", "config.py", "db.py", "msd.py", "validation.py", "engine.py", "pipeline.py", "analysis.py", "report.py", "advanced.py", "jobs.py", "verify.py")


def _sha(p: Path):
    try:
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()
    except OSError:
        return None


def _txt(p: str) -> str:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return "unknown"


def _versions() -> dict:
    out = {}
    for mod in ("radiomics", "SimpleITK", "numpy", "scipy", "pandas", "nibabel", "fastapi"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "unknown")
        except Exception:
            out[mod] = "not installed"
    return out


def record() -> dict:
    files = {n: _sha(_ROOT / n) for n in _CODE}
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    cases = calc = 0
    try:
        con = _con()
        cases = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        try:
            calc = con.execute("SELECT COUNT(DISTINCT case_id) FROM feature_values").fetchone()[0]
        except sqlite3.Error:
            pass
        con.close()
    except HTTPException:
        pass
    ds = Path(C.DATASET_DIR)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_sha256": combined, "code_files_sha256": files,
        "dataset": {"folder": ds.name, "dataset_json_sha256": _sha(ds / "dataset.json"), "cases_indexed": cases, "cases_calculated": calc},
        "software": {**_versions(), "pyradiomics_commit": _txt("/etc/pyradiomics_commit"), "python": sys.version.split()[0], "platform": platform.platform()},
        "settings": {"bin_width": getattr(C, "BIN_WIDTH", None), "radiomics": getattr(C, "RADIOMICS_SETTINGS", None),
                     "regions": {k: list(v) for k, v in getattr(C, "REGIONS", {}).items()},
                     "normalisation": getattr(C, "NORMALISATION", "z-score on non-zero voxels, per sequence and per patient")},
        "note": "Same dataset.json hash + same code hash + same library versions + same settings gives the same feature values.",
    }


@router.get("/api/reproducibility")
def repro() -> dict:
    return record()


@router.get("/api/reproducibility.json")
def repro_download() -> Response:
    return Response(json.dumps(record(), indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="neurovoxel_reproducibility.json"', "Cache-Control": "no-store"})


@router.get("/api/reproducibility.csv")
def repro_csv() -> Response:
    rows = []

    def walk(p, v):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{p}.{k}" if p else k, x)
        else:
            rows.append((p, v))
    walk("", record())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item", "value"])
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="neurovoxel_reproducibility.csv"', "Cache-Control": "no-store"})
