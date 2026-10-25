"""Cohort statistics among the cases that have been calculated. Descriptive and exploratory only.

Nothing here predicts anything. Every number is labelled with the number of cases it is based on.
  percentiles()    where one case lies among the calculated cases, per feature
  compare_groups() two groups of calculated cases: medians, Mann-Whitney U, Cliff's delta, FDR, correlation with region volume
  rank_sets()      which sequence x region (or feature class) separates the two groups more, by effect size
  feature_table()  one feature across all calculated cases, per sequence (for strip / box charts)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

import config as C
import db

MIN_GROUP = 3
MIN_COHORT = 5
VOLUME_COL = {"whole_tumour": "vol_whole_ml", "tumour_core": "vol_core_ml", "enhancing_tumour": "vol_enh_ml"}
DELTA_SMALL, DELTA_MEDIUM, DELTA_LARGE = 0.147, 0.33, 0.474     # Romano et al. conventions for Cliff's delta


def _split(col: str) -> tuple[str, str, str]:
    seq, region, rest = col.split("|")
    family, name = rest.split("_", 1)
    return seq, region, f"{family}_{name}"


def percentiles(case_id: str, region: str, families: list[str] | None = None) -> dict[str, Any]:
    ids = db.calculated_ids()
    frame = db.cohort_frame(ids)
    n = len(ids)
    if case_id not in ids:
        return {"n": n, "note": "This case has not been calculated.", "values": {}}
    if n < MIN_COHORT:
        return {"n": n, "note": f"Percentiles need at least {MIN_COHORT} calculated cases (have {n}).", "values": {}}
    out: dict[str, Any] = {}
    for col in frame.columns:
        seq, reg, feat = _split(col)
        if reg != region or (families and feat.split("_", 1)[0] not in families):
            continue
        series = frame[col].dropna()
        if case_id not in series.index or len(series) < MIN_COHORT:
            continue
        v = float(series[case_id])
        below, equal = float((series < v).sum()), float((series == v).sum())
        pct = (below + 0.5 * equal) / len(series) * 100.0            # mid-rank percentile
        out.setdefault(seq, {})[feat] = {"value": v, "percentile": round(pct, 1), "n": int(len(series)),
                                         "median": float(series.median()), "q1": float(series.quantile(.25)), "q3": float(series.quantile(.75))}
    return {"n": n, "note": f"Percentile among {n} calculated cases (not a reference population).", "values": out}


def _bh(p: np.ndarray) -> np.ndarray:
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(ranked, 1.0)
    return out


def compare_groups(ids_a: list[str], ids_b: list[str], min_delta: float = 0.0) -> dict[str, Any]:
    ids_a = [i for i in ids_a if i in set(db.calculated_ids())]
    ids_b = [i for i in ids_b if i in set(db.calculated_ids()) and i not in set(ids_a)]
    if len(ids_a) < MIN_GROUP or len(ids_b) < MIN_GROUP:
        return {"ok": False, "n_a": len(ids_a), "n_b": len(ids_b),
                "note": f"Each group needs at least {MIN_GROUP} calculated cases (A has {len(ids_a)}, B has {len(ids_b)}). Calculate more cases first."}
    frame = db.cohort_frame(ids_a + ids_b)
    case_rows = {r: db.get_case(r) for r in ids_a + ids_b}
    rows = []
    for col in frame.columns:
        seq, region, feat = _split(col)
        a, b = frame.loc[[i for i in ids_a if i in frame.index], col].dropna(), frame.loc[[i for i in ids_b if i in frame.index], col].dropna()
        if len(a) < MIN_GROUP or len(b) < MIN_GROUP or (np.ptp(np.concatenate([a, b])) == 0):
            continue
        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        delta = 2.0 * float(u) / (len(a) * len(b)) - 1.0                      # Cliff's delta, positive = A higher
        vol = [(case_rows[i][VOLUME_COL[region]] if region in VOLUME_COL else None) for i in a.index.tolist() + b.index.tolist()]
        rho = None
        if None not in vol and np.ptp(vol) > 0:
            rho = float(stats.spearmanr(np.concatenate([a, b]), vol)[0])
        rows.append({"sequence": seq, "region": region, "feature": feat, "family": feat.split("_", 1)[0],
                     "n_a": int(len(a)), "n_b": int(len(b)), "median_a": float(a.median()), "median_b": float(b.median()),
                     "q1_a": float(a.quantile(.25)), "q3_a": float(a.quantile(.75)), "q1_b": float(b.quantile(.25)), "q3_b": float(b.quantile(.75)),
                     "cliffs_delta": delta, "p": float(p), "rho_volume": rho})
    if not rows:
        return {"ok": False, "n_a": len(ids_a), "n_b": len(ids_b), "note": "No feature had enough variation in both groups."}
    df = pd.DataFrame(rows)
    df["q_fdr"] = _bh(df["p"].to_numpy())
    df["abs_delta"] = df["cliffs_delta"].abs()
    df["effect"] = pd.cut(df["abs_delta"], [-1, DELTA_SMALL, DELTA_MEDIUM, DELTA_LARGE, 2], labels=["negligible", "small", "medium", "large"]).astype(str)
    df["flag"] = (df["q_fdr"] < 0.05) & (df["abs_delta"] >= max(min_delta, DELTA_MEDIUM))
    return {"ok": True, "n_a": len(ids_a), "n_b": len(ids_b), "features": df.sort_values("abs_delta", ascending=False).to_dict("records"),
            "note": (f"Group A: {len(ids_a)} calculated cases, group B: {len(ids_b)}. Mann-Whitney U, Benjamini-Hochberg FDR across {len(df)} features, "
                     "Cliff's delta effect size (positive = higher in A). Exploratory: groups are defined by tumour size / location, not by outcome.")}


def rank_sets(result: dict[str, Any]) -> dict[str, Any]:
    """Which sequence x region, and which feature family, separates the groups more. Shape (mask-only) is reported separately."""
    if not result.get("ok"):
        return {}
    df = pd.DataFrame(result["features"])
    shape = df[df["sequence"] == "mask"]
    inten = df[df["sequence"] != "mask"]

    def summarise(g: pd.DataFrame) -> dict[str, Any]:
        return {"features": int(len(g)), "flagged": int(g["flag"].sum()), "flagged_pct": round(float(g["flag"].mean()) * 100, 1),
                "median_abs_delta": round(float(g["abs_delta"].median()), 3), "max_abs_delta": round(float(g["abs_delta"].max()), 3),
                "median_abs_rho_volume": None if g["rho_volume"].dropna().empty else round(float(g["rho_volume"].abs().median()), 3)}

    by_set = [{"sequence": s, "region": r, **summarise(g)} for (s, r), g in inten.groupby(["sequence", "region"])]
    by_set.sort(key=lambda x: (-x["median_abs_delta"], -x["flagged_pct"]))
    by_family = [{"family": f, **summarise(g)} for f, g in inten.groupby("family")]
    by_family.sort(key=lambda x: -x["median_abs_delta"])
    by_seq = [{"sequence": s, **summarise(g)} for s, g in inten.groupby("sequence")]
    by_seq.sort(key=lambda x: -x["median_abs_delta"])
    by_region = [{"region": r, **summarise(g)} for r, g in inten.groupby("region")]
    by_region.sort(key=lambda x: -x["median_abs_delta"])
    return {"by_set": by_set, "by_family": by_family, "by_sequence": by_seq, "by_region": by_region,
            "shape": summarise(shape) if len(shape) else None,
            "reading": ("'Separates more' means a larger median |Cliff's delta| between the two groups across that set's features. It says nothing about "
                        "clinical usefulness. Texture features are correlated with region volume (see median |rho| with volume), so when the groups differ in "
                        "size part of any separation reflects size.")}


def feature_table(region: str, feature: str, ids: list[str] | None = None) -> dict[str, Any]:
    ids = ids or db.calculated_ids()
    frame = db.cohort_frame(ids)
    out: dict[str, Any] = {"n": len(ids), "sequences": {}}
    for seq in (*C.SEQUENCES, "mask"):
        col = f"{seq}|{region}|{feature}"
        if col in frame.columns:
            s = frame[col].dropna()
            if len(s):
                out["sequences"][seq] = {"values": {k: float(v) for k, v in s.items()}, "median": float(s.median()),
                                         "q1": float(s.quantile(.25)), "q3": float(s.quantile(.75)), "min": float(s.min()), "max": float(s.max())}
    return out


def feature_catalogue() -> list[str]:
    ids = db.calculated_ids()
    if not ids:
        return []
    frame = db.cohort_frame(ids[:1])
    return sorted({_split(c)[2] for c in frame.columns})
