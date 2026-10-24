# Notes for coding agents

tierlist finds new open-source repositories every day, has a language model
rate each one, and publishes the result as a static site. Read
[README.md](./README.md) for what it does and
[docs/self-hosting.md](./docs/self-hosting.md) for setting it up.

## Layout

| Path | What is there |
|---|---|
| `intel/sources/` | One adapter per source (GitHub search, GitHub Trending, Hacker News, RSS). |
| `intel/score/` | The free prefilter and the LLM scorer. The endpoint and key are read in `intel/score/llm.py`. |
| `intel/rubrics/` | The taste: dimensions, weights, prompt and tier lines (`dev_tools.yaml`), and the tag vocabulary (`tags.yaml`). |
| `intel/store/` | Postgres access. `schema.py` is the whole schema, applied on every run. |
| `intel/editions.py` | Builds the daily, weekly and monthly page contexts. |
| `intel/deliver/` | Writes the site, the feeds, the sitemap and the markdown brief. |
| `intel/templates/`, `intel/render/static/` | Jinja templates and the one stylesheet. |
| `config.yaml` | Sources, filter rules, batch sizes, schedule-independent settings. |
| `tests/` | Offline suite: no network, no database, no key. |

## Rules

- Run `.venv/bin/python -m pytest` and `.venv/bin/ruff check .` after a change;
  both must pass.
- Never print or commit `.env`. Refer to settings by name.
- Pages, feeds and API documents show a tier, never the model's 0–100 score.
  Tests enforce this.
- A rubric change changes every future rating. Say what it changes and why.

## Running the daily job

Follow [.claude/skills/tierlist-daily/SKILL.md](./.claude/skills/tierlist-daily/SKILL.md).
Claude Code loads it as the `/tierlist-daily` skill; any other agent can read
it as a checklist.
