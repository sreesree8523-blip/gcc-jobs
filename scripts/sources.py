"""Fetchers for each hiring system (ATS). Standard library only.

Each fetcher returns a list of raw job dicts:
  {title, url, location, cities_hint(list), posted_days(int|None), posted_date(str|None), ext_id}
Only India jobs are returned.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# short tokens that need word boundaries
INDIA_RE = re.compile(
    r"\b(india|hyderabad|secunderabad|bengaluru|bangalore|pune|chennai|mumbai|gurgaon|"
    r"gurugram|noida|new delhi|delhi|kolkata|ahmedabad|kochi|cochin|trivandrum|"
    r"thiruvananthapuram|coimbatore|jaipur|indore|mohali|chandigarh|vadodara|nagpur|"
    r"bhubaneswar|visakhapatnam|mysore|mysuru|mangalore|mangaluru|gandhinagar|thane)\b",
    re.I)
# "IN" alone is avoided on purpose: it is also the US state code for Indiana.
IND_CODE_RE = re.compile(r"(^|[\s,(/_-])IND([\s,)/_-]|$)")


def is_india(text):
    if not text:
        return False
    if INDIA_RE.search(text):
        return True
    return bool(IND_CODE_RE.search(text))


class FetchError(Exception):
    pass


HTTP_TIMEOUT = 40
HTTP_RETRIES = 3


def http(url, data=None, headers=None, method=None, timeout=None, retries=None):
    timeout = timeout or HTTP_TIMEOUT
    retries = retries or HTTP_RETRIES
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                try:
                    return json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    raise FetchError(f"not json from {url[:120]}")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429:
                try:
                    wait = int(e.headers.get("Retry-After") or 0)
                except ValueError:
                    wait = 0
                time.sleep(min(max(wait, 15 * (attempt + 1)), 60))
                continue
            if e.code in (500, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            raise FetchError(last)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = str(e)[:120]
            time.sleep(2 * (attempt + 1))
    raise FetchError(last or "failed")


# ---------------------------------------------------------------- Workday
WD_POSTED_RE = re.compile(r"(\d+)\+?\s+days?\s+ago", re.I)


def wd_posted_days(text):
    if not text:
        return None
    t = text.lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = WD_POSTED_RE.search(t)
    if m:
        return int(m.group(1))
    return None


def wd_base(c):
    host = c.get("host") or f"{c['tenant']}.wd{c['wd']}.myworkdayjobs.com"
    return host, f"https://{host}/wday/cxs/{c['tenant']}/{c['site']}"


def _wd_leaves(facets, out):
    for f in facets or []:
        param = f.get("facetParameter")
        for v in f.get("values") or []:
            if isinstance(v, dict) and v.get("values") and v.get("facetParameter"):
                _wd_leaves([v], out)
            elif isinstance(v, dict) and "id" in v:
                out.append((param, v["id"], v.get("descriptor", ""), v.get("count", 0)))


def wd_india_facets(first):
    leaves = []
    _wd_leaves(first.get("facets"), leaves)
    # 1) a country facet with value India
    for p, i, d, n in leaves:
        if p and "country" in p.lower() and d.strip().lower() == "india":
            return {p: [i]}, "country-facet"
    # 2) location values that look Indian, grouped by facet parameter
    groups = {}
    for p, i, d, n in leaves:
        if p and "location" in p.lower() and is_india(d):
            g = groups.setdefault(p, [[], 0])
            g[0].append(i)
            g[1] += n or 0
    if groups:
        p = max(groups, key=lambda k: groups[k][1])
        return {p: groups[p][0]}, "location-facet"
    return None, "text-search"


def wd_probe(c):
    """Return total India jobs, method; raises FetchError."""
    _, base = wd_base(c)
    first = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    if "total" not in first:
        raise FetchError("no total")
    applied, method = wd_india_facets(first)
    if applied:
        r = http(base + "/jobs", {"appliedFacets": applied, "limit": 20, "offset": 0, "searchText": ""})
        return r.get("total", 0), method, first.get("total", 0)
    r = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "India"})
    return r.get("total", 0), method, first.get("total", 0)


def fetch_workday(c, keep=None, cap=3000):
    host, base = wd_base(c)
    first = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    applied, method = wd_india_facets(first)
    search = "" if applied else "India"
    applied = applied or {}
    jobs, off, total = [], 0, None
    while True:
        r = http(base + "/jobs", {"appliedFacets": applied, "limit": 20, "offset": off,
                                  "searchText": search})
        if total is None:
            total = r.get("total", 0)
        posts = r.get("jobPostings") or []
        if not posts:
            break
        for p in posts:
            loc = p.get("locationsText") or ""
            path = p.get("externalPath") or ""
            title = p.get("title") or ""
            if not path or not title:
                continue
            multi = bool(re.match(r"^\d+\s+locations?$", loc.strip(), re.I))
            if not applied and not multi and not is_india(loc):
                continue
            jobs.append({
                "title": title,
                "url": f"https://{host}/en-US/{c['site']}{path}",
                "location": loc,
                "posted_days": wd_posted_days(p.get("postedOn")),
                "ext_id": path,
                "_multi": multi,
                "_detail": base + path,
            })
        off += 20
        if off >= total or off >= cap:
            break
        time.sleep(0.25)
    # resolve "N Locations" only for jobs we keep
    for j in jobs:
        if j.pop("_multi") and (keep is None or keep(j["title"])):
            try:
                d = http(j["_detail"]).get("jobPostingInfo", {})
                locs = [d.get("location") or ""] + list(d.get("additionalLocations") or [])
                india = [x for x in locs if is_india(x)]
                j["location"] = "; ".join(india or locs)
                if not india and applied == {}:
                    j["_drop"] = True
            except FetchError:
                pass
            time.sleep(0.2)
        j.pop("_detail", None)
    return [j for j in jobs if not j.pop("_drop", False)]


# ---------------------------------------------------------------- Greenhouse
def fetch_greenhouse(c, keep=None, cap=None):
    r = http(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs")
    out = []
    for j in r.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        offices = " ".join(o.get("name", "") + " " + (o.get("location") or "")
                           for o in j.get("offices") or [])
        if not (is_india(loc) or is_india(offices)):
            continue
        out.append({"title": j.get("title", ""), "url": j.get("absolute_url"),
                    "location": loc if is_india(loc) else offices.strip(),
                    "posted_date": (j.get("first_published") or j.get("updated_at") or "")[:10] or None,
                    "ext_id": str(j.get("id"))})
    return out


# ---------------------------------------------------------------- Lever
def fetch_lever(c, keep=None, cap=None):
    region = c.get("region")
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    r = http(f"https://{host}/v0/postings/{c['token']}?mode=json")
    out = []
    for j in r if isinstance(r, list) else []:
        cat = j.get("categories") or {}
        locs = [cat.get("location") or ""] + list(cat.get("allLocations") or [])
        text = "; ".join(x for x in dict.fromkeys(locs) if x)
        if not (is_india(text) or (j.get("country") or "").upper() == "IN"):
            continue
        ts = j.get("createdAt")
        out.append({"title": j.get("text", ""), "url": j.get("hostedUrl"),
                    "location": text or "India",
                    "posted_date": time.strftime("%Y-%m-%d", time.gmtime(ts / 1000)) if ts else None,
                    "ext_id": j.get("id")})
    return out


# ---------------------------------------------------------------- SmartRecruiters
def fetch_smartrecruiters(c, keep=None, cap=3000):
    out, off = [], 0
    while True:
        r = http(f"https://api.smartrecruiters.com/v1/companies/{c['token']}/postings"
                 f"?country=in&limit=100&offset={off}")
        items = r.get("content") or []
        for j in items:
            l = j.get("location") or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("region"), "India"] if x)
            if l.get("remote"):
                loc = "Remote, " + loc
            out.append({"title": j.get("name", ""),
                        "url": f"https://jobs.smartrecruiters.com/{c['token']}/{j.get('id')}",
                        "location": loc,
                        "posted_date": (j.get("releasedDate") or "")[:10] or None,
                        "ext_id": j.get("id")})
        off += 100
        if not items or off >= (r.get("totalFound") or 0) or off >= cap:
            break
    return out


# ---------------------------------------------------------------- Ashby
def fetch_ashby(c, keep=None, cap=None):
    r = http(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}")
    out = []
    for j in r.get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        text = "; ".join(x for x in locs if x)
        if not (is_india(text) or is_india(country)):
            continue
        out.append({"title": j.get("title", ""), "url": j.get("jobUrl"),
                    "location": text or "India",
                    "posted_date": (j.get("publishedAt") or "")[:10] or None,
                    "ext_id": j.get("id")})
    return out


# ---------------------------------------------------------------- Amazon
def fetch_amazon(c, keep=None, cap=4000):
    out, off = [], 0
    while True:
        r = http("https://www.amazon.jobs/en/search.json?"
                 f"normalized_country_code%5B%5D=IND&result_limit=100&offset={off}&sort=recent")
        items = r.get("jobs") or []
        for j in items:
            out.append({"title": j.get("title", ""),
                        "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                        "location": j.get("normalized_location") or j.get("location") or "India",
                        "posted_text": j.get("posted_date"),
                        "ext_id": j.get("id_icims") or j.get("id")})
        off += 100
        if not items or off >= (r.get("hits") or 0) or off >= cap:
            break
        time.sleep(0.3)
    for j in out:
        pt = j.pop("posted_text", None)
        j["posted_date"] = None
        if pt:
            try:
                j["posted_date"] = time.strftime("%Y-%m-%d", time.strptime(pt.strip(), "%B %d, %Y"))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------- Oracle HCM (e.g. JPMorgan)
def fetch_oracle(c, keep=None, cap=3000):
    base = (f"https://{c['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations")

    def call(extra, off):
        finder = (f"findReqs;siteNumber={c['site']},facetsList=LOCATIONS,limit=25,"
                  f"offset={off},sortBy=POSTING_DATES_DESC{extra}")
        return http(base + "&finder=" + urllib.parse.quote(finder, safe=";=,"))

    first = call("", 0)
    item = (first.get("items") or [{}])[0]
    loc_id = None
    for f in item.get("locationsFacet") or []:
        if (f.get("Name") or "").strip().lower() == "india":
            loc_id = f.get("Id")
    extra = f",locationId={loc_id}" if loc_id else ",location=India"
    out, off = [], 0
    while True:
        item = (call(extra, off).get("items") or [{}])[0]
        reqs = item.get("requisitionList") or []
        for q in reqs:
            locs = [q.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in q.get("secondaryLocations") or []]
            if not loc_id and not is_india("; ".join(locs)):
                continue
            out.append({"title": q.get("Title", ""),
                        "url": (f"https://{c['ui_host']}/en/sites/{c['site']}/job/{q.get('Id')}" if c.get("ui_host")
                                else f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{q.get('Id')}"),
                        "location": "; ".join(x for x in locs if x),
                        "posted_date": (q.get("PostedDate") or "")[:10] or None,
                        "ext_id": q.get("Id")})
        off += 25
        if not reqs or off >= (item.get("TotalJobsCount") or 0) or off >= cap:
            break
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- Microsoft (Eightfold "pcsx")
def fetch_microsoft(c, keep=None, cap=3000):
    host = c.get("host", "apply.careers.microsoft.com")
    domain = c.get("domain", "microsoft.com")
    out, start, seen = [], 0, set()
    while True:
        r = http(f"https://{host}/api/pcsx/search?domain={domain}&query=&location=India"
                 f"&start={start}&sort_by=timestamp")
        pos = ((r.get("data") or {}).get("positions")) or []
        fresh = 0
        for p in pos:
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            fresh += 1
            locs = p.get("standardizedLocations") or p.get("locations") or []
            text = "; ".join(locs)
            if not is_india(text) and not any(l.endswith(", IN") for l in locs):
                continue
            ts = p.get("postedTs") or p.get("creationTs")
            out.append({"title": p.get("name", ""),
                        "url": f"https://{host}" + (p.get("positionUrl") or f"/careers/job/{p.get('id')}"),
                        "location": text,
                        "posted_date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
                        "ext_id": str(p.get("id"))})
        start += len(pos)
        if not pos or not fresh or start >= cap:
            break
        time.sleep(1.5)
    return out


# ---------------------------------------------------------------- Eightfold (Qualcomm, HSBC, ...)
def fetch_eightfold(c, keep=None, cap=3000):
    """Tries the newer 'pcsx' search first, then the older v2 API."""
    try:
        return fetch_microsoft(c, keep, cap)
    except FetchError:
        pass
    host, domain = c["host"], c["domain"]
    out, start = [], 0
    while True:
        r = http(f"https://{host}/api/apply/v2/jobs?domain={domain}&start={start}&num=100"
                 f"&location=India&sort_by=timestamp")
        pos = r.get("positions") or []
        for p in pos:
            locs = p.get("locations") or [p.get("location") or ""]
            text = "; ".join(x for x in locs if x)
            if not is_india(text):
                continue
            ts = p.get("t_update") or p.get("t_create")
            out.append({"title": p.get("name", ""),
                        "url": p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p.get('id')}",
                        "location": text,
                        "posted_date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
                        "ext_id": str(p.get("id"))})
        start += len(pos)
        if not pos or start >= (r.get("count") or 0) or start >= cap:
            break
        time.sleep(1.0)
    return out


# ---------------------------------------------------------------- Jibe / iCIMS (e.g. AMD)
def fetch_jibe(c, keep=None, cap=3000):
    host = c["host"]
    out, page = [], 1
    while True:
        r = http(f"https://{host}/api/jobs?location=India&page={page}&limit=100&sortBy=posted_date&descending=true")
        items = r.get("jobs") or []
        for it in items:
            d = it.get("data") or it
            loc = d.get("full_location") or ", ".join(
                x for x in [d.get("city"), d.get("state"), d.get("country")] if x)
            if not is_india(loc + " " + (d.get("country") or "")):
                continue
            rid = d.get("req_id") or d.get("slug") or d.get("id")
            out.append({"title": d.get("title", ""),
                        "url": f"https://{host}{c.get('path', '/careers-home/jobs/')}{rid}",
                        "location": loc,
                        "posted_date": (d.get("posted_date") or d.get("create_date") or "")[:10] or None,
                        "ext_id": str(rid)})
        if not items or page * 100 >= (r.get("totalCount") or r.get("count") or 0) or page * 100 >= cap:
            break
        page += 1
        time.sleep(0.5)
    return out


# ---------------------------------------------------------------- Atlassian (one public JSON feed)
def fetch_atlassian(c, keep=None, cap=None):
    r = http("https://www.atlassian.com/endpoint/careers/listings")
    out = []
    for j in r if isinstance(r, list) else r.get("listings") or r.get("data") or []:
        locs = j.get("locations") or []
        text = "; ".join(x if isinstance(x, str) else (x.get("name") or "") for x in locs) or (j.get("location") or "")
        if not is_india(text):
            continue
        post = j.get("portalJobPost") or {}
        out.append({"title": j.get("title", ""),
                    "url": post.get("portalUrl") or j.get("applyUrl"),
                    "location": text,
                    "posted_date": (post.get("updatedDate") or "")[:10] or None,
                    "ext_id": str(j.get("id") or post.get("id") or j.get("title"))})
    return [j for j in out if j["url"]]


# ---------------------------------------------------------------- Goldman Sachs (GraphQL)
GS_QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId corporateTitle jobTitle jobFunction division
            locations { primary state country city } }
  }
}"""


def fetch_goldman(c, keep=None, cap=3000):
    out, page, size = [], 0, 50
    while True:
        body = {"operationName": "GetRoles", "query": GS_QUERY, "variables": {"searchQueryInput": {
            "page": {"pageSize": size, "pageNumber": page},
            "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
            "filters": [{"filterCategoryType": "LOCATION", "filters": [{"filter": "India", "subFilters": []}]}],
            "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": ""}}}
        r = http("https://api-higher.gs.com/gateway/api/v1/graphql", body,
                 headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})
        rs = ((r.get("data") or {}).get("roleSearch")) or {}
        items = rs.get("items") or []
        for it in items:
            locs = it.get("locations") or []
            cities = [l.get("city") or "" for l in locs if (l.get("country") or "") == "India"]
            if not cities:
                continue
            title = it.get("jobTitle") or ""
            # GS titles end with "- <city>"; drop it so the rank ("- Vice President") is last.
            for cty in cities:
                title = re.sub(rf"\s*[-\u2013]\s*{re.escape(cty)}\s*$", "", title, flags=re.I)
            rid = (it.get("roleId") or "").split("_")[0]
            out.append({"title": title.strip(), "url": f"https://higher.gs.com/roles/{rid}",
                        "cls": f"{it.get('jobFunction') or ''} | {title.strip()}",
                        "location": "; ".join(c + ", India" for c in cities if c) or "India",
                        "posted_date": None, "ext_id": rid})
        page += 1
        if not items or page * size >= (rs.get("totalCount") or 0) or page * size >= cap:
            break
        time.sleep(0.5)
    return out


FETCHERS = {
    "goldman": fetch_goldman,
    "eightfold": fetch_eightfold,
    "jibe": fetch_jibe,
    "atlassian": fetch_atlassian,
    "microsoft": fetch_microsoft,
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
    "ashby": fetch_ashby,
    "amazon": fetch_amazon,
    "oracle": fetch_oracle,
}
