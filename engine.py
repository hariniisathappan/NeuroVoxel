"""Deterministic PyRadiomics extraction for NeuroVoxel: 4 sequences x 3 BraTS regions, plus boundary sensitivity.

Design rules
  * Every sequence is normalised the same way (z-score on its non-zero brain voxels) and discretised with the same
    fixed bin width. Settings live in config.py and are recorded with every result.
  * Shape features depend only on the mask, so they are computed once per region (stored under the pseudo-sequence "mask").
  * Heavy work runs in separate worker processes (CPU only). Each task opens its own files, so nothing large is pickled.
"""
from __future__ import annotations

import logging
import math
import multiprocessing as mp
import platform
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import SimpleITK as sitk

import config as C

Progress = Callable[[float, str], None]
MASK_SEQ = "mask"          # pseudo-sequence under which mask-only (shape) features are stored


# ------------------------------------------------------------------ small helpers
def versions() -> dict[str, str]:
    info = {"python": platform.python_version(), "numpy": np.__version__, "SimpleITK": sitk.Version_VersionString(),
            "neurovoxel": C.APP_VERSION}
    try:
        import radiomics
        info["pyradiomics"] = str(getattr(radiomics, "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        info["pyradiomics"] = "NOT INSTALLED"
    return info


def zscore_array(arr: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """z-score computed on non-zero voxels only; the zero background stays exactly zero."""
    a = arr.astype(np.float64)
    nz = a != 0
    if not nz.any():
        raise ValueError("image has no non-zero voxels")
    mean, sd = float(a[nz].mean()), float(a[nz].std())
    if sd < 1e-12:
        raise ValueError("image is constant inside the brain")
    out = np.zeros_like(a)
    out[nz] = (a[nz] - mean) / sd
    return out, {"brain_voxels": int(nz.sum()), "mean": mean, "sd": sd}


def _like(arr: np.ndarray, template: sitk.Image, dtype=None) -> sitk.Image:
    img = sitk.GetImageFromArray(arr if dtype is None else arr.astype(dtype))
    img.CopyInformation(template)
    return img


def case_paths(token: str) -> dict[str, Path]:
    """Stored files of a case, by slot."""
    out = {}
    for slot in C.SLOTS:
        p = C.UPLOADS / token / f"{slot}.nii.gz"
        if p.exists():
            out[slot] = p
    return out


def masks_dir(token: str) -> Path:
    return C.UPLOADS / token / "masks"


def mask_file(token: str, region: str, tag: str = "base") -> Path:
    return masks_dir(token) / f"{region}__{tag}.nii.gz"


def read_labels(path: Path) -> tuple[np.ndarray, sitk.Image]:
    img = sitk.ReadImage(str(path))
    return np.rint(sitk.GetArrayFromImage(img)).astype(np.int32), img


def prepare_masks(token: str, regions: list[str], perturb: bool = False) -> dict[tuple[str, str], dict[str, Any]]:
    """Write the binary mask of each region (and optionally its eroded/dilated versions) next to the case files."""
    paths = case_paths(token)
    lab, template = read_labels(paths["seg"])
    masks_dir(token).mkdir(parents=True, exist_ok=True)
    brain = None
    if perturb:
        brain = np.zeros(lab.shape, bool)
        for s in C.SEQUENCES:
            brain |= sitk.GetArrayFromImage(sitk.ReadImage(str(paths[s]))) != 0
    made: dict[tuple[str, str], dict[str, Any]] = {}
    for region in regions:
        base = np.isin(lab, C.REGIONS[region])
        variants = {"base": base}
        if perturb:
            b = _like(base, template, np.uint8)
            variants["erode1"] = sitk.GetArrayFromImage(sitk.BinaryErode(b, [1, 1, 1], sitk.sitkBall)).astype(bool)
            variants["dilate1"] = sitk.GetArrayFromImage(sitk.BinaryDilate(b, [1, 1, 1], sitk.sitkBall)).astype(bool) & brain
        for tag, arr in variants.items():
            target = mask_file(token, region, tag)
            n = int(arr.sum())
            if n:
                sitk.WriteImage(_like(arr, template, np.uint8), str(target), useCompression=True)
            made[(region, tag)] = {"path": str(target), "voxels": n}
    return made


# ------------------------------------------------------------------ worker-process task
def make_extractor(classes: tuple[str, ...]):
    from radiomics import featureextractor

    logging.getLogger("radiomics").setLevel(logging.ERROR)
    extractor = featureextractor.RadiomicsFeatureExtractor(**C.RADIOMICS_SETTINGS)
    extractor.disableAllImageTypes()
    extractor.enableImageTypeByName("Original")
    extractor.disableAllFeatures()
    for name in classes:
        extractor.enableFeatureClassByName(name)
    return extractor


def flatten(result: dict) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = {}
    for key, value in result.items():
        if not key.startswith("original_"):
            continue
        _, family, name = key.split("_", 2)
        try:
            if isinstance(value, complex) or np.iscomplexobj(value):
                if abs(complex(value).imag) > 1e-9:
                    continue
                value = complex(value).real
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            grouped.setdefault(family.lower(), {})[name] = number
    return grouped


def extract_task(spec: dict[str, Any]) -> dict[str, Any]:
    """spec: id, label, image, mask, classes, zscore. Runs in a worker process; never raises."""
    try:
        image = sitk.ReadImage(spec["image"])
        mask = sitk.ReadImage(spec["mask"])
        if spec["zscore"]:
            z, _ = zscore_array(sitk.GetArrayFromImage(image))
            image = _like(z, image)
        result = make_extractor(tuple(spec["classes"])).execute(image, mask, label=1)
        keep = ("diagnostics_Versions_", "diagnostics_Image-original_Hash", "diagnostics_Mask-original_VoxelNum",
                "diagnostics_Configuration_Settings")
        diagnostics = {k: str(v) for k, v in result.items() if k.startswith(keep)}
        return {"id": spec["id"], "ok": True, "features": flatten(result), "diagnostics": diagnostics}
    except Exception as error:  # noqa: BLE001 - reported per task
        return {"id": spec["id"], "ok": False, "error": f"{error.__class__.__name__}: {error}"}


def run_tasks(tasks: list[dict[str, Any]], progress: Progress, start: float, span: float) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not tasks:
        return results
    with ProcessPoolExecutor(max_workers=max(1, min(C.MAX_WORKERS, len(tasks))), mp_context=mp.get_context("spawn")) as pool:
        futures = {pool.submit(extract_task, t): t for t in tasks}
        for n, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                results[task["id"]] = future.result()
            except Exception as error:  # noqa: BLE001 - e.g. a worker was killed
                results[task["id"]] = {"id": task["id"], "ok": False, "error": f"{error.__class__.__name__}: {error}"}
            progress(start + span * n / len(tasks), f"Extracted {n}/{len(tasks)}: {task['label']}")
    return results


def _tid(seq: str, region: str, tag: str) -> str:
    return f"{seq}|{region}|{tag}"


def _rows(results: dict[str, dict[str, Any]]):
    """results -> feature rows (seq, region, tag, family, name, value) and error notes."""
    rows, errors, diag = [], {}, {}
    for tid, res in results.items():
        seq, region, tag = tid.split("|")
        if not res["ok"]:
            errors[tid] = res["error"]
            continue
        for family, values in res["features"].items():
            for name, value in values.items():
                rows.append((seq, region, tag, family, name, value))
        if seq != MASK_SEQ and tid not in diag:
            diag[tid] = res["diagnostics"]
    return rows, errors, diag


# ------------------------------------------------------------------ baseline extraction
def extract_baseline(token: str, available: list[str], progress: Progress) -> dict[str, Any]:
    """12 intensity extractions (4 sequences x 3 regions) + 3 mask-only shape extractions."""
    paths = case_paths(token)
    progress(0.02, "Preparing region masks")
    masks = prepare_masks(token, available)
    progress(0.05, "Measuring normalisation statistics")
    norm = {s: zscore_array(sitk.GetArrayFromImage(sitk.ReadImage(str(paths[s]))))[1] for s in C.SEQUENCES}
    tasks = []
    for region in available:
        m = masks[(region, "base")]["path"]
        tasks.append({"id": _tid(MASK_SEQ, region, "base"), "label": f"shape / {region}", "image": str(paths["t1ce"]), "mask": m,
                      "classes": ("shape",), "zscore": False})
        for s in C.SEQUENCES:
            tasks.append({"id": _tid(s, region, "base"), "label": f"{s.upper()} / {region}", "image": str(paths[s]), "mask": m,
                          "classes": C.INTENSITY_CLASSES, "zscore": True})
    results = run_tasks(tasks, progress, 0.08, 0.9)
    rows, errors, diag = _rows(results)
    return {"rows": rows, "errors": errors, "diagnostics": diag, "normalisation": norm,
            "voxels": {r: masks[(r, "base")]["voxels"] for r in available}}


# ------------------------------------------------------------------ boundary sensitivity
def extract_sensitivity(token: str, available: list[str], progress: Progress) -> dict[str, Any]:
    """Erode / dilate each region by one voxel, re-extract, and keep every result. Nothing is skipped silently."""
    paths = case_paths(token)
    progress(0.02, "Building eroded and dilated masks")
    masks = prepare_masks(token, available, perturb=True)
    runs, tasks = [], []
    for region in available:
        for tag in C.PERTURBATIONS:
            info = masks[(region, tag)]
            n = info["voxels"]
            if n < C.MIN_REGION_VOXELS:
                note = "region disappeared (empty mask)" if n == 0 else f"region too small ({n} voxels < {C.MIN_REGION_VOXELS})"
                for s in C.SEQUENCES + (MASK_SEQ,):
                    runs.append((s, region, tag, n, "too_small", note))
                continue
            tasks.append({"id": _tid(MASK_SEQ, region, tag), "label": f"shape / {region} / {tag}", "image": str(paths["t1ce"]),
                          "mask": info["path"], "classes": ("shape",), "zscore": False})
            for s in C.SEQUENCES:
                tasks.append({"id": _tid(s, region, tag), "label": f"{s.upper()} / {region} / {tag}", "image": str(paths[s]),
                              "mask": info["path"], "classes": C.INTENSITY_CLASSES, "zscore": True})
    results = run_tasks(tasks, progress, 0.06, 0.92)
    rows, errors, _ = _rows(results)
    for tid, res in results.items():
        seq, region, tag = tid.split("|")
        runs.append((seq, region, tag, masks[(region, tag)]["voxels"], "ok" if res["ok"] else "error", "" if res["ok"] else res["error"]))
    return {"rows": rows, "runs": runs, "errors": errors}


# ------------------------------------------------------------------ intensity distributions (viewer + comparison page)
_DIST_CACHE: dict[str, dict[str, Any]] = {}


def distributions(token: str) -> dict[str, Any]:
    """Per region and sequence: histogram of raw and z-scored intensities inside the region, plus summary statistics."""
    if token in _DIST_CACHE:
        return _DIST_CACHE[token]
    paths = case_paths(token)
    lab, _ = read_labels(paths["seg"])
    raw, z = {}, {}
    for s in C.SEQUENCES:
        a = sitk.GetArrayFromImage(sitk.ReadImage(str(paths[s]))).astype(np.float64)
        raw[s], z[s] = a, zscore_array(a)[0]
    out: dict[str, Any] = {}
    for region, labels in C.REGIONS.items():
        sel = np.isin(lab, labels)
        if int(sel.sum()) < C.MIN_REGION_VOXELS:
            continue
        pooled = np.concatenate([z[s][sel] for s in C.SEQUENCES])
        lo, hi = np.percentile(pooled, [0.5, 99.5])
        edges = np.linspace(lo, hi, 49)
        per: dict[str, Any] = {}
        for s in C.SEQUENCES:
            rv, zv = raw[s][sel], z[s][sel]
            counts_r, edges_r = np.histogram(rv, bins=40)
            counts_z, _ = np.histogram(np.clip(zv, lo, hi), bins=edges)
            per[s] = {
                "raw": {"edges": [round(float(e), 3) for e in edges_r], "counts": counts_r.astype(int).tolist()},
                "z": {"edges": [round(float(e), 4) for e in edges], "pct": [round(float(c) / len(zv) * 100, 4) for c in counts_z]},
                "stats": {"n": int(len(zv)), "raw_mean": float(rv.mean()), "raw_sd": float(rv.std()), "raw_median": float(np.median(rv)),
                          "z_mean": float(zv.mean()), "z_sd": float(zv.std()), "z_median": float(np.median(zv)),
                          "z_q1": float(np.percentile(zv, 25)), "z_q3": float(np.percentile(zv, 75))},
            }
        out[region] = per
    _DIST_CACHE[token] = out
    if len(_DIST_CACHE) > 8:
        _DIST_CACHE.pop(next(iter(_DIST_CACHE)))
    return out
