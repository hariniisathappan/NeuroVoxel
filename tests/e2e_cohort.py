"""Batch + cohort statistics smoke test. Usage: python tests/e2e_cohort.py SYNTH_DIR WORK_DIR"""
import os, sys, time, json
synth, work = sys.argv[1], sys.argv[2]
os.environ.update(NV_DATASET_DIR=synth, NEUROVOXEL_DATA_DIR=work, NV_WORKERS="3")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    import app as A
    with TestClient(A.app) as c:
        c.post("/api/dataset/index")
        while c.get("/api/dataset/summary").json()["index_job"]["state"] == "running": time.sleep(1)
        r = c.post("/api/cases/calculate-batch?limit=6", json={}); print("batch", r.status_code, r.json().get("queued"))
        while True:
            j = c.get("/api/dataset/summary").json()["batch_job"]
            if j["state"] != "running": break
            time.sleep(2)
        print("batch end", j, c.get("/api/status").json()["calculated"])
        res = c.post("/api/cohort/compare", json={"group_a": {"max_whole": 10.3}, "group_b": {"min_whole": 13}}).json()
        print(res["ok"], res["note"])
        rk = res["ranking"]
        print("by_sequence", [(x["sequence"], x["median_abs_delta"], x["flagged"]) for x in rk["by_sequence"]])
        print("by_region", [(x["region"], x["median_abs_delta"]) for x in rk["by_region"]])
        print("top", [(f["sequence"], f["region"], f["feature"], round(f["cliffs_delta"], 2), round(f["q_fdr"], 3)) for f in res["features"][:3]])
        p = c.post("/api/cohort/percentiles", json={"case_id": "BRATS_002", "region": "whole_tumour"}).json()
        print(p["note"], list(p["values"].keys()), p["values"]["flair"]["firstorder_Mean"])
        ft = c.get("/api/cohort/feature?region=whole_tumour&feature=firstorder_Mean").json(); print({k: round(v["median"], 3) for k, v in ft["sequences"].items()})
        print(c.post("/api/cases/BRATS_001/sensitivity/start").json()["state"])
        while c.get("/api/cases/BRATS_001/job/sensitivity").json()["state"] == "running": time.sleep(2)
        print("sens", c.get("/api/cases/BRATS_001/job/sensitivity").json(), list(c.get("/api/cases/BRATS_001/sensitivity").json().keys()))
        print("adv", c.get("/api/adv/subregions?uuid=BRATS_002").status_code, c.get("/api/adv/locator?uuid=BRATS_002").status_code, c.get("/api/adv/shell?uuid=BRATS_002").status_code, c.get("/api/cases/BRATS_002/distribution").status_code)
