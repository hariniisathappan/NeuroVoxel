"""NeuroVoxel v3.1: Case Explorer for the Medical Segmentation Decathlon Task01 (BraTS-derived brain tumour MRI).

Flow: dataset folder (read-only) -> index once -> filter cases -> Calculate PyRadiomics (4 sequences x 3 regions) ->
      compare sequences / sub-regions / patients / cohorts -> export + methods report.
No machine learning, no clinical claims, no synthetic values. Features are measurements, not scores.
"""
from __future__ import annotations

import csv
import io
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl.styles import Font, PatternFill

import analysis
import config as C
import db
import engine
import jobs
import msd
import pipeline
from report import methods_report


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="NeuroVoxel", version=C.APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
from advanced import router as advanced_router  # noqa: E402

app.include_router(advanced_router)
from verify import router as verify_router  # noqa: E402
from dbview import router as dbview_router  # noqa: E402

app.include_router(verify_router)
app.include_router(dbview_router)
from uploads import router as uploads_router  # noqa: E402

app.include_router(uploads_router)
app.mount("/static", StaticFiles(directory=C.STATIC), name="static")
DS = pipeline.DATASET


# ------------------------------------------------------------------ helpers
def _token(value: str) -> str:
    if not msd.CASE_RE.match(value or ""):
        raise HTTPException(400, "Invalid case id.")
    return value


def _case(token: str) -> dict:
    case = db.get_case(_token(token))
    if not case:
        raise HTTPException(404, "Unknown case. Index the dataset first.")
    return case


def _done_case(case_id: str) -> str:
    token = _token(case_id)
    if _case(token)["calc_status"] != "done":
        raise HTTPException(409, "PyRadiomics has not been calculated for this case yet.")
    return token


def _filters(p: dict) -> dict:
    keys = ("q", "dominant", "hemisphere", "size", "has_enhancing", "validation", "calculated",
            "min_whole", "max_whole", "min_enh", "max_enh", "min_core", "max_core", "min_edema", "max_edema")
    out = {}
    for k in keys:
        v = p.get(k)
        if v in (None, ""):
            continue
        out[k] = float(v) if k.startswith(("min_", "max_")) else v
    return out


# ------------------------------------------------------------------ pages and status
@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    return FileResponse(C.STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="#06b6d4"/></svg>'
    return Response(svg, media_type="image/svg+xml")


@app.get("/api/status")
def api_status() -> dict:
    v = engine.versions()
    summary = db.dataset_summary()
    return {"database": "connected", "cases": summary["cases"], "calculated": summary["calculated"],
            "dataset_found": (C.DATASET_DIR / "dataset.json").exists(), "dataset_dir": str(C.DATASET_DIR),
            "research_engine": "ready" if v["pyradiomics"] != "NOT INSTALLED" else "install requirements.txt", "versions": v,
            "settings": {"normalisation": C.NORMALISATION, "bin_width": C.BIN_WIDTH, "radiomics": C.RADIOMICS_SETTINGS,
                         "min_region_voxels": C.MIN_REGION_VOXELS, "regions": {k: list(x) for k, x in C.REGIONS.items()},
                         "max_batch": C.MAX_BATCH_CASES}}


# ------------------------------------------------------------------ 1. dataset index
@app.get("/api/dataset/summary")
def dataset_summary() -> dict:
    return {**db.dataset_summary(), "index_job": jobs.status(DS, "index"), "batch_job": jobs.status(DS, "batch"),
            "dataset_found": (C.DATASET_DIR / "dataset.json").exists(), "region_labels": C.REGION_LABELS,
            "dominant_labels": C.DOMINANT_LABELS, "hemisphere_note": C.HEMISPHERE_MIDLINE_NOTE}


@app.get("/api/dataset/charts")
def dataset_charts() -> dict:
    """One compact row per indexed case, for the overview charts."""
    cols = ("case_id", "dominant", "hemisphere", "index_status", "calc_status", "vol_whole_ml", "vol_core_ml", "vol_enh_ml",
            "vol_edema_ml", "vol_net_ml", "enh_pct", "edema_pct", "net_pct")
    with db.connect() as con:
        rows = con.execute(f"SELECT {', '.join(cols)} FROM cases").fetchall()
    return {"rows": [dict(zip(cols, r)) for r in rows]}


@app.post("/api/dataset/index")
def dataset_index(force: bool = Query(False)) -> dict:
    if not (C.DATASET_DIR / "dataset.json").exists():
        raise HTTPException(404, f"dataset.json not found in {C.DATASET_DIR}. Mount the Task01_BrainTumour folder (see README) and restart.")
    return jobs.start(DS, "index", lambda progress: pipeline.index_dataset(progress, force))


@app.post("/api/jobs/{kind}/cancel")
def cancel_job(kind: str) -> dict:
    if kind not in ("index", "batch"):
        raise HTTPException(404, "Unknown job.")
    jobs.cancel(DS, kind)
    return {"cancelled": True}


@app.get("/api/cases")
def list_cases(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), sort: str = "case_id", desc: bool = False,
               q: str = "", dominant: str = "", hemisphere: str = "", size: str = "", has_enhancing: str = "", validation: str = "",
               calculated: str = "", min_whole: str = "", max_whole: str = "", min_enh: str = "", max_enh: str = "",
               min_core: str = "", max_core: str = "", min_edema: str = "", max_edema: str = "") -> dict:
    f = _filters(dict(q=q, dominant=dominant, hemisphere=hemisphere, size=size, has_enhancing=has_enhancing, validation=validation,
                      calculated=calculated, min_whole=min_whole, max_whole=max_whole, min_enh=min_enh, max_enh=max_enh,
                      min_core=min_core, max_core=max_core, min_edema=min_edema, max_edema=max_edema))
    return db.query_cases(f, limit, offset, sort, desc)


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict:
    case = _case(case_id)
    return {"case": case, "report": db.get_report(case["case_id"]), "job": jobs.status(case["case_id"], "extract")}


# ------------------------------------------------------------------ 2. calculation jobs
@app.post("/api/cases/{case_id}/calculate")
def calculate(case_id: str) -> dict:
    token = _token(case_id)
    case = _case(token)
    if case["index_status"] == "fail":
        raise HTTPException(422, f"This case failed the index checks: {case['index_note']}")
    if case["calc_status"] == "done":
        return {"state": "done", "progress": 1.0, "message": "Complete", "error": None}
    return jobs.start(token, "extract", lambda progress: pipeline.calculate_case(token, progress))


@app.post("/api/cases/calculate-batch")
def calculate_batch(filters: dict = Body(default={}), limit: int = Query(20, ge=1)) -> dict:
    cap = min(limit, C.MAX_BATCH_CASES)
    ids = [i for i in db.matching_ids(_filters(filters), only_uncalculated=True, cap=cap * 3)
           if db.get_case(i)["index_status"] != "fail"][:cap]
    if not ids:
        raise HTTPException(409, "No uncalculated, valid cases match the current filters.")
    job = jobs.start(DS, "batch", lambda progress: pipeline.batch_calculate(ids, progress))
    return {**job, "queued": len(ids), "cap": cap}


@app.get("/api/cases/{case_id}/job/{kind}")
def job_status(case_id: str, kind: str) -> dict:
    if kind not in ("extract", "sensitivity"):
        raise HTTPException(404, "Unknown job.")
    return jobs.status(_token(case_id), kind)


@app.post("/api/cases/{case_id}/sensitivity/start")
def sensitivity_start(case_id: str) -> dict:
    token = _done_case(case_id)
    return jobs.start(token, "sensitivity", lambda progress: pipeline.run_sensitivity(token, progress))


# ------------------------------------------------------------------ clean-up
@app.get("/api/storage")
def storage() -> dict:
    items = pipeline.staged_cases()
    return {"staged_cases": len(items), "staged_mb": round(sum(i[2] for i in items) / 2**20, 1),
            "database_mb": round(C.DATABASE.stat().st_size / 2**20, 2) if C.DATABASE.exists() else 0}


@app.post("/api/storage/cleanup")
def storage_cleanup(everything: bool = Query(False)) -> dict:
    return pipeline.cleanup_staged(keep=0 if everything else 6, everything=everything)


@app.delete("/api/cases/{case_id}/results")
def delete_results(case_id: str) -> dict:
    token = _token(case_id)
    _case(token)
    if jobs.status(token, "extract")["state"] == "running":
        raise HTTPException(409, "This case is being calculated.")
    db.clear_results(token)
    import shutil
    shutil.rmtree(C.UPLOADS / token, ignore_errors=True)
    return {"deleted": token}


@app.delete("/api/results")
def delete_all_results() -> dict:
    if jobs.status(DS, "batch")["state"] == "running":
        raise HTTPException(409, "A batch is running. Stop it first.")
    db.purge_all_results()
    return {"cleanup": pipeline.cleanup_staged(everything=True)}


# ------------------------------------------------------------------ cohort analysis
@app.post("/api/cohort/percentiles")
def cohort_percentiles(case_id: str = Body(...), region: str = Body("whole_tumour")) -> dict:
    if region not in C.REGIONS:
        raise HTTPException(400, "Unknown region.")
    return analysis.percentiles(_done_case(case_id), region)


@app.post("/api/cohort/compare")
def cohort_compare(group_a: dict = Body(default={}), group_b: dict = Body(default={})) -> dict:
    """Groups are two filter sets (evaluated among calculated cases only), e.g. size=large vs size=small."""
    ids = []
    for g in (group_a, group_b):
        f = _filters(g)
        f["calculated"] = "yes"
        ids.append([r["case_id"] for r in db.query_cases(f, limit=100000)["rows"]])
    result = analysis.compare_groups(ids[0], ids[1])
    if not result.get("ok"):
        return result
    return {**result, "ranking": analysis.rank_sets(result)}


@app.get("/api/cohort/feature")
def cohort_feature(region: str = "whole_tumour", feature: str = "firstorder_Mean") -> dict:
    return analysis.feature_table(region, feature)


@app.get("/api/cohort/features")
def cohort_features() -> dict:
    return {"features": analysis.feature_catalogue(), "n": db.count_cases()}


@app.post("/api/compare/cases")
def compare_cases(case_ids: list[str] = Body(...), region: str = Body("whole_tumour")) -> dict:
    """Side-by-side feature values of 2-4 calculated cases for one region, with their percentile among calculated cases."""
    if not 2 <= len(case_ids) <= 60:
        raise HTTPException(400, "Choose 2 to 60 cases.")
    ids = [_done_case(i) for i in case_ids]
    out = {"region": region, "cases": {}, "n_cohort": db.count_cases()}
    for i in ids:
        res = db.load_features(i)
        out["cases"][i] = {"features": {s: res["features"].get(s, {}).get(region) for s in C.SEQUENCES},
                           "shape": res["shape"].get(region), "info": db.get_case(i),
                           "percentiles": analysis.percentiles(i, region)["values"]}
    return out


# ------------------------------------------------------------------ 3. results
@app.get("/api/cases/{case_id}/results")
def results(case_id: str) -> dict:
    token = _done_case(case_id)
    case, res = _case(token), db.load_features(token)
    return {"case_id": token, "calculated_at": case["calculated_at"], "case": case, "report": db.get_report(token),
            "regions": res["regions"], "normalisation": res["normalisation"], "features": res["features"], "shape": res["shape"],
            "provenance": res["provenance"], "sensitivity_ready": db.load_sensitivity(token) is not None,
            "settings": {"normalisation": C.NORMALISATION, "bin_width": C.BIN_WIDTH, "min_region_voxels": C.MIN_REGION_VOXELS},
            "region_labels": C.REGION_LABELS}


@app.get("/api/cases/{case_id}/sensitivity")
def sensitivity_results(case_id: str) -> dict:
    data = db.load_sensitivity(_done_case(case_id))
    if data is None:
        raise HTTPException(404, "Boundary sensitivity has not been run for this case.")
    return data


@app.get("/api/cases/{case_id}/distribution")
def distribution(case_id: str) -> dict:
    token = _token(case_id)
    _case(token)
    pipeline.stage_for_viewing(token)
    return engine.distributions(token)


@app.get("/api/cases/{case_id}/volume/{name}")
def volume(case_id: str, name: str) -> FileResponse:
    """Staged volumes for the viewer: t1, t1ce, t2, flair, seg, or a region mask. Works for any indexed case (re-staged on demand)."""
    token = _token(case_id)
    _case(token)
    base = re.sub(r"\.nii(\.gz)?$", "", name)
    if base not in C.SLOTS and base not in C.REGIONS:
        raise HTTPException(404, "Unknown volume.")
    pipeline.stage_for_viewing(token)
    if base in C.SLOTS:
        path = engine.case_paths(token).get(base)
    else:
        path = engine.mask_file(token, base)
        if not path.exists():
            engine.prepare_masks(token, [base])
    if not path or not Path(path).exists():
        raise HTTPException(404, "The file could not be prepared (region may be empty).")
    return FileResponse(path, media_type="application/gzip", filename=f"{token}_{base}.nii.gz")


# ------------------------------------------------------------------ 4. exports
@app.get("/api/cases/{case_id}/download/segmentation")
def download_segmentation(case_id: str) -> FileResponse:
    token = _token(case_id)
    _case(token)
    pipeline.stage_for_viewing(token)
    path = engine.case_paths(token).get("seg")
    if not path:
        raise HTTPException(404, "The segmentation is no longer stored.")
    return FileResponse(path, media_type="application/gzip", filename="NeuroVoxel_Segmentation.nii.gz")


@app.get("/api/cases/{case_id}/export.csv")
def export_csv(case_id: str) -> Response:
    token = _done_case(case_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["case_id", "sequence", "region", "feature_family", "feature_name", "value"])
    for r in db.long_rows(token):
        writer.writerow([token, r["sequence"], r["region"], r["family"], r["name"], repr(float(r["value"]))])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="NeuroVoxel_features_long.csv"', "Cache-Control": "no-store"})


@app.get("/api/cases/{case_id}/report.md")
def export_report(case_id: str) -> Response:
    token = _done_case(case_id)
    return Response(methods_report(token), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="NeuroVoxel_methods_report.md"', "Cache-Control": "no-store"})


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame({"note": ["no data"]})


@app.get("/api/cases/{case_id}/export.xlsx")
def export_xlsx(case_id: str) -> StreamingResponse:
    token = _done_case(case_id)
    case, report, res, sens = _case(token), db.get_report(token), db.load_features(token), db.load_sensitivity(token)
    seq_name = C.SEQUENCE_LABELS
    book = io.BytesIO()
    with pd.ExcelWriter(book, engine="openpyxl") as w:
        _frame([{"Item": "Case ID", "Value": token}, {"Item": "Calculated (UTC)", "Value": case["calculated_at"]},
                {"Item": "NeuroVoxel", "Value": case["app_version"]}, {"Item": "Use", "Value": "Research only. No diagnosis or prediction."},
                {"Item": "Normalisation", "Value": C.NORMALISATION}, {"Item": "Bin width (z units)", "Value": C.BIN_WIDTH},
                {"Item": "Validation", "Value": f"{report['status'].upper()} - {C.WORDING}"},
                {"Item": "Sheets", "Value": "VALIDATION, REGIONS, SHAPE_MASK_ONLY, SEQ_*, COMPARE_*, ALL_FEATURES_LONG, SENSITIVITY, PROVENANCE"}]).to_excel(w, sheet_name="README", index=False)
        _frame([{"Check": c["title"], "Status": c["status"].upper(), "Detail": c["detail"]} for c in report["checks"]]).to_excel(w, sheet_name="VALIDATION", index=False)
        _frame([{"Region": C.REGION_LABELS[k], "Labels": str(list(C.REGIONS[k])), "Voxels": v["voxels"], "Volume (mL)": v["volume_ml"],
                 "Analysed": "yes" if v["included"] else "no: " + v["note"]} for k, v in res["regions"].items()]).to_excel(w, sheet_name="REGIONS", index=False)
        names = sorted({n for r in res["shape"].values() for n in r})
        _frame([{"Feature": n, **{C.REGION_LABELS[r]: res["shape"][r].get(n) for r in res["shape"]}} for n in names]).to_excel(w, sheet_name="SHAPE_MASK_ONLY", index=False)
        for s in C.SEQUENCES:
            data = res["features"].get(s, {})
            keys = sorted({(f, n) for reg in data.values() for f, vals in reg.items() for n in vals})
            _frame([{"Family": f, "Feature": n, **{C.REGION_LABELS[r]: data[r].get(f, {}).get(n) for r in data}} for f, n in keys]).to_excel(w, sheet_name=f"SEQ_{seq_name[s].upper()}"[:31], index=False)
        short = {"whole_tumour": "WHOLE", "tumour_core": "CORE", "enhancing_tumour": "ENHANCING"}
        for region in res["regions"]:
            if not res["regions"][region]["included"]:
                continue
            keys = sorted({(f, n) for s in C.SEQUENCES for f, vals in res["features"].get(s, {}).get(region, {}).items() for n in vals})
            rows = []
            for f, n in keys:
                vals = {seq_name[s]: res["features"].get(s, {}).get(region, {}).get(f, {}).get(n) for s in C.SEQUENCES}
                got = [v for v in vals.values() if v is not None]
                rows.append({"Family": f, "Feature": n, **vals, "Max - min": (max(got) - min(got)) if got else None})
            _frame(rows).to_excel(w, sheet_name=f"COMPARE_{short[region]}", index=False)
        _frame([{"case_id": token, "sequence": r["sequence"], "region": r["region"], "feature_family": r["family"], "feature_name": r["name"], "value": r["value"]}
                for r in db.long_rows(token)]).to_excel(w, sheet_name="ALL_FEATURES_LONG", index=False)
        if sens:
            rows = []
            for s, regs in sens["values"].items():
                for region, perts in regs.items():
                    base = (res["shape"].get(region, {}) and {f"shape_{k}": v for k, v in res["shape"][region].items()}) if s == "mask" else \
                        {f"{f}_{n}": v for f, vals in res["features"].get(s, {}).get(region, {}).items() for n, v in vals.items()}
                    for tag, feats in perts.items():
                        for key, value in feats.items():
                            b = base.get(key)
                            rows.append({"sequence": s, "region": region, "perturbation": C.PERTURBATION_LABELS[tag], "feature": key, "baseline": b, "perturbed": value,
                                         "abs_change": None if b is None else value - b,
                                         "rel_change_pct": None if b is None or abs(b) < 1e-12 else abs(value - b) / abs(b) * 100})
            rows += [{"sequence": r["sequence"], "region": r["region"], "perturbation": C.PERTURBATION_LABELS[r["perturbation"]], "feature": "(region status)",
                      "baseline": None, "perturbed": None, "abs_change": None, "rel_change_pct": None, "note": r["note"]} for r in sens["runs"] if r["status"] != "ok"]
            _frame(rows).to_excel(w, sheet_name="SENSITIVITY", index=False)
        _frame([{"Key": k, "Value": v} for k, v in res["provenance"].items()]).to_excel(w, sheet_name="PROVENANCE", index=False)
        for sheet in w.book.worksheets:
            for cell in sheet[1]:
                cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0F766E")
            sheet.freeze_panes = "A2"
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = min(60, max(14, max(len(str(c.value or "")) for c in column[:200]) + 2))
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.000000"
    book.seek(0)
    return StreamingResponse(book, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="NeuroVoxel_Radiomics_Report.xlsx"', "Cache-Control": "no-store"})
