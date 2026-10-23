#!/usr/bin/env python3
"""
Narrow the attendee list to your targets, then fill three gaps per row:
company website (domain), company LinkedIn page, personal LinkedIn profile.

Claude does the searching (web search). This script keeps the queue and the state, and it decides
whether a find is accepted, so the verdict never rests on Claude's word alone:
  - personal profile: accepted only when the NAME and the COMPANY are both visible in the search
    result (title or snippet) or in the URL. Name only -> review. Name not matching -> rejected.
  - domain / company page: accepted when the company name shows in the domain, URL or result text.

Commands:
  values   most common countries, company types, occupations... (to write icp.json)
  queue    apply icp.json to attendees.csv -> <work>/queue.csv, print the gaps
  next     print the next searches to run (JSON lines with ready-made queries)
  record   store finds from a JSON-lines file; the verdict is computed here
  status   counts per field and status
  export   <work>/enriched.csv (queue rows + filled fields + status, source and evidence)

Optional free-tier tools (dry run unless --live; keys read from environment or ./.env):
  firecrawl  FIRECRAWL_API_KEY  person search, ~2 credits per search
  apify      APIFY_API_TOKEN    person search by name, ~$0.004 per search
  hunter     HUNTER_API_KEY     company domain search, 1 search credit per company (untested)

Usage:
  python3 enrich.py queue --attendees slush-list/attendees.csv --icp icp.json
  python3 enrich.py next --n 10
  python3 enrich.py record --jsonl finds.jsonl
  python3 enrich.py export
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, unquote

STOP = {"the", "and", "of", "inc", "ltd", "llc", "ag", "sa", "sas", "bv", "gmbh", "oy", "oyj", "ab", "nv", "co",
        "plc", "group", "technologies", "technology", "labs", "ai", "io", "com", "limited", "corp", "company",
        "se", "as", "aps", "spa", "srl", "holding", "international", "solutions", "ventures", "capital"}
NICK = {"mike": "michael", "will": "william", "bill": "william", "bob": "robert", "rob": "robert",
        "chris": "christopher", "tom": "thomas", "jim": "james", "dave": "david", "alex": "alexander",
        "matt": "matthew", "nick": "nicholas", "dan": "daniel", "ben": "benjamin", "sam": "samuel",
        "kate": "katherine", "liz": "elizabeth", "tony": "anthony", "andy": "andrew", "joe": "joseph"}
FIELDS = ("domain", "company_li", "li_url")
QUEUE_COLS = ["user_id", "first_name", "last_name", "title", "company_name", "company_type", "country",
              "partner_type", "domain", "company_li", "li_url"]


# ------------------------------------------------------------- matching --
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def key(s):
    return norm(s).replace(" ", "")


def li_profile(url):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "", re.I)
    return f"https://www.linkedin.com/in/{m.group(1).lower()}/" if m else ""


def li_company(url):
    m = re.search(r"linkedin\.com/(company|school|showcase)/([^/?#\s]+)", url or "", re.I)
    return f"https://www.linkedin.com/{m.group(1).lower()}/{m.group(2).lower()}/" if m else ""


def domain_root(u):
    u = re.sub(r"^www\.", "", re.sub(r"^https?://", "", (u or "").lower().strip()).split("/")[0])
    if not u or "linkedin.com" in u or "." not in u:
        return ""
    p = [t for t in u.split(".") if t]
    return ".".join(p[-3:]) if len(p) >= 3 and p[-2] in ("co", "com", "org", "gov", "ac") else ".".join(p[-2:])


def sld(dom):
    p = (dom or "").split(".")
    return p[-3] if len(p) >= 3 and p[-2] in ("co", "com", "org", "gov", "ac") else p[-2] if len(p) >= 2 else ""


def search_name(r):
    toks = [t.strip(",;()") for t in f"{r['first_name']} {r['last_name']}".split()]
    toks = [t for t in toks if len(t) > 1 and not t.endswith(".")]
    return (toks[0], toks[-1]) if len(toks) >= 2 else ("", "")


def slug_matches(url, first, last):
    slug = key(unquote(re.sub(r".*/in/", "", url)).strip("/"))
    f, l = key(first), key(last)
    return bool(f and l and l in slug and (f in slug or slug.startswith(f[0])))


def name_matches(r, text):
    """True when the attendee's name appears at the start of a result title ('Jane Doe - CRO - Acme')."""
    head = re.split(r" [–\-|] ", text or "")[0]
    first, last = search_name(r)
    toks = [t for t in norm(head).split() if t not in ("dr", "prof", "phd", "mba", "msc", "jr", "sr")]
    if not toks or not first:
        return False
    f, l = norm(first).split()[0], norm(last).split()[-1]
    if len(toks) == 2 and len(toks[1]) == 1 and toks[0] == f and l.startswith(toks[1]):
        return True  # LinkedIn shows 'Jane D.' outside your network
    lasts = {t for t in norm(r["last_name"]).split() if len(t) > 2} | {l}  # double surnames: one part is enough
    if not lasts & set(toks):
        return False
    if f in NICK and NICK[f] in toks:
        return True
    return any(t == f or SequenceMatcher(None, f, t).ratio() >= 0.8 or
               (min(len(f), len(t)) >= 3 and (t.startswith(f) or f.startswith(t))) for t in toks)


def company_tokens(name):
    return [t for t in norm(name).split() if len(t) > 2 and t not in STOP]


def company_seen(r, text="", url="", ignore=()):
    """The company shows in the text or URL: by its domain, by the first distinctive word of its name,
    or by two of its words ('Barclays Bank' does not prove 'European Investment Bank').
    Words in `ignore` (the person's own name) never count as proof."""
    toks = company_tokens(r["company_name"])
    words = [t for t in f"{norm(text)} {norm(unquote(url))}".split() if t not in ignore]
    blob = f" {' '.join(words)} "
    dom = r.get("domain") or ""
    if dom and (dom in (text or "").lower() or (len(sld(dom)) > 3 and f" {sld(dom)} " in blob)):
        return True
    hits = [t for t in toks if f" {t} " in blob]
    return bool(toks) and (toks[0] in hits or len(set(hits)) >= 2)


# ---------------------------------------------------------------- state --
class Work:
    def __init__(self, folder: Path):
        self.dir = folder
        self.queue_path, self.state_path = folder / "queue.csv", folder / "state.json"

    def queue(self):
        if not self.queue_path.exists():
            raise SystemExit(f"No queue in {self.dir}. Run `queue` first.")
        with self.queue_path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def state(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"company": {}, "person": {}}

    def save(self, updates):
        """Merge into the state on disk (re-read first), never overwrite it wholesale."""
        st = self.state()
        for scope in ("company", "person"):
            for k, fields in updates.get(scope, {}).items():
                st[scope].setdefault(k, {}).update(fields)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
        tmp.replace(self.state_path)
        return st


def resolved(r, st):
    """Current value and status of each field for a queue row (Slush data first, then finds)."""
    co = st["company"].get(key(r["company_name"]), {})
    pe = st["person"].get(r["user_id"], {})
    out = {}
    for f, src in (("domain", co), ("company_li", co), ("li_url", pe)):
        if r[f]:
            out[f] = {"value": r[f], "status": "accepted", "source": "slush", "evidence": ""}
        else:
            out[f] = src.get(f) or {"value": "", "status": "", "source": "", "evidence": ""}
    return out


def open_gap(cell):
    return cell["status"] not in ("accepted", "review", "not_found")


# ---------------------------------------------------------------- queue --
def load_icp(path):
    icp = json.loads(Path(path).read_text()) if path else {}
    return {k: icp.get(k) for k in ("countries", "company_types", "partner_types", "occupations",
                                    "business_types", "title_include", "title_exclude",
                                    "max_per_company", "limit")}


def cmd_values(args, w):
    """Most common values of each filterable column, to fill icp.json with real values."""
    with open(args.attendees, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"{len(rows)} attendees")
    for col in ("country", "company_type", "partner_type", "occupation", "business_types"):
        n = {}
        for r in rows:
            for v in (r[col].split("; ") if r[col] else ["(empty)"]):
                n[v] = n.get(v, 0) + 1
        print(f"{col}: " + ", ".join(f"{v} {c}" for v, c in sorted(n.items(), key=lambda x: -x[1])[:args.n]))


def words_rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w.lower()) for w in words) + r")\b") if words else None


def cmd_queue(args, w):
    icp = load_icp(args.icp)
    with open(args.attendees, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    inc, exc = words_rx(icp["title_include"]), words_rx(icp["title_exclude"])
    any_of = lambda allowed, value: not allowed or bool(set(value.split("; ")) & set(allowed))
    keep, per_co, why = [], {}, {}
    for r in rows:
        t = r["title"].lower()
        reason = ("country" if icp["countries"] and r["country"] not in icp["countries"] else
                  "company type" if not any_of(icp["company_types"], r["company_type"]) else
                  "partner type" if not any_of(icp["partner_types"], r["partner_type"]) else
                  "occupation" if not any_of(icp["occupations"], r["occupation"]) else
                  "business type" if not any_of(icp["business_types"], r["business_types"]) else
                  "title" if (inc and not inc.search(t)) or (exc and exc.search(t)) else
                  "no company" if not r["company_name"] else "")
        k = key(r["company_name"])
        if not reason and icp["max_per_company"] and per_co.get(k, 0) >= icp["max_per_company"]:
            reason = "max per company"
        if reason:
            why[reason] = why.get(reason, 0) + 1
            continue
        per_co[k] = per_co.get(k, 0) + 1
        keep.append({"user_id": r["user_id"], "first_name": r["first_name"], "last_name": r["last_name"],
                     "title": r["title"], "company_name": r["company_name"], "company_type": r["company_type"],
                     "country": r["country"], "partner_type": r["partner_type"],
                     "domain": domain_root(r["company_website"]), "company_li": li_company(r["company_linkedin"]),
                     "li_url": li_profile(r["linked_in"])})
    if icp["limit"] and len(keep) > icp["limit"]:
        why["over limit"] = len(keep) - icp["limit"]
        keep = keep[:icp["limit"]]
    w.dir.mkdir(parents=True, exist_ok=True)
    with w.queue_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=QUEUE_COLS)
        wr.writeheader()
        wr.writerows(keep)
    print(f"{len(rows)} attendees -> {len(keep)} in queue ({len({key(r['company_name']) for r in keep})} companies)")
    for reason, n in sorted(why.items(), key=lambda x: -x[1]):
        print(f"  out, {reason}: {n}")
    cmd_status(args, w)


# ----------------------------------------------------------------- next --
def short_company(name):
    toks = [t for t in re.sub(r"[\"(),./|]", " ", name).split() if norm(t) not in STOP and len(norm(t)) > 1]
    return " ".join(toks[:3]) or name


def tasks(w):
    q, st = w.queue(), w.state()
    seen = set()
    for r in q:  # company gaps first: a known domain and company page make person searches sharper
        k = key(r["company_name"])
        if k in seen:
            continue
        seen.add(k)
        res = resolved(r, st)
        dom = res["domain"]["value"]
        if open_gap(res["domain"]):
            yield {"type": "company", "field": "domain", "company": r["company_name"], "country": r["country"],
                   "query": f'"{r["company_name"]}" official website'}
        if open_gap(res["company_li"]):
            yield {"type": "company", "field": "company_li", "company": r["company_name"],
                   "query": f'site:linkedin.com/company "{dom or short_company(r["company_name"])}"'}
    for r in q:
        res = resolved(r, st)
        first, last = search_name(r)
        if open_gap(res["li_url"]) and first:
            yield {"type": "person", "user_id": r["user_id"], "name": f"{r['first_name']} {r['last_name']}",
                   "title": r["title"], "company": r["company_name"],
                   "query": f'site:linkedin.com/in "{first} {last}" {short_company(r["company_name"])}'}


def cmd_next(args, w):
    todo = list(tasks(w))
    for t in todo[:args.n]:
        print(json.dumps(t, ensure_ascii=False))
    print(f"# {min(args.n, len(todo))} shown, {len(todo)} searches left", file=sys.stderr)


# --------------------------------------------------------------- record --
def judge_person(r, url, evidence):
    url = li_profile(url)
    if not url:
        return "rejected", "not a linkedin.com/in/ profile URL"
    first, last = search_name(r)
    named = slug_matches(url, first, last) or name_matches(r, evidence)
    if not named:
        return "rejected", "name not found in the result"
    own = set(norm(f"{r['first_name']} {r['last_name']}").split())
    seen = company_seen(r, evidence, "", ignore=own)  # the profile slug is the person's name, not proof
    return ("accepted", "") if seen else ("review", "company not visible in the result")


def judge_company(r, field, value, evidence):
    if field == "domain":
        value = domain_root(value)
        if not value:
            return value, "rejected", "not a website domain"
    else:
        value = li_company(value)
        if not value:
            return value, "rejected", "not a linkedin.com/company/ URL"
    return value, ("accepted" if company_seen(r, evidence, value) else "review"), ""


def apply_finds(w, finds, source_default="web search"):
    q, st = w.queue(), w.state()
    by_user = {r["user_id"]: r for r in q}
    by_co = {key(r["company_name"]): r for r in q}
    upd, tally = {"company": {}, "person": {}}, {}
    for f in finds:
        src = f.get("source") or source_default
        ev = (f.get("evidence") or "")[:400]
        if f.get("user_id"):
            r = by_user.get(f["user_id"])
            if not r:
                print(f"  ! unknown user_id {f['user_id']}", file=sys.stderr)
                continue
            if f.get("not_found"):
                cell = {"value": "", "status": "not_found", "source": src, "evidence": ev}
            else:
                status, note = judge_person(r, f.get("url", ""), ev)
                cell = {"value": li_profile(f.get("url", "")) if status != "rejected" else "",
                        "status": status, "source": src, "evidence": ev, "note": note,
                        "candidate": f.get("url", "")}
            upd["person"].setdefault(r["user_id"], {})["li_url"] = cell
            tally[("li_url", cell["status"])] = tally.get(("li_url", cell["status"]), 0) + 1
            continue
        r = by_co.get(key(f.get("company", "")))
        if not r:
            print(f"  ! unknown company {f.get('company')!r}", file=sys.stderr)
            continue
        r = dict(r, domain=resolved(r, st)["domain"]["value"])  # a known domain also proves a company page
        for field in ("domain", "company_li"):
            if field not in f and not f.get(f"{field}_not_found"):
                continue
            if f.get(f"{field}_not_found"):
                cell = {"value": "", "status": "not_found", "source": src, "evidence": ev}
            else:
                value, status, note = judge_company(r, field, f[field], ev)
                cell = {"value": value if status != "rejected" else "", "status": status, "source": src,
                        "evidence": ev, "note": note, "candidate": f[field]}
                if field == "domain" and status != "rejected":
                    r["domain"] = value
            upd["company"].setdefault(key(r["company_name"]), {})[field] = cell
            tally[(field, cell["status"])] = tally.get((field, cell["status"]), 0) + 1
    w.save(upd)
    for (field, status), n in sorted(tally.items()):
        print(f"  {field:10} {status:9} {n}")
    return tally


def cmd_record(args, w):
    text = Path(args.jsonl).read_text() if args.jsonl else args.json
    finds = [json.loads(line) for line in text.splitlines() if line.strip()]
    apply_finds(w, finds)


# --------------------------------------------------------- status/export --
def cmd_status(args, w):
    q, st = w.queue(), w.state()
    counts = {f: {} for f in FIELDS}
    for r in q:
        res = resolved(r, st)
        for f in FIELDS:
            s = res[f]["status"] or "to search"
            counts[f][s] = counts[f].get(s, 0) + 1
    for f in FIELDS:
        print(f"  {f:10} " + " · ".join(f"{s} {n}" for s, n in sorted(counts[f].items())))


def cmd_export(args, w):
    q, st = w.queue(), w.state()
    cols = QUEUE_COLS[:8] + [f"{f}{s}" for f in FIELDS for s in ("", "_status", "_source", "_evidence")]
    out = Path(args.out) if args.out else w.dir / "enriched.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in q:
            res, row = resolved(r, st), dict(r)
            for f in FIELDS:
                row.update({f: res[f]["value"] if res[f]["status"] in ("accepted", "review") else "",
                            f"{f}_status": res[f]["status"] or "not_searched", f"{f}_source": res[f]["source"],
                            f"{f}_evidence": res[f]["evidence"]})
            wr.writerow(row)
    print(f"{len(q)} rows -> {out}")
    cmd_status(args, w)


# ------------------------------------------------------- optional tools --
def env_key(name):
    if os.environ.get(name):
        return os.environ[name]
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                return v.strip().strip("'\"")
    raise SystemExit(f"{name} not set (environment variable or a line in ./.env)")


def gate(args, n):
    """Paid or metered call: --live runs only with --approve N, N being the count the user was shown."""
    if not args.live:
        print(f"Dry run, nothing sent. Show the user this count and cost. Only after their yes, rerun with: "
              f"--live --approve {n}")
        return False
    if args.approve != n:
        print(f"Refused: --approve must equal {n}, the count shown to the user in the dry run. "
              "Run without --live first and get the user's yes on that count and cost.")
        return False
    return n > 0


def person_gaps(w, limit, tool):
    """People without an accepted profile that this tool has not searched yet."""
    q, st = w.queue(), w.state()
    out = []
    for r in q:
        res = resolved(r, st)
        if res["li_url"]["status"] != "accepted" and res["li_url"]["source"] != tool and search_name(r)[0]:
            out.append(dict(r, company_li=res["company_li"]["value"], domain=res["domain"]["value"]))
    return out[:limit] if limit else out


def cmd_firecrawl(args, w):
    todo = person_gaps(w, args.limit, "firecrawl")
    print(f"Firecrawl: {len(todo)} people without an accepted profile -> about {2 * len(todo)} credits "
          "(free plan: check your remaining credits at firecrawl.dev before running)")
    if not gate(args, len(todo)):
        return
    import requests
    tok, finds = env_key("FIRECRAWL_API_KEY"), []
    for r in todo:
        first, last = search_name(r)
        query = f'site:linkedin.com/in "{first} {last}" {short_company(r["company_name"])}'
        resp = requests.post("https://api.firecrawl.dev/v2/search", headers={"Authorization": f"Bearer {tok}"},
                             json={"query": query, "limit": 5}, timeout=60)
        if resp.status_code in (401, 402, 403, 429):
            print(f"Firecrawl HTTP {resp.status_code} (key, credits or rate limit): stopping")
            break
        web = (resp.json().get("data") or {}).get("web") or [] if resp.ok else []
        hit = next((x for x in web if li_profile(x.get("url"))
                    and judge_person(r, x["url"], f"{x.get('title')} {x.get('description')}")[0] != "rejected"), None)
        finds.append({"user_id": r["user_id"], "source": "firecrawl", "url": hit["url"],
                      "evidence": f"{hit.get('title')} | {hit.get('description')}"} if hit
                     else {"user_id": r["user_id"], "source": "firecrawl", "not_found": True})
        time.sleep(7)  # free plan: about 10 requests per minute
    apply_finds(w, finds, "firecrawl")


APIFY_ACTOR = "harvestapi~linkedin-profile-search-by-name"


def cmd_apify(args, w):
    todo = person_gaps(w, args.limit, "apify")
    print(f"Apify: {len(todo)} people -> about ${0.004 * len(todo):.2f} "
          f"({sum(bool(r['company_li']) for r in todo)} filtered by their company page, the rest by country). "
          "The free plan includes $5 of usage per month.")
    if not gate(args, len(todo)):
        return
    import requests
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {env_key('APIFY_API_TOKEN')}"})
    finds = []
    for r in todo:
        first, last = search_name(r)
        body = {"profileScraperMode": "Short", "firstName": first, "lastName": last, "strictSearch": True,
                "maxPages": 1, "maxItems": 5}  # free plan: 10 items max per run
        if r["company_li"]:
            body["currentCompanies"] = [r["company_li"]]
        resp = s.post(f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?timeout=180",
                      json=body, timeout=240)
        items = resp.json() if resp.ok else [{"error": f"HTTP {resp.status_code}"}]
        err = next((i["error"] for i in items if isinstance(i, dict) and i.get("error")), None)
        if err:
            print(f"Apify error ({err}): stopping, this person stays in the queue")
            break
        for i in items:
            ev = f"{i.get('name')} | {i.get('position') or ''}"
            if r["company_li"]:
                ev += f" | {r['company_name']}"  # the company filter proves the employer
            if li_profile(i.get("linkedinUrl")) and judge_person(r, i["linkedinUrl"], ev)[0] != "rejected":
                finds.append({"user_id": r["user_id"], "source": "apify", "url": i["linkedinUrl"], "evidence": ev})
                break
        else:
            finds.append({"user_id": r["user_id"], "source": "apify", "not_found": True})
    apply_finds(w, finds, "apify")


def cmd_hunter(args, w):
    q, st = w.queue(), w.state()
    seen, todo = set(), []
    for r in q:
        k = key(r["company_name"])
        if k not in seen and resolved(r, st)["domain"]["status"] != "accepted":
            seen.add(k)
            todo.append(r)
    todo = todo[:args.limit] if args.limit else todo
    print(f"Hunter: {len(todo)} companies without an accepted domain -> {len(todo)} searches "
          "(free plan: 25 per month). UNTESTED integration: run --live --limit 1 first and check the result.")
    if not gate(args, len(todo)):
        return
    import requests
    tok, finds = env_key("HUNTER_API_KEY"), []
    for r in todo:
        resp = requests.get(f"https://api.hunter.io/v2/domain-search?company={quote(r['company_name'])}&limit=1",
                            headers={"X-API-KEY": tok}, timeout=30)
        if resp.status_code in (401, 403, 429):
            print(f"Hunter HTTP {resp.status_code} (key or quota): stopping")
            break
        d = (resp.json().get("data") or {}) if resp.ok else {}
        finds.append({"company": r["company_name"], "source": "hunter", "domain": d["domain"],
                      "evidence": f"{d.get('organization') or ''} {d['domain']}"} if d.get("domain")
                     else {"company": r["company_name"], "source": "hunter", "domain_not_found": True})
        time.sleep(1)
    apply_finds(w, finds, "hunter")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["values", "queue", "next", "record", "status", "export", "firecrawl", "apify", "hunter"])
    ap.add_argument("--work", type=Path, default=Path("enrichment"), help="working folder (queue, state, export)")
    ap.add_argument("--attendees", default="slush-list/attendees.csv")
    ap.add_argument("--icp", default=None, help="icp.json (see icp.example.json)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--jsonl", default=None, help="record: file with one find per line")
    ap.add_argument("--json", default=None, help="record: finds given inline, one per line")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--live", action="store_true", help="optional tools: actually call the API")
    ap.add_argument("--approve", type=int, default=None, help="optional tools: the count the user approved")
    args = ap.parse_args()
    w = Work(args.work)
    {"values": cmd_values, "queue": cmd_queue, "next": cmd_next, "record": cmd_record, "status": cmd_status, "export": cmd_export,
     "firecrawl": cmd_firecrawl, "apify": cmd_apify, "hunter": cmd_hunter}[args.command](args, w)


if __name__ == "__main__":
    main()
