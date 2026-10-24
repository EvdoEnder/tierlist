---
name: tierlist-daily
description: Run tierlist's daily job end to end and confirm it worked - check the settings, run the sweep, read the log for the known failure shapes, rebuild the site. Use when asked to run the daily job, the daily sweep, today's issue, or to check why today's issue is missing or empty. Also covers the weekly and monthly issues and a full site rebuild.
---

# Run the daily job

Work from the repository root. Use the project's virtual environment:
`.venv/bin/python`. Never print the contents of `.env`; refer to settings by
name only.

## 1. Check the settings

Without reading values, confirm `.env` defines:

- `TIERLIST_PG_DSN`, the Postgres store. Without it the sweep runs but stores and
  publishes nothing.
- A model key: `TIERLIST_LLM_API_KEY` (with `TIERLIST_LLM_BASE_URL` and
  `TIERLIST_LLM_MODEL`) or `DEEPSEEK_API_KEY`.

```bash
grep -oE '^(TIERLIST_PG_DSN|TIERLIST_LLM_API_KEY|TIERLIST_LLM_BASE_URL|TIERLIST_LLM_MODEL|DEEPSEEK_API_KEY)=.' .env | cut -d= -f1
```

A name that does not print is unset. Stop and tell the user which one.

## 2. Run the sweep

```bash
.venv/bin/python -m intel.run --vertical dev_tools 2>&1 | tee data/run-$(date -u +%F).log
```

It fetches the last 72 hours of new repositories, drops bought-star batches,
rates the rest in requests of 50, stores everything and writes the site. Expect a few minutes; do not
start a second copy while one runs.

On Sunday also run the weekly issue, and on the first of the month the
monthly one:

```bash
.venv/bin/python -m intel.run --vertical dev_tools --edition weekly
.venv/bin/python -m intel.run --vertical dev_tools --edition monthly
```

## 3. Read the log

A run that looks normal can still be empty. Check each of these and report
what you find:

| Line | Meaning |
|---|---|
| `store: pg (...)` | The store is connected. `store: off` means nothing was saved. |
| `scorer: batch N of M failed: ...` | One request failed and was skipped; the others still count. Several in a row usually means the provider is down or the key is out of credit (HTTP 402). |
| `scorer: ... candidates in, 0 parsed (finish_reason='length' ...)` | The model ran out of output budget before finishing the JSON. Raise `scoring.max_tokens` in `config.yaml`, staying under the provider's output limit. |
| `scorer: N of M candidates absent from the batch, asking again` | The model skipped rows; it is asked once more for those. |
| `... tags: failed (...)` | Tagging failed; boards render without tags. Not fatal. |
| `site:  <path>` | A page was written. No `site:` lines means nothing was published. |

Then confirm the issue exists:

```bash
ls data/web/daily/$(date -u +%F).html data/web/zh/daily/$(date -u +%F).html
```

(`TIERLIST_WEB_DIR` replaces `data/web` when it is set.)

## 4. If a day failed at the scoring stage

The fetch window moves on, so do not rerun the sweep for a past day. Rate the
stored cohort instead; items already rated that day are skipped, so it is
safe to repeat:

```bash
.venv/bin/python -m intel.run --date YYYY-MM-DD
```

## 5. After changing templates or styles

```bash
.venv/bin/python -m intel.run --rebuild-site
.venv/bin/python -m intel.run_news
```

The first re-renders every past issue and the shared pages; the second writes
the landing and news pages, which the rebuild does not touch.

## Report

Tell the user: how many repositories were fetched and rated, how many batches
failed, the cost line from the report, and the path of today's issue. Say
plainly if anything above did not check out.
