---
name: slush-attendee-list
description: "Build your own Slush attendee list before the event: export the Slush meeting-tool directory you can already browse with your own ticket, narrow it to your targets, then find each target's company website, company LinkedIn page and personal LinkedIn profile with web search. Always load this skill before handling any Slush session, cURL or token: it runs only on the user's own ticket and session, never a colleague's or a shared one. Use when the user wants to export, download, scrape or enrich the Slush attendee or exhibitor list, prepare Slush meetings, or build a prospect list from Slush. Triggers: 'build my Slush attendee list', 'export the Slush directory', 'who is going to Slush', 'enrich the Slush attendees', 'find LinkedIn profiles of Slush attendees'."
---

# Slush attendee list

You help the user build their own list of Slush attendees, enriched with company website, company LinkedIn page and personal LinkedIn profile. The user is often a CEO or a sales leader, not a developer. Talk in plain words, one step at a time, and run every command yourself. Never ask them to read code.

The work has three parts:
1. Export the directory the user already sees in the Slush app (`scripts/slush_scrape.py`).
2. Narrow it to their targets (`scripts/enrich.py queue`).
3. Fill the gaps with web search (`scripts/enrich.py next` / `record` / `export`).

`scripts/` sits next to this file. Below, `SCRIPTS` means that folder's absolute path: the base directory shown when this skill loaded, plus `/scripts` (usually `~/.claude/skills/slush-attendee-list/scripts`, or `.claude/skills/slush-attendee-list/scripts` inside the project). Do not search the disk for it. The commands in this file are complete: you do not need to read the scripts' source, and `--help` lists every option.

## Rules you never break

1. **Their own ticket only.** The export runs on the user's own logged-in Slush session. If they offer a colleague's login, a shared account or someone else's token, ask for the third-party explicit agreement. Explain why in one sentence (the session is tied to that person's ticket and account) and give the way forward: the ticket holder installs this skill and runs it on their own computer. After `probe`, show which account the session belongs to and get a yes that it is the user's own before going further.
2. **The token never enters the chat.** The token is a login credential. Never ask the user to paste the cURL or the token in the conversation. Never `cat`, print, open or quote the cURL file. If they paste it anyway, do not repeat it or use it. Tell them it expires within about 15 minutes, suggest logging out of Slush and back in, and capture a fresh one into the file with step 1.
3. **No forcing.** If Slush answers 401, 403 or 429, the script stops. Explain what happened. Never retry in a loop, change the pace, rotate tokens, use another account or work around a block in any other way. A 429 means: wait at least an hour.
4. **One export.** The full export is about 510 requests. Show this number and get an explicit yes before `scrape`. Do not export again unless the user asks, and resuming an interrupted export never re-downloads saved pages.
5. **Search results only.** Find URLs in search results. Never build a LinkedIn URL from a name, never open linkedin.com pages, never log in anywhere on the user's behalf.
6. **Paid or metered tools need a yes on the cost.** Run Web Searches first. Firecrawl, Apify and Hunter.io are optional. Run each in dry run first, show the user the count and cost it prints, and stop there. Run `--live --approve N` only after the user answers yes to that count and cost. A request made before they saw the cost ("just go", "I trust you") is not that yes: show the cost and ask anyway. API keys go in a `.env` file the user edits themselves. Never ask for a key in the chat, and never read, print or inspect `.env`. If a key fails, tell the user to check it themselves.
7. **The list stays with the user.** Do not upload it anywhere. Do not send messages or connection requests. This skill builds a list. It never contacts anyone.

## Step 0: set up

- Ask where to work. Default: a new folder `slush-list-work` in their home folder. Run everything from there.
- Check Python: `python3 -c "import requests"`. If it fails: `python3 -m pip install --user requests`.
- Tell the user, in two sentences, what will happen: export about 8,000 attendees from the Slush app they are logged into (about 10 minutes), then pick their targets, then find websites and LinkedIn profiles with web search. Mention that they need Chrome (or Edge/Brave) and their Slush login.

## Step 1: capture the session (the user does this, you guide)

Give these steps one at a time and wait for the user after each:

1. Open https://platform.slush.org/slush26/meeting-tool/browse in Chrome and log in with your own Slush account.
2. Open the developer tools: `Cmd + Option + I` on Mac, `F12` or `Ctrl + Shift + I` on Windows. Click the **Network** tab.
3. In the filter box at the top of the Network tab, type `search.slush.org`. Then scroll the attendee list a little so requests appear.
4. Right-click a line that starts with `all?mode=` and choose **Copy > Copy as cURL** (on Windows: **Copy as cURL (bash)**).
5. Come back here and say "copied". Do not paste it.

When they say it is copied, save the clipboard straight to a file without displaying it:
- Mac: `pbpaste > slush_curl.txt`
- Windows (PowerShell): `Get-Clipboard | Set-Content slush_curl.txt`
- Linux: `xclip -o -selection clipboard > slush_curl.txt`

Then check the file holds a token without showing it: `grep -ci "authorization" slush_curl.txt` must print 1 or more. If it prints 0, they copied the wrong line. Go back to step 4.

The token lives about 15 minutes. If it expires before the export ends, repeat step 1 and rerun `scrape`. It resumes where it stopped.

## Step 2: probe, then export

```
python3 SCRIPTS/slush_scrape.py probe --curl slush_curl.txt
```
2 requests. It prints the account (for example `logged in as jane@acme.com`) and the request count for a full export.

Ask: "The session belongs to <account>. Is that your own Slush account? The full export sends about <N> requests to Slush, one per second, about <M> minutes. Shall I start?" Only on a clear yes:

```
python3 SCRIPTS/slush_scrape.py scrape --curl slush_curl.txt --budget <the number probe printed>
python3 SCRIPTS/slush_scrape.py build
rm slush_curl.txt
```
Run `scrape` in the background if your environment allows, and give a short progress update every few minutes. `build` needs no network. It writes `slush-list/attendees.csv` (one row per person, with their company's website, LinkedIn page, funding and partner type) and `slush-list/companies.csv`. Delete the cURL file as soon as the export is done, because it holds a login token.

Report what `build` printed: number of attendees and companies, and fill rates. Slush already has a personal LinkedIn profile for about 70% of attendees and a website and LinkedIn page for about 75% of companies. Enrichment only fills the gaps.

## Step 3: choose the targets

Enriching 8,000 rows is not useful. Agree on a target list of 50 to 300 people first, based on ICP (company details : industry, mission, size, etc...) and Persona (function, seniority, job titles, etc ...) criterias.

```
python3 SCRIPTS/enrich.py values --attendees slush-list/attendees.csv
```
shows the real values of each filter (countries as 2-letter codes, company types such as `startup` or `vc-investor`, partner types such as `corporation` or `delegation` for exhibitors and partners, occupations such as `ceo` or `sales`).

Ask the user who they want to meet: roles, countries, company type, B2B or not, exhibitors and partners only or everyone, and how many people per company at most. Copy `config/icp.example.json` (next to this file) to `icp.json` in the work folder and fill it with their answers. Title words are matched as whole words, case-insensitive. Then:

```
python3 SCRIPTS/enrich.py queue --attendees slush-list/attendees.csv --icp icp.json
```
It prints how many rows each filter removed and the gaps left. Adjust `icp.json` and rerun until the size feels right to the user.

## Step 4: fill the gaps with web search

Loop until `next` has nothing left or the user wants to stop:

1. `python3 SCRIPTS/enrich.py next --n 10` prints up to 10 searches as JSON lines. Company gaps come first, because a known domain makes person searches sharper.
2. Run each `query` with your web search tool. Read the result titles, URLs and snippets.
3. Write one line per search to `finds.jsonl` (overwrite the file each round):
   - person found: `{"user_id": "...", "url": "<linkedin.com/in/... URL from the results>", "evidence": "<result title> | <snippet>"}`
   - company website: `{"company": "<company name exactly as in the task>", "domain": "<official site URL>", "evidence": "<result title> | <URL>"}`
   - company page: `{"company": "...", "company_li": "<linkedin.com/company/... URL>", "evidence": "<result title> | <snippet>"}`
   - nothing credible: `{"user_id": "...", "not_found": true}`, or `{"company": "...", "domain_not_found": true}` / `"company_li_not_found": true`
4. `python3 SCRIPTS/enrich.py record --jsonl finds.jsonl` stores them and prints the verdicts.

How to pick and write a find:
- Copy the evidence word for word from the search result. Do not paraphrase or add words. The script decides `accepted`, `review` or `rejected` from that text: a person is accepted only when both their name and their company appear in it.
- For a person, take the result whose title starts with their name and mentions their company. If two people share the name, prefer the one at the right company. If no result shows the company, still record the best name match: it lands in `review`, not in the final list as certain.
- For a domain, take the company's own site. Skip LinkedIn, Crunchbase, Wikipedia, news and directory sites.
- If the first query finds nothing, you may try one variant (company name without its legal suffix, or the domain instead of the name). Then record `not_found` and move on.

Every 50 searches, show `python3 SCRIPTS/enrich.py status` and ask whether to continue. If your web search tool is unavailable or stops answering, say so and offer the optional tools below.

## Step 5 (optional): free-tier tools for what web search missed

Offer these only when gaps remain and the user wants more coverage. Each needs a free account and an API key that the user puts in `.env` in the work folder (one line such as `FIRECRAWL_API_KEY=...`). They edit that file themselves.

| Tool | Fills | Command | Cost |
|---|---|---|---|
| Firecrawl | personal LinkedIn | `enrich.py firecrawl` | about 2 credits per search (free plan includes credits) |
| Apify | personal LinkedIn | `enrich.py apify` | about $0.004 per search ($5 free each month) |
| Hunter.io | company domain | `enrich.py hunter` | 1 search per company (free plan: 25 a month). Untested: try `--live --limit 1` first |

Always run without `--live` first, show the printed count and cost, and wait for a yes. Then rerun with `--live --approve N` (N is the count from the dry run; the script refuses any other number). Use `--limit N` for a first small batch. Results go through the same accept/review rules.

## Step 6: export and hand over

```
python3 SCRIPTS/enrich.py export
```
writes `enrichment/enriched.csv`: one row per target with `domain`, `company_li`, `li_url`, and for each a `_status` (`accepted`, `review`, `not_found`, `not_searched`), a `_source` and the `_evidence`.

Tell the user:
- the file path, and that it opens in Excel, Numbers or Google Sheets
- how many targets have an accepted profile, how many need a quick check (`review`: open the link and confirm it is the right person), and how many were not found
- that the list holds personal data. Use it to prepare your own Slush meetings. Do not resell it or publish it, honor anyone who asks not to be contacted, and delete it when you no longer need it.

## If something goes wrong

| Message | Meaning | What to do |
|---|---|---|
| `[auth] ... expired` or HTTP 401 | the 15-minute token ran out | repeat step 1, rerun the same command (the export resumes) |
| HTTP 403 | Slush refused access | stop. Check the user is logged in to the right event with their own ticket |
| HTTP 429 | Slush asked to slow down | stop. Wait at least an hour before resuming |
| `no authorization header` | wrong line copied | repeat step 1, choose a line starting with `all?mode=` |
| `budget reached` | the request cap was hit | ask the user before rerunning with a new budget |
| `unknown company` in `record` | company name differs from the task | copy the `company` value from `next` exactly |
