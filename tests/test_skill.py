#!/usr/bin/env python3
"""
Safeguard tests for the slush-attendee-list skill. No network: Slush is replaced by a fake session.
Run from the repository root: python3 -I tests/test_skill.py
"""

import base64
import contextlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import slush_scrape as s  # noqa: E402

s.THROTTLE = 0.0
SECRET_SIG = "SIGNATURE-must-never-be-printed"


def fake_token(minutes=10):
    body = base64.urlsafe_b64encode(json.dumps({"email": "me@example.com", "exp": time.time() + minutes * 60})
                                    .encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.{SECRET_SIG}"


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._data, self.ok = code, data or {}, 200 <= code < 300

    def json(self):
        return self._data


class FakeSession:
    """Serves `pages` result pages per mode, or a fixed status code."""

    def __init__(self, code=200, pages=3, real_pages=None, total=99):
        self.code, self.pages, self.real_pages, self.total, self.calls = code, pages, real_pages, total, []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(url)
        if self.code != 200:
            return Resp(self.code)
        mode = "users" if "mode=users" in url else "companies"
        after = (json or {}).get("options", {}).get("page", {}).get("searchAfter")
        n = int(after[1].split("-")[1]) + 1 if after else 1  # like Slush: the cursor decides the page
        real = self.real_pages or self.pages  # real_pages > pages: Slush reports a capped count
        if n > real:
            return Resp(200, {"results": [], "meta": {"page": {"total_pages": self.pages}, "searchAfter": None}})
        res = [{"user": {"user_id": f"{mode}-{n}-{i}", "first_name": "A", "last_name": f"B{n}"}, "company": {}}
               for i in range(s.PAGE_SIZE if n < real else 5)]
        return Resp(200, {"results": res, "meta": {"page": {"total_pages": self.pages, "total_results": self.total},
                                                   "searchAfter": [1, f"{mode}-{n}"]}})


def run(cmd, tmp, session, budget=0, curl_text=None):
    curl = Path(tmp) / "curl.txt"
    curl.write_text(curl_text or f"curl 'https://search.slush.org/...' -H 'authorization: {fake_token()}'")
    args = type("A", (), {"curl": curl, "event": "slush26", "out": Path(tmp) / "out",
                          "raw_dir": Path(tmp) / "out" / "raw", "budget": budget})()
    out, err, code = io.StringIO(), io.StringIO(), None
    orig = s.requests.Session
    s.requests.Session = lambda: session
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            {"probe": s.cmd_probe, "scrape": s.cmd_scrape, "build": s.cmd_build}[cmd](args)
    except SystemExit as e:
        code = str(e)
    finally:
        s.requests.Session = orig
    return out.getvalue() + err.getvalue() + (code or ""), code


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    return cond


def enrich_checks():
    import enrich as e
    ok = True
    r = {"first_name": "Jane", "last_name": "Doe", "company_name": "Acme Robotics Oy", "domain": "acmerobotics.fi"}
    for url, ev, want in (
            ("https://fi.linkedin.com/in/janedoe", "Jane Doe - CEO - Acme Robotics | LinkedIn", "accepted"),
            ("https://www.linkedin.com/in/janedoe", "Jane Doe - CEO | LinkedIn", "review"),
            ("https://www.linkedin.com/in/johnsmith", "John Smith - CEO - Acme Robotics", "rejected"),
            ("https://www.linkedin.com/in/janedoe", "Jane Doe - Director - Barclays Robotics Bank", "review"),
            ("https://www.linkedin.com/in/janedoe2", "Jane Doe - Director - Barclays Bank", "review"),
            ("https://www.linkedin.com/company/acme", "Jane Doe - CEO - Acme Robotics", "rejected")):
        got = e.judge_person(r, url, ev)[0]
        ok &= check(f"judge_person: {ev[:40]!r} -> {want}", got == want, got)
    A = lambda live, approve: type("A", (), {"live": live, "approve": approve})()
    ok &= check("gate: dry run by default", not e.gate(A(False, None), 5))
    ok &= check("gate: --live without --approve refused", not e.gate(A(True, None), 5))
    ok &= check("gate: --approve with another count refused", not e.gate(A(True, 3), 5))
    ok &= check("gate: --live --approve N runs", e.gate(A(True, 5), 5))
    return ok


def main():
    ok = True
    for code in (401, 403, 429):
        with tempfile.TemporaryDirectory() as t:
            fs = FakeSession(code=code)
            text, stop = run("scrape", t, fs, budget=50)
            ok &= check(f"HTTP {code}: stops after 1 request, no retry", stop and len(fs.calls) == 1,
                        f"{len(fs.calls)} calls")
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession(code=503)
        text, stop = run("scrape", t, fs, budget=50)
        ok &= check("HTTP 503: one retry then stop", stop and len(fs.calls) == 2, f"{len(fs.calls)} calls")
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession(pages=40)
        text, stop = run("scrape", t, fs, budget=7)
        ok &= check("budget 7: never more than 7 requests", stop and len(fs.calls) == 7, f"{len(fs.calls)} calls")
        saved = len(list((Path(t) / "out" / "raw").glob("*.json")))
        fs2 = FakeSession(pages=40)
        run("scrape", t, fs2, budget=200)
        total = len(list((Path(t) / "out" / "raw").glob("*.json")))
        ok &= check("resume: saved pages are not requested again",
                    saved == 7 and total == 80 and len(fs2.calls) == 73, f"saved {saved}, total {total}, "
                    f"second run {len(fs2.calls)} calls")
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession(pages=4, real_pages=6, total=10000)
        text, stop = run("scrape", t, fs, budget=50)
        saved = len(list((Path(t) / "out" / "raw").glob("*.json")))
        ok &= check("capped count (Slush says 4 pages, 6 exist): all 6 pages saved per mode", saved == 12,
                    f"{saved} pages saved")
        text, stop = run("probe", t, FakeSession(pages=417, total=10000))
        ok &= check("probe flags a capped count and adds a margin", "at least 10000" in text and "up to" in text)
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession()
        text, stop = run("scrape", t, fs, budget=0)
        ok &= check("scrape without --budget refuses to start", stop and not fs.calls)
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession(pages=3)
        text, stop = run("probe", t, fs)
        ok &= check("probe sends exactly 2 requests", len(fs.calls) == 2, f"{len(fs.calls)} calls")
        ok &= check("probe output names the account and the budget",
                    "me@example.com" in text and "--budget" in text)
        text2, _ = run("scrape", t, FakeSession(pages=3), budget=20)
        ok &= check("token never printed (probe, scrape)", SECRET_SIG not in text + text2)
    with tempfile.TemporaryDirectory() as t:
        expired = fake_token(minutes=-1)
        fs = FakeSession()
        text, stop = run("probe", t, fs, curl_text=f"-H 'authorization: {expired}'")
        ok &= check("expired token: stops before any request", stop and not fs.calls and SECRET_SIG not in text)
    with tempfile.TemporaryDirectory() as t:
        fs = FakeSession()
        text, stop = run("probe", t, fs, curl_text="curl https://example.com")
        ok &= check("cURL without token: clear error, no request", stop and not fs.calls)
    for raw, want in (("https://linkedin/in/jane-doe", "https://www.linkedin.com/in/jane-doe/"),
                      ("linkedin.com.in/jane", "https://www.linkedin.com/in/jane/"),
                      ("https://www.linkedin.com/company/acme/", ""), ("https://www.linkedin.com/feed/", "")):
        ok &= check(f"clean_profile({raw})", s.clean_profile(raw) == want, s.clean_profile(raw))
    ok &= enrich_checks()
    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
