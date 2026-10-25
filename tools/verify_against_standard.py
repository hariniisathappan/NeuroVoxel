#!/usr/bin/env python3
"""
NeuroVoxel verification: are the numbers shown on the website the numbers PyRadiomics gives?

Usage (website running, dataset mounted; inside Docker this is the easiest way):

    docker compose exec neurovoxel python tools/verify_against_standard.py BRATS_001

Outside Docker (needs: pip install nibabel SimpleITK numpy pyradiomics):

    python tools/verify_against_standard.py BRATS_001 --dataset E:/Dataset_tumor/Task01_BrainTumour --api http://127.0.0.1:8000

The case must already be CALCULATED on the website. Nothing from NeuroVoxel's own code is used for the checks
(only the stored numbers are read, through the same web API your browser uses).

PART A  The international standard (IBSI-1 digital phantom, CC-BY 4.0, files in reference/ibsi_phantom/).
        The phantom is run through plain PyRadiomics and compared with the published IBSI reference values.
PART B  Independent re-run on the real case. The 4D MSD file is read directly, split with the channel order from
        dataset.json, masks are built from the raw labels with the label names from dataset.json, each sequence is
        z-scored (non-zero voxels), and plain PyRadiomics is run with the settings printed below. Every feature is
        compared with what the website stored.
PART C  Same normalisation numbers (brain voxels, mean, SD) as the website.

Output: a table on screen plus verification_<case>.md and verification_<case>.csv in the current folder.
Exit code 0 = everything passed, 1 = something differs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import nibabel as nib
import numpy as np
import SimpleITK as sitk

HERE = Path(__file__).resolve().parent
PHANTOM = HERE.parent / "reference" / "ibsi_phantom"

# ---------------------------------------------------------------------------------------------------------------------
# The settings of the independent run. Written out in full on purpose so a reviewer can read them.
# (NeuroVoxel additionally sets preCrop=True and additionalInfo=True; both are speed/metadata options and must not change values.)
SETTINGS = {"binWidth": 0.25, "normalize": False, "resampledPixelSpacing": None, "label": 1}
CLASSES = ["firstorder", "glcm", "glrlm", "glszm", "ngtdm", "gldm"]
MIN_REGION_VOXELS = 64                 # NeuroVoxel does not analyse regions smaller than this
REL_TOL = 1e-6                         # same library + same inputs: results should agree to ~1e-9, we allow 1e-6

# ---------------------------------------------------------------------------------------------------------------------
# IBSI-1 digital phantom reference values (IBSI reference manual, "digital phantom" column; tolerance as printed there).
# First-order values have no printed tolerance: we use half of the last printed digit.
IBSI_SHAPE = {            # name: (value, tolerance)
    "MeshVolume": (556, 4), "VoxelVolume": (592, 4), "SurfaceArea": (388, 3), "SurfaceVolumeRatio": (0.698, 0.004),
    "Sphericity": (0.843, 0.005), "Maximum3DDiameter": (13.1, 0.1), "MajorAxisLength": (11.4, 0.1),
    "MinorAxisLength": (9.31, 0.06), "LeastAxisLength": (8.54, 0.05), "Elongation": (0.816, 0.005), "Flatness": (0.749, 0.005)}
IBSI_FIRSTORDER = {       # name: (value, half-last-digit tolerance, note)
    "Mean": (2.15, 0.005, ""), "Variance": (3.05, 0.005, "population variance"), "Skewness": (1.08, 0.005, ""),
    "Kurtosis": (-0.355, 0.0005, "IBSI quotes EXCESS kurtosis; PyRadiomics reports kurtosis, so 3 is subtracted"),
    "Median": (1, 0.5, ""), "Minimum": (1, 0.5, ""), "10Percentile": (1, 0.5, ""), "90Percentile": (4, 0.5, ""),
    "Maximum": (6, 0.5, ""), "InterquartileRange": (3, 0.5, ""), "Range": (5, 0.5, ""),
    "MeanAbsoluteDeviation": (1.55, 0.005, ""), "RobustMeanAbsoluteDeviation": (1.11, 0.005, ""),
    "Energy": (567, 0.5, ""), "RootMeanSquared": (2.77, 0.005, "")}
AXES = ("MajorAxisLength", "MinorAxisLength", "LeastAxisLength")


def extractor(classes):
    from radiomics import featureextractor
    import logging
    logging.getLogger("radiomics").setLevel(logging.ERROR)
    ex = featureextractor.RadiomicsFeatureExtractor(**SETTINGS)
    ex.disableAllImageTypes()
    ex.enableImageTypeByName("Original")
    ex.disableAllFeatures()
    for c in classes:
        ex.enableFeatureClassByName(c)
    return ex


def flatten(result):
    out = {}
    for key, value in result.items():
        if not key.startswith("original_"):
            continue
        _, family, name = key.split("_", 2)
        try:
            v = complex(value)
            if abs(v.imag) > 1e-9:
                continue
            v = v.real
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.setdefault(family.lower(), {})[name] = v
    return out


# ---------------------------------------------------------------------------------------------------------------------
def part_a():
    """IBSI digital phantom through plain PyRadiomics."""
    if not (PHANTOM / "phantom.nii.gz").exists() or not (PHANTOM / "mask.nii.gz").exists():
        sys.exit(f"IBSI phantom files not found in {PHANTOM}. Put phantom.nii.gz and mask.nii.gz there (reference/ibsi_phantom/), "
                 "rebuild with docker compose up -d --build, or run with --skip-ibsi.")
    image = sitk.ReadImage(str(PHANTOM / "phantom.nii.gz"))
    mask = sitk.ReadImage(str(PHANTOM / "mask.nii.gz"))
    got = flatten(extractor(["shape", "firstorder"]).execute(image, mask, label=1))
    n = int(got["shape"]["VoxelVolume"] / (np.prod(image.GetSpacing())))
    rows = []
    for name, (ref, tol) in IBSI_SHAPE.items():
        v = got["shape"][name]
        ok = abs(v - ref) <= tol
        note = ""
        if not ok and name in AXES:
            corrected = v * math.sqrt(n / (n - 1))
            if abs(corrected - ref) <= tol:
                ok, note = True, (f"raw {v:.3f} is {abs(v - ref):.3f} below the IBSI value because PyRadiomics uses the sample covariance (N-1) "
                                  f"and IBSI uses N; x sqrt(N/(N-1)) = {corrected:.3f}, inside the tolerance")
                v = corrected
        rows.append(("shape", name, ref, tol, v, ok, note))
    for name, (ref, tol, note) in IBSI_FIRSTORDER.items():
        v = got["firstorder"][name] - (3 if name == "Kurtosis" else 0)
        rows.append(("firstorder", name, ref, tol, v, abs(v - ref) <= tol, note))
    return rows, n


# ---------------------------------------------------------------------------------------------------------------------
def read_dataset_json(dataset: Path):
    raw = json.loads((dataset / "dataset.json").read_text())
    channels = {}
    for idx, name in raw["modality"].items():
        n = re.sub(r"[^a-z0-9]", "", name.lower())
        key = "flair" if "flair" in n else "t1ce" if any(t in n for t in ("t1gd", "t1c", "t1ce", "gd")) else "t1" if "t1" in n else "t2" if "t2" in n else None
        channels[int(idx)] = key
    labels = {}
    for idx, name in raw["labels"].items():
        n = name.lower()
        if int(idx) == 0:
            continue
        labels["edema" if "edema" in n else "net" if ("non" in n and "enhanc" in n) or "necro" in n else "enhancing"] = int(idx)
    return channels, labels


def _task(spec):
    """Worker: one plain PyRadiomics extraction."""
    image = sitk.GetImageFromArray(spec["array"])
    image.SetSpacing(spec["spacing"]); image.SetOrigin(spec["origin"]); image.SetDirection(spec["direction"])
    mask = sitk.GetImageFromArray(spec["mask"].astype(np.uint8))
    mask.CopyInformation(image)
    return spec["id"], flatten(extractor(spec["classes"]).execute(image, mask, label=1))


def part_b(case: str, dataset: Path, jobs: int):
    channels, labels = read_dataset_json(dataset)
    img = nib.load(str(dataset / "imagesTr" / f"{case}.nii.gz"))
    lab = np.rint(np.asarray(nib.load(str(dataset / "labelsTr" / f"{case}.nii.gz")).dataobj)).astype(np.int16)
    zoom = [float(z) for z in img.header.get_zooms()[:3]]
    masks = {"whole_tumour": np.isin(lab, [labels["edema"], labels["net"], labels["enhancing"]]),
             "tumour_core": np.isin(lab, [labels["net"], labels["enhancing"]]),
             "enhancing_tumour": lab == labels["enhancing"]}
    # NIfTI array (x,y,z) -> SimpleITK array (z,y,x); geometry from the RAS affine converted to ITK's LPS
    aff = img.affine
    spacing = zoom
    direction_ras = aff[:3, :3] / np.array(spacing)
    lps = np.diag([-1, -1, 1]) @ direction_ras
    origin = tuple(float(v) for v in (np.diag([-1, -1, 1]) @ aff[:3, 3]))
    direction = tuple(float(v) for v in lps.flatten())
    specs, norm = [], {}
    sizes = {k: int(m.sum()) for k, m in masks.items()}
    t1ce_idx = next(i for i, k in channels.items() if k == "t1ce")
    t1ce = np.asarray(img.dataobj[..., t1ce_idx], dtype=np.float32)
    for i, seq in channels.items():
        vol = np.asarray(img.dataobj[..., i], dtype=np.float64)
        nz = vol != 0
        mean, sd = float(vol[nz].mean()), float(vol[nz].std())            # population SD over non-zero voxels
        z = np.zeros_like(vol); z[nz] = (vol[nz] - mean) / sd
        norm[seq] = {"brain_voxels": int(nz.sum()), "mean": mean, "sd": sd}
        for region, m in masks.items():
            if sizes[region] >= MIN_REGION_VOXELS:
                specs.append({"id": (seq, region), "array": np.transpose(z, (2, 1, 0)), "mask": np.transpose(m, (2, 1, 0)), "classes": CLASSES,
                              "spacing": tuple(spacing), "origin": origin, "direction": direction})
    for region, m in masks.items():
        if sizes[region] >= MIN_REGION_VOXELS:
            specs.append({"id": ("mask", region), "array": np.transpose(t1ce, (2, 1, 0)), "mask": np.transpose(m, (2, 1, 0)), "classes": ["shape"],
                          "spacing": tuple(spacing), "origin": origin, "direction": direction})
    print(f"  independent extraction: {len(specs)} runs on {jobs} process(es) ...", flush=True)
    results = {}
    if jobs > 1:
        import multiprocessing as mp
        with ProcessPoolExecutor(max_workers=jobs, mp_context=mp.get_context("spawn")) as pool:
            for k, (sid, feats) in enumerate(pool.map(_task, specs), 1):
                results[sid] = feats
                print(f"    {k}/{len(specs)}", end="\r", flush=True)
    else:
        for k, s in enumerate(specs, 1):
            sid, feats = _task(s); results[sid] = feats
            print(f"    {k}/{len(specs)}", end="\r", flush=True)
    print()
    return results, norm, sizes


# ---------------------------------------------------------------------------------------------------------------------
def fetch_website(case: str, api: str):
    url = f"{api.rstrip('/')}/api/cases/{case}/results"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"The website answered {e.code} for {url}. Is {case} calculated (CALCULATE button)?")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Cannot reach the website at {api} ({e}). Start it first, or pass --api http://host:port or --results-json file.json")


def compare(site: dict, mine: dict, sizes: dict):
    rows = []
    for (seq, region), fams in mine.items():
        for fam, feats in fams.items():
            for name, b in feats.items():
                if seq == "mask":
                    a = (site.get("shape", {}).get(region) or {}).get(name)
                else:
                    a = ((((site.get("features", {}).get(seq) or {}).get(region)) or {}).get(fam) or {}).get(name)
                if a is None:
                    rows.append((seq, region, fam, name, None, b, None, None, "missing on website")); continue
                diff = abs(a - b); rel = diff / max(abs(a), abs(b), 1e-12)
                rows.append((seq, region, fam, name, a, b, diff, rel, "ok" if (rel <= REL_TOL or diff <= 1e-9) else "DIFFERENT"))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", help="case id, e.g. BRATS_001")
    ap.add_argument("--dataset", default=os.getenv("NV_DATASET_DIR", "/data/msd"), help="folder holding dataset.json, imagesTr, labelsTr")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="NeuroVoxel web address")
    ap.add_argument("--results-json", help="use a saved copy of /api/cases/<id>/results instead of the web address")
    ap.add_argument("--jobs", type=int, default=4, help="parallel PyRadiomics processes")
    ap.add_argument("--skip-ibsi", action="store_true")
    a = ap.parse_args()
    dataset, case = Path(a.dataset), a.case
    import radiomics
    lines = [f"# NeuroVoxel verification: {case}", f"Run {datetime.now():%Y-%m-%d %H:%M}. PyRadiomics {radiomics.__version__}, SimpleITK {sitk.Version.VersionString()}, numpy {np.__version__}.", ""]
    ok_all = True

    if not a.skip_ibsi:
        print("PART A  IBSI-1 digital phantom (international standard)")
        rows, n = part_a()
        bad = [r for r in rows if not r[5]]
        for fam, name, ref, tol, v, ok, note in rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {fam:10s} {name:28s} IBSI {ref:>8g} +/- {tol:<7g} got {v:>10.4f}" + (f"   <- {note}" if note else ""))
        print(f"  => {len(rows) - len(bad)}/{len(rows)} IBSI reference values reproduced\n")
        ok_all &= not bad
        lines += ["## A. IBSI-1 digital phantom (standard reference values)", "Phantom: 5x4x4 voxels, 2 mm, 74 ROI voxels (reference/ibsi_phantom, CC-BY 4.0). Reference values and tolerances: IBSI reference manual, digital-phantom column.", "",
                  "| Class | Feature | IBSI value | Tolerance | PyRadiomics | Result | Note |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {f} | {nm} | {r:g} | ±{t:g} | {v:.4f} | {'PASS' if ok else 'FAIL'} | {note} |" for f, nm, r, t, v, ok, note in rows]
        lines += ["", f"**{len(rows) - len(bad)} of {len(rows)} reference values reproduced.**", ""]

    print(f"PART B  independent PyRadiomics run on {case} versus the website")
    site = json.loads(Path(a.results_json).read_text()) if a.results_json else fetch_website(case, a.api)
    mine, norm, sizes = part_b(case, dataset, max(1, a.jobs))
    rows = compare(site, mine, sizes)
    n_ok = sum(1 for r in rows if r[8] == "ok"); n_bad = [r for r in rows if r[8] != "ok"]
    exact = sum(1 for r in rows if r[7] is not None and r[7] <= 1e-9)
    worst = max((r for r in rows if r[7] is not None), key=lambda r: r[7], default=None)
    print(f"  regions (voxels): {sizes}")
    print(f"  compared {len(rows)} feature values: {n_ok} agree (relative difference <= {REL_TOL:g}), {exact} agree to <= 1e-9, {len(n_bad)} differ")
    if worst:
        print(f"  largest relative difference: {worst[7]:.2e}  ({worst[0]} / {worst[1]} / {worst[2]}_{worst[3]})")
    for r in n_bad[:15]:
        print(f"    DIFFERENT {r[0]}/{r[1]}/{r[2]}_{r[3]}: website {r[4]} vs independent {r[5]}  ({r[8]})")
    ok_all &= not n_bad and len(rows) > 0

    print("\nPART C  normalisation numbers")
    lines += [f"## B. Independent re-run on {case}", "The 4D MSD file was read directly, channels split by dataset.json, masks built from raw labels, each sequence z-scored over non-zero voxels, plain PyRadiomics run with:",
              f"`{json.dumps(SETTINGS)}`, Original image only, classes {', '.join(CLASSES)}, shape from the unnormalised T1c with the mask only.", "",
              f"Region sizes (voxels): {sizes}", "", f"- Feature values compared: **{len(rows)}**", f"- Agree within relative {REL_TOL:g}: **{n_ok}**  (agree to 1e-9: {exact})",
              f"- Differ: **{len(n_bad)}**", f"- Largest relative difference: **{worst[7]:.2e}**" if worst else "", "", "### Per sequence / region", "", "| Sequence | Region | Features | Agree | Largest rel. diff |", "|---|---|---|---|---|"]
    groups = {}
    for r in rows:
        groups.setdefault((r[0], r[1]), []).append(r)
    for (seq, region), g in sorted(groups.items()):
        w = max((x[7] for x in g if x[7] is not None), default=0)
        lines.append(f"| {seq} | {region} | {len(g)} | {sum(1 for x in g if x[8] == 'ok')} | {w:.1e} |")
    lines += ["", "### Normalisation (z-score over non-zero voxels)", "", "| Sequence | Brain voxels (independent) | Brain voxels (website) | Mean (independent) | Mean (website) | SD (independent) | SD (website) | Result |", "|---|---|---|---|---|---|---|---|"]
    site_norm = site.get("normalisation") or {}
    for seq, nv in norm.items():
        w = site_norm.get(seq) or {}
        good = bool(w) and w.get("brain_voxels") == nv["brain_voxels"] and math.isclose(w["mean"], nv["mean"], rel_tol=1e-6) and math.isclose(w["sd"], nv["sd"], rel_tol=1e-6)
        ok_all &= good
        print(f"  {'PASS' if good else 'FAIL'}  {seq:6s} brain voxels {nv['brain_voxels']} / {w.get('brain_voxels')}   mean {nv['mean']:.6f} / {w.get('mean')}   sd {nv['sd']:.6f} / {w.get('sd')}")
        lines.append(f"| {seq} | {nv['brain_voxels']} | {w.get('brain_voxels')} | {nv['mean']:.6f} | {w.get('mean')} | {nv['sd']:.6f} | {w.get('sd')} | {'PASS' if good else 'FAIL'} |")
    if n_bad:
        lines += ["", "### Features that differ", "", "| Sequence | Region | Feature | Website | Independent | Note |", "|---|---|---|---|---|---|"]
        lines += [f"| {r[0]} | {r[1]} | {r[2]}_{r[3]} | {r[4]} | {r[5]} | {r[8]} |" for r in n_bad]
    lines += ["", "## What this does and does not show", "",
              "- Part A shows that the PyRadiomics build used here reproduces the published IBSI reference values for shape and first-order features (the three axis lengths use PyRadiomics' N-1 covariance convention and match after that factor).",
              "- Part B shows that the website reports exactly what plain PyRadiomics computes for this case with the stated settings; NeuroVoxel adds no hidden processing.",
              "- It does not show clinical validity, and it does not prove that the z-score normalisation is the best choice; it proves that the choice is applied as described.",
              "- Texture families (GLCM, GLRLM, GLSZM, NGTDM, GLDM) are PyRadiomics' own implementations; IBSI texture benchmarks depend on the discretisation configuration and are not repeated here.", ""]
    verdict = "ALL CHECKS PASSED" if ok_all else "SOMETHING DIFFERS: read the table above"
    lines += [f"## Verdict: {verdict}"]
    md = Path(f"verification_{case}.md"); md.write_text("\n".join(lines), encoding="utf-8")
    with open(f"verification_{case}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["sequence", "region", "family", "feature", "website", "independent", "abs_diff", "rel_diff", "status"]); w.writerows(rows)
    print(f"\n{verdict}\nWritten: {md.resolve()} and verification_{case}.csv")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
