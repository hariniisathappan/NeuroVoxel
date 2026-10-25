"""Create a tiny SYNTHETIC dataset in the MSD Task01 layout, to test NeuroVoxel without the real data.
Usage: python tools/make_synthetic_msd.py OUT_DIR [N_CASES]
Images are random smooth blobs, not real anatomy: never use them for any scientific statement."""
import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

out = Path(sys.argv[1])
n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
shape = (240, 240, 155)
(out / "imagesTr").mkdir(parents=True, exist_ok=True)
(out / "labelsTr").mkdir(parents=True, exist_ok=True)
affine = np.diag([1.0, 1.0, 1.0, 1.0])
affine[:3, 3] = [-120, -120, -77]
affine = np.array([[-1, 0, 0, 120], [0, -1, 0, 120], [0, 0, 1, -77], [0, 0, 0, 1]], float)   # RAS-like MSD orientation (LAS flipped)
x, y, z = np.meshgrid(*[np.arange(s, dtype=np.float32) for s in shape], indexing="ij")
brain = (((x - 120) / 80) ** 2 + ((y - 120) / 100) ** 2 + ((z - 77) / 65) ** 2) < 1
meta = {"name": "BRATS", "description": "synthetic", "licence": "synthetic", "tensorImageSize": "4D",
        "modality": {"0": "FLAIR", "1": "T1w", "2": "t1gd", "3": "T2w"},
        "labels": {"0": "background", "1": "edema", "2": "non-enhancing tumor", "3": "enhancing tumour"}, "training": [], "test": []}
for i in range(n):
    rng = np.random.default_rng(100 + i)
    c = np.array([120 + rng.integers(-45, 45), 120 + rng.integers(-35, 35), 77 + rng.integers(-20, 20)], np.float32)
    r = rng.uniform(10, 26)
    d = np.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2)
    lab = np.zeros(shape, np.uint8)
    lab[(d < r) & brain] = 1                      # edema (MSD number)
    lab[(d < r * 0.65) & brain] = 2               # non-enhancing
    if i % 3 != 2:
        lab[(d < r * 0.35) & brain] = 3           # enhancing (absent in every third case)
    base = rng.uniform(300, 500, 4)
    chans = []
    for k in range(4):
        tex = rng.normal(0, 25, shape).astype(np.float32)
        v = (base[k] + tex) * brain
        v[lab == 1] *= 1.5 if k == 0 else 1.0     # FLAIR-bright edema
        if k == 2:
            v[lab == 3] *= 1.8                    # T1gd enhancement
            v[lab == 2] *= 0.7
        chans.append((v * brain).astype(np.float32))
    cid = f"BRATS_{i + 1:03d}"
    nib.save(nib.Nifti1Image(np.stack(chans, -1), affine), str(out / "imagesTr" / f"{cid}.nii.gz"))
    nib.save(nib.Nifti1Image(lab, affine), str(out / "labelsTr" / f"{cid}.nii.gz"))
    meta["training"].append({"image": f"./imagesTr/{cid}.nii.gz", "label": f"./labelsTr/{cid}.nii.gz"})
    print("made", cid)
meta["numTraining"] = n
(out / "dataset.json").write_text(json.dumps(meta, indent=1))
