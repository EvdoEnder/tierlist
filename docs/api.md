# tierlist.tech JSON API, v1

Static JSON files, regenerated with the site after every daily run. No key,
no sign-up, no rate limit beyond the CDN's own. Every document carries
`"api_version": 1`. A change that removes or renames a field goes to
`/api/v2/`, and v1 keeps being written; new fields can appear in v1 at any
time, so ignore keys you do not know.

Base URL: `https://tierlist.tech/api/v1/`

Documents never contain the model's internal 0–100 score or its
per-dimension breakdown. A rating is published as its tier only, `S`, `A`,
`B`, `C` or `D`; [/docs/](https://tierlist.tech/docs/) explains how a tier is
reached.

## Where to start

### `latest.json`

| Field | Type | Meaning |
|---|---|---|
| `latest.daily`, `latest.weekly`, `latest.monthly` | URL or null | The newest issue document of each edition. |
| `issues.daily` | list of `YYYY-MM-DD` | Every daily issue that exists, oldest first. |
| `issues.weekly` | list of `YYYY-Www` | Every weekly issue (ISO week). |
| `issues.monthly` | list of `YYYY-MM` | Every monthly issue. |
| `tags` | URL | The tag index. |
| `costs` | URL | The spend document. |

## Issues

`daily/<YYYY-MM-DD>.json`, `weekly/<YYYY-Www>.json`, `monthly/<YYYY-MM>.json`

| Field | Type | Meaning |
|---|---|---|
| `edition` | `daily` · `weekly` · `monthly` | |
| `period` | string | The slug in the file name. |
| `window.from`, `window.to` | date | The inclusive date range the issue covers. |
| `page` | URL | The issue's page on the site. |
| `found` | int or null | New repositories found in the window. |
| `fetched` | int or null | Repositories read from the sources (daily only). |
| `model` | string or null | The model that rated the issue (daily only). |
| `items` | list of item references | Every rated repository, best first. A daily issue lists all of the day's ratings; a weekly or monthly issue lists its ten best. |
| `show_hn` | list of item references | Show HN posts. Only issues before 2026-10-23 have any. |
| `boards` | object | The boards shown below the index; see below. |

An **item reference**:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `github:<owner>/<repo>` or `hn:<id>`. |
| `name`, `url` | string | |
| `source` | `github` · `hn` | |
| `tier` | `S` · `A` · `B` · `C` · `D` | |
| `reason` | string | The model's one-line reasoning. |
| `description` | string | The repository's own description, shortened. |
| `tags` | list of tag slugs | |
| `stars` | int | Stars when it was rated (points for a Show HN post). |
| `language` | string or null | The main language GitHub reports. |
| `api` | URL or null | The item's own document. |

`boards` holds whichever of these the issue showed:

| Key | Rows |
|---|---|
| `hot_new` | `name`, `url`, `stars`, `description`: the day's new repositories by stars, not rated. |
| `gain_1d`, `gain_7d`, `gain_30d` | `name`, `url`, `gain`, `stars_now`: stars gained over the window. |
| `trending_daily`, `trending_weekly`, `trending_monthly` | `name`, `url`, `gain`, `stars`, `tags`: github.com/trending in GitHub's order, not rated. |

## Items

`items/github/<owner>/<repo>.json`, `items/hn/<id>.json`, one per repository
or post that has been rated at least once.

| Field | Type | Meaning |
|---|---|---|
| `id`, `name`, `url`, `source`, `description`, `language`, `tags` | | As in an item reference. |
| `first_seen` | date | The day tierlist.tech first saw it. |
| `ratings` | list | Every rating, newest first: `date`, `tier`, `reason`, `stars`. |

## Tags

`tags/index.json`: `tags` is a list of `tag` (slug), `label` (`zh` and `en`),
`count` and `api`.

`tags/<slug>.json`: `tag`, `label`, `page`, and `items`, up to 200 rows of
`name`, `url`, `tier` (null when not rated), `first_seen` and `description`,
best first.

## Costs

`costs.json` publishes what the language model has cost. Electricity and the
server are not counted.

| Field | Type | Meaning |
|---|---|---|
| `source` | `runs` · null | `runs`: scoring calls only, from each run's own report. Null before the first run. |
| `average_daily_usd` | number or null | Mean daily scoring spend over the last 7 days with a run. |
| `total_usd` | number | Scoring spend since the first run. |
| `months` | list | `month`, `spent_usd`, newest first. |
| `scoring_runs` | list | `date`, `runs`, `cost_usd`, `models`: the scoring call's own cost per day. |
| `donations` | object | `count`, `total_usd`, and `entries` of `date`, `name`, `amount_usd`, `via`. |
| `excludes` | string | What the figures leave out. |
