"""Single source of truth for NeuroVoxel's scientific and runtime settings.

Everything that changes a number in the results lives here, so the methods report,
the validator and the extraction engine can never disagree with each other.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "3.1.0"

# ------------------------------------------------------------------ paths
ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("NEUROVOXEL_DATA_DIR", ROOT))
STATIC = ROOT / "static"
UPLOADS = DATA_DIR / "work"                        # staged per-case files (split channels, masks). Always re-creatable.
DATABASE = DATA_DIR / "neurovoxel_v3.sqlite3"      # new file: the old v2 database is left untouched
# The dataset is mounted READ-ONLY and never copied into the project. Point this at the Task01_BrainTumour folder.
DATASET_DIR = Path(os.getenv("NV_DATASET_DIR", "/data/msd"))
MAX_INDEX_THREADS = int(os.getenv("NV_INDEX_THREADS", str(min(4, os.cpu_count() or 1))))
MAX_BATCH_CASES = int(os.getenv("NV_MAX_BATCH", "60"))   # safety cap for "calculate all matching"

# ------------------------------------------------------------------ internal convention
# MSD Task01 files are converted ("staged") once per case into one 3D file per sequence plus a segmentation whose labels are
# renamed to the BraTS 2017-2021 numbers below. MSD's own label numbers are read from dataset.json (see msd.py), never assumed.
SEQUENCES = ("t1", "t1ce", "t2", "flair")
SEQUENCE_LABELS = {"t1": "T1", "t1ce": "T1c", "t2": "T2", "flair": "FLAIR"}
SLOTS = SEQUENCES + ("seg",)

BRATS_LABELS = {1: "Necrotic / non-enhancing core", 2: "Peritumoral edema", 4: "Enhancing tumour"}
ALLOWED_LABELS = (0, 1, 2, 4)

# Standard BraTS evaluation regions. They overlap on purpose (tumour core is inside whole tumour).
REGIONS = {"whole_tumour": (1, 2, 4), "tumour_core": (1, 4), "enhancing_tumour": (4,)}
REGION_LABELS = {
    "whole_tumour": "Whole tumour (labels 1+2+4)",
    "tumour_core": "Tumour core (labels 1+4)",
    "enhancing_tumour": "Enhancing tumour (label 4)",
}

EXPECTED_SIZE = (240, 240, 155)      # x, y, z  (SimpleITK order)
EXPECTED_SPACING = (1.0, 1.0, 1.0)   # mm

# A region smaller than this is not analysed (texture features of a handful of voxels are meaningless).
# 64 = 4x4x4 voxels. This is a pragmatic project choice, not a published standard. Override with NV_MIN_REGION_VOXELS.
MIN_REGION_VOXELS = int(os.getenv("NV_MIN_REGION_VOXELS", "64"))

# ------------------------------------------------------------------ intensity normalisation + PyRadiomics
# Each sequence of each patient is z-scored on its non-zero (brain) voxels only, then PyRadiomics discretises the
# z-scored values with a FIXED bin width. PyRadiomics' own normalisation is switched off because it would include the
# zero background of a skull-stripped volume in its mean and standard deviation.
NORMALISATION = "z-score on non-zero (brain) voxels, per sequence and per patient"
BIN_WIDTH = 0.25                     # in z-score units; a typical tumour spans roughly 15-40 bins

RADIOMICS_SETTINGS = {
    "binWidth": BIN_WIDTH,
    "normalize": False,              # we normalise ourselves (see above)
    "resampledPixelSpacing": None,   # no resampling: BraTS is already 1 mm isotropic
    "preCrop": True,
    "label": 1,
    "additionalInfo": True,
}
FEATURE_CLASSES = ("shape", "firstorder", "glcm", "glrlm", "glszm", "ngtdm", "gldm")
INTENSITY_CLASSES = tuple(c for c in FEATURE_CLASSES if c != "shape")

# ------------------------------------------------------------------ boundary sensitivity
PERTURBATIONS = ("erode1", "dilate1")
PERTURBATION_LABELS = {"erode1": "Erode 1 voxel", "dilate1": "Dilate 1 voxel"}
# Perturbed masks are built per region as binary masks (ball structuring element, radius 1 voxel).
# Dilation is limited to voxels that are non-zero in at least one of the four sequences (inside the brain).

# ------------------------------------------------------------------ validation thresholds
MASK_INSIDE_BRAIN_PASS = 0.90
MASK_INSIDE_BRAIN_WARN = 0.60
BRAIN_DICE_PASS = 0.90
BRAIN_DICE_FAIL = 0.50
MIN_ZERO_FRACTION = 0.20             # a skull-stripped brain leaves most of the 240x240x155 box at exactly zero
FLAIR_EDEMA_Z = 0.5                  # plausibility only (warning): edema should be FLAIR-bright
ENHANCE_Z = 0.3                      # plausibility only (warning): enhancing tumour should be brighter on T1c than T1

# ------------------------------------------------------------------ runtime
MAX_WORKERS = int(os.getenv("NV_WORKERS", str(min(4, os.cpu_count() or 1))))
WORDING = (
    "Consistent with the Medical Segmentation Decathlon Task01 (BraTS-derived) format. These checks cannot prove how the data "
    "were produced; they confirm that the files are mutually consistent and follow the conventions NeuroVoxel supports."
)
HEMISPHERE_MIDLINE_NOTE = "Approximate: midline = centre of the non-zero brain along the left-right axis. No atlas registration."
DOMINANT_LABELS = {"edema": "Peritumoral edema", "net": "Non-enhancing / necrotic", "enhancing": "Enhancing tumour"}
