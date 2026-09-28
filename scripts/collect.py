"""Daily collector: pulls India jobs from every company in companies.json,
keeps the five role groups, cleans up cities, and writes docs/data/jobs.json."""
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import sources as S  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "docs", "data")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# ------------------------------------------------------------------ role groups
def rx(p):
    return re.compile(p, re.I)

LEAD = r"(director|head|vp|vice president|svp|evp|avp|chief|managing director|general manager|leader)"
TECH = (r"(engineering|software|technology|technologies|tech\b|platform|data|software development|"
        r"architecture|cloud|devops|infrastructure|sre|reliability|quality engineering|qa|"
        r"security|cyber|it\b|information technology|digital|ai\b|machine learning|analytics|"
        r"applications?|product engineering|r&d|research and development)")
OPS = (r"(operations|ops\b|delivery|shared services|business services|service delivery|"
       r"process excellence|transformation|site|centre|center|gcc|capability|"
       r"global business|client service|customer service|fund services|finance operations)")

ENG_LEAD = [
    rx(rf"\b{LEAD}\b.{{0,40}}\b{TECH}"),
    rx(rf"\b{TECH}.{{0,40}}\b{LEAD}\b"),
    rx(r"\b(engineering|software development|software|qa|sre|devops|data engineering|platform) manager\b"),
    rx(r"\bmanager,?\s*(software|engineering|development)\b"),
    rx(r"\b(cto|cio|chief technology officer|chief information officer)\b"),
]
OPS_LEAD = [
    rx(rf"\b{LEAD}\b.{{0,40}}\b{OPS}"),
    rx(rf"\b{OPS}.{{0,40}}\b{LEAD}\b"),
    rx(r"\b(site lead|site leader|centre head|center head|country head|location head|coo)\b"),
]
PRODUCT = [
    rx(r"\bproduct\s+(owner|manager|management|lead|leader|director|head|strategy|strategist|"
       r"specialist|analyst|operations|delivery)\b"),
    rx(r"\b(head|director|vp|vice president|chief)\b.{0,20}\bproducts?\b(?!\s*(engineer|development|security|design engineer))"),
    rx(r"\b(associate|senior|principal|group|technical|digital|lead)\s+product\s+(owner|manager)\b"),
    rx(r"\b(apm|tpm|cpo)\b"),
    rx(r"\bbusiness analyst\b.{0,30}\bproduct|\bproduct\b.{0,30}\bbusiness analyst\b"),
]
LND_NOT = rx(r"(machine|deep|reinforcement|federated|statistical|supervised)\s+learning|"
             r"\bml\b|\bllm\b|model training|training data|ai training|trainee|learning engineer|"
             r"learning scientist|data scientist")
LND = [
    rx(r"\b(learning|training)\b.{0,25}\b(development|design|designer|experience|specialist|"
       r"manager|lead|partner|consultant|coordinator|facilitator|program|programme|delivery|"
       r"operations|head|director|analyst|content|solutions|strategy|technology|administrator)\b"),
    rx(r"\b(l&d|l & d|lnd|instructional design(er)?|curriculum|e-?learning|trainer|facilitator|"
       r"talent development|leadership development|capability development|learning & development|"
       r"learning and development|(sales|gtm|go-to-market|revenue|partner|field|customer success|cs|"
       r"customer|learning|talent|seller) enablement)\b"),
    rx(r"\b(head|director|vp|vice president|chief|lead)\b.{0,15}\blearning\b|\blearning officer\b"),
]
DEV_NOT = rx(r"\b(sales engineer|pre-?sales|field service|service engineer|civil|mechanical|"
             r"electrical engineer|hardware|analog|rf engineer|validation engineer|"
             r"manufacturing|process engineer|chemical|mechatronics|sales|recruit)")
DEV = [
    rx(r"\b(software|swe|sde|sdet|full[\s-]?stack|front[\s-]?end|back[\s-]?end|web|mobile|android|ios|"
       r"cloud|devops|devsecops|site reliability|sre|platform|data|ml|machine learning|ai|"
       r"automation|qa|test|quality|integration|infrastructure|security|application|systems?|"
       r"salesforce|sap|servicenow|workday|java|python|\.net|dotnet|c\+\+|golang|react|angular|"
       r"node(\.js)?|ui|database|big data|etl|embedded|firmware|network|api)\s*"
       r"(engineer|developer|development engineer|programmer|architect)"),
    rx(r"\b(developer|programmer|member of technical staff|mts|smts|lmts|tech lead|technical lead|"
       r"solution architect|solutions architect|software architect|enterprise architect|"
       r"cloud architect|data architect|devops|sre)\b"),
    rx(r"\bengineer\b.{0,25}\b(software|java|python|backend|frontend|full stack|data|cloud|devops|"
       r"platform|ml|ai|mobile|qa|test automation|salesforce)\b"),
    rx(r"\b(software engineering|software development|application development|application engineering)\b"),
]


# "Software Engineer - Vice President", "Vice President - Data Analyst": bank ranks, not leadership.
RANK_SUFFIX = rx(r"[\s,\-\u2013\u2014|/(]+(assistant vice president|vice president|avp|vp)\)?\s*$")
RANK_PREFIX = rx(r"^\s*(assistant vice president|vice president|avp|vp)\s*[-\u2013\u2014:|]\s*")
CHIEF_OK = rx(r"\bchief\b.{0,30}\bofficer\b|\b(cto|cio|coo|cpo)\b")
IC_NOUN = rx(r"\b(engineer|developer|architect|analyst|scientist|designer|specialist|associate|"
              r"consultant|programmer|administrator)\b")
STRONG_LEAD = rx(r"\b(director|head|chief|manager|managing director|leader|general manager|cto|cio|coo)\b")
BANK_RANK = rx(r"\b(senior vice president|assistant vice president|vice president|svp|avp|vp|evp)\b")


def classify(title):
    t = title or ""
    if any(p.search(t) for p in LND) and not LND_NOT.search(t):
        return "lnd"
    # Banks use VP/AVP as ranks for individual contributors ("Software Engineer - VP").
    lead_t = RANK_PREFIX.sub("", RANK_SUFFIX.sub("", t))
    if IC_NOUN.search(lead_t) and not STRONG_LEAD.search(lead_t):
        lead_t = BANK_RANK.sub(" ", lead_t)
    if rx(r"\bchief\b").search(lead_t) and not CHIEF_OK.search(lead_t):
        lead_t = rx(r"\bchief\b").sub(" ", lead_t)
    t_rest = lead_t
    if any(p.search(lead_t) for p in ENG_LEAD) and not rx(r"\bsales\b").search(t):
        return "eng_lead"
    if any(p.search(lead_t) for p in OPS_LEAD):
        return "ops_lead"
    t = t_rest
    if any(p.search(t) for p in PRODUCT) and not rx(r"production|product (engineer|developer|security|design engineer)").search(t):
        return "product"
    if any(p.search(t) for p in DEV) and not DEV_NOT.search(t):
        return "dev"
    return None


def keep(title):
    return classify(title) is not None

# ------------------------------------------------------------------ cities
CITY_MAP = [
    ("Hyderabad", r"hyderabad|secunderabad|hitec|gachibowli|madhapur|kondapur|nanakramguda|telangana"),
    ("Bengaluru", r"bengaluru|bangalore|bengalore|whitefield|electronic city|karnataka(?!.*mysuru)"),
    ("Pune", r"\bpune\b|hinjewadi|kharadi|maharashtra(?!.*mumbai)"),
    ("Chennai", r"chennai|madras|tamil nadu(?!.*coimbatore)"),
    ("Mumbai", r"mumbai|bombay|thane|powai|goregaon|andheri|malad"),
    ("Delhi NCR", r"gurgaon|gurugram|noida|new delhi|\bdelhi\b|ghaziabad|faridabad|\bncr\b|haryana|uttar pradesh"),
    ("Kolkata", r"kolkata|calcutta|west bengal"),
    ("Ahmedabad", r"ahmedabad|gandhinagar|gift city|gujarat(?!.*vadodara)"),
    ("Kochi", r"kochi|cochin"),
    ("Thiruvananthapuram", r"thiruvananthapuram|trivandrum|technopark"),
    ("Coimbatore", r"coimbatore"),
    ("Jaipur", r"jaipur"),
    ("Indore", r"indore"),
    ("Chandigarh / Mohali", r"chandigarh|mohali|panchkula"),
    ("Vadodara", r"vadodara|baroda"),
    ("Nagpur", r"nagpur"),
    ("Bhubaneswar", r"bhubaneswar"),
    ("Visakhapatnam", r"visakhapatnam|vizag"),
    ("Mysuru", r"mysuru|mysore"),
    ("Mangaluru", r"mangaluru|mangalore"),
]
CITY_RX = [(n, re.compile(p, re.I)) for n, p in CITY_MAP]
REMOTE_RX = re.compile(r"\b(remote|work from home|wfh|virtual|anywhere)\b", re.I)


def cities_of(loc):
    loc = loc or ""
    found = [n for n, r in CITY_RX if r.search(loc)]
    if REMOTE_RX.search(loc) and not found:
        found.append("Remote")
    return found or ["India (city not listed)"]

# ------------------------------------------------------------------ run
def posted_iso(j, today):
    if j.get("posted_date"):
        return j["posted_date"]
    if j.get("posted_days") is not None:
        return (today - dt.timedelta(days=j["posted_days"])).isoformat()
    return None


def run_company(c):
    t0 = time.time()
    try:
        raw = S.FETCHERS[c["ats"]](c, keep=keep)
        return c, raw, None, round(time.time() - t0, 1)
    except Exception as e:  # noqa
        return c, [], f"{type(e).__name__}: {e}"[:160], round(time.time() - t0, 1)


def main():
    companies = json.load(open(os.path.join(ROOT, "companies.json")))["companies"]
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "jobs.json")
    prev = {}
    prev_by_co = {}
    baseline = None
    if os.path.exists(path):
        old = json.load(open(path))
        baseline = old.get("baseline")
        for j in old.get("jobs", []):
            prev[j["id"]] = j.get("seen")
            prev_by_co.setdefault(j["co"], []).append(j)
    now = dt.datetime.now(IST)
    today = now.date()
    if baseline is None:
        baseline = today.isoformat()

    jobs, report = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, raw, err, secs in ex.map(run_company, companies):
            kept = 0
            for j in raw:
                g = classify(j.get("cls") or j["title"])
                if not g:
                    continue
                jid = f"{c['name']}|{j.get('ext_id') or j['url']}"
                cities = cities_of(j.get("location"))
                jobs.append({
                    "id": jid, "t": j["title"].strip(), "co": c["name"], "sec": c.get("sector", ""),
                    "g": g, "c": cities, "loc": (j.get("location") or "")[:160], "u": j["url"],
                    "p": posted_iso(j, today), "seen": prev.get(jid) or today.isoformat(),
                })
                kept += 1
            stale = False
            if err and prev_by_co.get(c["name"]):
                # Company unreachable today: keep yesterday's jobs so they don't vanish from the page.
                jobs.extend(prev_by_co[c["name"]])
                kept = len(prev_by_co[c["name"]])
                stale = True
            report.append({"name": c["name"], "ats": c["ats"], "india": len(raw), "kept": kept,
                           "error": err, "stale": stale, "secs": secs})
    # de-duplicate by id
    uniq = {}
    for j in jobs:
        uniq.setdefault(j["id"], j)
    jobs = sorted(uniq.values(), key=lambda j: (j["seen"], j["p"] or ""), reverse=True)

    out = {"updated": now.strftime("%Y-%m-%d %H:%M IST"), "today": today.isoformat(),
           "baseline": baseline, "jobs": jobs,
           "companies": sorted(report, key=lambda r: r["name"])}
    json.dump(out, open(path, "w"), separators=(",", ":"))

    # summary for the daily notification
    new = [j for j in jobs if j["seen"] == today.isoformat() and today.isoformat() != baseline]
    summary = {"updated": out["updated"], "total": len(jobs), "new_total": len(new),
               "new_by_group": {}, "new_hyd_by_group": {},
               "companies_ok": sum(1 for r in report if not r["error"]),
               "companies_failed": [r["name"] for r in report if r["error"]]}
    for j in new:
        summary["new_by_group"][j["g"]] = summary["new_by_group"].get(j["g"], 0) + 1
        if "Hyderabad" in j["c"]:
            summary["new_hyd_by_group"][j["g"]] = summary["new_hyd_by_group"].get(j["g"], 0) + 1
    json.dump(summary, open(os.path.join(DATA, "summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
