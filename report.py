"""Automatic methods / reproducibility report for one case (Markdown)."""
from __future__ import annotations

import json

import config as C
import db

ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


def methods_report(token: str) -> str:
    case, report = db.get_case(token), db.get_report(token)
    if not case or not report:
        raise KeyError(token)
    res = db.load_features(token)
    sens = db.load_sensitivity(token)
    n_features = sum(len(v) for seq in res["features"].values() for reg in seq.values() for v in reg.values())
    n_shape = sum(len(v) for v in res["shape"].values())
    L: list[str] = []
    a = L.append
    a(f"# NeuroVoxel methods and reproducibility report\n")
    a(f"- Case ID: `{token}`")
    a(f"- Calculated (UTC): {case['calculated_at']}")
    a(f"- Dataset: {db.get_meta('dataset_name', 'MSD Task01 BrainTumour')} (CC-BY-SA 4.0), label mapping: {json.dumps((db.get_meta('label_mapping') or {}).get('map'))}, channel order: {(db.get_meta('channel_order') or {}).get('order')}")
    a(f"- NeuroVoxel version: {case['app_version']}")
    a(f"- Research use only. No diagnosis, prediction or clinical interpretation is made.\n")
    a("## 1. Input")
    a(f"- Source image: `{case['image_rel']}`; label: `{case['label_rel']}` (read-only dataset folder; channels split and labels renamed to the internal 1/2/4 convention before analysis)")
    geo = report.get("geometry") or {}
    if geo:
        a(f"- Grid: size {geo['size']}, spacing {geo['spacing_mm']} mm\n")
    a("## 2. Input validation")
    a(f"Overall result: **{report['status'].upper()}**  ({report['summary']['pass']} pass, {report['summary']['warn']} warn, {report['summary']['fail']} fail)\n")
    a(f"> {report['wording']}\n")
    for c in report["checks"]:
        a(f"- **{ICON[c['status']]}** {c['title']}: {c['detail']}")
    a("\n## 3. Regions (internal labels: 1 non-enhancing/necrotic, 2 edema, 4 enhancing; MSD numbers 2, 1, 3 respectively)")
    a("| Region | Labels | Voxels | Volume (mL) | Analysed |\n|---|---|---|---|---|")
    for name, r in res["regions"].items():
        a(f"| {C.REGION_LABELS.get(name, name)} | {list(C.REGIONS[name])} | {r['voxels']:,} | {r['volume_ml']:.3f} | {'yes' if r['included'] else 'no: ' + r['note']} |")
    a("\n## 4. Intensity normalisation and feature extraction")
    a(f"- Normalisation: {C.NORMALISATION}. Applied identically to all four sequences.")
    a("| Sequence | Brain voxels | Mean | SD |\n|---|---|---|---|")
    for s, n in res["normalisation"].items():
        a(f"| {C.SEQUENCE_LABELS[s]} | {n['brain_voxels']:,} | {n['mean']:.4f} | {n['sd']:.4f} |")
    a(f"- PyRadiomics settings: `{json.dumps(C.RADIOMICS_SETTINGS)}` (image type: Original only; feature classes: {', '.join(C.FEATURE_CLASSES)})")
    a(f"- Discretisation: fixed bin width {C.BIN_WIDTH} on z-scored intensities. PyRadiomics' own normalisation is disabled because it would include the zero background.")
    a(f"- Shape features depend only on the mask and are computed once per region ({n_shape} values); intensity and texture features are computed per sequence and region ({n_features - 0} values in total across sequences).")
    a(f"- Minimum region size: {C.MIN_REGION_VOXELS} voxels. Smaller regions are excluded and reported.")
    a("- First-order Energy, TotalEnergy and RootMeanSquared depend on the intensity scale; they are comparable only between cases normalised the same way.")
    a("\n## 5. Software")
    for k, v in report.get("software", {}).items():
        a(f"- {k}: {v}")
    if res["provenance"]:
        a("\n## 6. Provenance recorded during extraction")
        for k, v in res["provenance"].items():
            a(f"- `{k}`: {v}")
    a("\n## 7. Boundary sensitivity")
    if sens:
        bad = [r for r in sens["runs"] if r["status"] != "ok" and r["sequence"] == "mask"]
        a(f"Each region mask was eroded and dilated by one voxel ({', '.join(C.PERTURBATION_LABELS.values())}) and all features were re-extracted with identical settings. "
          "Dilation is limited to non-zero brain voxels. This describes sensitivity of feature values to small boundary changes; it is not a measure of inter-observer reliability.")
        for r in bad:
            a(f"- {C.REGION_LABELS.get(r['region'], r['region'])}, {C.PERTURBATION_LABELS.get(r['perturbation'])}: {r['note']}")
    else:
        a("Not run for this case.")
    a("\n## 8. Limitations")
    a("- The MSD segmentations were produced semi-automatically and then reviewed; features inside a region partly reflect how that region was defined.")
    a("- Radiomic values depend on normalisation and bin width; compare values only with results produced with the same settings.")
    a("- Single-case values have no reference population. They are measurements, not scores.")
    a("- Skull-stripping, co-registration and resampling are not performed by NeuroVoxel; they are assumed from the MSD/BraTS convention and checked only for consistency.")
    return "\n".join(L) + "\n"
