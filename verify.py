"""Verification tab of NeuroVoxel: re-checks the stored numbers of one case in four independent ways.

  1. Independent re-run      raw MSD files -> own channel split / label mapping / z-score -> plain PyRadiomics -> compare every stored value
  2. Textbook formulas       first-order features recomputed with numpy only
  3. Official baseline       PyRadiomics on its own published brain MRI test cases, compared with the project's official baseline values
  4. Known shapes            a sphere and an ellipsoid whose volume / area / axes are known from mathematics

Used by the web page (routes below) and nothing else; the stand-alone script is tools/verify_against_standard.py.
"""
from __future__ import annotations

import ast
import csv
import io
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import SimpleITK as sitk
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import config as C
import db
import jobs

router = APIRouter()
ROOT = Path(__file__).resolve().parent
BRAIN = ROOT / "reference" / "pyradiomics_brain"
REL_TOL = 1e-6          # independent re-run: same library + same inputs
NUMPY_TOL = 1e-4        # numpy (float64) against PyRadiomics (float32 image): small rounding differences only
BASELINE_TOL_PCT = 3.0  # the PyRadiomics project's own acceptance limit for its test suite
Progress = Any


def _tool():
    """The stand-alone verification script is reused as a library (same code path as when it is run from the command line)."""
    import sys
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)      # also lets the worker processes (spawn) import it
    import verify_against_standard
    return verify_against_standard


# ------------------------------------------------------------------ 1. independent re-run
def check_independent(case: str, site: dict, progress: Progress) -> tuple[dict, list]:
    tool = _tool()
    progress(0.05, "Independent re-run: reading the raw MSD files")
    mine, norm, sizes = tool.part_b(case, C.DATASET_DIR, jobs=max(1, min(C.MAX_WORKERS, 4)))
    progress(0.55, "Independent re-run: comparing every value")
    rows = tool.compare(site, mine, sizes)
    compared = [r for r in rows if r[4] is not None]
    missing = [r for r in rows if r[4] is None]
    identical = sum(1 for r in compared if r[6] == 0)
    within = sum(1 for r in compared if r[8] == "ok")
    rels = [r[7] for r in compared]
    worst = sorted(compared, key=lambda r: -r[7])[:5]
    channels_raw, labels = tool.read_dataset_json(C.DATASET_DIR)
    names = _json(C.DATASET_DIR / "dataset.json").get("modality", {})
    out = {
        "compared": len(compared), "identical": identical, "within_tolerance": within, "tolerance": REL_TOL,
        "max_rel_diff": float(max(rels)) if rels else 0.0, "pass": bool(compared) and within == len(compared) and not missing,
        "note": ("The raw 4D MSD file is read directly, split with the channel order from dataset.json, regions are built from the MSD label numbers, "
                 "each sequence is z-scored on its non-zero voxels, and plain PyRadiomics is run with the settings recorded in this app "
                 f"(bin width {C.BIN_WIDTH}, no resampling). {len(missing)} value(s) missing on the website."),
        "mapping": {"channels": {k: f"channel {i} ({names.get(str(i), '?')})" for i, k in channels_raw.items()}, "labels": labels},
        "errors": [f"{r[0]}/{r[1]}/{r[2]}_{r[3]} missing on website" for r in missing[:5]],
        "worst": [{"feature": f"{r[0]}|{r[1]}|{r[2]}_{r[3]}", "rel_diff": float(r[7])} for r in worst],
    }
    csv_rows = [["independent_rerun", r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]] for r in rows]
    return out, csv_rows, norm


def _json(path: Path) -> dict:
    import json
    return json.loads(path.read_text())


# ------------------------------------------------------------------ 2. textbook formulas (numpy only)
def _first_order(x: np.ndarray) -> dict[str, float]:
    x = x.astype(np.float64)
    mean = x.mean()
    d = x - mean
    m2, m3, m4 = (d ** 2).mean(), (d ** 3).mean(), (d ** 4).mean()
    p10, p25, p75, p90 = np.percentile(x, [10, 25, 75, 90])
    sub = x[(x >= p10) & (x <= p90)]
    return {
        "Mean": mean, "Median": float(np.median(x)), "Minimum": x.min(), "Maximum": x.max(), "Range": x.max() - x.min(),
        "Variance": m2, "Skewness": m3 / m2 ** 1.5 if m2 > 0 else 0.0, "Kurtosis": m4 / m2 ** 2 if m2 > 0 else 0.0,
        "Energy": float((x ** 2).sum()), "RootMeanSquared": math.sqrt((x ** 2).mean()),
        "10Percentile": p10, "90Percentile": p90, "InterquartileRange": p75 - p25,
        "MeanAbsoluteDeviation": float(np.abs(d).mean()), "RobustMeanAbsoluteDeviation": float(np.abs(sub - sub.mean()).mean()),
    }


def check_numpy(case: str, site: dict, progress: Progress) -> tuple[dict, list]:
    progress(0.6, "Textbook formulas: recomputing first-order features with numpy")
    tool = _tool()
    channels, labels = tool.read_dataset_json(C.DATASET_DIR)
    img = nib.load(str(C.DATASET_DIR / "imagesTr" / f"{case}.nii.gz"))
    lab = np.rint(np.asarray(nib.load(str(C.DATASET_DIR / "labelsTr" / f"{case}.nii.gz")).dataobj)).astype(np.int16)
    region_labels = {"whole_tumour": [labels["edema"], labels["net"], labels["enhancing"]], "tumour_core": [labels["net"], labels["enhancing"]],
                     "enhancing_tumour": [labels["enhancing"]]}
    seq = "flair"
    region = next((r for r in ("whole_tumour", "tumour_core", "enhancing_tumour")
                   if (site.get("features", {}).get(seq) or {}).get(r, {}).get("firstorder")), "whole_tumour")
    idx = next(i for i, k in channels.items() if k == seq)
    vol = np.asarray(img.dataobj[..., idx], dtype=np.float64)
    nz = vol != 0
    z = np.zeros_like(vol)
    z[nz] = (vol[nz] - vol[nz].mean()) / vol[nz].std()
    values = z[np.isin(lab, region_labels[region])]
    mine = _first_order(values)
    stored = ((site.get("features", {}).get(seq) or {}).get(region) or {}).get("firstorder", {})
    rows, csv_rows = [], []
    for name, v in mine.items():
        s = stored.get(name)
        if s is None:
            continue
        rel = abs(v - s) / max(abs(v), abs(s), 1e-12)
        rows.append({"feature": f"firstorder_{name}", "numpy": float(v), "stored": float(s), "rel_diff": float(rel)})
        csv_rows.append(["textbook_numpy", seq, region, "firstorder", name, s, float(v), abs(v - s), rel, "ok" if rel <= NUMPY_TOL else "DIFFERENT"])
    mx = max((r["rel_diff"] for r in rows), default=0.0)
    return {"sequence": seq, "region": region, "voxels": int(values.size), "rows": rows, "max_rel_diff": mx, "pass": bool(rows) and mx <= NUMPY_TOL,
            "note": ("Mean, median, variance, skewness, kurtosis, energy, percentiles and deviations are computed here from their textbook definitions "
                     "(population variance, non-excess kurtosis as PyRadiomics defines it) and compared with the values stored by PyRadiomics.")}, csv_rows


# ------------------------------------------------------------------ 3. PyRadiomics' official baseline
def check_baseline(progress: Progress) -> tuple[dict, list]:
    from radiomics import featureextractor
    import logging
    logging.getLogger("radiomics").setLevel(logging.ERROR)
    classes = ["firstorder", "glcm", "gldm", "glrlm", "glszm", "ngtdm", "shape"]
    if not (BRAIN / "brain1_image.nrrd").exists():
        return {"rows": [], "tolerance_pct": BASELINE_TOL_PCT, "pass": False,
                "note": f"Reference files not found in {BRAIN}. Copy the folder reference/pyradiomics_brain into the project and rebuild."}, []
    rows, csv_rows = [], []
    for k, case in enumerate(("brain1", "brain2")):
        image = sitk.ReadImage(str(BRAIN / f"{case}_image.nrrd"))
        mask = sitk.ReadImage(str(BRAIN / f"{case}_label.nrrd"), sitk.sitkUInt32)
        for n, cls in enumerate(classes):
            progress(0.7 + 0.22 * (k * len(classes) + n) / (2 * len(classes)), f"Official baseline: {case} / {cls}")
            table = list(csv.reader(open(BRAIN / f"baseline_{cls}.csv", newline="")))
            col = table[0].index(case)
            settings = ast.literal_eval(next(r for r in table if r[0] == "diagnostics_Configuration_Settings")[col])
            base = {r[0]: r[col] for r in table if r[0].startswith("original_")}
            ex = featureextractor.RadiomicsFeatureExtractor(**settings)
            ex.disableAllImageTypes()
            ex.enableImageTypeByName("Original")
            ex.disableAllFeatures()
            ex.enableFeatureClassByName(cls)
            got = ex.execute(image, mask, label=1)
            n_feat = n_same = 0
            worst = 0.0
            for key, ref in base.items():
                if key not in got:
                    continue
                try:
                    r, v = float(ref), float(got[key])
                except (TypeError, ValueError):
                    continue
                pct = (0.0 if v == r else 100.0) if r == 0 else abs(1.0 - v / r) * 100.0
                n_feat += 1
                n_same += int(pct < 1e-6)
                worst = max(worst, pct)
                csv_rows.append(["official_baseline", case, "", cls, key.replace(f"original_{cls}_", ""), r, v, abs(v - r), pct / 100.0, "ok" if pct < BASELINE_TOL_PCT else "DIFFERENT"])
            rows.append({"case": case, "class": cls, "features": n_feat, "identical": n_same, "max_pct_diff": float(worst)})
    ok = bool(rows) and all(r["max_pct_diff"] < BASELINE_TOL_PCT for r in rows)
    return {"rows": rows, "tolerance_pct": BASELINE_TOL_PCT, "pass": ok,
            "note": ("PyRadiomics' own test suite runs the software on two published brain MRI cases (brain1, brain2) and requires every feature to stay within "
                     "3 % of stored baseline values. The same is done here with the PyRadiomics that is installed in this app, using the settings stored in the baseline.")}, csv_rows


# ------------------------------------------------------------------ 4. shapes with known answers
def _solid(axes: tuple[float, float, float], n: int = 80) -> tuple[sitk.Image, sitk.Image]:
    z, y, x = np.mgrid[:n, :n, :n].astype(np.float64)
    c = (n - 1) / 2
    inside = ((x - c) / axes[0]) ** 2 + ((y - c) / axes[1]) ** 2 + ((z - c) / axes[2]) ** 2 <= 1.0
    mask = sitk.GetImageFromArray(inside.astype(np.uint8))
    image = sitk.GetImageFromArray(np.ones_like(inside, dtype=np.float32))
    image.CopyInformation(mask)
    return image, mask


def check_geometry(progress: Progress) -> tuple[dict, list]:
    from radiomics import featureextractor
    import logging
    logging.getLogger("radiomics").setLevel(logging.ERROR)
    progress(0.94, "Known shapes: sphere and ellipsoid")
    ex = featureextractor.RadiomicsFeatureExtractor(binWidth=0.25, resampledPixelSpacing=None, label=1)
    ex.disableAllImageTypes()
    ex.enableImageTypeByName("Original")
    ex.disableAllFeatures()
    ex.enableFeatureClassByName("shape")
    rows, csv_rows = [], []
    s5 = math.sqrt(5)
    # (name, semi-axes in mm, expected features, tolerance %). Axis lengths in PyRadiomics are 4 * sqrt(eigenvalue); for a solid ellipsoid
    # the variance along a semi-axis a is a^2 / 5, so the expected axis length is 4a / sqrt(5).
    cases = [("sphere r=18 mm", (18, 18, 18)), ("ellipsoid 26 x 18 x 11 mm", (26, 18, 11))]
    for title, a in cases:
        image, mask = _solid(a)
        got = ex.execute(image, mask, label=1)
        A, B, Cc = sorted(a, reverse=True)
        vol = 4 / 3 * math.pi * a[0] * a[1] * a[2]
        exp = [("VoxelVolume", vol, 2.0), ("MeshVolume", vol, 2.0), ("Maximum3DDiameter", 2 * A, 3.0),
               ("MajorAxisLength", 4 * A / s5, 2.0), ("MinorAxisLength", 4 * B / s5, 2.0), ("LeastAxisLength", 4 * Cc / s5, 2.0),
               ("Elongation", B / A, 2.0), ("Flatness", Cc / A, 2.0)]
        if a[0] == a[1] == a[2]:
            exp += [("SurfaceArea", 4 * math.pi * a[0] ** 2, 10.0), ("Sphericity", 1.0, 10.0)]
        for name, e, tol in exp:
            v = float(got[f"original_shape_{name}"])
            pct = abs(v - e) / e * 100.0
            rows.append({"check": f"{title}: {name}", "expected": float(e), "got": v, "rel_diff_pct": float(pct), "tolerance_pct": tol, "pass": bool(pct <= tol)})
            csv_rows.append(["known_shapes", title, "", "shape", name, e, v, abs(v - e), pct / 100.0, "ok" if pct <= tol else "DIFFERENT"])
    return {"rows": rows, "pass": all(r["pass"] for r in rows),
            "note": ("Solid shapes on a 1 mm grid, so the true answer is known from mathematics. Limits allow for the staircase error of a voxel grid "
                     "(about 2 % for volume and axes, 3 % for diameter). The surface area of a curved shape built from voxels is over-estimated by the mesh "
                     "(about 9 % for this sphere), which also lowers sphericity, so those two get a 10 % limit; the IBSI phantom check covers surface area exactly.")}, csv_rows


# ------------------------------------------------------------------ the whole run
def run(case: str, progress: Progress) -> None:
    c = db.get_case(case)
    site = db.load_features(case)
    ind, rows_a, _norm = check_independent(case, site, progress)
    npy, rows_b = check_numpy(case, site, progress)
    base, rows_c = check_baseline(progress)
    geo, rows_d = check_geometry(progress)
    import SimpleITK
    from radiomics import __version__ as pr_version
    result = {
        "ready": True, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "calculated_at": c["calculated_at"],
        "versions": {"pyradiomics": pr_version, "SimpleITK": SimpleITK.Version_VersionString(), "numpy": np.__version__},
        "independent": ind, "numpy": npy, "baseline": base, "geometry": geo,
        "pass": bool(ind["pass"] and npy["pass"] and base["pass"] and geo["pass"]),
        "csv": rows_a + rows_b + rows_c + rows_d,
    }
    db.set_meta(f"verify:{case}", result)
    progress(1.0, "Verification finished")


def _case_id(value: str) -> str:
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", value or ""):
        raise HTTPException(400, "Invalid case id.")
    c = db.get_case(value)
    if not c:
        raise HTTPException(404, "Unknown case. Index the dataset first.")
    return value


@router.get("/api/cases/{case_id}/verify")
def verify_result(case_id: str) -> dict:
    case = _case_id(case_id)
    saved = db.get_meta(f"verify:{case}")
    if not saved:
        return {"ready": False}
    out = {k: v for k, v in saved.items() if k != "csv"}
    out["stale"] = saved.get("calculated_at") != db.get_case(case)["calculated_at"]
    return out


@router.post("/api/cases/{case_id}/verify/start")
def verify_start(case_id: str) -> dict:
    case = _case_id(case_id)
    if db.get_case(case)["calc_status"] != "done":
        raise HTTPException(409, "PyRadiomics has not been calculated for this case yet. Calculate it first.")
    return jobs.start(case, "verify", lambda progress: run(case, progress))


@router.get("/api/cases/{case_id}/job/verify")
def verify_job(case_id: str) -> dict:
    return jobs.status(_case_id(case_id), "verify")


@router.get("/api/cases/{case_id}/verify.csv")
def verify_csv(case_id: str) -> Response:
    case = _case_id(case_id)
    saved = db.get_meta(f"verify:{case}")
    if not saved:
        raise HTTPException(404, "Run the verification first.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["check", "sequence_or_case", "region", "feature_family", "feature", "reference_or_website", "recomputed", "abs_diff", "rel_diff", "status"])
    w.writerows(saved["csv"])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="NeuroVoxel_verification_{case}.csv"', "Cache-Control": "no-store"})
