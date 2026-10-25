"""Reader for the Medical Segmentation Decathlon Task01_BrainTumour folder (read-only).

* Label numbers and channel order are READ from dataset.json (matched by name), never assumed. The mapping that was used is
  stored in the database and shown in the interface.
* Index step: cheap, one pass over every case (label file + FLAIR channel only) -> volumes, dominant sub-region,
  approximate hemisphere, quick consistency checks.
* Stage step: on demand, split one case into 4 single-channel 3D files + a segmentation relabelled to the internal
  convention (1 non-enhancing, 2 edema, 4 enhancing). Staged files live in the work folder, never in the dataset folder.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

import config as C

CASE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_CHANNELS = ["flair", "t1", "t1ce", "t2"]          # documented MSD order: FLAIR, T1w, T1gd, T2w
DEFAULT_LABELS = {"edema": 1, "net": 2, "enhancing": 3}   # documented MSD numbering (edema, non-enhancing, enhancing)
INTERNAL = {"net": 1, "edema": 2, "enhancing": 4}         # BraTS 2017-2021 numbers used inside NeuroVoxel


# ------------------------------------------------------------------ dataset.json
def load_meta() -> dict[str, Any]:
    path = C.DATASET_DIR / "dataset.json"
    if not path.exists():
        raise FileNotFoundError(f"dataset.json not found in {C.DATASET_DIR}. Mount the Task01_BrainTumour folder and set NV_DATASET_DIR.")
    raw = json.loads(path.read_text())
    return {"raw": raw, "channels": channel_order(raw), "labels": label_mapping(raw)}


def channel_order(raw: dict[str, Any]) -> dict[str, Any]:
    names = raw.get("modality") or {}
    ordered = [str(names[k]).lower() for k in sorted(names, key=lambda x: int(x))] if names else []
    out: list[str | None] = []
    for n in ordered:
        compact = re.sub(r"[^a-z0-9]", "", n)
        if "flair" in compact:
            out.append("flair")
        elif any(t in compact for t in ("t1gd", "t1c", "t1ce", "t1post", "gd")):
            out.append("t1ce")
        elif "t1" in compact:
            out.append("t1")
        elif "t2" in compact:
            out.append("t2")
        else:
            out.append(None)
    if len(out) == 4 and None not in out and len(set(out)) == 4:
        return {"order": out, "names": ordered, "source": "dataset.json"}
    return {"order": list(DEFAULT_CHANNELS), "names": ordered, "source": "default (dataset.json names not recognised)"}


def label_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, int] = {}
    for key, name in (raw.get("labels") or {}).items():
        n = str(name).lower()
        if n in ("background", "0") or int(key) == 0:
            continue
        if "edema" in n or "oedema" in n:
            found["edema"] = int(key)
        elif "enhanc" in n and "non" in n:
            found["net"] = int(key)
        elif "necro" in n:
            found.setdefault("net", int(key))
        elif "enhanc" in n:
            found["enhancing"] = int(key)
    if set(found) == {"edema", "net", "enhancing"} and len(set(found.values())) == 3:
        return {"map": found, "names": raw.get("labels"), "source": "dataset.json"}
    return {"map": dict(DEFAULT_LABELS), "names": raw.get("labels"), "source": "default (dataset.json names not recognised)"}


def list_cases(meta: dict[str, Any]) -> list[dict[str, str]]:
    cases = []
    for entry in meta["raw"].get("training", []):
        image = entry["image"] if isinstance(entry, dict) else entry
        label = entry.get("label") if isinstance(entry, dict) else None
        cid = re.sub(r"\.nii(\.gz)?$", "", Path(image).name)
        if cid.startswith("._") or not CASE_RE.match(cid):
            continue
        cases.append({"case_id": cid, "image": str(image).lstrip("./"), "label": str(label).lstrip("./") if label else ""})
    if not cases:      # fall back to the folders
        for p in sorted((C.DATASET_DIR / "imagesTr").glob("*.nii*")):
            if p.name.startswith("._"):
                continue
            cid = re.sub(r"\.nii(\.gz)?$", "", p.name)
            lab = next(iter((C.DATASET_DIR / "labelsTr").glob(cid + ".nii*")), None)
            if lab and CASE_RE.match(cid):
                cases.append({"case_id": cid, "image": f"imagesTr/{p.name}", "label": f"labelsTr/{lab.name}"})
    return cases


def _safe(rel: str) -> Path:
    path = (C.DATASET_DIR / rel).resolve()
    if C.DATASET_DIR.resolve() not in path.parents:
        raise ValueError("path escapes the dataset folder")
    return path


# ------------------------------------------------------------------ index (cheap)
def _lr_axis(affine: np.ndarray) -> tuple[int, bool]:
    """Array axis that runs left-right, and whether increasing index points to the patient's RIGHT."""
    codes = nib.aff2axcodes(affine)
    for axis, code in enumerate(codes):
        if code in ("L", "R"):
            return axis, code == "R"
    return 0, True


def index_case(entry: dict[str, str], meta: dict[str, Any]) -> dict[str, Any]:
    """One pass: label volume + FLAIR channel. Never raises; problems become status 'fail'."""
    row: dict[str, Any] = {"case_id": entry["case_id"], "image_rel": entry["image"], "label_rel": entry["label"]}
    notes: list[tuple[str, str]] = []          # (status, text)
    try:
        img = nib.load(str(_safe(entry["image"])))
        lab = nib.load(str(_safe(entry["label"])))
        shape = tuple(int(v) for v in img.shape)
        row["shape"] = "x".join(map(str, shape[:3]))
        if len(shape) != 4 or shape[3] != 4:
            return {**row, "index_status": "fail", "index_note": f"expected 4 channels, found shape {shape}"}
        if tuple(lab.shape) != shape[:3]:
            return {**row, "index_status": "fail", "index_note": f"label shape {tuple(lab.shape)} differs from image {shape[:3]}"}
        if not np.allclose(img.affine, lab.affine, atol=1e-3):
            notes.append(("fail", "image and label affines differ"))
        zooms = img.header.get_zooms()[:3]
        row["spacing"] = "x".join(f"{float(z):g}" for z in zooms)
        if not np.allclose(zooms, C.EXPECTED_SPACING, atol=1e-3):
            notes.append(("fail", f"voxel size {tuple(round(float(z), 3) for z in zooms)} mm is not 1 mm isotropic"))
        if shape[:3] != C.EXPECTED_SIZE:
            notes.append(("warn", f"size {shape[:3]} differs from 240x240x155"))
        arr = np.rint(np.asarray(lab.dataobj)).astype(np.int16)
        lm = meta["labels"]["map"]
        present = {int(v) for v in np.unique(arr)}
        unexpected = sorted(present - {0} - set(lm.values()))
        if unexpected:
            notes.append(("fail", f"unexpected label values {unexpected}"))
        counts = {k: int((arr == v).sum()) for k, v in lm.items()}
        row.update(n_edema=counts["edema"], n_net=counts["net"], n_enh=counts["enhancing"])
        vox = float(np.prod(zooms)) / 1000.0
        whole = sum(counts.values())
        core = counts["net"] + counts["enhancing"]
        row.update(vol_whole_ml=round(whole * vox, 3), vol_core_ml=round(core * vox, 3), vol_enh_ml=round(counts["enhancing"] * vox, 3),
                   vol_edema_ml=round(counts["edema"] * vox, 3), vol_net_ml=round(counts["net"] * vox, 3))
        row["enh_pct"] = round(counts["enhancing"] / whole * 100, 2) if whole else None
        row["edema_pct"] = round(counts["edema"] / whole * 100, 2) if whole else None
        row["net_pct"] = round(counts["net"] / whole * 100, 2) if whole else None
        if whole == 0:
            return {**row, "dominant": None, "hemisphere": None, "index_status": "fail", "index_note": "segmentation is empty"}
        if whole < C.MIN_REGION_VOXELS:
            notes.append(("fail", f"whole tumour has only {whole} voxels"))
        dom = max(("edema", "net", "enhancing"), key=lambda k: counts[k])
        row["dominant"] = dom
        # brain extent from the first channel in file order (cheap: decompresses only the first quarter of the file)
        flair_idx = meta["channels"]["order"].index("flair")
        ch = np.asarray(img.dataobj[..., flair_idx], dtype=np.float32)
        brain = ch != 0
        tumour = arr != 0
        zero_fraction = float(1.0 - brain.mean())
        # heuristics below are informational on a curated dataset: they can warn, they never fail a case
        if zero_fraction < 0.05:
            notes.append(("warn", f"FLAIR has almost no zero background (zero fraction {zero_fraction:.0%})"))
        inside = float(tumour[brain].sum()) / float(tumour.sum())
        if inside < C.MASK_INSIDE_BRAIN_PASS:      # FLAIR alone is not a clean pass: check all four sequences (slower, rare)
            any_ch = brain.copy()
            for k in range(4):
                if k != flair_idx:
                    any_ch |= np.asarray(img.dataobj[..., k], dtype=np.float32) != 0
            inside = float(tumour[any_ch].sum()) / float(tumour.sum())
            if inside < C.MASK_INSIDE_BRAIN_WARN:
                notes.append(("warn", f"{inside:.0%} of the segmentation lies inside the brain (any sequence)"))
        axis, plus_is_right = _lr_axis(img.affine)
        other = tuple(a for a in range(3) if a != axis)
        extent = np.flatnonzero(brain.any(axis=other))
        mid = (extent.min() + extent.max()) / 2.0
        profile = tumour.sum(axis=other).astype(np.float64)
        pos = np.arange(len(profile))
        plus_fraction = float(profile[pos > mid].sum() / profile.sum())
        right_fraction = plus_fraction if plus_is_right else 1.0 - plus_fraction
        crossing = min(right_fraction, 1 - right_fraction) >= 0.10
        row["right_fraction"] = round(right_fraction, 3)
        row["hemisphere"] = "bilateral" if crossing else ("right" if right_fraction > 0.5 else "left")
        c = np.argwhere(tumour).mean(0)
        row.update(cx=round(float(c[0]), 1), cy=round(float(c[1]), 1), cz=round(float(c[2]), 1))
    except Exception as error:  # noqa: BLE001 - one bad file must not stop the index
        return {**row, "index_status": "fail", "index_note": f"{error.__class__.__name__}: {str(error)[:160]}"}
    states = {s for s, _ in notes}
    row["index_status"] = "fail" if "fail" in states else "warn" if "warn" in states else "pass"
    row["index_note"] = "; ".join(t for _, t in notes)
    return row


# ------------------------------------------------------------------ stage (on demand)
def staged_dir(case_id: str) -> Path:
    return C.UPLOADS / case_id


def is_staged(case_id: str) -> bool:
    d = staged_dir(case_id)
    return all((d / f"{n}.nii.gz").exists() for n in (*C.SEQUENCES, "seg"))


def stage_case(case_id: str, meta: dict[str, Any], entry: dict[str, str]) -> dict[str, Any]:
    """Split the 4D file into single-channel volumes and relabel the segmentation. Returns the raw label check."""
    out = staged_dir(case_id)
    out.mkdir(parents=True, exist_ok=True)
    img = nib.load(str(_safe(entry["image"])))
    lab = nib.load(str(_safe(entry["label"])))
    order = meta["channels"]["order"]
    for i, name in enumerate(order):
        data = np.asarray(img.dataobj[..., i], dtype=np.float32)
        nib.save(nib.Nifti1Image(data, img.affine), str(out / f"{name}.nii.gz"))
    raw = np.rint(np.asarray(lab.dataobj)).astype(np.int16)
    lm = meta["labels"]["map"]
    seg = np.zeros(raw.shape, np.uint8)
    for kind, value in lm.items():
        seg[raw == value] = INTERNAL[kind]
    unexpected = sorted({int(v) for v in np.unique(raw)} - {0} - set(lm.values()))
    nib.save(nib.Nifti1Image(seg, lab.affine), str(out / "seg.nii.gz"))
    return {"raw_labels": sorted(int(v) for v in np.unique(raw)), "unexpected": unexpected}
