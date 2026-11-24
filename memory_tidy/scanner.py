"""Read-only scan: classify every MEMORY.md / USER.md entry against SOUL.md, skills and each other."""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import store
from .textsim import (
    Corpus,
    coverage,
    pair_overlap,
    snippet,
    specials,
    split_sentences,
    tokens,
    word_list,
)

VERDICTS = ("duplicate_of_soul", "duplicate_of_skill", "overlaps_entry", "procedure_like", "ok")

DEFAULT_EXACT_THRESHOLD = 0.92
DUPLICATE_COVERAGE = 0.8  # token-weighted containment that makes an entry a duplicate
PARTIAL_COVERAGE = 0.4  # below a duplicate but worth reporting
PROCEDURE_SCORE = 4.0
OVERLAP_CONTAINMENT = 0.6
OVERLAP_MIN_SHARED = 5
OVERLAP_SHINGLE = 0.5
MIN_SKILL_MATCH = 0.12
STALE_DAYS = 120


def entry_id(text: str) -> str:
    """Stable short id of an entry's text; plans may use it instead of an index."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


# ── procedure heuristics ──────────────────────────────────────────────────────

_STEP_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]|step\s*\d+[:.)]?)\s+\S", re.I | re.M)
_INLINE_STEP = re.compile(r"(?:^|\s)\(?\d{1,2}\)\s+\S")
_ARROW = re.compile(r"\s(?:->|→|=>)\s")
_COMMAND = re.compile(
    r"(?:^|[\s`$(])(?:sudo|git|npm|npx|pnpm|yarn|pip3?|uv|uvx|python3?|node|deno|bun|curl|wget|docker|"
    r"kubectl|systemctl|journalctl|hermes|wrangler|ffmpeg|make|bash|sh|ssh|scp|rsync|chmod|chown|apt|"
    r"apt-get|brew|cargo|go|gh|tar|unzip|sed|awk|grep|rg|cd|ls|cp|mv|rm|mkdir|cat|echo|export)\s+[-\w./~$\"']",
    re.I,
)
_FLAG = re.compile(r"(?<![\w-])--?[a-z][\w-]*", re.I)
_IMPERATIVE = re.compile(
    r"^(?:run|install|open|click|set|use|add|copy|paste|deploy|restart|edit|create|check|call|then|first|"
    r"next|finally|start|stop|build|push|pull|download|upload|configure|enable|disable|type|press|select|"
    r"go to|navigate|login|log in|execute|launch|update|replace|delete|remove|save|export|import)\b",
    re.I,
)
_ENV_ASSIGN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}=\S+|\$\{?[A-Z][A-Z0-9_]{2,}\}?")

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_TEXT_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(20\d{2})\b", re.I)
_TEMPORAL = re.compile(r"\b(?:currently|for now|temporar(?:y|ily)|until|this week|today|tomorrow|right now)\b", re.I)


def procedure_score(text: str) -> tuple[float, list[str]]:
    """Heuristic score for "this reads like a how-to that belongs in a skill"."""
    feats: list[str] = []
    score = 0.0

    def add(points: float, cap: float, count: int, label: str) -> None:
        nonlocal score
        if count:
            gained = min(cap, points * count)
            score += gained
            feats.append(f"{label} x{count}")

    steps = len(_STEP_LINE.findall(text))
    add(1.0, 3.0, steps if steps >= 2 else 0, "numbered step lines")
    inline = len(_INLINE_STEP.findall(text))
    add(0.75, 2.0, inline if inline >= 2 else 0, "inline numbered steps")
    arrows = len(_ARROW.findall(text))
    add(0.5, 1.5, arrows if arrows >= 2 else 0, "arrow chains")
    add(1.5, 4.5, len(_COMMAND.findall(text)), "command lines")
    add(0.5, 2.0, len(set(_FLAG.findall(text)) - {"-", "--"}), "cli flags")
    sp = specials(text)
    paths = [s for s in sp if "/" in s or "\\" in s]
    paths = [p for p in paths if not p.startswith("http")]
    urls = [s for s in sp if s.startswith("http")]
    versions = [s for s in sp if re.fullmatch(r"v?\d+\.\d+(?:\.\d+){0,2}(?:[-+][\w.]+)?", s)]
    ids = [s for s in sp if re.fullmatch(r"\d{4,}|[0-9a-f]{7,64}", s)]
    add(1.0, 3.0, len(paths), "file paths")
    add(0.5, 1.5, len(urls), "urls")
    add(0.5, 1.5, len(versions), "version numbers")
    add(0.5, 1.5, len(ids), "ids/long numbers")
    imperative = sum(1 for _, s in split_sentences(text) if _IMPERATIVE.match(s.lstrip("`*_ ")))
    add(0.75, 3.0, imperative, "imperative sentences")
    add(0.5, 1.5, len(_ENV_ASSIGN.findall(text)), "env variables")
    return round(score, 2), feats


def stale_hints(text: str, today: _dt.date | None = None) -> list[str]:
    today = today or _dt.date.today()
    hints = []
    dates: list[_dt.date] = []
    for y, m, d in _ISO_DATE.findall(text):
        try:
            dates.append(_dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, mon, y in _TEXT_DATE.findall(text):
        try:
            dates.append(_dt.date(int(y), _MONTHS.index(mon[:3].lower()) + 1, int(d)))
        except ValueError:
            pass
    old = [x for x in dates if (today - x).days > STALE_DAYS]
    if old:
        oldest = min(old)
        hints.append(f"mentions a date {(today - oldest).days} days old ({oldest.isoformat()}); check it is still true")
    if _TEMPORAL.search(text):
        hints.append("uses temporary wording (currently/for now/until...); check it is still true")
    return hints


# ── corpus building ───────────────────────────────────────────────────────────

@dataclass
class Sources:
    corpus: Corpus
    soul_docs: set
    skill_docs: set
    skill_main_docs: set  # SKILL.md files only (move_to_skill candidates)
    skills_indexed: int


_SOURCES_CACHE: dict[tuple, Sources] = {}


def _fingerprint(layout: store.Layout, include_skills: bool) -> tuple:
    parts = [str(layout.home.resolve()), include_skills]
    for p in [layout.soul] + (list(store.iter_skill_files(layout.skills)) if include_skills else []):
        try:
            st = p.stat()
            parts.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((str(p), None))
    return tuple(parts)


def build_sources(layout: store.Layout, include_skills: bool = True,
                  skill_overrides: dict[str, str] | None = None) -> Sources:
    """Index SOUL.md and (optionally) skills. ``skill_overrides`` maps label -> text (used by apply).

    Without overrides the result is cached per file fingerprint (paths, mtimes, sizes), so repeated
    scans in a long-running process are cheap. Callers must not add documents to a cached corpus.
    """
    if skill_overrides is None:
        key = _fingerprint(layout, include_skills)
        hit = _SOURCES_CACHE.get(key)
        if hit is not None:
            return hit
        built = _build_sources(layout, include_skills, None)
        _SOURCES_CACHE.clear()
        _SOURCES_CACHE[key] = built
        return built
    return _build_sources(layout, include_skills, skill_overrides)


def _build_sources(layout: store.Layout, include_skills: bool,
                   skill_overrides: dict[str, str] | None) -> Sources:
    corpus = Corpus()
    soul_text = store.read_text(layout.soul)
    soul_docs = {corpus.add(store.SOUL_NAME, "soul", soul_text)} if soul_text.strip() else set()
    skill_docs: set = set()
    main_docs: set = set()
    seen: set = set()
    if include_skills:
        overrides = dict(skill_overrides or {})
        for path in store.iter_skill_files(layout.skills):
            label = skill_label(layout, path)
            seen.add(label)
            text = overrides.pop(label, None)
            if text is None:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
            doc = corpus.add(label, "skill", text)
            skill_docs.add(doc)
            if path.name == "SKILL.md":
                main_docs.add(doc)
        for label, text in overrides.items():  # new files a plan would create
            doc = corpus.add(label, "skill", text)
            skill_docs.add(doc)
            if label.endswith("/SKILL.md"):
                main_docs.add(doc)
    return Sources(corpus, soul_docs, skill_docs, main_docs, len(skill_docs))


def skill_label(layout: store.Layout, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(layout.skills.resolve())
    except ValueError:
        rel = Path(path.name)
    return f"{store.SKILLS_DIR}/{rel.as_posix()}"


def suggest_skill(text: str, src: Sources) -> tuple[str | None, float]:
    """Best SKILL.md for a procedure-like entry: idf-weighted token overlap."""
    toks = tokens(text)
    if not toks or not src.skill_main_docs:
        return None, 0.0
    weights = {t: src.corpus.idf(t) for t in toks}
    total = sum(weights.values())
    best, best_score = None, 0.0
    for d in src.skill_main_docs:
        doc = src.corpus.docs[d]
        score = sum(w for t, w in weights.items() if t in doc.toks) / total
        if score > best_score:
            best, best_score = doc.label, score
    if best_score < MIN_SKILL_MATCH:
        return None, round(best_score, 3)
    return best, round(best_score, 3)


# ── scan ──────────────────────────────────────────────────────────────────────

def _usage(entries: list[str], limit: int) -> dict:
    chars = store.char_count(entries)
    return {"chars": chars, "limit": limit, "usage_pct": round(100.0 * chars / limit, 1) if limit else 0.0,
            "entries": len(entries)}


def scan(home: Path | None = None, *, exact_threshold: float = DEFAULT_EXACT_THRESHOLD,
         include_skills: bool = True, limits: dict[str, int] | None = None,
         today: _dt.date | None = None) -> dict:
    """Return the structured report. Never writes anything."""
    layout = store.Layout(Path(home) if home else store.hermes_home())
    limits = limits or store.read_limits(layout.home)
    src = build_sources(layout, include_skills=include_skills)
    corpus = src.corpus
    files = {t: store.read_entries(layout.memory_file(t)) for t in store.TARGETS}

    results: list[dict] = []
    for target in store.TARGETS:
        for idx, text in enumerate(files[target]):
            results.append(_classify(target, idx, text, src, exact_threshold, today))

    _mark_overlaps(results, files)
    for r in results:
        _finalize(r)

    summary = {v: 0 for v in VERDICTS}
    for r in results:
        summary[r["verdict"]] += 1
    report = {
        "tool": "memory-tidy",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "exact_threshold": exact_threshold,
        "files": {t: _usage(files[t], limits[t]) for t in store.TARGETS},
        "soul_chars": len(store.read_text(layout.soul)),
        "skills_indexed": src.skills_indexed,
        "summary": summary,
        "exact_soul_duplicates": [
            {"target": r["target"], "index": r["index"], "id": r["id"]}
            for r in results if r["verdict"] == "duplicate_of_soul" and r["scores"]["soul_exact"] >= exact_threshold
        ],
        "entries": results,
    }
    for t in store.TARGETS:
        report["files"][t]["file"] = f"memories/{store.FILE_NAMES[t]}"
    return report


def _classify(target: str, idx: int, text: str, src: Sources, exact_threshold: float,
              today: _dt.date | None) -> dict:
    corpus = src.corpus
    soul = coverage(text, corpus, src.soul_docs)
    skill_best = None
    skill_cov = None
    if src.skill_docs:
        # Candidate skill files: those holding the best sentence matches for this entry.
        cands: dict[int, float] = {}
        for _, sent in split_sentences(text):
            toks = frozenset(word_list(sent))
            if not toks:
                continue
            m = corpus.best_sentence(toks, src.skill_docs)
            if m.sentence is not None:
                cands[m.sentence.doc] = cands.get(m.sentence.doc, 0.0) + m.score * len(toks)
        for d, _ in sorted(cands.items(), key=lambda kv: -kv[1])[:3]:
            cov = coverage(text, corpus, {d})
            if skill_cov is None or (cov.score, cov.specials) > (skill_cov.score, skill_cov.specials):
                skill_cov, skill_best = cov, corpus.docs[d].label
    proc, feats = procedure_score(text)
    r = {
        "target": target,
        "index": idx,
        "id": entry_id(text),
        "chars": len(text),
        "preview": snippet(text, 90),
        "scores": {
            "soul_coverage": soul.score,
            "soul_exact": soul.exact,
            "soul_specials": soul.specials,
            "skill_coverage": skill_cov.score if skill_cov else 0.0,
            "skill_specials": skill_cov.specials if skill_cov else 0.0,
            "procedure": proc,
        },
        "evidence": [],
        "hints": stale_hints(text, today),
        "_soul": soul,
        "_skill": skill_cov,
        "_skill_label": skill_best,
        "_procedure_feats": feats,
        "_overlaps": [],
        "_text": text,
        "_src": src,
        "_exact_threshold": exact_threshold,
    }
    return r


def _mark_overlaps(results: list[dict], files: dict[str, list[str]]) -> None:
    for i, a in enumerate(results):
        for b in results[i + 1:]:
            ov = pair_overlap(a["_text"], b["_text"])
            strong = (ov["containment"] >= OVERLAP_CONTAINMENT and ov["shared"] >= OVERLAP_MIN_SHARED) \
                or ov["shingle"] >= OVERLAP_SHINGLE
            if not strong:
                continue
            a["_overlaps"].append((b, ov))
            b["_overlaps"].append((a, ov))


def _ref(me: dict, other: dict) -> str:
    return str(other["index"]) if other["target"] == me["target"] else f"{other['target']}:{other['index']}"


def _finalize(r: dict) -> None:
    soul = r.pop("_soul")
    skill = r.pop("_skill")
    skill_label = r.pop("_skill_label")
    feats = r.pop("_procedure_feats")
    overlaps = r.pop("_overlaps")
    text = r.pop("_text")
    src: Sources = r.pop("_src")
    exact_threshold = r.pop("_exact_threshold")
    sc = r["scores"]

    soul_dup = (soul.exact >= exact_threshold) or (soul.score >= DUPLICATE_COVERAGE and soul.specials >= 1.0)
    skill_dup = skill is not None and skill.score >= DUPLICATE_COVERAGE and skill.specials >= 1.0
    if soul_dup:
        r["verdict"], r["suggested_action"] = "duplicate_of_soul", "remove"
        r["evidence"] = [dict(e, source="soul") for e in soul.evidence]
    elif skill_dup:
        r["verdict"], r["suggested_action"] = "duplicate_of_skill", "remove"
        r["evidence"] = [dict(e, source="skill") for e in skill.evidence]
    elif overlaps:
        # The shorter entry merges into the longer one; the longer keeps the merge pointer too.
        other, ov = max(overlaps, key=lambda p: (p[1]["containment"], p[1]["shingle"]))
        r["verdict"] = "overlaps_entry"
        r["suggested_action"] = f"merge_with:{_ref(r, other)}"
        r["evidence"] = [{"source": "entry", "where": f"{other['target']}#{other['index']}",
                          "matched": other["preview"], **ov}]
    elif sc["procedure"] >= PROCEDURE_SCORE:
        target_skill, conf = (skill_label, sc["skill_coverage"]) if (
            skill_label and skill and skill.score >= PARTIAL_COVERAGE) else suggest_skill(text, src)
        r["verdict"] = "procedure_like"
        r["suggested_action"] = f"move_to_skill:{target_skill}" if target_skill else "move_to_skill:<new skill>"
        r["evidence"] = [{"source": "heuristics", "features": feats, "skill_match": conf}]
    else:
        r["verdict"], r["suggested_action"] = "ok", "keep"

    # Partial coverage is worth showing even when the entry stays.
    if r["verdict"] not in ("duplicate_of_soul", "duplicate_of_skill"):
        if soul.score >= PARTIAL_COVERAGE:
            r["hints"].append(f"{int(soul.score * 100)}% already said in SOUL.md; trim to the unique part")
            r["evidence"] += [dict(e, source="soul") for e in soul.evidence[:2]]
        elif skill is not None and skill.score >= PARTIAL_COVERAGE:
            r["hints"].append(f"{int(skill.score * 100)}% already said in {skill_label}; trim to the unique part")
            r["evidence"] += [dict(e, source="skill") for e in skill.evidence[:2]]
    if r["verdict"] in ("duplicate_of_soul", "duplicate_of_skill"):
        missing = soul.missing_specials if r["verdict"] == "duplicate_of_soul" else skill.missing_specials
        if missing:
            r["hints"].append("literals not found in the source: " + ", ".join(missing[:5]))
    if r["verdict"] != "procedure_like" and feats and sc["procedure"] >= PROCEDURE_SCORE / 2:
        r["hints"].append("partly procedural: " + ", ".join(feats[:4]))
