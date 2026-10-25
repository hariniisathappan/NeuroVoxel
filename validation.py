"""Strict validation of one staged MSD Task01 case (four sequences + one segmentation with internal labels 1, 2, 4).

The report is deliberately worded "consistent with BraTS format": the checks show the files agree with each other and
with the BraTS conventions NeuroVoxel supports. They cannot prove the official pipeline produced the data.

Check statuses
  pass : fine
  warn : allowed, but the user must read it (shown in the report and stored with the results)
  fail : blocks the analysis
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk

import config as C

PASS, WARN, FAIL = "pass", "warn", "fail"

# ------------------------------------------------------------------ file-name -> expected role
_ROLE_WORDS = {
    "seg": {"seg", "mask", "label", "labels", "segmentation", "gt"},
    "flair": {"flair"},
    "t1ce": {"t1ce", "t1c", "t1gd", "t1post", "t1contrast"},
    "t2": {"t2", "t2w"},
    "t1": {"t1", "t1w", "t1pre"},
}


def detect_role(filename: str | None) -> str | None:
    """Best guess of the slot a BraTS-style file name belongs to, or None if unknown or ambiguous."""
    name = re.sub(r"\.nii(\.gz)?$", "", (filename or "").lower())
    tokens = {t for t in re.split(r"[^a-z0-9]+", name) if t}
    if "t1" in tokens and tokens & {"ce", "c", "gd"}:
        return "t1ce"
    hits = [role for role, words in _ROLE_WORDS.items() if tokens & words]
    return hits[0] if len(hits) == 1 else None


class _Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, cid: str, title: str, status: str, detail: str, **extra: Any) -> None:
        self.checks.append({"id": cid, "title": title, "status": status, "detail": detail, **extra})

    @property
    def status(self) -> str:
        states = {c["status"] for c in self.checks}
        return FAIL if FAIL in states else WARN if WARN in states else PASS


def _fmt(values) -> str:
    return "(" + ", ".join(f"{float(v):g}" for v in values) + ")"


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    total = int(a.sum()) + int(b.sum())
    return 1.0 if total == 0 else 2.0 * int((a & b).sum()) / total


def validate_case(paths: dict[str, Path], names: dict[str, str] | None = None) -> dict[str, Any]:
    """Run every check. `paths` maps slot -> file; `names` maps slot -> the user's original file name."""
    names = names or {}
    rep = _Report()
    from engine import versions  # local import keeps this module light

    out: dict[str, Any] = {
        "validated_at": datetime.now(timezone.utc).isoformat(), "wording": C.WORDING, "software": versions(),
        "geometry": None, "label_voxels": {}, "regions": {}, "slots": {s: names.get(s) for s in C.SLOTS},
        "thresholds": {"min_region_voxels": C.MIN_REGION_VOXELS, "expected_size": list(C.EXPECTED_SIZE),
                       "expected_spacing_mm": list(C.EXPECTED_SPACING)},
    }

    def finish() -> dict[str, Any]:
        counts = {s: sum(1 for c in rep.checks if c["status"] == s) for s in (PASS, WARN, FAIL)}
        out.update(status=rep.status, checks=rep.checks, summary=counts, can_analyze=rep.status != FAIL)
        return out

    # ---- 1. all five slots present
    missing = [s for s in C.SLOTS if s not in paths or not Path(paths[s]).exists()]
    if missing:
        rep.add("slots", "All five files supplied", FAIL, "Missing: " + ", ".join(missing) + ".")
        return finish()
    rep.add("slots", "All five files supplied", PASS, "T1, T1c, T2, FLAIR and the segmentation were all found.")

    # ---- 2. file name agrees with the slot it was placed in
    problems, unknown = [], []
    for slot in (C.SLOTS if names else ()):
        role = detect_role(names.get(slot))
        if role is None:
            unknown.append(slot)
        elif role != slot:
            problems.append(f"'{names.get(slot)}' looks like {role.upper()} but was placed in the {slot.upper()} slot")
    if not names:
        pass          # staged from dataset.json: the channel order comes from the dataset's own metadata, not from file names
    elif problems:
        rep.add("slot_names", "File names match their slots", FAIL, "; ".join(problems) + ".")
    elif unknown:
        rep.add("slot_names", "File names match their slots", WARN,
                "The file name of " + ", ".join(s.upper() for s in unknown) + " does not say which sequence it is, "
                "so the slot assignment could not be cross-checked. Make sure each file is in the right slot.")
    else:
        rep.add("slot_names", "File names match their slots", PASS, "Every file name agrees with the slot it was placed in.")

    # ---- 3. readable, finite, 3D NIfTI
    imgs: dict[str, sitk.Image] = {}
    arrs: dict[str, np.ndarray] = {}
    unreadable: list[str] = []
    for slot in C.SLOTS:
        try:
            img = sitk.ReadImage(str(paths[slot]))
            if img.GetDimension() != 3:
                raise ValueError(f"{img.GetDimension()}-dimensional data; a 3D volume is required")
            if img.GetNumberOfComponentsPerPixel() != 1:
                raise ValueError("multi-component voxels; a scalar volume is required")
            imgs[slot], arrs[slot] = img, sitk.GetArrayFromImage(img)       # arrays are (z, y, x)
        except Exception as error:  # noqa: BLE001 - reported to the user
            unreadable.append(f"{slot.upper()}: {str(error).splitlines()[0][:140]}")
    if unreadable:
        rep.add("readable", "Files are readable 3D NIfTI volumes", FAIL, "; ".join(unreadable) + ".")
        return finish()
    rep.add("readable", "Files are readable 3D NIfTI volumes", PASS, "All five files opened as single-channel 3D volumes.")

    nonfinite = [s.upper() for s in C.SLOTS if not np.isfinite(arrs[s]).all()]
    rep.add("finite", "All voxel values are finite", FAIL if nonfinite else PASS,
            ("NaN or infinite values in " + ", ".join(nonfinite) + ".") if nonfinite else "No NaN or infinite values.")
    if nonfinite:
        return finish()

    # ---- 4. geometry shared by all five
    ref, diffs = imgs["t1"], []
    for slot in C.SLOTS[1:]:
        im = imgs[slot]
        if im.GetSize() != ref.GetSize():
            diffs.append(f"{slot.upper()} size {im.GetSize()} vs T1 {ref.GetSize()}")
        if not np.allclose(im.GetSpacing(), ref.GetSpacing(), atol=1e-4):
            diffs.append(f"{slot.upper()} spacing {_fmt(im.GetSpacing())} vs T1 {_fmt(ref.GetSpacing())}")
        if not np.allclose(im.GetOrigin(), ref.GetOrigin(), atol=1e-3):
            diffs.append(f"{slot.upper()} origin {_fmt(im.GetOrigin())} vs T1 {_fmt(ref.GetOrigin())}")
        if not np.allclose(im.GetDirection(), ref.GetDirection(), atol=1e-4):
            diffs.append(f"{slot.upper()} orientation differs from T1")
    out["geometry"] = {"size": list(ref.GetSize()), "spacing_mm": list(ref.GetSpacing()),
                       "origin": list(ref.GetOrigin()), "direction": list(ref.GetDirection())}
    rep.add("geometry", "Size, spacing, origin and orientation are identical in all five files",
            FAIL if diffs else PASS, ("; ".join(diffs) + ".") if diffs else
            f"All five files share size {_fmt(ref.GetSize())}, spacing {_fmt(ref.GetSpacing())} mm, origin and orientation.")
    if diffs:
        return finish()

    spacing_ok = np.allclose(ref.GetSpacing(), C.EXPECTED_SPACING, atol=1e-3)
    rep.add("spacing", "Voxel spacing is 1 mm isotropic", PASS if spacing_ok else FAIL,
            "Spacing is " + _fmt(ref.GetSpacing()) + " mm." + ("" if spacing_ok else " BraTS data are resampled to 1 x 1 x 1 mm."))
    size_ok = tuple(ref.GetSize()) == C.EXPECTED_SIZE
    rep.add("size", "Volume size is 240 x 240 x 155", PASS if size_ok else WARN,
            f"Size is {_fmt(ref.GetSize())}." + ("" if size_ok else " This is not the standard BraTS size. Analysis can continue, "
                                                  "but the data may not follow the BraTS pipeline."))
    direction = np.array(ref.GetDirection()).reshape(3, 3)
    aligned = bool((np.abs(direction).max(axis=0) > 0.99).all())
    rep.add("orientation", "Axes are aligned with the scanner grid", PASS if aligned else WARN,
            "Direction cosines are axis-aligned." if aligned else "The volume is oblique; anatomical labels in the viewer may be approximate.")

    # ---- 5. segmentation labels
    seg = arrs["seg"]
    integer_valued = bool(np.all(seg == np.rint(seg)))
    lab = np.rint(seg).astype(np.int32)
    present = sorted(int(v) for v in np.unique(lab))
    bad = [v for v in present if v not in C.ALLOWED_LABELS]
    hint = " BraTS 2023+ uses label 3 for enhancing tumour; NeuroVoxel supports the 2017-2021 scheme (1, 2, 4)." if 3 in bad and 4 not in present else ""
    rep.add("labels", "Segmentation labels are only the three tumour labels",
            FAIL if (bad or not integer_valued) else PASS,
            ("Labels found: " + ", ".join(map(str, present)) + "." + (" Non-integer values present." if not integer_valued else "")
             + (f" Not allowed: {', '.join(map(str, bad))}." if bad else "") + hint))
    label_voxels = {int(v): int((lab == v).sum()) for v in present if v != 0}
    out["label_voxels"] = label_voxels
    tumour = int((lab > 0).sum())
    rep.add("mask_nonempty", "Segmentation is not empty", PASS if tumour else FAIL,
            f"{tumour:,} labelled voxels." if tumour else "The segmentation contains no tumour voxels.")
    voxel_mm3 = float(np.prod(ref.GetSpacing()))
    regions: dict[str, Any] = {}
    for name, labels in C.REGIONS.items():
        n = int(np.isin(lab, labels).sum())
        regions[name] = {"labels": list(labels), "voxels": n, "volume_mm3": round(n * voxel_mm3, 2), "available": n >= C.MIN_REGION_VOXELS}
    out["regions"] = regions
    whole = regions["whole_tumour"]
    skipped = [C.REGION_LABELS[k] for k, r in regions.items() if not r["available"]]
    if not whole["available"]:
        rep.add("region_size", f"Regions have at least {C.MIN_REGION_VOXELS} voxels", FAIL,
                f"The whole tumour has only {whole['voxels']} voxels, below the minimum of {C.MIN_REGION_VOXELS}.")
    elif skipped:
        # a small/absent sub-region is a property of this tumour, not a file problem -> PASS with a clear note
        rep.add("region_size", f"Regions have at least {C.MIN_REGION_VOXELS} voxels", PASS,
                "Note: these regions are too small or absent in this tumour and are skipped for radiomics: " + "; ".join(skipped) + ". "
                "(Normal: low-grade gliomas often have no enhancing tumour. Not a data problem.)")
    else:
        rep.add("region_size", f"Regions have at least {C.MIN_REGION_VOXELS} voxels", PASS,
                "Whole tumour, tumour core and enhancing tumour are all large enough.")
    if bad or not tumour:
        return finish()

    # ---- 6. image content: skull-stripped appearance, not constant, not duplicated
    fg = {s: arrs[s] != 0 for s in C.SEQUENCES}
    stats: dict[str, tuple[float, float]] = {}
    strip_issues, constant_issues, negative = [], [], []
    for s in C.SEQUENCES:
        a = arrs[s]
        shell = np.concatenate([a[0].ravel(), a[-1].ravel(), a[:, 0].ravel(), a[:, -1].ravel(), a[:, :, 0].ravel(), a[:, :, -1].ravel()])
        zero_fraction = float((~fg[s]).mean())
        if np.any(shell != 0) or zero_fraction < C.MIN_ZERO_FRACTION:
            strip_issues.append(f"{s.upper()} (background zero fraction {zero_fraction:.0%}, "
                                f"{'non-zero values on the volume border' if np.any(shell != 0) else 'border is zero'})")
        values = a[fg[s]].astype(np.float64)
        if values.size == 0 or float(values.max() - values.min()) == 0.0 or np.unique(values[:: max(1, values.size // 200000)]).size < 8:
            constant_issues.append(s.upper())
            stats[s] = (0.0, 1.0)
            continue
        stats[s] = (float(values.mean()), float(values.std()))
        if float(a.min()) < 0:
            negative.append(s.upper())
    rep.add("skull_stripped", "Looks skull-stripped (background exactly zero)", WARN if strip_issues else PASS,
            ("Background is not exactly zero in: " + "; ".join(strip_issues) + ". BraTS volumes are skull-stripped with a zero background.")
            if strip_issues else "The background of all four sequences is exactly zero, as in skull-stripped BraTS volumes.")
    rep.add("not_constant", "Each sequence has real intensity variation", FAIL if constant_issues else PASS,
            ("Constant or label-like content in: " + ", ".join(constant_issues) + ".") if constant_issues else "All four sequences vary within the brain.")
    if negative:
        rep.add("non_negative", "Intensities are non-negative", WARN, "Negative values in " + ", ".join(negative) + "; BraTS intensities are normally non-negative.")
    if constant_issues:
        return finish()

    same, near = [], []
    union = np.logical_or.reduce([fg[s] for s in C.SEQUENCES])
    idx = np.flatnonzero(union.ravel())[::5]
    for a_s, b_s in combinations(C.SEQUENCES, 2):
        if np.array_equal(arrs[a_s], arrs[b_s]):
            same.append(f"{a_s.upper()} = {b_s.upper()}")
        else:
            r = float(np.corrcoef(arrs[a_s].ravel()[idx].astype(np.float64), arrs[b_s].ravel()[idx].astype(np.float64))[0, 1])
            if r > 0.999:
                near.append(f"{a_s.upper()}/{b_s.upper()} (r = {r:.4f})")
    rep.add("distinct", "The four sequences are different images", FAIL if same else WARN if near else PASS,
            ("Identical voxel data: " + "; ".join(same) + ".") if same else
            ("Almost identical: " + "; ".join(near) + ". Check that the right files were uploaded.") if near else "No two sequences are identical.")
    if same:
        return finish()

    # ---- 7. brain extents agree between sequences; mask lies inside the brain
    dice = {f"{a}/{b}": _dice(fg[a], fg[b]) for a, b in combinations(C.SEQUENCES, 2)}
    worst_pair, worst = min(dice.items(), key=lambda kv: kv[1])
    rep.add("brain_agreement", "Brain extents agree between sequences",
            WARN if worst < C.BRAIN_DICE_PASS else PASS,
            f"Lowest overlap of the non-zero brain between two sequences is {worst:.3f} ({worst_pair.upper()}). "
            + ("Sequences do not seem to be in the same space." if worst < C.BRAIN_DICE_FAIL else ""))
    inside = float((lab > 0)[np.logical_or.reduce([fg[s] for s in C.SEQUENCES])].sum()) / tumour
    rep.add("mask_in_brain", "Segmentation lies inside the brain",
            PASS if inside >= C.MASK_INSIDE_BRAIN_PASS else WARN,
            f"{inside:.1%} of labelled voxels are inside the non-zero brain (any of the four sequences)."
            + ("" if inside >= C.MASK_INSIDE_BRAIN_WARN else " The mask may be shifted, flipped or from a different scan."))

    # ---- 8. plausibility (heuristic; warnings only, never a proof)
    z = {s: (arrs[s].astype(np.float64) - stats[s][0]) / stats[s][1] for s in C.SEQUENCES}
    notes, good = [], []
    n_edema = int((lab == 2).sum())
    if n_edema >= C.MIN_REGION_VOXELS:
        m = float(np.median(z["flair"][lab == 2]))
        (good if m > C.FLAIR_EDEMA_Z else notes).append(f"median FLAIR in label 2 is {m:+.2f} SD from the brain mean")
    n_et, n_nec = int((lab == 4).sum()), int((lab == 1).sum())
    if n_et >= C.MIN_REGION_VOXELS:
        gain = float(np.median(z["t1ce"][lab == 4]) - np.median(z["t1"][lab == 4]))
        (good if gain > C.ENHANCE_Z else notes).append(f"label 4 is {gain:+.2f} SD brighter on T1c than on T1")
        if n_nec >= C.MIN_REGION_VOXELS:
            dark = float(np.median(z["t1ce"][lab == 1]) - np.median(z["t1ce"][lab == 4]))
            (good if dark < 0 else notes).append(f"on T1c, label 1 minus label 4 is {dark:+.2f} SD")
    if notes:
        rep.add("plausibility", "Mask and images are biologically consistent (heuristic)", WARN,
                "Unexpected: " + "; ".join(notes) + ". This can happen with swapped labels, a shifted or flipped mask, "
                "swapped sequences, or an unusual tumour. It is a warning, not a proof of error.")
    elif good:
        rep.add("plausibility", "Mask and images are biologically consistent (heuristic)", PASS, "; ".join(good).capitalize() + ".")
    else:
        rep.add("plausibility", "Mask and images are biologically consistent (heuristic)", WARN, "Regions were too small for the plausibility checks.")

    return finish()
