# Running your own copy

tierlist is one Python package, a PostgreSQL database and a folder of static
files. A small VPS or a home server runs it; the daily job takes a few minutes
and the site needs nothing but a web server pointed at the output folder.

## 1. Install

Python 3.12 and PostgreSQL 14 or newer.

```bash
git clone https://github.com/EvdoEnder/tierlist.git
cd tierlist
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

On Windows the virtual environment's programs live in `.venv\Scripts\`
instead of `.venv/bin/`: run `.venv\Scripts\pip install -e ".[dev]"`, then
`copy .env.example .env`, and read `.venv\Scripts\python` for
`.venv/bin/python` everywhere below.

Create an empty database and a user that owns it. On Debian or Ubuntu, with
PostgreSQL installed from the system packages:

```bash
sudo -u postgres createuser --pwprompt tierlist   # asks for a password
sudo -u postgres createdb --owner tierlist tierlist
```

Put its address in `.env` as `TIERLIST_PG_DSN`, for example
`postgresql://tierlist:YOUR_PASSWORD@localhost/tierlist`. The engine creates
the `intel` schema and its tables on first run.

Put a GitHub token in `.env` as `GITHUB_TOKEN`. A fine-grained personal access
token with no extra permissions is enough, since everything it reads is public.
The engine runs without one, but then GitHub's anonymous search limit stops
the recently-public source part way through, and the daily star refresh does
not run at all because GitHub's GraphQL API refuses anonymous requests. The
star-gain boards are thin without it.

Check the setup before adding a model:

```bash
.venv/bin/python -m intel.run --rebuild-site   # every page the store can build; empty at first
.venv/bin/python -m intel.run_news             # the home page and the news page
```

The home page (`index.html`) is written only by the news job, so a fresh
install has no home page until `intel.run_news` has run once.

## 2. Plug in a language model

The scorer speaks the OpenAI chat-completions protocol, so any provider or
gateway that offers `/chat/completions` works. Everything is set in `.env`:

| Variable | Meaning |
|---|---|
| `DEEPSEEK_API_KEY` | The default: DeepSeek's own API, with no other setting. |
| `TIERLIST_LLM_BASE_URL` | Your own endpoint instead, without the `/chat/completions` suffix. |
| `TIERLIST_LLM_API_KEY` | The key for that endpoint. It takes precedence over `DEEPSEEK_API_KEY`. |
| `TIERLIST_LLM_MODEL` | The model id exactly as that endpoint names it. |

The model needs a large output budget: each request rates up to 50
repositories (`SCORE_BATCH` in `intel/score/llm.py`) and a reasoning model
spends much of its budget before the JSON starts. `scoring.max_tokens` in
`config.yaml` sets it; keep it under your provider's output limit.

A local model works the same way: point `TIERLIST_LLM_BASE_URL` at any server
with an OpenAI-compatible API, such as Ollama or vLLM.

Without a key, the daily job still fetches and stores the day's repositories,
then stops at rating with `No LLM key — set TIERLIST_LLM_API_KEY (or
DEEPSEEK_API_KEY) in .env`. The next run with a key rates whatever its own
fetch returns that has no rating yet, and the fetch covers the last 72 hours
(`lookback_hours` in `config.yaml`), so a day or two without a key loses
nothing. For an older day, `python -m intel.run --date YYYY-MM-DD` rates what
that day stored.

## 3. Choose the taste

What the model looks for is plain text in `intel/rubrics/dev_tools.yaml`: four
dimensions, their weights, the prompt, and the score lines that turn the
weighted score into tiers. Edit it to rate for a different reader. The lines
under `tiers:` are percentiles of that rubric's own score distribution, so a
rubric with a different taste needs its lines set again from its own scores.

`intel/rubrics/tags.yaml` is the closed vocabulary topic tags are chosen from.

### When the model changes

A provider alias such as DeepSeek's `deepseek-flash` moves to each new model
the provider releases, and a new model scores the same rubric differently.
The engine keeps tier lines per model version so the share of each tier
stays put:

- Before each daily run it reads the version from the endpoint's
  `GET /models` (the display name, such as `DeepSeek-V4.1-Flash`), or uses
  the model id when the endpoint has no such route. Every score records it.
- The first version a database sees takes the lines under `tiers:`.
- A new version is calibrated on the spot: the items rated in the last 30
  days are scored again by it, kept apart from the published scores, and
  its lines are the same percentiles of those scores. Past issues keep
  their tiers. One calibration rescores at most 2,000 items, about $1 at
  DeepSeek's prices. If it cannot finish, the day is still published, its
  scores borrow the previous lines, and the next run resumes.
- A daily drift check warns in the log when a week's share of A-or-better
  strays far from the expected 10%, which can mean the model changed under
  the same name. It never recalibrates on its own.

`calibration:` in `config.yaml` holds the window, the item ceiling and the
drift band; `auto: false` turns the automatic calibration off.

```bash
.venv/bin/python -m intel.calibrate --status   # versions, lines, drift; no model call
.venv/bin/python -m intel.calibrate --force    # measure the current version again
```

With a dated model id (a gateway's `deepseek/deepseek-v4.1-flash-1017`, say)
the version never changes and none of this runs.

## 4. Schedule it

In UTC, the schedule tierlist.tech runs:

```
0 0 * * *     python -m intel.run --vertical dev_tools
10 0 * * 0    python -m intel.run --vertical dev_tools --edition weekly
10 0 1 * *    python -m intel.run --vertical dev_tools --edition monthly
20 0 * * 0    python -m intel.run --rebuild-site
*/15 * * * *  python -m intel.run_news
```

Run each with the virtual environment's Python. The engine loads `.env` from
the project folder itself, so cron needs no environment of its own.

As a file in `/etc/cron.d/`, with the project in `/opt/tierlist`, run by a
user named `tierlist`, and logs in `/var/log/tierlist/` (create that folder
and give the user write access first):

```
P=/opt/tierlist
PY=/opt/tierlist/.venv/bin/python

0 0 * * *     tierlist cd $P && flock -n /tmp/tierlist-daily.lock $PY -m intel.run --vertical dev_tools >> /var/log/tierlist/daily.log 2>&1
10 0 * * 0    tierlist cd $P && flock /tmp/tierlist-daily.lock $PY -m intel.run --vertical dev_tools --edition weekly >> /var/log/tierlist/weekly.log 2>&1
10 0 1 * *    tierlist cd $P && flock /tmp/tierlist-daily.lock $PY -m intel.run --vertical dev_tools --edition monthly >> /var/log/tierlist/monthly.log 2>&1
20 0 * * 0    tierlist cd $P && flock /tmp/tierlist-daily.lock $PY -m intel.run --rebuild-site >> /var/log/tierlist/rebuild.log 2>&1
*/15 * * * *  tierlist cd $P && flock -n /tmp/tierlist-news.lock $PY -m intel.run_news >> /var/log/tierlist/news.log 2>&1
```

`flock` keeps two copies of a job from running at once. With `-n` a job that
finds its lock taken skips its turn: a news refresh that comes round while
the last one is still running, or a second daily run. Without `-n` it waits,
so the weekly, monthly and rebuild jobs queue behind the daily one, which
shares their lock because they write the same pages. A file in
`/etc/cron.d/` names the user on each line; in a personal `crontab -e` leave
that column out. cron reads the hours in the machine's own time zone; on a
machine not set to UTC (`timedatectl` shows it), shift them.

The logs grow without limit. A `logrotate` rule such as this one in
`/etc/logrotate.d/tierlist` keeps them small:

```
/var/log/tierlist/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
```

## 5. Serve it

The site is written to `data/web/` (or `TIERLIST_WEB_DIR`). English is at the
root and Chinese under `/zh/`. Serve the folder with any web server, and set
`TIERLIST_SITE_URL` to the public address so feeds, canonical links and the
sitemap name the right host.

Every page is a plain file and every link names its file, so the server
needs no rewrite rules. With [Caddy](https://caddyserver.com/), which fetches
the HTTPS certificate on its own, this is the whole configuration in
`/etc/caddy/Caddyfile`:

```
tierlist.example.com {
	root * /opt/tierlist/data/web
	file_server
	encode zstd gzip
}
```

Replace the name with your own domain, point its DNS at the machine, and
run `sudo systemctl reload caddy`. Any other server (nginx, Apache, a static
host that serves a folder) works the same way: serve the folder, nothing
more. To look at the site on your own computer first:

```bash
.venv/bin/python -m http.server 8765 --directory data/web
```

then open <http://localhost:8765/>.

## Back up the database

Everything the site shows is rebuilt from the database, so the database is
the one thing to keep. One more cron line writes a compressed copy every
night and keeps two weeks of them:

```
30 3 * * *    tierlist pg_dump -Fc tierlist > /var/backups/tierlist/tierlist-$(date +\%F).dump && find /var/backups/tierlist -name '*.dump' -mtime +14 -delete
```

Create `/var/backups/tierlist/` and give the user write access first; `%`
must be written `\%` inside a crontab. Restore a copy into an empty database
with `pg_restore -d tierlist tierlist-YYYY-MM-DD.dump`. A copy on the same
disk does not survive that disk; copy the folder somewhere else as well.

## On macOS

A Mac runs tierlist well enough to try it out or keep it for yourself; a
laptop that sleeps is a poor host for a public site. Install the two
prerequisites with [Homebrew](https://brew.sh/):

```bash
brew install python@3.12 postgresql@16
brew services start postgresql@16
createdb tierlist
```

Homebrew's PostgreSQL makes your own macOS account its administrator, so no
separate user or password is needed: set
`TIERLIST_PG_DSN=postgresql://localhost/tierlist`. Create the virtual
environment with `python3.12 -m venv .venv`; everything else in this guide
runs as written.

cron skips a job whose time passes while the Mac sleeps. launchd, macOS's
own scheduler, runs it once on waking, so use it instead. Each job is one
file in `~/Library/LaunchAgents/`. The daily one, as
`tech.tierlist.daily.plist`, with the project in `~/tierlist` (launchd does
not expand `~`, so write the full path):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>tech.tierlist.daily</string>
  <key>WorkingDirectory</key><string>/Users/you/tierlist</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/tierlist/.venv/bin/python</string>
    <string>-m</string><string>intel.run</string>
    <string>--vertical</string><string>dev_tools</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/you/tierlist/data/daily.log</string>
  <key>StandardErrorPath</key><string>/Users/you/tierlist/data/daily.log</string>
</dict>
</plist>
```

`Hour` is in the Mac's own time zone, so convert 00:00 UTC to it. Load it with
`launchctl load ~/Library/LaunchAgents/tech.tierlist.daily.plist`. The other
jobs are copies with their own label, arguments and time; for the news job,
replace `StartCalendarInterval` with `<key>StartInterval</key><integer>900</integer>`
(every 15 minutes); for the weekly one add `<key>Weekday</key><integer>0</integer>`
(Sunday) to the calendar entry, and for the monthly one `<key>Day</key><integer>1</integer>`.
launchd never starts a second copy of a job that is still running, but it
does not hold one job back for another: give the weekly, monthly and rebuild
jobs an hour after the daily one, so they do not write the same pages at
once.

## 6. Let an AI agent run it

[`AGENTS.md`](../AGENTS.md) tells any coding agent how the project is laid
out and how to check a run. For Claude Code, the skill in
[`.claude/skills/tierlist-daily/`](../.claude/skills/tierlist-daily/SKILL.md)
runs the daily job end to end: it checks the settings, runs the sweep, reads
the log for the known failure shapes and rebuilds the site. Open the project
in Claude Code and ask it to run the daily job, or type `/tierlist-daily`.

## Optional parts

- `features:` in `config.yaml` switches on parts that tierlist.tech has
  built but not yet published: the JSON API and the funding page.
