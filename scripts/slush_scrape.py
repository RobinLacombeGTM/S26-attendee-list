#!/usr/bin/env python3
"""
Export the Slush meeting-tool attendee directory you can already browse with your own ticket.

The Slush platform (platform.slush.org) loads its directory from one search endpoint:
  POST https://search.slush.org/search/meeting-tool/<event>/all?mode={users|companies}
Your browser calls it with a short-lived login token (about 15 minutes). This script reads that
token from a cURL command you copy from your own browser and saves to a file. The token is
never printed.

Commands:
  probe    2 requests: checks the token and prints how many pages a full export needs
  scrape   saves every page to <out>/raw/ (one request per second, stops at the first refusal)
  build    turns the saved pages into <out>/attendees.csv and <out>/companies.csv (no network)

Safeguards built in:
  - one request at a time, at least 1 second apart
  - stops at once on 401 (token expired), 403 (refused) or 429 (rate limited); no retry loop
  - `scrape` needs --budget N (the page count shown by `probe`) and never sends more than N requests
  - an interrupted export resumes where it stopped: saved pages are never requested twice

Usage:
  python3 slush_scrape.py probe  --curl slush_curl.txt
  python3 slush_scrape.py scrape --curl slush_curl.txt --budget 520
  python3 slush_scrape.py build
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:  # build needs no network, so only fail when a request is made
    requests = None

MODES = ["users", "companies"]
PAGE_SIZE = 24
THROTTLE = 1.0           # seconds between requests, never lower
STOP_CODES = {401: "token expired or invalid: copy a fresh cURL from your browser and rerun",
              403: "access refused by Slush: stop here, do not retry",
              429: "Slush asked to slow down: stop here, wait at least an hour before resuming"}

PEOPLE_COLS = ["user_id", "first_name", "last_name", "title", "industry", "occupation", "country", "city",
               "linked_in", "linked_in_raw", "twitter", "website", "bio",
               "company_name", "company_id", "company_type", "company_website", "company_linkedin",
               "company_country", "raised_funding", "founding_year", "latest_equity_round",
               "business_types", "partner_type", "software_hardware", "attendees_from_company",
               "source_mode"]
COMPANY_COLS = ["company_id", "company_name", "company_type", "country", "city", "website", "linked_in",
                "employees_total", "description", "raised_funding", "founding_year", "latest_equity_round",
                "business_types", "partner_type", "software_hardware"]


# ------------------------------------------------------------------ auth --
def read_token(curl_path: Path) -> str:
    """Parse the `authorization` header from a cURL file copied from the browser."""
    if not curl_path.exists():
        raise SystemExit(f"[auth] {curl_path} not found. Save the copied cURL command in that file first.")
    try:
        os.chmod(curl_path, 0o600)
    except OSError:
        pass
    m = re.search(r"authorization:\s*(?:Bearer\s+)?([^'\"\n\\]+)", curl_path.read_text(), re.I)
    if not m:
        raise SystemExit("[auth] no authorization header in that cURL. Copy a request to search.slush.org "
                         "(Network tab, filter 'search.slush.org', right-click > Copy > Copy as cURL).")
    tok = m.group(1).strip()
    _describe_token(tok)
    return tok


def _describe_token(tok: str) -> None:
    """Print whose session this is and how long it lives. Never prints the token itself."""
    try:
        part = tok.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        print("[auth] token found (could not read its expiry)", file=sys.stderr)
        return
    left = int((data.get("exp") or 0) - time.time())
    who = data.get("email") or data.get("sub") or "unknown account"
    if left <= 0:
        raise SystemExit(f"[auth] session of {who} expired {-left}s ago: copy a fresh cURL and rerun")
    print(f"[auth] logged in as {who}, token valid for {left // 60} min {left % 60} s", file=sys.stderr)


# --------------------------------------------------------------- network --
def payload(event: str, search_after=None) -> dict:
    page = {"size": PAGE_SIZE}
    if search_after is not None:
        page["searchAfter"] = search_after
    return {"options": {"query": "", "filters": {"must": [], "should": [], "must_not": [], "filter": []},
                        "page": page,
                        "sort": [{"_score": {"order": "desc"}}, {"random_string": {"order": "asc"}},
                                 {"id": {"order": "asc"}}],
                        "range": []},
            "target_ids": [event]}


def headers(tok: str) -> dict:
    return {"accept": "application/json, text/plain, */*", "authorization": tok,
            "content-type": "application/json", "origin": "https://platform.slush.org",
            "referer": "https://platform.slush.org/"}


class Client:
    """One request at a time, a fixed pause between requests, a hard request budget."""

    def __init__(self, tok: str, event: str, budget: int):
        if requests is None:
            raise SystemExit("The `requests` package is missing: run `python3 -m pip install requests`")
        self.s, self.tok, self.event, self.budget, self.sent, self.last = requests.Session(), tok, event, budget, 0, 0.0

    def page(self, mode: str, search_after):
        if self.sent >= self.budget:
            raise SystemExit(f"[budget] {self.sent} requests sent, budget reached. Rerun with a fresh "
                             "--budget only if the user agrees; saved pages are kept.")
        wait = THROTTLE - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        url = f"https://search.slush.org/search/meeting-tool/{self.event}/all?mode={mode}&sortBy=relevancy"
        for attempt in (1, 2):
            self.sent += 1
            self.last = time.time()
            r = self.s.post(url, headers=headers(self.tok), json=payload(self.event, search_after), timeout=30)
            if r.status_code in STOP_CODES:
                raise SystemExit(f"[stop] HTTP {r.status_code}: {STOP_CODES[r.status_code]}")
            if r.status_code >= 500 and attempt == 1 and self.sent < self.budget:
                print(f"[server] HTTP {r.status_code}, one retry in 10 s", file=sys.stderr)
                time.sleep(10)
                continue
            if not r.ok:
                raise SystemExit(f"[stop] HTTP {r.status_code} from Slush: stopping")
            return r.json()


def total_pages(data: dict) -> int:
    return int((data.get("meta") or {}).get("page", {}).get("total_pages") or 0)


def last_page(data: dict) -> bool:
    """End of the directory: a short or empty page, or no cursor. Not total_pages: the search engine
    caps the reported count at 10,000 results, so the real directory can be longer."""
    return len(data.get("results") or []) < PAGE_SIZE or (data.get("meta") or {}).get("searchAfter") is None


# ---------------------------------------------------------------- probe --
def cmd_probe(args):
    c = Client(read_token(args.curl), args.event, budget=2)
    need, capped = 0, False
    for mode in MODES:
        data = c.page(mode, None)
        meta = (data.get("meta") or {}).get("page", {})
        total = int(meta.get("total_results") or 0)
        if total >= 10000:  # the search engine stops counting at 10,000: the real number is unknown
            capped = True
            need += int(total_pages(data) * 1.3) + 1
            print(f"{mode:9} at least {total} results (Slush stops counting at 10,000), {total_pages(data)}+ pages")
        else:
            need += total_pages(data)
            print(f"{mode:9} {total} results, {total_pages(data)} pages")
    done = sum(len(list(raw_files(args.raw_dir, m))) for m in MODES) if args.raw_dir.exists() else 0
    print(f"\nFull export: {'up to ' if capped else 'about '}{need} requests "
          f"(~{need * 1.5 / 60:.0f} min at 1 request per second; a login token lasts about 15 min, "
          "so expect to copy a fresh one and resume once).")
    if capped:
        print("The budget includes a 30% margin because the real count is above 10,000. "
              "Requests not needed are not sent.")
    if done:
        print(f"{done} pages already saved in {args.raw_dir}: those will not be requested again.")
    print(f"Next step, only with the user's OK:  scrape --budget {need - done + 10}")


# --------------------------------------------------------------- scrape --
def raw_files(raw_dir: Path, mode: str):
    return sorted(raw_dir.glob(f"{mode}_[0-9][0-9][0-9][0-9].json"))


def cmd_scrape(args):
    if not args.budget:
        raise SystemExit("[budget] run `probe` first, show the user the request count, then pass --budget N")
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    c = Client(read_token(args.curl), args.event, budget=args.budget)
    for mode in MODES:
        saved = list(raw_files(args.raw_dir, mode))
        n, search_after, pages = len(saved), None, None
        if saved:  # resume after the last saved page
            last = json.loads(saved[-1].read_text())
            search_after, pages = (last.get("meta") or {}).get("searchAfter"), total_pages(last)
            if last_page(last):
                print(f"[{mode}] complete ({n} pages already saved)", file=sys.stderr)
                continue
            print(f"[{mode}] resuming after page {n}", file=sys.stderr)
        while True:
            data = c.page(mode, search_after)
            n += 1
            (args.raw_dir / f"{mode}_{n:04d}.json").write_text(json.dumps(data, ensure_ascii=False))
            pages = pages or total_pages(data)
            search_after = (data.get("meta") or {}).get("searchAfter")
            if n % 25 == 0 or n == pages:
                print(f"[{mode}] page {n}/{pages}", file=sys.stderr)
            if last_page(data):
                break
        print(f"[{mode}] done: {n} pages", file=sys.stderr)
    print(f"Export finished with {c.sent} requests. Delete the cURL file now: it holds your login token.")


# ---------------------------------------------------------------- build --
def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(_s(x) for x in v if x not in (None, ""))
    return str(v).strip()


def clean_profile(url: str) -> str:
    """Keep only real linkedin.com/in/<slug> profiles (repairs common typos, drops company pages, feeds, redirects)."""
    u = (url or "").strip()
    u = re.sub(r"linkedin\.com\.in/", "linkedin.com/in/", u, flags=re.I)
    u = re.sub(r"^(https?://)?(www\.)?linkedin/in/", "https://www.linkedin.com/in/", u, flags=re.I)
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", u, re.I)
    return f"https://www.linkedin.com/in/{m.group(1)}/" if m else ""


def person(p: dict, company: dict, source: str) -> dict:
    links = p.get("external_links") or {}
    pc = p.get("company") if isinstance(p.get("company"), dict) else {}
    raw_li = _s(p.get("linked_in") or links.get("linked_in"))
    return {"user_id": _s(p.get("user_id") or p.get("id")),
            "first_name": _s(p.get("first_name")), "last_name": _s(p.get("last_name")),
            "title": _s(p.get("title")), "industry": _s(p.get("industry")), "occupation": _s(p.get("occupation")),
            "country": _s(p.get("country")), "city": _s(p.get("city")),
            "linked_in": clean_profile(raw_li), "linked_in_raw": raw_li,
            "twitter": _s(p.get("twitter") or links.get("twitter")),
            "website": _s(p.get("website") or links.get("website")),
            "bio": _s(p.get("bio")).replace("\n", " "),
            "company_id": _s(p.get("company_id") or pc.get("company_id") or company.get("company_id")),
            "company_name": _s(p.get("company_name") or pc.get("company_name") or company.get("company_name")),
            "company_type": _s(p.get("company_type") or pc.get("company_type") or company.get("company_type")),
            "source_mode": source}


def company_row(c: dict) -> dict:
    links = c.get("external_links") or {}
    return {"company_id": _s(c.get("company_id")), "company_name": _s(c.get("company_name")),
            "company_type": _s(c.get("company_type")), "country": _s(c.get("country")), "city": _s(c.get("city")),
            "website": _s(links.get("website") or c.get("website")),
            "linked_in": _s(links.get("linked_in") or c.get("linked_in")),
            "employees_total": _s(c.get("employees_total")),
            "description": _s(c.get("description")).replace("\n", " "),
            "raised_funding": _s(c.get("raised_funding")), "founding_year": _s(c.get("founding_year")),
            "latest_equity_round": _s(c.get("latest_equity_round")), "business_types": _s(c.get("business_types")),
            "partner_type": _s(c.get("partner_type")), "software_hardware": _s(c.get("software_hardware"))}


def merge(prev: dict, new: dict) -> None:
    """A record recurs across pages, sometimes partial: keep the first non-empty value of each field."""
    for k, v in new.items():
        if v and not prev.get(k):
            prev[k] = v


def cmd_build(args):
    files = [f for m in MODES for f in raw_files(args.raw_dir, m)]
    if not files:
        raise SystemExit(f"No saved pages in {args.raw_dir}. Run `scrape` first.")
    people, companies = {}, {}
    for f in files:
        source = f.name.split("_")[0]
        for res in json.loads(f.read_text()).get("results", []):
            c = res.get("company") or {}
            if c.get("company_id"):
                merge(companies.setdefault(_s(c["company_id"]), {}), company_row(c))
            found = [person(e, c, source) for e in (c.get("employees") or [])]
            u = res.get("user") or res.get("person")
            if isinstance(u, dict):
                found.append(person(u, c, source))
            for p in found:
                if p["user_id"]:
                    merge(people.setdefault(p["user_id"], {}), p)
    for p in people.values():
        co = companies.get(p.get("company_id") or "", {})
        p.update({"company_website": co.get("website", ""), "company_linkedin": co.get("linked_in", ""),
                  "company_country": co.get("country", ""), "raised_funding": co.get("raised_funding", ""),
                  "founding_year": co.get("founding_year", ""),
                  "latest_equity_round": co.get("latest_equity_round", ""),
                  "business_types": co.get("business_types", ""), "partner_type": co.get("partner_type", ""),
                  "software_hardware": co.get("software_hardware", ""),
                  "attendees_from_company": co.get("employees_total", "")})
    write_csv(args.out / "attendees.csv", list(people.values()), PEOPLE_COLS)
    write_csv(args.out / "companies.csv", list(companies.values()), COMPANY_COLS)
    rows = list(people.values())
    rate = lambda k: f"{100 * sum(bool(r.get(k)) for r in rows) / max(len(rows), 1):.0f}%"
    print(f"{len(rows)} attendees -> {args.out / 'attendees.csv'}")
    print(f"{len(companies)} companies -> {args.out / 'companies.csv'}")
    print(f"Filled: title {rate('title')} · company {rate('company_name')} · personal LinkedIn {rate('linked_in')}"
          f" · company website {rate('company_website')} · company LinkedIn {rate('company_linkedin')}")


def write_csv(path: Path, rows: list, cols: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["probe", "scrape", "build"])
    ap.add_argument("--curl", type=Path, default=Path("slush_curl.txt"), help="file holding the copied cURL")
    ap.add_argument("--event", default="slush26", help="Slush event id, as in platform.slush.org/<event>/...")
    ap.add_argument("--out", type=Path, default=Path("slush-list"), help="output folder")
    ap.add_argument("--raw-dir", type=Path, default=None, help="saved pages (default: <out>/raw)")
    ap.add_argument("--budget", type=int, default=0, help="scrape: maximum number of requests to send")
    args = ap.parse_args()
    args.raw_dir = args.raw_dir or args.out / "raw"
    {"probe": cmd_probe, "scrape": cmd_scrape, "build": cmd_build}[args.command](args)


if __name__ == "__main__":
    main()
