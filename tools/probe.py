"""Discovery probe: checks which candidate companies' job feeds work and how many
India jobs each has. Writes tools/probe_results.json. Run on GitHub Actions."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import sources as S  # noqa: E402

HERE = os.path.dirname(__file__)
cand = json.load(open(os.path.join(HERE, "candidates.json")))
GENERIC_SITES = ["External", "Careers", "careers", "External_Careers", "ExternalCareers", "jobs"]
ALL_WD = [1, 5, 3, 12, 10, 103, 108]


def probe_workday(row):
    name, tenant, wds, sites = row
    tried = []
    order = [(n, s) for n in wds for s in sites]
    order += [(n, s) for n in ALL_WD for s in sites if (n, s) not in order]
    order += [(n, s) for n in wds for s in GENERIC_SITES if (n, s) not in order]
    for n, s in order[:40]:
        c = {"tenant": tenant, "wd": n, "site": s}
        try:
            india, method, total = S.wd_probe(c)
            return {"name": name, "ats": "workday", "tenant": tenant, "wd": n, "site": s,
                    "india": india, "total": total, "method": method, "ok": True}
        except S.FetchError as e:
            tried.append(f"wd{n}/{s}:{e}")
        except Exception as e:  # noqa
            tried.append(f"wd{n}/{s}:{type(e).__name__}")
    return {"name": name, "ats": "workday", "tenant": tenant, "ok": False, "tried": tried[:6]}


def probe_simple(ats, row):
    name, token = row[0], row[1]
    c = {"token": token}
    if ats == "oracle":
        c = {"host": row[1], "site": row[2]}
    t0 = time.time()
    try:
        jobs = S.FETCHERS[ats](c)
        return {"name": name, "ats": ats, **{k: v for k, v in c.items()}, "india": len(jobs),
                "ok": True, "sample": [j["title"] + " | " + j["location"] for j in jobs[:3]],
                "secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa
        return {"name": name, "ats": ats, **c, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def probe_raw(label, url, data=None):
    try:
        r = S.http(url, data)
        return {"name": label, "ok": True, "keys": list(r)[:15] if isinstance(r, dict) else type(r).__name__,
                "snippet": json.dumps(r)[:1500]}
    except Exception as e:  # noqa
        return {"name": label, "ok": False, "error": str(e)[:200]}


tasks = []
with ThreadPoolExecutor(max_workers=12) as ex:
    for row in cand["workday"]:
        tasks.append(ex.submit(probe_workday, row))
    for ats in ["greenhouse", "lever", "smartrecruiters", "ashby", "oracle"]:
        for row in cand.get(ats, []):
            tasks.append(ex.submit(probe_simple, ats, row))
    tasks.append(ex.submit(probe_simple, "amazon", ["Amazon", "amazon"]))
    for label, url in [
        ("ms-eightfold-v2", "https://apply.careers.microsoft.com/api/apply/v2/jobs?domain=microsoft.com&start=0&num=10&location=India&sort_by=timestamp"),
        ("ms-pcsx", "https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query=&location=India&start=0&sort_by=timestamp"),
        ("ms-gcs", "https://gcsservices.careers.microsoft.com/search/api/v1/search?lc=India&l=en_us&pg=1&pgSz=20&o=Recent"),
        ("google", "https://careers.google.com/api/v3/search/?location=India&page_size=20"),
        ("apple", "https://jobs.apple.com/api/role/search"),
    ]:
        tasks.append(ex.submit(probe_raw, label, url))
    results = [t.result() for t in tasks]

ok = sorted([r for r in results if r.get("ok") and "india" in r], key=lambda r: -r["india"])
out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
       "working": ok, "failed": [r for r in results if not r.get("ok")],
       "raw": [r for r in results if r.get("ok") and "india" not in r]}
json.dump(out, open(os.path.join(HERE, "probe_results.json"), "w"), indent=1)
print(f"working {len(ok)}, failed {len(out['failed'])}")
