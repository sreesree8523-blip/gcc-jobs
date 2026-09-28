"""Discovery round for big employers. Writes tools/probe2_results.json.
A) Workday cluster scan for tenants that failed before
B) Direct tests of new fetchers (Eightfold, Jibe, Oracle, Atlassian)
C) Real-browser network capture for sites with no known feed
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import sources as S  # noqa: E402
S.HTTP_TIMEOUT = 12
S.HTTP_RETRIES = 1
HERE = os.path.dirname(__file__)

CLUSTERS = [1, 3, 5, 12, 10, 100, 101, 102, 103, 104, 105, 106, 107, 108,
            500, 501, 502, 503, 504, 505, 110, 111, 112, 113, 114, 115]
WD = [  # name, tenant, sites
    ("Dell Technologies", "dell", ["External"]),
    ("Visa", "visa", ["Visa"]),
    ("UBS", "ubs", ["UBS_Experienced_Professionals", "External"]),
    ("BNY", "bnymellon", ["BNY_Careers", "BNYM_Careers"]),
    ("Nomura", "nomura", ["Nomura", "Nomura_Careers"]),
    ("Moody's", "moodys", ["External", "Careers"]),
    ("MSCI", "msci", ["MSCI", "Careers"]),
    ("Principal Financial", "principal", ["Principal", "External"]),
    ("Swiss Re", "swissre", ["SwissRe", "External"]),
    ("Intuit", "intuit", ["jobs", "Intuit"]),
    ("McDonald's", "mcdonalds", ["McD_Careers", "External"]),
    ("Kenvue", "kenvue", ["KenvueCareers"]),
    ("John Deere", "johndeere", ["Deere", "External"]),
    ("Eaton", "eaton", ["eaton_careers", "External"]),
    ("Emerson", "emerson", ["Emerson", "External"]),
    ("Providence", "providence", ["Careers", "External"]),
    ("Liberty Mutual", "libertymutual", ["LMI", "External"]),
    ("DTCC", "dtcc", ["dtcc", "External"]),
    ("American Express", "aexp", ["External", "Careers"]),
    ("ICE", "ice", ["External", "ICE"]),
    ("Novo Nordisk", "novonordisk", ["NovoNordiskCareers", "External"]),
    ("Bayer", "bayer", ["Bayer_Careers", "External"]),
    ("Chubb", "chubb", ["ChubbCareers", "External", "Chubb"]),
    ("Wabtec", "wabtec", ["wabtec_careers", "External"]),
    ("Franklin Templeton", "franklintempleton", ["Primary-External-1"]),
    ("Accenture", "accenture", ["AccentureCareers"]),
    ("Barclays", "barclays", ["External_Career_Site_Barclays", "External"]),
    ("Standard Chartered", "scb", ["Careers", "External"]),
    ("Optum / UnitedHealth", "uhg", ["External"]),
    ("Honeywell", "honeywell", ["Honeywell", "External"]),
    ("Siemens Healthineers", "siemenshealthineers", ["External"]),
    ("ABB", "abb", ["External"]),
    ("Lowe's", "lowes", ["LWS_External_CS", "External"]),
    ("Home Depot", "homedepot", ["CareerDepot", "External"]),
    ("Kroger", "kroger", ["External"]),
    ("Tesco", "tesco", ["External"]),
    ("Ford", "ford", ["FordCareers", "External"]),
    ("General Motors", "generalmotors", ["Careers_GM", "External"]),
    ("Unilever", "unilever", ["External"]),
    ("PepsiCo", "pepsico", ["PepsiCoJobs", "External"]),
    ("Procter & Gamble", "pg", ["1000", "External"]),
    ("Carelon (Elevance)", "elevancehealth", ["ANT"]),
]


def wd_scan(row):
    name, tenant, sites = row
    tried = 0
    for s in sites:
        for n in CLUSTERS:
            tried += 1
            try:
                india, method, total = S.wd_probe({"tenant": tenant, "wd": n, "site": s})
                return {"name": name, "ats": "workday", "tenant": tenant, "wd": n, "site": s,
                        "india": india, "total": total, "method": method, "ok": True}
            except Exception:  # noqa
                continue
    # last resort: follow the public page's redirect
    for n in [1, 5, 3, 12]:
        try:
            req = urllib.request.Request(f"https://{tenant}.wd{n}.myworkdayjobs.com/{sites[0]}",
                                         headers={"User-Agent": S.UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                return {"name": name, "ok": False, "redirect": r.geturl(), "tried": tried}
        except Exception as e:  # noqa
            last = str(e)[:80]
    return {"name": name, "ok": False, "tried": tried, "last": last}


DIRECT = [
    ("HSBC", "eightfold", {"host": "portal.careers.hsbc.com", "domain": "hsbc.com"}),
    ("Qualcomm", "eightfold", {"host": "careers.qualcomm.com", "domain": "qualcomm.com"}),
    ("AMD", "jibe", {"host": "careers.amd.com"}),
    ("Atlassian", "atlassian", {}),
    ("American Express", "oracle", {"host": "careers.americanexpress.com", "site": "CX_1"}),
]


def direct(row):
    name, ats, c = row
    try:
        jobs = S.FETCHERS[ats](c)
        return {"name": name, "ats": ats, **c, "india": len(jobs), "ok": True,
                "sample": [j["title"] + " | " + j["location"] + " | " + str(j["url"]) for j in jobs[:4]]}
    except Exception as e:  # noqa
        return {"name": name, "ats": ats, **c, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


SNIFF = [
    ("Goldman Sachs", "https://higher.gs.com/results?LOCATION=Bengaluru%7CHyderabad&page=1&sort=POSTED_DATE"),
    ("UBS", "https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25008&siteid=5012&PageType=searchResults&SearchType=linkquery&LinkID=4138"),
    ("HSBC", "https://portal.careers.hsbc.com/careers?location=India"),
    ("American Express", "https://careers.americanexpress.com/en/sites/CX_1/jobs?location=India"),
    ("Uber", "https://www.uber.com/in/en/careers/list/?location=IND-Karnataka-Bangalore&location=IND-Telangana-Hyderabad"),
    ("Chubb", "https://about.chubb.com/careers/engineering-centers/india-engineering-center.html"),
    ("Qualcomm", "https://careers.qualcomm.com/careers?location=India&domain=qualcomm.com"),
    ("AMD", "https://careers.amd.com/careers-home/jobs?location=India"),
]
KEYS = ("job", "role", "position", "requisition", "posting", "graphql", "search")


def sniff():
    out = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [{"error": "playwright missing"}]
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, url in SNIFF:
            ctx = b.new_context(user_agent=S.UA, locale="en-IN")
            page = ctx.new_page()
            caps = []

            def on_resp(r, caps=caps):
                try:
                    rt = r.request.resource_type
                    if rt not in ("xhr", "fetch"):
                        return
                    u = r.url
                    if not any(k in u.lower() for k in KEYS):
                        return
                    body = ""
                    try:
                        body = r.text()[:1500]
                    except Exception:  # noqa
                        pass
                    caps.append({"url": u[:400], "method": r.request.method, "status": r.status,
                                 "post": (r.request.post_data or "")[:2500],
                                 "req_headers": {k: v for k, v in r.request.headers.items()
                                                 if k.lower() not in ("cookie", "user-agent")},
                                 "body": body})
                except Exception:  # noqa
                    pass
            page.on("response", on_resp)
            final = None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception as e:  # noqa
                caps.append({"nav_error": str(e)[:200]})
            try:
                final = page.url
                title = page.title()
                text = page.inner_text("body")[:800]
            except Exception:  # noqa
                title, text = "", ""
            out.append({"name": name, "start": url, "final": final, "title": title,
                        "text": text, "captures": caps[:25]})
            ctx.close()
        b.close()
    return out


with ThreadPoolExecutor(max_workers=24) as ex:
    wd = list(ex.map(wd_scan, WD))
    dr = list(ex.map(direct, DIRECT))
sn = sniff()
json.dump({"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "workday": wd, "direct": dr, "sniff": sn},
          open(os.path.join(HERE, "probe2_results.json"), "w"), indent=1)
print("done")
