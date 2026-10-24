"""The paid stage: ONE batched LLM call ranks the day's candidates.

Cost control is the point: every candidate goes out in a single
request and comes back as strict JSON. The model scores four dimensions
0–25 each; the weighted total is recomputed HERE from the frozen decision
weights (§11 #4), never taken from the model — deterministic, and the only
number the Top 榜 trusts.

Parsing is defensive: an entry with an unknown id, a missing dimension, an
out-of-range number or an unrecognized verdict is dropped and counted,
never guessed into shape.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime

import httpx

from intel.rubrics import Rubric
from intel.sources.base import Item

_TIMEOUT_S = 1800.0  # one daily batch call at reasoning_effort=high. 900s
                     # was the ceiling until one slow model took 867s over
                     # fifty candidates on 2026-09-27 against a usual
                     # ~230s; the ceiling has to cover the slow day
_REASON_MAX_CHARS = 200
# The English line says the same thing in up to three times the characters.
_REASON_EN_MAX_CHARS = 700
# Headroom a reasoner burns before any JSON appears (2k-9k measured
# 2026-08-23); config.yaml's scoring.max_tokens carries the same floor.
_REASONING_FLOOR = 12000
_RETRIES = 2         # 429/5xx/network — the call is idempotent, retry is free

_DEFAULT_BASE_URL = "https://api.deepseek.com"
# The id DeepSeek's own `GET /models` lists. `deepseek-v4-flash` and
# `deepseek-v4-flash-vision-exp` were retired on 2026-09-10 and are only
# aliased onto V4.1-Flash for compatibility, so neither is a name to
# depend on.
_DEFAULT_MODEL = "deepseek-flash"
_OPENCODE_SESSION = "tierlist"


def llm_endpoint() -> str:
    """Any OpenAI-compatible `/chat/completions` endpoint works: the default
    is DeepSeek direct, `TIERLIST_LLM_BASE_URL` reroutes through a gateway."""
    base = os.environ.get("TIERLIST_LLM_BASE_URL",
                          _DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/chat/completions"


def llm_api_key() -> str:
    """Gateway key (`TIERLIST_LLM_API_KEY`) wins over the DeepSeek-native name."""
    return (os.environ.get("TIERLIST_LLM_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def llm_model() -> str:
    """The scoring model id — `TIERLIST_LLM_MODEL` overrides the default.

    The one place the name is decided. The page's provenance line used to
    resolve it a second time with a different default, so an unset variable
    made the site name a model that had never been called."""
    return os.environ.get("TIERLIST_LLM_MODEL", _DEFAULT_MODEL)


def gateway_headers(endpoint: str) -> dict[str, str]:
    """Headers a specific gateway requires beyond the OpenAI-API surface.

    OpenCode's Go endpoint rejects a request without `x-opencode-session`
    (HTTP 400, `MissingSessionID`) — it routes by session, and an absent id
    is refused rather than defaulted. The value only has to be stable and
    identify the caller; the run is one batched call, so one id per service
    is enough."""
    if "opencode.ai" in endpoint:
        return {"x-opencode-session": _OPENCODE_SESSION}
    return {}


def build_body(model: str, system: str, user: str, *, temperature: float,
               max_tokens: int) -> dict:
    """Request body — one shape for every OpenAI-compatible endpoint.

    `response_format=json_object` and `reasoning_effort` are both standard
    OpenAI-API surface, accepted by DeepSeek direct and by a gateway alike,
    so nothing endpoint-specific is left to branch on.

    Thinking is bought at `high` deliberately. The native endpoint used to be
    sent `thinking=disabled` to keep a metered bill down; measured against a
    real day — 5k input and 13k output tokens for fifty candidates — a year
    of reasoning costs a few dollars, and the judgment is the whole point of
    the Top 榜. What it does cost is a ceiling large enough to hold it: a
    49-candidate batch on 2026-09-09 spent all 34700 completion tokens
    reasoning and returned an empty string.

    `temperature` is sent for the endpoints that honour it. DeepSeek ignores
    it in thinking mode rather than erroring, so the config value is a
    preference, not a guarantee."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    body["reasoning_effort"] = "high"
    return body


def thinking_off(body: dict, endpoint: str) -> dict:
    """Switch a request's reasoning off, for calls with nothing to judge."""
    body.pop("reasoning_effort", None)
    body["thinking"] = {"type": "disabled"}
    return body


@dataclass(frozen=True, slots=True)
class ScoredItem:
    item_id: str
    score: int                 # 0–100 normalized; frozen weights applied here
    dims: dict[str, int]       # raw 0–25 per dimension as judged by the model
    reason: str
    verdict: str               # 'watch' | 'skip'
    tags: list[str] = field(default_factory=list)   # vocabulary slugs
    # The same judgment in English, for the English pages. Empty when the
    # model left it out; `intel.translate_reasons` fills it in afterwards.
    reason_en: str = ""
    model_version: str | None = None   # which model made it; see model_info


def weighted_total(dims: dict[str, int], weights: dict[str, float]) -> int:
    """Per-dimension 0–25, weighted by the rubric, renormalized to 0–100."""
    max_weighted = 25.0 * sum(weights.values())
    raw = sum(dims[name] * weight for name, weight in weights.items())
    return round(raw / max_weighted * 100)


def build_system_prompt(rubric: Rubric) -> str:
    dim_lines = "\n".join(
        f"- {name} (weight ×{rubric.weights[name]}): {text}"
        for name, text in rubric.dimensions.items()
    )
    return (
        f"{rubric.prompt_head}\n\n"
        f"维度定义(每维 0-25 整数分):\n{dim_lines}\n\n"
        "输出严格 JSON 对象,不要多余文字,不要代码围栏:\n"
        '{"results": [{"id": "<候选 id 原样返回>",'
        ' "dims": {' + ", ".join(f'"{n}": <0-25>' for n in rubric.dimensions) + "},"
        ' "reason": "<60-120 字实质判断",'
        ' "reason_en": "<同一判断的英文版,1-2 句,25-45 词>",'
        ' "tags": ["<slug>", "..."],'
        ' "verdict": "watch"|"skip"}]}\n'
        + _tag_instructions() + "\n"
        "不要输出总分——总分由调用方加权计算。每个候选都必须出现在 results 里。"
        "id 必须逐字符原样复制输入里的值(含 github: 前缀),不要改写或缩写。"
    )


def clean_tags(raw) -> list[str]:
    """Model tag list → the tags that get stored, resolved against the
    controlled vocabulary (`intel.tags`).

    This used to accept whatever the model said: lowercase pure-ASCII,
    dedupe, cap at 6. That is how the corpus reached 266 tags over 114 items
    with 229 of them used exactly once, and how all 17 `crack` items also
    carried `破解` — both spellings were equally "valid", so both got a page.
    Resolution now runs through the alias table and the topic list, so the
    two halves of a bilingual pair land on one slug and an invented concept
    lands on none."""
    from intel.tags import normalize

    return normalize(raw)


def _age_days(c: Item, now: datetime) -> str:
    if c.created_at is None:
        return "?"
    return f"{max((now - c.created_at).total_seconds() / 86400.0, 0.0):.1f}"


def candidate_line(c: Item, now: datetime) -> str:
    """One candidate, described in the units its own platform uses.

    A repository has stars and a language; a Hacker News post has points, a
    link, and a title doing the work of a description. Printing the post's
    points under a `stars=` label asks the model to reason about a number
    that does not mean what the label says.
    """
    if c.source == "hn":
        return (f"- {c.id} | {c.name} | 来源=Hacker News (Show HN) | "
                f"points={c.stars} | age_days={_age_days(c, now)}\n"
                f"  链接: {c.url}"
                + (f"\n  {c.description}" if c.description else ""))
    return (f"- {c.id} | {c.name} | lang={c.lang or 'unknown'} | "
            f"stars={c.stars} | age_days={_age_days(c, now)}\n"
            f"  {c.description}")


def build_user_prompt(candidates: list[Item], now: datetime) -> str:
    return "候选列表:\n" + "\n".join(
        candidate_line(c, now) for c in candidates)


def parse_response(content: str, candidates: list[Item],
                   rubric: Rubric,
                   problems: list[str] | None = None) -> list[ScoredItem]:
    """Strict JSON → ScoredItems for known ids only. Bad entries are dropped.
    When `problems` is a list, every drop appends a one-line reason (capped
    at 12) so a paid zero-parse run is diagnosable from the cron log alone."""

    def note(msg: str) -> None:
        if problems is not None and len(problems) < 12:
            problems.append(msg)

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        note(f"content is not JSON: {content[:120]!r}")
        return []
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        keys = sorted(map(str, payload)) if isinstance(payload, dict) \
            else type(payload).__name__
        note(f"no 'results' list in payload (keys: {keys})")
        return []
    by_id = {c.id: c for c in candidates}
    sample = f" (expected like {candidates[0].id!r})" if candidates else ""
    scored: list[ScoredItem] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            note(f"row[{i}] not an object: {type(row).__name__}")
            continue
        # The model mangles the id it was given from time to time — the bare
        # repo name without the 'github:' prefix (26/26 rows dropped for
        # this on 2026-08-25), or a whole prompt line echoed back. A strict
        # rejection voids a paid batch over a formatting mutation, so both
        # parsers share one tolerant resolver.
        raw_id = row.get("id")
        item_id = resolve_item_id(raw_id, by_id.keys())
        if item_id is None:
            note(f"row[{i}] unknown id {raw_id!r}{sample}")
            continue
        if item_id in seen:
            note(f"row[{i}] duplicate id {item_id!r}")
            continue
        dims_raw = row.get("dims")
        if not isinstance(dims_raw, dict):
            note(f"row[{i}] ({item_id}) dims is {type(dims_raw).__name__}, "
                 f"not an object: {str(dims_raw)[:80]!r}")
            continue
        dims: dict[str, int] = {}
        ok = True
        for name in rubric.dimensions:
            v = dims_raw.get(name)
            if v is None:
                note(f"({item_id}) dims missing {name!r} (got keys "
                     f"{sorted(map(str, dims_raw))[:8]})")
                ok = False
                break
            if isinstance(v, bool) or not isinstance(v, int):
                note(f"({item_id}) dim {name!r}={v!r} "
                     f"({type(v).__name__}), want int 0-25")
                ok = False
                break
            if not 0 <= v <= 25:
                note(f"({item_id}) dim {name!r}={v} out of 0-25")
                ok = False
                break
            dims[name] = v
        if not ok:
            continue
        # The verdict annotates; the dims decide. A model that omits or
        # garbles it (observed live 2026-08-23: fifty clean entries, no
        # verdicts) degrades to skip instead of voiding a paid judgment.
        verdict = str(row.get("verdict", "")).strip().lower()
        if verdict not in ("watch", "skip"):
            verdict = "skip"
        seen.add(item_id)
        scored.append(ScoredItem(
            item_id=item_id,
            score=weighted_total(dims, rubric.weights),
            dims=dims,
            reason=str(row.get("reason", "")).strip()[:_REASON_MAX_CHARS],
            verdict=verdict,
            tags=clean_tags(row.get("tags")),
            reason_en=str(row.get("reason_en") or "").strip()[
                :_REASON_EN_MAX_CHARS],
        ))
    return scored


@dataclass(frozen=True, slots=True)
class ModelInfo:
    version: str                    # what tier lines are kept under
    max_output_tokens: int | None   # the endpoint's own ceiling, when it says


async def model_info(client: httpx.AsyncClient | None = None) -> ModelInfo:
    """The version of the model `llm_model()` names, read from the endpoint's
    `GET /models`.

    DeepSeek answers a call to the alias `deepseek-flash` with the alias
    itself in the `model` field, so the call cannot say which model served
    it. `/models` lists the alias with a display name
    (`DeepSeek-V4.1-Flash`), which changes when the alias moves to a new
    model, and that name is the version. An endpoint without the route, or
    one whose entry carries no name, falls back to the model id: a dated id
    such as a gateway's names its version already."""
    model = llm_model()
    base = os.environ.get("TIERLIST_LLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    api_key = llm_api_key()
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.get(f"{base}/models",
                                headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        for entry in resp.json().get("data") or []:
            if entry.get("id") == model:
                limit = entry.get("max_output_tokens")
                return ModelInfo(str(entry.get("name") or model),
                                 int(limit) if limit else None)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    finally:
        if own:
            await client.aclose()
    return ModelInfo(model, None)


async def _post_chat(client: httpx.AsyncClient, endpoint: str, body: dict,
                     api_key: str) -> dict:
    """POST /chat/completions with retries on the transient tiers.

    4xx is a contract violation — retrying cannot help and would double-spend
    on a billable error; 429/5xx/network are transient and the request is
    idempotent (a batch scored twice parses to the same judgment).
    """
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    headers.update(gateway_headers(endpoint))
    for attempt in range(_RETRIES):
        try:
            resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError:
            if attempt == _RETRIES - 1:
                raise
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == _RETRIES - 1:
                resp.raise_for_status()
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        resp.raise_for_status()  # genuine errors (400, 401, 403): no retry
        return resp.json()
    raise RuntimeError("llm post retry exhausted")  # pragma: no cover


# Candidates per scoring request. Every new repository of the day is rated,
# a few hundred of them, and one request for all of them would need an output
# ceiling past what an endpoint serves; fifty is the batch size every
# day's scoring ran at before, so each request stays the shape that was
# measured.
SCORE_BATCH = 50


async def score_candidates(candidates: list[Item], rubric: Rubric, *,
                           now: datetime, temperature: float,
                           max_tokens: int, model_version: str | None = None,
                           ) -> tuple[list[ScoredItem], dict]:
    """All candidates, in requests of `SCORE_BATCH`. Returns (scores, usage);
    `usage` is the provider token reports summed, for the run summary.
    Every score is stamped with `model_version` (see `model_info`).

    A batch that raises is reported and skipped, so one failed request costs
    its own fifty rows rather than the whole day; when every batch fails the
    last error is raised, since a day with no scores at all must not publish
    as a quiet day."""
    api_key = llm_api_key()
    if not api_key:
        raise RuntimeError(
            "No LLM key — set TIERLIST_LLM_API_KEY (or DEEPSEEK_API_KEY) in .env")
    endpoint = llm_endpoint()
    scores: list[ScoredItem] = []
    usage: dict = {}
    error: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for start in range(0, len(candidates), SCORE_BATCH):
            batch = candidates[start:start + SCORE_BATCH]
            try:
                got, got_usage = await _score_with_retry(
                    client, endpoint, api_key, batch, rubric, now=now,
                    temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001 — reported, then re-raised if alone
                print(f"scorer: batch {start // SCORE_BATCH + 1} of "
                      f"{len(batch)} failed: {exc}", file=sys.stderr)
                error = exc
                continue
            scores += got
            usage = merge_usage(usage, got_usage)
    if error is not None and not scores:
        raise error
    return [replace(s, model_version=model_version) for s in scores], usage


async def _score_with_retry(client: httpx.AsyncClient, endpoint: str,
                            api_key: str, candidates: list[Item],
                            rubric: Rubric, *, now: datetime,
                            temperature: float,
                            max_tokens: int) -> tuple[list[ScoredItem], dict]:
    """One request for one batch. Returns (scores, usage).

    A batch that comes back with some candidates absent gets exactly one
    follow-up request for the absent ones. Both MiMo models were measured
    doing this on 2026-09-27 (one of fifty rows missing, finish_reason
    `stop`, nothing malformed): the model simply wrote fewer rows than it was
    given, and an absent row is a candidate with no score that day and no
    error anywhere. The follow-up is small, so it costs a fraction of the
    batch; `usage` is the sum of both calls.

    `max_tokens` is the reasoning floor, not the whole ceiling — the rows are
    added on top by `score_max_tokens`."""
    scores, usage = await _score_batch(
        client, endpoint, api_key, candidates, rubric, now=now,
        temperature=temperature, max_tokens=max_tokens)
    got = {s.item_id for s in scores}
    missing = [c for c in candidates if c.id not in got]
    if scores and missing:
        print(f"scorer: {len(missing)} of {len(candidates)} candidates "
              f"absent from the batch, asking again: "
              f"{[c.id for c in missing]}", file=sys.stderr)
        extra, extra_usage = await _score_batch(
            client, endpoint, api_key, missing, rubric, now=now,
            temperature=temperature, max_tokens=max_tokens)
        scores += [s for s in extra if s.item_id not in got]
        usage = merge_usage(usage, extra_usage)
    return scores, usage


async def _score_batch(client: httpx.AsyncClient, endpoint: str, api_key: str,
                       candidates: list[Item], rubric: Rubric, *,
                       now: datetime, temperature: float,
                       max_tokens: int) -> tuple[list[ScoredItem], dict]:
    """One scoring request over `candidates` and its parse."""
    body = build_body(
        llm_model(),
        build_system_prompt(rubric),
        build_user_prompt(candidates, now),
        temperature=temperature,
        max_tokens=score_max_tokens(len(candidates), max_tokens),
    )
    data = await _post_chat(client, endpoint, body, api_key)
    choice = data["choices"][0]
    content = choice["message"]["content"]
    problems: list[str] = []
    scores = parse_response(content, candidates, rubric, problems)
    if not scores and candidates:
        # A paid call that parses to nothing is the one failure mode this
        # stage can hide — the report would just show an empty Top 榜. Say
        # exactly what came back, once, where the cron log will keep it.
        try:
            payload = json.loads(content)
            rows = payload.get("results") if isinstance(payload, dict) else None
            why = (f"json ok but 0/{len(rows or [])} rows survived the "
                   "strict per-entry checks")
        except json.JSONDecodeError as exc:
            why = (f"json invalid ({exc.msg} at pos {exc.pos}); "
                   f"tail: {content[-200:]!r}")
        detail = "".join(f"\n  drop: {p}" for p in problems)
        print(f"scorer: {len(candidates)} candidates in, 0 parsed "
              f"(finish_reason={choice.get('finish_reason')!r}, "
              f"completion={data.get('usage', {}).get('completion_tokens')} "
              f"tokens) — {why}{detail}\n  head: {content[:400]!r}",
              file=sys.stderr)
    return scores, data.get("usage") or {}


def merge_usage(a: dict, b: dict) -> dict:
    """Sum two provider usage reports field by field, nested dicts included.
    Numbers add; anything else keeps the first report's value."""
    out = dict(a)
    for key, value in b.items():
        if isinstance(value, bool):
            out.setdefault(key, value)
        elif isinstance(value, (int, float)) and isinstance(
                out.get(key, 0), (int, float)):
            out[key] = (out.get(key) or 0) + value
        elif isinstance(value, dict) and isinstance(out.get(key, {}), dict):
            out[key] = merge_usage(out.get(key) or {}, value)
        else:
            out.setdefault(key, value)
    return out


def _tag_instructions() -> str:
    """The tag half of BOTH prompts, built from the vocabulary file so the
    list the model is handed and the list it is graded against cannot drift
    apart.

    The Chinese gloss rides along with each slug on purpose: a model
    reasoning in Chinese that is shown only `game-cheat` writes 游戏外挂
    beside it, which is the bilingual split this vocabulary exists to close.
    """
    from intel import tags as _tags

    return (
        f"tags:从下面这份**固定清单**里选 {_tags.MIN_TOPICS}-{_tags.MAX_TOPICS} 个"
        "(只写等号左边的 slug,不要写中文说明):\n"
        f"{_tags.prompt_block()}\n"
        "从不同角度选(做什么、给谁用、跑在哪个平台),**不要自己造词**;"
        "ai、llm、productivity 这种大类词最多选一个,优先选具体的。"
        "alternative 只给**点名对标某个付费或闭源产品**的项目"
        "(描述里写了 X alternative、复刻 X、免费版 X),"
        "把对标的产品名同时写成专有名词(如 photoshop、tradingview);"
        "没点名对标对象的不算。"
        f"另外最多再加 {_tags.MAX_ENTITIES} 个专有名词"
        "(产品/协议/游戏/格式名,如 valorant、ffmpeg、x64dbg),"
        "小写 ASCII,不要中文。"
        "**不要输出编程语言**(rust/python/...),每个条目本来就单独显示语言。"
    )


def _tag_system_prompt() -> str:
    return (
        "你是开发者工具仓库的标签员。\n"
        "输出严格 JSON 对象,不要多余文字,不要代码围栏:\n"
        '{"tags": [{"id": "<候选 id 原样返回>", "tags": ["<slug>", "..."]}]}\n'
        "每个候选都必须出现在 tags 里。\n"
        + _tag_instructions()
    )


def build_tag_user_prompt(candidates: list[Item],
                          readmes: dict[str, str] | None = None) -> str:
    """One line per candidate. The name is NOT repeated beside the id: an id
    is `github:owner/repo` and the name is `owner/repo`, so the old
    `- {id} | {name}` line said it twice and invited the model to echo the
    whole line back as the id (observed 2026-08-25, a 25-item batch lost to
    `"github:x/y | x/y"`). One value, one place to copy it from.

    `readmes` carries a README excerpt for the rows whose description is too
    thin to tag from (`sources.github_readme`); it rides on its own line
    under the description."""
    readmes = readmes or {}
    lines = []
    for c in candidates:
        line = f"- {c.id}\n  {c.description[:200]}"
        if readmes.get(c.id):
            line += f"\n  README 摘录: {readmes[c.id]}"
        lines.append(line)
    return "候选列表:\n" + "\n".join(lines)


def resolve_item_id(raw, known_ids) -> str | None:
    """The id the model meant, or None. Shared by both parsers because both
    see the same slips, and a strict rejection voids a whole paid batch over
    a formatting mutation.

    Tolerated: the pipe-suffixed copy of a whole prompt line, and the bare
    repo name with the `source:` prefix dropped."""
    if not isinstance(raw, str):
        return None
    if raw in known_ids:
        return raw
    head = raw.split(" | ", 1)[0].strip()
    if head in known_ids:
        return head
    if "/" in head and ":" not in head:
        for known in known_ids:
            if known.endswith(":" + head):
                return known
    return None


def parse_tag_response(content: str, known_ids: set[str]) -> dict[str, list[str]]:
    """Strict-ish JSON → {item_id: tags} for known ids only; junk is dropped."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    rows = payload.get("tags") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = resolve_item_id(row.get("id"), known_ids)
        if item_id is None or item_id in out:
            continue
        out[item_id] = clean_tags(row.get("tags"))
    return out


async def tag_candidates(candidates: list[Item],
                         readmes: dict[str, str] | None = None,
                         ) -> tuple[dict[str, list[str]], dict]:
    """Backfill helper: ONE request asking ONLY for tags. Returns
    ({item_id: tags}, usage); ids the model skipped are simply absent."""
    api_key = llm_api_key()
    if not api_key:
        raise RuntimeError(
            "No LLM key — set TIERLIST_LLM_API_KEY (or DEEPSEEK_API_KEY) in .env")
    endpoint = llm_endpoint()
    body = build_body(
        llm_model(),
        _tag_system_prompt(),
        build_tag_user_prompt(candidates, readmes),
        temperature=0.2,
        max_tokens=tag_max_tokens(len(candidates)),
    )
    known = {c.id for c in candidates}
    usage: dict = {}
    # A batch that parses to nothing although the model stopped normally is
    # asked once more. Observed 2026-10-18: a well-formed 50-row prompt came
    # back as `{"tags":[],"note":"候选列表为空..."}`, the model answering the
    # system prompt as though the list were absent. It was the first such
    # reply in 18 batches; the same request is expected to succeed. A
    # truncated reply is not retried: the same ceiling would cut it again.
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for attempt in range(2):
            data = await _post_chat(client, endpoint, body, api_key)
            usage = merge_usage(usage, data.get("usage") or {})
            choice = data["choices"][0]
            content = choice["message"]["content"]
            tagged = parse_tag_response(content, known)
            if tagged or choice.get("finish_reason") == "length":
                break
            if attempt == 0:
                print(f"tag batch of {len(candidates)} parsed to 0 items, "
                      "asking again", file=sys.stderr)

    # A truncated or unparseable batch must NOT come back as "the model had
    # no tags for anyone". The caller writes an empty array for every miss —
    # that is what keeps a skipped item out of the NULL queue — so a silent
    # empty result would overwrite the real tags of every item in the batch.
    # Observed 2026-08-25: max_tokens was a fixed 2000, a 30-item batch cut
    # off mid-JSON, and a --dry-run showed all 30 heading for [].
    if candidates and not tagged:
        reason = choice.get("finish_reason")
        detail = ("response hit the token ceiling"
                  if reason == "length"
                  else f"finish_reason={reason!r}, content={content[:160]!r}")
        raise RuntimeError(
            f"tag batch of {len(candidates)} parsed to 0 items — {detail}")
    return tagged, usage


def score_max_tokens(n_candidates: int, floor: int) -> int:
    """Output ceiling for a scoring batch: the reasoning floor plus the rows.

    Same shape as `tag_max_tokens` and for the same reason, arrived at the
    hard way. The scoring call asked for a flat `scoring.max_tokens`, so the
    thinking and the JSON competed for one budget that only the thinking
    could win: on 2026-08-29 a 37-candidate batch spent all 16003 completion
    tokens on `reasoning_tokens` and returned an empty string, and the day
    published as "nothing cleared the threshold" — a failure wearing the face
    of a quiet day.

    A scored row is far fatter than a tag row: four dims, a verdict, tags and
    a 60–120 character Chinese reason and its English version, ~400 tokens
    once punctuation is escaped. The floor is the larger term and neither
    term is optional.

    Unused ceiling is free — it caps, it does not spend."""
    return max(floor, _REASONING_FLOOR) + 400 * max(n_candidates, 1)


def tag_max_tokens(n_candidates: int) -> int:
    """Output ceiling for a tag batch: the reasoning floor plus the rows.

    The floor dominates and is not optional. Reasoner-family models spend
    thousands of tokens thinking before the JSON starts — 2k–9k per batch,
    measured 2026-08-23, which is why `scoring.max_tokens` in config.yaml is
    16000 for a job whose answer is a few hundred tokens of JSON. The tag
    call asked for a flat 2000, i.e. below the floor, so it could truncate
    mid-thought and never emit a character of JSON. That is very likely how
    239 items came to hold an empty tag array: not "the model had nothing"
    but "the model never got to speak", recorded as a result.

    Unused ceiling is free — it caps, it does not spend."""
    return _REASONING_FLOOR + 90 * max(n_candidates, 1)


TRANSLATE_BATCH = 40
_TRANSLATE_SYSTEM = (
    "You translate short Chinese editorial judgments of software projects "
    "into English for a developer audience. For each input, write one or two "
    "natural English sentences (25-45 words) that keep every point: what the "
    "project is, why it is worth a look, and the main risk. Keep product, "
    "company and project names as written. Do not add facts.\n"
    'Output strict JSON only: {"results": [{"k": "<key>", "en": "<English>"}]}, '
    "one entry per input, with each key copied exactly."
)

# A repository's own description, written in Chinese, Japanese, Korean or
# Russian, for the English page. The author's words, so nothing is added.
DESCRIBE_SYSTEM = (
    "You translate the one-line descriptions of software repositories into "
    "English for a developer audience. For each input, write one natural "
    "English line that says what the original says and nothing more. Keep "
    "product, company and project names, commands and code as written. If "
    "part is already English, keep it.\n"
    'Output strict JSON only: {"results": [{"k": "<key>", "en": "<English>"}]}, '
    "one entry per input, with each key copied exactly."
)


def translate_max_tokens(n: int) -> int:
    """Output ceiling for a translation batch. Thinking is off, so this is
    the rows alone (~70 tokens each, measured) with room to spare."""
    return 1000 + 250 * max(n, 1)


def parse_translations(content: str, n: int) -> dict[int, str]:
    """`{"results": [{"k", "en"}]}` → {index: English}. Keys outside 0..n-1,
    repeats and empty strings are dropped, so a confused reply can leave a
    row untranslated but can never attach English to the wrong row."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    rows = payload.get("results") if isinstance(payload, dict) else None
    out: dict[int, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            k = int(str(row.get("k", "")).strip())
        except ValueError:
            continue
        en = str(row.get("en") or "").strip()[:_REASON_EN_MAX_CHARS]
        if 0 <= k < n and k not in out and en:
            out[k] = en
    return out


async def translate_to_english(texts: list[str], *,
                               client: httpx.AsyncClient | None = None,
                               system: str = _TRANSLATE_SYSTEM,
                               ) -> tuple[dict[int, str], dict]:
    """ONE request translating up to `TRANSLATE_BATCH` Chinese reasons, or
    repository descriptions with `system=DESCRIBE_SYSTEM`.
    Returns ({index: English}, usage); an index the model skipped is absent.
    Keys are list positions rather than item ids: a two-digit number is
    copied back reliably, where a long id invites a near-miss.

    Thinking is switched off: there is nothing to judge. Measured on 40 rows
    (2026-10-24), low effort still spent 18,030 completion tokens, ~450 a
    row, where thinking off spent 2,687 for the same quality."""
    api_key = llm_api_key()
    if not api_key:
        raise RuntimeError(
            "No LLM key — set TIERLIST_LLM_API_KEY (or DEEPSEEK_API_KEY) in .env")
    user = "\n".join(f"{i}\t{' '.join(t.split())}"
                     for i, t in enumerate(texts))
    endpoint = llm_endpoint()
    body = build_body(llm_model(), system, user, temperature=0.2,
                      max_tokens=translate_max_tokens(len(texts)))
    thinking_off(body, endpoint)
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        data = await _post_chat(client, endpoint, body, api_key)
    finally:
        if own:
            await client.aclose()
    content = data["choices"][0]["message"]["content"] or ""
    return parse_translations(content, len(texts)), data.get("usage") or {}
