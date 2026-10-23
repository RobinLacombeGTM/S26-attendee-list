# Audit guide

Everything this skill does is in 5 files, about 1,100 lines. This page tells you where to look and what to check. Allow about 10 minutes.

## 1. Where data goes

The code sends requests to four places, and nowhere else:

| Destination | Code | When |
|---|---|---|
| `search.slush.org` (Slush's own directory search) | [scripts/slush_scrape.py#L132](scripts/slush_scrape.py#L132) | `probe` (2 requests) and `scrape` (about 510 requests, after you approve the number) |
| Firecrawl | [scripts/enrich.py#L447](scripts/enrich.py#L447) | optional, only with `--live --approve N` |
| Apify | [scripts/enrich.py#L482](scripts/enrich.py#L482) | optional, only with `--live --approve N` |
| Hunter.io | [scripts/enrich.py#L517](scripts/enrich.py#L517) | optional, only with `--live --approve N` |

Check it yourself from the repository root:

```
grep -nE "requests\.|\.post\(|\.get\(f?\"http" scripts/*.py
```

Web searches for websites and LinkedIn profiles are made by Claude's own web search tool, not by the scripts. The list is written to CSV files in your work folder. Nothing uploads it, and nothing sends messages or connection requests to anyone.

## 2. What Claude is told to do

[SKILL.md](SKILL.md) has the instructions Claude follows. The 7 rules at the top are the contract: your own ticket only, the token never in the chat, no forcing past a refusal from Slush, one export, URLs from search results only, paid tools only after you approve the cost, and the list stays with you.

## 3. Safeguards in the code

They are enforced by the scripts, so they hold even if Claude misreads an instruction.

| Safeguard | Code |
|---|---|
| One request at a time, 1 second apart | `THROTTLE`, [scripts/slush_scrape.py#L45](scripts/slush_scrape.py#L45) and `Client.page` [#L121](scripts/slush_scrape.py#L121) |
| Stops on 401, 403 or 429, no retry loop | `STOP_CODES` [#L46](scripts/slush_scrape.py#L46), check at [#L133](scripts/slush_scrape.py#L133) |
| Never more requests than the approved budget | [#L122](scripts/slush_scrape.py#L122); `scrape` refuses to start without `--budget` [#L170](scripts/slush_scrape.py#L170); `probe` is capped at 2 [#L150](scripts/slush_scrape.py#L150) |
| Token read from a local file, file set to owner-only, token never printed | `read_token` [#L62](scripts/slush_scrape.py#L62), `_describe_token` [#L79](scripts/slush_scrape.py#L79) (prints the account and expiry only) |
| Paid tools need `--live --approve N`, N being the count shown in the dry run | `gate` [scripts/enrich.py#L412](scripts/enrich.py#L412), called at [#L440](scripts/enrich.py#L440), [#L470](scripts/enrich.py#L470), [#L512](scripts/enrich.py#L512) |
| A LinkedIn profile is accepted only when the result shows both the name and the company | `judge_person` [scripts/enrich.py#L290](scripts/enrich.py#L290), `name_matches` [#L101](scripts/enrich.py#L101), `company_seen` [#L124](scripts/enrich.py#L124) |
| API keys read from your environment or `./.env`, never printed | `env_key` [scripts/enrich.py#L400](scripts/enrich.py#L400) |

## 4. Run the tests

```
python3 -I tests/test_skill.py
```

27 checks, no network. Slush is replaced by a fake server that answers 401, 403, 429 or 503, or serves pages. The tests cover: stopping on each refusal, the request budget, resuming without re-downloading, the token never appearing in output, cleaning of LinkedIn URLs, the accept/review/reject rule, and the lock on paid tools. Expected last line: `ALL PASS`.

## 5. Watch a run

Claude Code shows every command before it runs and asks for your permission. During a run you can read each command and refuse any of them.

## Dependencies

Python 3 and the `requests` package. Nothing else is installed.
