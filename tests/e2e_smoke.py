"""End-to-end smoke test on a synthetic MSD-style dataset. Usage: python tests/e2e_smoke.py SYNTH_DIR WORK_DIR"""
import os, sys, time
synth, work = sys.argv[1], sys.argv[2]
os.environ["NV_DATASET_DIR"] = synth
os.environ["NEUROVOXEL_DATA_DIR"] = work
os.environ["NV_WORKERS"] = "3"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def wait(client, url, timeout=900):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(url).json()
        if j["state"] in ("done", "error", "idle"):
            return j
        time.sleep(1)
    raise TimeoutError(url)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    import app as A
    with TestClient(A.app) as c:
        print("status", c.get("/api/status").json()["dataset_found"])
        assert c.post("/api/dataset/index").status_code == 200
        t = time.time()
        while c.get("/api/dataset/summary").json()["index_job"]["state"] == "running":
            time.sleep(1)
        s = c.get("/api/dataset/summary").json()
        print("index", s["index_job"], s["cases"], s["validation"], s["dominant"], s["hemisphere"], s["size_thresholds"], f"{time.time()-t:.0f}s")
        rows = c.get("/api/cases?limit=10").json()["rows"]
        for r in rows:
            print(r["case_id"], r["vol_whole_ml"], r["enh_pct"], r["dominant"], r["hemisphere"], r["index_status"], r["index_note"])
        for r in rows[:4]:
            t = time.time()
            assert c.post(f"/api/cases/{r['case_id']}/calculate").status_code == 200
            while True:
                j = c.get(f"/api/cases/{r['case_id']}/job/extract").json()
                if j["state"] != "running": break
                time.sleep(1)
            print("calc", r["case_id"], j, f"{time.time()-t:.0f}s")
        res = c.get(f"/api/cases/{rows[0]['case_id']}/results").json()
        print("regions", {k: v["voxels"] for k, v in res["regions"].items()}, "features first seq", {s: sum(len(v) for r in d.values() for v in r.values()) for s, d in res["features"].items()})
        print("percentiles", c.post("/api/cohort/percentiles", json={"case_id": rows[0]["case_id"], "region": "whole_tumour"}).json()["note"])
        cmp = c.post("/api/cohort/compare", json={"group_a": {"size": "large"}, "group_b": {"size": "small"}}).json()
        print("compare", cmp.get("ok"), cmp.get("note"))
        cc = c.post("/api/compare/cases", json={"case_ids": [rows[0]["case_id"], rows[1]["case_id"]], "region": "whole_tumour"})
        print("compare cases", cc.status_code)
        for u in ("export.xlsx", "export.csv", "report.md"):
            r = c.get(f"/api/cases/{rows[0]['case_id']}/{u}"); print(u, r.status_code, len(r.content))
        v = c.get(f"/api/cases/{rows[5]['case_id']}/volume/flair"); print("volume (restage)", v.status_code, len(v.content))
        print("storage", c.get("/api/storage").json(), c.post("/api/storage/cleanup?everything=true").json())
        print("delete", c.delete(f"/api/cases/{rows[0]['case_id']}/results").json(), c.get("/api/status").json()["calculated"])
