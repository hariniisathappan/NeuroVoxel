"""NeuroVoxel viewer geometry endpoints (no PyRadiomics here): per-label volumes, spatial position
(centroid, hemisphere, depth, 3-view locator), sampled voxels and surface splats for the 3D views.
Works on the staged copy of a case (internal labels 1 non-enhancing, 2 edema, 4 enhancing)."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("NEUROVOXEL_DATA_DIR", ROOT))
UPLOADS = DATA_DIR / "storage"

router = APIRouter(prefix="/api/adv", tags=["advanced"])

MAX_LABELS = 12
NOISE_FRACTION, NOISE_SEED = 0.05, 42
STABLE_CV, MODERATE_CV = 10.0, 25.0
PERTURBATIONS = ["Erode 1 vox", "Dilate 1 vox", f"Noise {int(NOISE_FRACTION * 100)}%", "Bin 16", "Bin 32"]


# ---------------------------------------------------------------- helpers
def _token(value: str) -> str:
    import msd
    if not msd.CASE_RE.match(value or ""):
        raise HTTPException(400, "Invalid case id.")
    return value


def _paths(token: str, seq: str = "t1ce") -> tuple[Path, Path]:
    import db
    import engine
    import pipeline
    if token.startswith("upload-"):
        import config as C
        folder = C.UPLOADS / token
        mask = folder / "seg.nii.gz"
        image = next(iter(sorted(folder.glob(f"{seq}.nii*"))), None)
        if image is None:
            image = next(iter(sorted(p for p in folder.glob("*.nii*") if p.name != "seg.nii.gz")), None)
        if image is None or not mask.exists(): raise HTTPException(404, "Uploaded case or selected sequence was deleted.")
        return image, mask
    if not db.get_case(token):
        raise HTTPException(404, "Unknown case.")
    pipeline.stage_for_viewing(token)
    paths = engine.case_paths(token)
    return paths[seq if seq in paths else "t1ce"], paths["seg"]


def _load(token: str, seq: str = "t1ce"):
    import numpy as np
    import SimpleITK as sitk

    image_path, mask_path = _paths(token, seq)
    image = sitk.ReadImage(str(image_path))
    mask = sitk.ReadImage(str(mask_path))
    labels = np.rint(sitk.GetArrayFromImage(mask)).astype(np.int32)
    # Uploads are remapped to NeuroVoxel's canonical labels (1, 2, 4). Ignore
    # any other values only for Pipeline A uploads so they cannot appear as
    # extra regions. Leave the configured reference-dataset path untouched.
    if token.startswith("upload-"):
        labels[~np.isin(labels, (1, 2, 4))] = 0
    return sitk, np, image, mask, labels


def _present_labels(np, arr) -> list[int]:
    values, counts = np.unique(arr[arr != 0], return_counts=True)
    if len(values) > MAX_LABELS:                       # keep the largest labels only
        values = values[np.argsort(-counts)[:MAX_LABELS]]
    return sorted(int(v) for v in values)


def _parse_groups(text: str) -> dict[str, list[int]]:
    """'Core:1+4; Rim:2' -> {'Core': [1, 4], 'Rim': [2]}"""
    groups: dict[str, list[int]] = {}
    for chunk in (c for c in text.split(";") if c.strip()):
        try:
            name, ids = chunk.split(":", 1)
            labels = [int(x) for x in ids.replace("+", ",").split(",") if x.strip()]
        except ValueError as error:
            raise HTTPException(400, "Groups look like 'Core: 1+4; Rim: 2'.") from error
        name = name.strip()[:24]
        if not name or not labels:
            raise HTTPException(400, "Each group needs a name and at least one label.")
        groups[name] = labels
    return groups


def _definitions(ids: list[int], groups: dict[str, list[int]]) -> dict[str, tuple[str, list[int] | None]]:
    defs: dict[str, tuple[str, list[int] | None]] = {"all": ("Whole ROI", None)}
    defs.update({f"label_{i}": (f"Label {i}", [i]) for i in ids})
    defs.update({f"group_{n}": (n, ls) for n, ls in groups.items()})
    return defs


def _select(np, arr, labels):
    return arr != 0 if labels is None else np.isin(arr, labels)


def _to_ras(np, image, idx_zyx):
    """Array indices (z, y, x) -> scanner RAS millimetres (ITK works in LPS)."""
    ijk = idx_zyx[:, ::-1].astype(float)
    direction = np.array(image.GetDirection()).reshape(3, 3)
    lps = np.array(image.GetOrigin()) + ijk @ (direction @ np.diag(image.GetSpacing())).T
    return lps * np.array([-1.0, -1.0, 1.0])


def _canonical(np, image, arrays: dict):
    """Reorient arrays to (z, y, x) where z->Superior, y->Posterior, x->Left (increasing).
    Returns (arrays, (sx, sy, sz) physical spacings)."""
    direction = np.array(image.GetDirection()).reshape(3, 3)
    axis_of = {}
    for j in range(3):
        axis_of[int(np.argmax(np.abs(direction[:, j])))] = (j, 1 if direction[int(np.argmax(np.abs(direction[:, j]))), j] > 0 else -1)
    if len(axis_of) != 3:                               # heavily oblique: assume stored order
        axis_of = {0: (0, 1), 1: (1, 1), 2: (2, 1)}
    order = [2 - axis_of[a][0] for a in (2, 1, 0)]
    out = {}
    for name, arr in arrays.items():
        view = arr.transpose(order)
        for n, a in enumerate((2, 1, 0)):
            if axis_of[a][1] < 0:
                view = np.flip(view, axis=n)
        out[name] = view
    spacing = image.GetSpacing()
    return out, tuple(float(spacing[axis_of[a][0]]) for a in (0, 1, 2))


def _geometry(np, image, selection) -> dict[str, Any]:
    idx = np.argwhere(selection)
    ras = _to_ras(np, image, idx)
    extent_ijk = (idx.max(0) - idx.min(0) + 1)[::-1] * np.array(image.GetSpacing())
    direction = np.array(image.GetDirection()).reshape(3, 3)
    extent = {"LR": 0.0, "AP": 0.0, "SI": 0.0}
    for j in range(3):
        extent[("LR", "AP", "SI")[int(np.argmax(np.abs(direction[:, j])))]] = round(float(extent_ijk[j]), 1)
    return {
        "centroid_ras_mm": [round(float(v), 2) for v in ras.mean(0)],
        "centroid_voxel": [round(float(v), 1) for v in idx[:, ::-1].mean(0)],
        "extent_mm": extent,
    }


def _head(sitk, np, image, roi):
    """Foreground (non-zero brain voxels of the skull-stripped scan), the ROI in canonical orientation, hemisphere and depth."""
    fg_img = sitk.Cast(image != 0, sitk.sitkUInt8)
    fg = sitk.GetArrayFromImage(fg_img) > 0
    if not fg.any():
        fg = np.ones(roi.shape, bool)
        fg_img = sitk.GetImageFromArray(fg.astype(np.uint8))
        fg_img.CopyInformation(image)
    canon, (sx, sy, sz) = _canonical(np, image, {"roi": roi, "fg": fg})
    cfg, croi = canon["fg"], canon["roi"]
    xs = np.nonzero(cfg.any(axis=(0, 1)))[0]
    lo, hi = float(xs.min()), float(xs.max())
    mid = (lo + hi) / 2.0
    counts = croi.sum(axis=(0, 1)).astype(float)
    coords = np.arange(len(counts), dtype=float)
    left_fraction = float(counts[coords > mid].sum() / counts.sum())
    offset_mm = float((counts * coords).sum() / counts.sum() - mid) * sx
    crossing = min(left_fraction, 1 - left_fraction) >= 0.10
    hemisphere = {
        "side": "Bilateral (crosses midline)" if crossing else ("Left" if left_fraction > 0.5 else "Right"),
        "offset_mm": round(offset_mm, 1),
        "left_fraction": round(left_fraction, 3),
        "half_width_mm": round((hi - lo + 1) / 2.0 * sx, 1),
        "method": "Midline = centre of the non-zero brain along the left-right axis. No atlas registration.",
    }
    depth = None
    try:
        filled = sitk.BinaryFillhole(fg_img)
        dist = sitk.GetArrayFromImage(sitk.SignedMaurerDistanceMap(filled, insideIsPositive=True, squaredDistance=False, useImageSpacing=True))
        values = dist[roi]
        c = np.argwhere(roi).mean(0).round().astype(int)
        depth = {"nearest_mm": round(max(float(values.min()), 0.0), 1), "centroid_mm": round(max(float(dist[tuple(c)]), 0.0), 1)}
    except Exception:  # noqa: BLE001 - depth is a bonus metric
        pass
    return hemisphere, depth, canon, (sx, sy, sz)


# ---------------------------------------------------------------- endpoints
@router.get("/subregions")
def subregions(session: str = Query("", alias="uuid"), seq: str = Query("t1ce"), groups: str = Query("")) -> dict:
    token = _token(session)
    grp = _parse_groups(groups)
    sitk, np, image, _mask, arr = _load(token, seq)
    ids = _present_labels(np, arr)
    if not ids:
        raise HTTPException(422, "The mask contains no labelled voxels.")
    voxel = float(np.prod(image.GetSpacing()))
    total = int((arr != 0).sum())
    regions, geometry = {}, {}
    for key, (name, labels) in _definitions(ids, grp).items():
        selection = _select(np, arr, labels)
        n = int(selection.sum())
        regions[key] = {"name": name, "labels": labels if labels is not None else ids, "voxels": n,
                        "mm3": round(n * voxel, 2), "ml": round(n * voxel / 1000.0, 4), "pct": round(n / total * 100, 2) if total else None}
        if n:
            geometry[key] = _geometry(np, image, selection)
    hemisphere, depth, _canon, _sp = _head(sitk, np, image, arr != 0)
    return {"labels": ids, "voxel_mm3": round(voxel, 4), "regions": regions,
            "spatial": {"regions": geometry, "hemisphere": hemisphere, "depth": depth}}


def _pool(np, plane, rows_out: int, cols_out: int):
    rows, cols = plane.shape
    for axis, (n_in, n_out) in enumerate(((rows, rows_out), (cols, cols_out))):
        if n_out < n_in:
            edges = np.linspace(0, n_in, n_out + 1).astype(int)
            plane = np.add.reduceat(plane, edges[:-1], axis=axis) / np.maximum(np.diff(edges), 1).reshape((-1, 1) if axis == 0 else (1, -1))
        elif n_out > n_in:
            plane = np.take(plane, np.minimum((np.arange(n_out) * n_in // n_out), n_in - 1), axis=axis)
    return plane


@router.get("/locator")
def locator(session: str = Query("", alias="uuid"), seq: str = Query("t1ce")) -> dict:
    """Axial / coronal / sagittal projections of the scan and ROI for the position panel."""
    token = _token(session)
    sitk, np, image, _mask, arr = _load(token, seq)
    ids = _present_labels(np, arr)
    if not ids:
        raise HTTPException(422, "The mask contains no labelled voxels.")
    roi = arr != 0
    _h, _d, canon, (sx, sy, sz) = _head(sitk, np, image, roi)
    cfg, croi = canon["fg"], canon["roi"]
    clab = _canonical(np, image, {"lab": arr})[0]["lab"]
    nz, ny, nx = cfg.shape
    # axis to collapse, rows spacing, cols spacing, flip rows so Superior is on top
    plan = {"axial": (0, sy, sx, False), "coronal": (1, sz, sx, True), "sagittal": (2, sz, sy, True)}
    views = {}
    for name, (axis, sr, sc, flip) in plan.items():
        head2d, roi2d = cfg.sum(axis=axis).astype(float), croi.sum(axis=axis).astype(float)
        stack = np.stack([(clab == i).sum(axis=axis) for i in ids])
        lab2d = np.where(stack.max(0) > 0, stack.argmax(0) + 1, 0)
        if flip:
            head2d, roi2d, lab2d = head2d[::-1], roi2d[::-1], lab2d[::-1]
        rows, cols = head2d.shape
        t = max(rows * sr, cols * sc) / 90.0
        r_out, c_out = max(8, round(rows * sr / t)), max(8, round(cols * sc / t))
        head, dens = _pool(np, head2d, r_out, c_out), _pool(np, roi2d, r_out, c_out)
        rr = np.minimum(((np.arange(r_out) + 0.5) * rows / r_out).astype(int), rows - 1)
        cc = np.minimum(((np.arange(c_out) + 0.5) * cols / c_out).astype(int), cols - 1)
        lab = lab2d[rr][:, cc]
        views[name] = {"w": int(c_out), "h": int(r_out), "aspect": round(cols * sc / (rows * sr), 4),
                       "head": (head / max(head.max(), 1e-9) * 255).astype(np.uint8).ravel().tolist(),
                       "roi": (dens / max(dens.max(), 1e-9) * 255).astype(np.uint8).ravel().tolist(),
                       "lab": lab.astype(np.uint8).ravel().tolist()}
    idx = np.argwhere(croi)
    frac = lambda sel: [float((sel[:, 2].mean() + .5) / nx), float((sel[:, 1].mean() + .5) / ny), float((sel[:, 0].mean() + .5) / nz)]  # noqa: E731
    centroids = {"all": frac(idx)}
    for i in ids:
        centroids[f"label_{i}"] = frac(np.argwhere(clab == i))
    xs = np.nonzero(cfg.any(axis=(0, 1)))[0]
    return {"views": views, "label_ids": ids, "centroids": centroids,
            "bbox": [float(idx[:, 2].min() / nx), float((idx[:, 2].max() + 1) / nx), float(idx[:, 1].min() / ny), float((idx[:, 1].max() + 1) / ny),
                     float(idx[:, 0].min() / nz), float((idx[:, 0].max() + 1) / nz)],
            "mid": float(((xs.min() + xs.max()) / 2.0 + .5) / nx)}


@router.get("/points")
def points(session: str = Query("", alias="uuid"), seq: str = Query("t1ce"), max_points: int = Query(14000, ge=1000, le=40000)) -> dict:
    """Sampled true voxels of every label, centred on the whole ROI (RAS mm)."""
    token = _token(session)
    _s, np, image, _m, arr = _load(token, seq)
    ids = _present_labels(np, arr)
    if not ids:
        raise HTTPException(422, "The mask contains no labelled voxels.")
    indices = {f"label_{i}": np.argwhere(arr == i) for i in ids}
    center = _to_ras(np, image, np.concatenate(list(indices.values()))).mean(0)
    weights = {k: math.sqrt(len(v)) for k, v in indices.items()}
    total_weight = sum(weights.values()) or 1.0
    rng = np.random.default_rng(7)
    jitter, radius, regions = float(np.mean(image.GetSpacing())), 1.0, {}
    for key, idx in indices.items():
        take = min(len(idx), max(600, int(max_points * weights[key] / total_weight)))
        pick = idx[rng.choice(len(idx), take, replace=False)]
        ras = _to_ras(np, image, pick) - center + rng.uniform(-0.5, 0.5, (take, 3)) * jitter
        radius = max(radius, float(np.linalg.norm(ras, axis=1).max()))
        regions[key] = {"count": int(len(idx)), "xyz": np.round(ras, 2).ravel().tolist()}
    return {"regions": regions, "radius_mm": round(radius, 2)}


# ---------------------------------------------------------------- shape lab (surface splats with normals)
@router.get("/ping")
def ping() -> dict:
    return {"version": 4}


def _surface_mask(np, m):
    p = np.pad(m, 1)
    core = (p[1:-1, 1:-1, 1:-1] & p[:-2, 1:-1, 1:-1] & p[2:, 1:-1, 1:-1] & p[1:-1, :-2, 1:-1]
            & p[1:-1, 2:, 1:-1] & p[1:-1, 1:-1, :-2] & p[1:-1, 1:-1, 2:])
    return m & ~core


def _normals(sitk, np, image, mask_bool, all_idx, pick):
    """Outward unit normals (RAS) at the picked surface voxels, from a smoothed copy of the mask."""
    lo = np.maximum(all_idx.min(0) - 4, 0)
    hi = np.minimum(all_idx.max(0) + 5, mask_bool.shape)
    crop = mask_bool[tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))].astype(np.float32)
    img = sitk.GetImageFromArray(crop)
    img.SetSpacing(image.GetSpacing())
    smooth = sitk.GetArrayFromImage(sitk.SmoothingRecursiveGaussian(img, 1.5 * min(image.GetSpacing())))
    sp = image.GetSpacing()
    gz, gy, gx = np.gradient(smooth, sp[2], sp[1], sp[0])
    loc = tuple((pick - lo).T)
    grad_ijk = np.stack([gx[loc], gy[loc], gz[loc]], axis=1)
    direction = np.array(image.GetDirection()).reshape(3, 3)
    n_ras = -(grad_ijk @ direction.T) * np.array([-1.0, -1.0, 1.0])
    return n_ras / np.maximum(np.linalg.norm(n_ras, axis=1, keepdims=True), 1e-9)


def _max_diameter(np, pts) -> float:
    best = 0.0
    for start in range(0, len(pts), 400):
        d = np.sqrt(((pts[start:start + 400, None, :] - pts[None, :, :]) ** 2).sum(-1))
        best = max(best, float(d.max()))
    return round(best, 1)


def _region_shapes(token: str) -> dict:
    """Exact PyRadiomics shape features (calculated for the case) keyed like the cinema regions."""
    import db
    key_of = {"whole_tumour": "all", "tumour_core": "group_Tumour core", "enhancing_tumour": "label_4"}
    out: dict = {}
    try:
        with db.connect() as con:
            rows = con.execute("SELECT region, name, value FROM feature_values WHERE case_id=? AND sequence='mask' AND family='shape'", (token,)).fetchall()
        for r in rows:
            out.setdefault(key_of.get(r["region"], r["region"]), {})[r["name"]] = r["value"]
    except Exception:  # noqa: BLE001 - optional enrichment
        pass
    return out


def _fill_missing_shapes(token: str, shapes: dict, sitk, np, image, arr, ids: list[int]) -> dict:
    """Exact PyRadiomics shape features for every region the database does not hold (single labels, or a case not
    yet calculated). Mask-only, ~1 s per region, cached next to the staged case."""
    import engine
    wanted = {"all": arr != 0}
    wanted["group_Tumour core"] = np.isin(arr, [1, 4])
    for i in ids:
        wanted[f"label_{i}"] = arr == i
    cache_file = Path(engine.case_paths(token)["seg"]).parent / "shape_cache.json"
    try:
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    except Exception:  # noqa: BLE001
        cache = {}
    dirty = False
    for key, m in wanted.items():
        if key in shapes:
            continue
        if key not in cache:
            if int(m.sum()) < 64:
                continue
            try:
                mask = sitk.GetImageFromArray(m.astype(np.uint8))
                mask.CopyInformation(image)
                res = engine.make_extractor(("shape",)).execute(image, mask, label=1)
                cache[key] = engine.flatten(res).get("shape", {})
                dirty = True
            except Exception:  # noqa: BLE001 - optional enrichment
                continue
        if cache.get(key):
            shapes[key] = cache[key]
    if dirty:
        try:
            cache_file.write_text(json.dumps(cache))
        except Exception:  # noqa: BLE001
            pass
    return shapes


def _main_shape(token: str):
    return _region_shapes(token).get("all")


@router.get("/shell")
def shell(session: str = Query("", alias="uuid"), seq: str = Query("t1ce"), max_points: int = Query(8000, ge=2000, le=60000)) -> dict:
    """Surface voxels of each label with smoothed outward normals, for lit 3D rendering. No PyRadiomics."""
    token = _token(session)
    sitk, np, image, _mask, arr = _load(token, seq)
    ids = _present_labels(np, arr)
    if not ids:
        raise HTTPException(422, "The mask contains no labelled voxels.")
    center = _to_ras(np, image, np.argwhere(arr != 0)).mean(0)
    rng = np.random.default_rng(11)
    raw = sitk.GetArrayFromImage(image).astype(np.float64)
    nz = raw != 0
    zimg = np.zeros_like(raw)
    if nz.any() and raw[nz].std() > 1e-12:
        zimg[nz] = (raw[nz] - raw[nz].mean()) / raw[nz].std()      # same z-score as the PyRadiomics input
    surfaces = {}
    for i in ids:
        m = arr == i
        surfaces[f"label_{i}"] = (m, np.argwhere(_surface_mask(np, m)))
    weights = {k: math.sqrt(max(len(v[1]), 1)) for k, v in surfaces.items()}
    total = sum(weights.values()) or 1.0
    regions, radius = {}, 1.0
    for key, (m, idx) in surfaces.items():
        if not len(idx):
            continue
        take = min(len(idx), max(1500, int(max_points * weights[key] / total)))
        pick = idx[rng.choice(len(idx), take, replace=False)] if len(idx) > take else idx
        ras = _to_ras(np, image, pick) - center
        radius = max(radius, float(np.linalg.norm(ras, axis=1).max()))
        sample = idx[rng.choice(len(idx), min(len(idx), 1000), replace=False)]
        regions[key] = {"count": int(m.sum()), "xyz": np.round(ras, 2).ravel().tolist(),
                        "val": np.round(np.clip(zimg[tuple(pick.T)], -3, 3) / 6 + 0.5, 3).tolist(),
                        "nrm": np.round(_normals(sitk, np, image, m, idx, pick), 3).ravel().tolist(),
                        "max_diameter_mm": _max_diameter(np, _to_ras(np, image, sample))}
    whole_idx = np.argwhere(_surface_mask(np, arr != 0))
    whole_sample = whole_idx[rng.choice(len(whole_idx), min(len(whole_idx), 1000), replace=False)]
    regions["all"] = {"max_diameter_mm": _max_diameter(np, _to_ras(np, image, whole_sample))}
    return {"regions": regions, "radius_mm": round(radius, 2), "main_shape": _main_shape(token), "shapes": _fill_missing_shapes(token, _region_shapes(token), sitk, np, image, arr, ids)}


