"""Private, one-request upload analysis for Pipeline A.

Uploaded scans are stored only in the NeuroVoxel work directory. They are never
added to the public reference-case database and can be removed with the returned
upload id.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import config as C
import db
import engine
import jobs
import pipeline
from validation import detect_role

router = APIRouter(prefix="/api/upload", tags=["Pipeline A"])
MAX_FILE_BYTES = 1024 * 1024 * 1024
SEQUENCES = {"t1", "t1ce", "t2", "flair"}


def _load(path: Path) -> tuple[sitk.Image, np.ndarray]:
    image = sitk.ReadImage(str(path))
    if image.GetDimension() != 3 or image.GetNumberOfComponentsPerPixel() != 1:
        raise ValueError("Every uploaded sequence and segmentation must be a scalar 3D NIfTI volume.")
    arr = sitk.GetArrayFromImage(image)
    if not np.isfinite(arr).all():
        raise ValueError("A file contains NaN or infinite voxel values.")
    return image, arr


def _validation(paths: dict[str, Path], names: dict[str, str], label_map: dict[int, str]) -> tuple[dict, dict]:
    checks: list[dict[str, str]] = []
    def add(key: str, title: str, status: str, detail: str):
        checks.append({"id": key, "title": title, "status": status, "detail": detail})
    imgs, arrs = {}, {}
    try:
        for slot, path in paths.items():
            imgs[slot], arrs[slot] = _load(path)
    except Exception as exc:
        add("readable", "Files are readable 3D NIfTI volumes", "fail", str(exc)[:240])
        return {"status": "fail", "can_analyze": False, "checks": checks}, {}
    add("readable", "Files are readable 3D NIfTI volumes", "pass", f"{len(paths)-1} selected MRI sequence(s) and the segmentation opened successfully.")
    wrong, unclear = [], []
    for slot, filename in names.items():
        role = detect_role(filename)
        if role is None: unclear.append(slot.upper())
        elif role != slot: wrong.append(f"{filename} appears to be {role.upper()}, but was assigned to {slot.upper()}")
    detail = "; ".join(wrong) + (". " if wrong else "")
    detail += ("Could not infer file role from name for: " + ", ".join(unclear) + ". Confirm your sequence assignments." if unclear else "Each file name agrees with its assigned sequence.")
    add("slot_names", "File names agree with selected sequence roles", "fail" if wrong else "warn" if unclear else "pass", detail)
    ref = imgs[next(iter(k for k in paths if k != "seg"))]
    diffs = []
    for slot, img in imgs.items():
        if img.GetSize() != ref.GetSize() or not np.allclose(img.GetSpacing(), ref.GetSpacing(), atol=1e-4) or not np.allclose(img.GetOrigin(), ref.GetOrigin(), atol=1e-3) or not np.allclose(img.GetDirection(), ref.GetDirection(), atol=1e-4):
            diffs.append(slot.upper())
    add("geometry", "Sequences and segmentation share one grid", "fail" if diffs else "pass", "Different size, spacing, origin or orientation: " + ", ".join(diffs) if diffs else "All selected volumes share size, spacing, origin and orientation.")
    spacing_ok = np.allclose(ref.GetSpacing(), C.EXPECTED_SPACING, atol=1e-3)
    add("spacing", "Voxel spacing is 1 mm isotropic", "pass" if spacing_ok else "fail", f"Spacing is {tuple(round(x, 4) for x in ref.GetSpacing())} mm. Resample to 1 mm isotropic before analysis." if not spacing_ok else "Voxel spacing is 1 mm isotropic.")
    if diffs or not spacing_ok:
        return {"status": "fail", "can_analyze": False, "checks": checks, "geometry": {"size": list(ref.GetSize()), "spacing_mm": list(ref.GetSpacing())}}, {}
    size_ok = tuple(ref.GetSize()) == C.EXPECTED_SIZE
    add("size", "Volume size follows the BraTS reference grid", "pass" if size_ok else "warn", f"Volume size is {tuple(ref.GetSize())}." + (" This differs from the common 240 x 240 x 155 grid." if not size_ok else ""))
    label_arr = arrs["seg"]
    labels_integer = bool(np.all(label_arr == np.rint(label_arr)))
    seg = np.rint(label_arr).astype(np.int32)
    present = set(int(v) for v in np.unique(seg)) - {0}
    unmapped = sorted(present - set(label_map))
    missing = sorted(set(label_map) - present)
    badmap = len(set(label_map.values())) != len(label_map) or not set(label_map.values()) <= {"net", "edema", "enhancing"}
    label_ok = labels_integer and not unmapped and not missing and not badmap and len(label_map) >= 1
    detail = f"Found labels {sorted(present)}; mapped labels {sorted(label_map)}."
    if unmapped: detail += f" Unmapped nonzero labels: {unmapped}."
    if missing: detail += f" Selected label values not present: {missing}."
    add("labels", "Segmentation labels are mapped unambiguously", "pass" if label_ok else "fail", detail)
    if not label_ok:
        return {"status": "fail", "can_analyze": False, "checks": checks}, {}
    canonical = np.zeros(seg.shape, dtype=np.uint8)
    internal = {"net": 1, "edema": 2, "enhancing": 4}
    for value, region in label_map.items(): canonical[seg == value] = internal[region]
    n = int((canonical > 0).sum())
    add("mask_nonempty", "Segmentation is not empty", "pass" if n else "fail", f"{n:,} labelled voxels." if n else "Segmentation contains no tumour voxels.")
    if n < C.MIN_REGION_VOXELS:
        add("region_size", "Whole tumour meets the radiomics minimum", "fail", f"{n} voxels; at least {C.MIN_REGION_VOXELS} are required.")
    else:
        add("region_size", "Whole tumour meets the radiomics minimum", "pass", f"{n:,} voxels; sub-regions below {C.MIN_REGION_VOXELS} voxels will be skipped.")
    seqs = [s for s in paths if s != "seg"]
    identical = []
    for i, first in enumerate(seqs):
        for second in seqs[i+1:]:
            if np.array_equal(arrs[first], arrs[second]): identical.append(f"{first.upper()} and {second.upper()}")
    add("distinct", "Selected sequences contain distinct images", "fail" if identical else "pass", "Identical voxel data found for " + "; ".join(identical) + "." if identical else "No selected sequences are identical.")
    fg = {s: arrs[s] != 0 for s in seqs}
    union = np.logical_or.reduce(list(fg.values()))
    inside = float(union[canonical > 0].sum()) / max(n, 1)
    add("mask_in_brain", "Segmentation overlaps the non-zero image region", "pass" if inside >= C.MASK_INSIDE_BRAIN_PASS else "warn" if inside >= C.MASK_INSIDE_BRAIN_WARN else "fail", f"{inside:.1%} of labelled voxels overlap the union of selected image foregrounds." + (" The mask may be shifted or from a different scan." if inside < C.MASK_INSIDE_BRAIN_WARN else ""))
    flat = []
    for s in seqs:
        a = arrs[s]
        shell = np.concatenate([a[0].ravel(), a[-1].ravel(), a[:, 0].ravel(), a[:, -1].ravel(), a[:, :, 0].ravel(), a[:, :, -1].ravel()])
        values = a[a != 0]
        if values.size < 8 or np.ptp(values) == 0:
            flat.append(s.upper())
        if np.any(shell != 0) or np.mean(a == 0) < C.MIN_ZERO_FRACTION:
            add(f"skull_{s}", f"{s.upper()} appears skull-stripped", "warn", "Non-zero border or low zero-background fraction. Similarity to the reference cohort is intended for preprocessed, skull-stripped data.")
    if flat: add("intensity", "Selected sequences contain intensity variation", "fail", "Empty or constant image data: " + ", ".join(flat) + ".")
    else: add("intensity", "Selected sequences contain intensity variation", "pass", "Each selected sequence contains varying non-zero intensities.")
    report = {"status": "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "pass", "can_analyze": not any(c["status"] == "fail" for c in checks), "checks": checks, "labels_found": sorted(present), "label_mapping": {str(k): v for k, v in label_map.items()}, "sequence_names": {k: names[k] for k in seqs}, "geometry": {"size": list(ref.GetSize()), "spacing_mm": list(ref.GetSpacing()), "origin": list(ref.GetOrigin()), "direction": list(ref.GetDirection())}}
    return report, {"arrays": arrs, "images": imgs, "canonical": canonical, "sequences": seqs}


def _extract(upload_id: str, paths: dict[str, Path], data: dict, progress=lambda *_: None) -> dict:
    seqs, lab = data["sequences"], data["canonical"]
    work = C.UPLOADS / upload_id
    seg_img = data["images"]["seg"]
    seg_path = work / "seg.nii.gz"
    # Keep the uploaded segmentation's physical geometry on the canonicalized
    # label map. The voxel values are remapped, but their grid must still match
    # the MRI for the multiplanar and 3D viewers to line up correctly.
    canonical_seg = sitk.GetImageFromArray(lab.astype(np.uint8))
    canonical_seg.CopyInformation(seg_img)
    sitk.WriteImage(canonical_seg, str(seg_path), True)
    paths["seg"] = seg_path
    regions = {"whole_tumour": np.isin(lab, (1, 2, 4)), "tumour_core": np.isin(lab, (1, 4)), "enhancing_tumour": lab == 4}
    labels_for_region = {"whole_tumour": [1,2,4], "tumour_core": [1,4], "enhancing_tumour": [4]}
    results, counts, volumes = {}, {}, {}
    masks = {}
    for region, mask in regions.items():
        n = int(mask.sum()); counts[region] = n
        volumes[region] = round(n * float(np.prod(seg_img.GetSpacing())), 2)
        if n < C.MIN_REGION_VOXELS: continue
        mp = work / f"{region}.nii.gz"
        mi = sitk.GetImageFromArray(mask.astype(np.uint8)); mi.CopyInformation(seg_img); sitk.WriteImage(mi, str(mp), True); masks[region] = str(mp)
    tasks = []
    for region, mask_path in masks.items():
        tasks.append({"id": f"mask|{region}|base", "label": f"shape / {region}", "image": str(paths[seqs[0]]), "mask": mask_path, "classes": ("shape",), "zscore": False})
        for seq in seqs:
            tasks.append({"id": f"{seq}|{region}|base", "label": f"{seq.upper()} / {region}", "image": str(paths[seq]), "mask": mask_path, "classes": C.INTENSITY_CLASSES, "zscore": True})
    raw = engine.run_tasks(tasks, progress, 0.08, 0.9)
    features = {s: {} for s in seqs}; shape = {}
    errors = {}
    for key, res in raw.items():
        seq, region, _ = key.split("|")
        if not res["ok"]: errors[key] = res["error"]; continue
        if seq == "mask": shape[region] = res["features"].get("shape", {})
        else: features[seq][region] = res["features"]
    if not shape and not any(regions for regions in features.values()):
        raise RuntimeError("PyRadiomics did not return any features. Check the input mask and install the project requirements.")
    positions = np.argwhere(lab > 0)
    centroid = positions.mean(axis=0).tolist() if len(positions) else [None]*3
    xyz = [float(centroid[2]), float(centroid[1]), float(centroid[0])]
    world = seg_img.TransformContinuousIndexToPhysicalPoint(xyz)
    label_volumes = {"net": int((lab == 1).sum()) * float(np.prod(seg_img.GetSpacing())), "edema": int((lab == 2).sum()) * float(np.prod(seg_img.GetSpacing())), "enhancing": int((lab == 4).sum()) * float(np.prod(seg_img.GetSpacing()))}
    return {"upload_id": upload_id, "sequences": seqs, "regions": {r:{"labels":labels_for_region[r],"voxels":counts[r],"volume_mm3":volumes[r],"available":r in masks} for r in regions}, "label_volumes": label_volumes, "features": features, "shape": shape, "errors": errors, "centroid_voxel_zyx": [round(float(v),2) for v in centroid], "centroid_world_xyz_mm": [round(float(v),2) for v in world], "normalisation": {s: engine.zscore_array(data["arrays"][s])[1] for s in seqs}}


@router.post("/analyze")
async def analyze_upload(files: list[UploadFile] = File(...), slots: str = Form(...), label_mapping: str = Form(...)) -> dict[str, Any]:
    """Analyze 1-4 named 3D NIfTI sequences and one segmentation. Values are explicit and mapped by the user."""
    try:
        slot_list = json.loads(slots)
        raw_map = json.loads(label_mapping)
        if not isinstance(slot_list, list) or not 1 <= len(slot_list) <= 4 or any(s not in SEQUENCES for s in slot_list) or len(set(slot_list)) != len(slot_list):
            raise ValueError("Choose one to four distinct sequence types.")
        if len(files) != len(slot_list) + 1:
            raise ValueError("Upload one segmentation and exactly one file for each selected sequence.")
        if not isinstance(raw_map, dict): raise ValueError("Label mapping must be an object such as {\"1\":\"net\",\"2\":\"edema\",\"4\":\"enhancing\"}.")
        label_map = {int(k): str(v) for k,v in raw_map.items()}
        if any(k <= 0 for k in label_map) or not label_map or len(label_map) > 16:
            raise ValueError("Map each non-zero segmentation label to one tumour sub-region.")
        uid = "upload-" + uuid.uuid4().hex[:16]
        work = C.UPLOADS / uid; work.mkdir(parents=True, exist_ok=False)
        paths, names = {}, {}
        assigned = [*slot_list, "seg"]
        try:
            for slot, file in zip(assigned, files):
                filename = Path(file.filename or "upload.nii.gz").name
                if not re.search(r"\.nii(\.gz)?$", filename, re.I): raise ValueError(f"{filename}: upload a .nii or .nii.gz file.")
                target = work / f"{slot}{'.nii.gz' if filename.lower().endswith('.nii.gz') else '.nii'}"
                size = 0
                with target.open("wb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES: raise ValueError(f"{filename} exceeds the 1 GB per-file upload limit.")
                        stream.write(chunk)
                paths[slot], names[slot] = target, filename
            report, data = _validation(paths, names, label_map)
            if not report["can_analyze"]:
                shutil.rmtree(work, ignore_errors=True)
                return {"validation": report, "results": None}
            result = _extract(uid, paths, data)
            result["validation"] = report
            result["sequence_files"] = {slot: Path(paths[slot]).name for slot in slot_list}
            (work / "result.json").write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
            db.save_uploaded_case(result)
            return {"validation": report, "results": result}
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{upload_id}/similar")
def similar_cases(upload_id: str) -> dict[str, Any]:
    """Shortlist by indexed geometry/volume, calculate missing reference cases, then compare full features."""
    if not re.fullmatch(r"upload-[a-f0-9]{16}", upload_id): raise HTTPException(400, "Invalid upload id.")
    result_path = C.UPLOADS / upload_id / "result.json"
    if not result_path.exists(): raise HTTPException(404, "Upload results expired or were deleted.")
    uploaded = json.loads(result_path.read_text(encoding="utf-8"))
    rows = db.query_cases({"validation": "pass"}, limit=500)["rows"]
    rows += db.query_cases({"validation": "warn"}, limit=500)["rows"]
    if not rows: raise HTTPException(409, "No validated reference cases are available. Index the reference database first.")
    target = uploaded["regions"]["whole_tumour"]["volume_mm3"] / 1000.0
    target_centroid = uploaded["centroid_voxel_zyx"]
    # Candidate ranking uses inexpensive indexed measurements. Coordinates are a coarse tie-breaker only.
    def shortlist(row):
        vol = max(float(row.get("vol_whole_ml") or 0), 1e-6)
        score = abs(math.log(max(target, 1e-6) / vol))
        xyz = [row.get("cz"), row.get("cy"), row.get("cx")]
        if all(v is not None for v in xyz) and all(v is not None for v in target_centroid):
            score += 0.15 * sum(abs(float(a)-float(b)) / 240 for a,b in zip(target_centroid, xyz))
        return score
    candidates = sorted(rows, key=shortlist)[:5]
    full = []
    for row in candidates:
        cid = row["case_id"]
        if row.get("calc_status") != "done":
            if row.get("index_status") == "fail": continue
            pipeline.calculate_case(cid, lambda *_: None)
        reference = db.load_features(cid)
        diffs, by_sequence, evidence = [], {}, []
        shared = 0
        for seq in uploaded["sequences"]:
            for region, families in uploaded["features"].get(seq, {}).items():
                other = reference["features"].get(seq, {}).get(region, {})
                for family, values in families.items():
                    for name, value in values.items():
                        rv = other.get(family, {}).get(name)
                        if rv is None or not math.isfinite(float(rv)): continue
                        denom = max(abs(float(value)), abs(float(rv)), 1e-8)
                        difference = min(abs(float(value)-float(rv))/denom, 10.0)
                        diffs.append(difference)
                        by_sequence.setdefault(seq, []).append(difference)
                        evidence.append({"sequence": seq, "region": region, "feature": f"{family}_{name}", "uploaded_value": float(value), "reference_value": float(rv), "relative_difference_pct": round(difference * 100, 1)})
                        shared += 1
        if not shared: continue
        mean_relative_difference = sum(diffs)/shared
        full.append({"case_id": cid, "similarity_percent": round(100/(1+mean_relative_difference), 1), "shared_feature_values": shared,
                     "by_sequence_similarity": {s: round(100/(1+sum(v)/len(v)), 1) for s,v in by_sequence.items() if v},
                     "closest_features": sorted(evidence, key=lambda x: x["relative_difference_pct"])[:5],
                     "volume_ml": row.get("vol_whole_ml"), "dominant": row.get("dominant"), "hemisphere": row.get("hemisphere")})
    full.sort(key=lambda x: x["similarity_percent"], reverse=True)
    return {"cases": full[:5], "shortlisted": len(candidates), "basis": "Ranked by mean relative difference across shared PyRadiomics values for uploaded sequences and available sub-regions. Descriptive numeric similarity only; not a diagnosis.", "sequences": uploaded["sequences"]}


@router.get("/recent")
def recent_uploads() -> dict[str, Any]:
    """List persisted private uploads so users can reopen results without recalculating."""
    return {"uploads": db.list_uploaded_cases()}


@router.get("/{upload_id}")
def get_upload(upload_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"upload-[a-f0-9]{16}", upload_id): raise HTTPException(400, "Invalid upload id.")
    result = db.load_uploaded_case(upload_id)
    if result is None: raise HTTPException(404, "Upload results were deleted or are unavailable.")
    return result


@router.delete("/{upload_id}")
def delete_upload(upload_id: str) -> dict:
    if not re.fullmatch(r"upload-[a-f0-9]{16}", upload_id): raise HTTPException(400, "Invalid upload id.")
    path = C.UPLOADS / upload_id
    if path.exists(): shutil.rmtree(path)
    db.delete_uploaded_case(upload_id)
    return {"deleted": upload_id}


def _upload_result(upload_id: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"upload-[a-f0-9]{16}", upload_id): raise HTTPException(400, "Invalid upload id.")
    work = C.UPLOADS / upload_id
    path = work / "result.json"
    if not path.exists(): raise HTTPException(404, "Upload results expired or were deleted.")
    return work, json.loads(path.read_text(encoding="utf-8"))


@router.get("/{upload_id}/volume/{name}")
def upload_volume(upload_id: str, name: str) -> FileResponse:
    """Serve only the uploaded case's own selected NIfTI volume or canonicalized segmentation."""
    work, result = _upload_result(upload_id)
    allowed = set(result["sequences"]) | {"seg"}
    if name not in allowed: raise HTTPException(404, "Volume not found.")
    paths = sorted(work.glob(f"{name}.nii*"))
    if not paths: raise HTTPException(404, "Volume file not found.")
    return FileResponse(paths[0], media_type="application/octet-stream", filename=paths[0].name, headers={"Cache-Control": "no-store"})


@router.get("/{upload_id}/percentiles")
def upload_percentiles(upload_id: str, region: str = "whole_tumour") -> dict[str, Any]:
    """Place uploaded feature values within the currently calculated reference cohort."""
    if region not in C.REGIONS: raise HTTPException(400, "Unknown region.")
    _work, uploaded = _upload_result(upload_id)
    import analysis
    ids = db.calculated_ids()
    if len(ids) < analysis.MIN_COHORT:
        return {"n": len(ids), "note": f"Percentiles need at least {analysis.MIN_COHORT} calculated reference cases (have {len(ids)}).", "values": {}}
    frame = db.cohort_frame(ids)
    out: dict[str, dict] = {}
    for seq, regions in uploaded.get("features", {}).items():
        families = regions.get(region, {})
        for family, values in families.items():
            for name, value in values.items():
                col = f"{seq}|{region}|{family}_{name}"
                if col not in frame.columns: continue
                series = frame[col].dropna()
                if len(series) < analysis.MIN_COHORT: continue
                val = float(value); below, equal = float((series < val).sum()), float((series == val).sum())
                out.setdefault(seq, {})[f"{family}_{name}"] = {"value": val, "percentile": round((below + .5 * equal) / len(series) * 100, 1), "n": int(len(series)), "median": float(series.median()), "q1": float(series.quantile(.25)), "q3": float(series.quantile(.75))}
    return {"n": len(ids), "note": f"Uploaded case placed among {len(ids)} calculated MSD reference cases. Percentiles are descriptive, not diagnostic.", "values": out}
