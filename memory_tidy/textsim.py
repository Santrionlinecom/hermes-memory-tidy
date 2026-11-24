"""Deterministic text similarity: normalised tokens, sentence containment, shingles and "specials".

No network, no model. Everything here is pure functions over strings plus a small inverted
index (``Corpus``) so thousands of skill sentences can be searched quickly.

Vocabulary used across the plugin:

* **tokens** - lower-cased word tokens without stopwords (light plural folding);
* **specials** - high-information literals: URLs, paths, version numbers, long numbers/IDs,
  hex hashes, ``code spans``, ENV_VARS, emails. A duplicate must contain all of them;
* **containment** - share of a sentence's tokens found in a source sentence (or that sentence
  plus its neighbour, because sentence boundaries rarely line up);
* **shingles** - consecutive 3-token runs, used for the near-exact score.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have he her his i if in
    into is it its just me my no not of on or our she so than that the their them then there these they
    this those to too us was we were what when where which who will with would you your yours also about
    over under via per any all each very more most such only own same both few other some""".split()
)

_URL = re.compile(r"\bhttps?://[^\s<>()\"'`]+", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_WINPATH = re.compile(r"\b[A-Za-z]:\\[^\s\"'`]+")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2}|\$\w+)?/(?:[\w.@+-]+/)*[\w.@+-]+/?")
_VERSION = re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+){0,2}(?:[-+][\w.]+)?(?![\w.])", re.I)
_LONGNUM = re.compile(r"(?<![\w.])\d{4,}(?![\w.])")
_HEX = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b", re.I)
_CODE = re.compile(r"`([^`\n]{2,120})`")
_ENVVAR = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b")
_MIXED_ID = re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+\b")

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\w\"'(`*\[])|;\s+|\s+[—–]\s+|\s+->\s+|\s+→\s+")
_TRAIL = ".,;:)]}>'\"!?*_"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _fold(token: str) -> str:
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_TOKEN_CACHE: dict[str, str] = {}


def word_list(text: str) -> list[str]:
    """Content tokens in order (stopwords and 1-char tokens dropped)."""
    out = []
    cache = _TOKEN_CACHE
    for tok in _WORD.findall(normalize(text)):
        folded = cache.get(tok)
        if folded is None:
            folded = "" if (len(tok) < 2 or tok in STOPWORDS) else _fold(tok)
            if len(cache) < 500_000:
                cache[tok] = folded
        if folded:
            out.append(folded)
    return out


def tokens(text: str) -> set[str]:
    return set(word_list(text))


def shingles(words: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def specials(text: str) -> set[str]:
    """High-information literals that must survive any deduplication."""
    found: set[str] = set()
    raw = text or ""
    for rx in (_URL, _EMAIL, _WINPATH):
        for m in rx.findall(raw):
            found.add(m.rstrip(_TRAIL).lower())
    scrubbed = _URL.sub(" ", raw)
    for m in _PATH.findall(scrubbed):
        m = m.rstrip(_TRAIL)
        # A path needs two segments or a leading ~ / . to avoid matching "and/or", "A/B".
        if m.count("/") >= 2 or m.startswith(("~", ".", "$")):
            if len(m) >= 4:
                found.add(m.lower())
    for rx in (_VERSION, _LONGNUM, _HEX, _ENVVAR, _MIXED_ID):
        for m in rx.findall(scrubbed):
            found.add(m.rstrip(_TRAIL).lower())
    for m in _CODE.findall(raw):
        m = m.strip().lower()
        if len(m) >= 3:
            found.add(m)
    return {s for s in found if s}


def split_sentences(text: str) -> list[tuple[int, str]]:
    """(line_number, sentence) pairs; lines are natural boundaries in markdown."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip().lstrip("#>*-+• \t").strip()
        if not line or set(line) <= set("|-:= `"):
            continue
        for part in _SENT_SPLIT.split(line):
            part = part.strip()
            if part:
                out.append((lineno, part))
    return out


def snippet(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


_PARSE_CACHE: dict[str, tuple[list, list]] = {}
_PARSE_CACHE_MAX = 20_000


def _parse(text: str) -> tuple[list[tuple[int, str, frozenset]], list[str]]:
    """Sentences with their token sets, plus the doc's word sequence. Memoised per text so
    repeated scans in a long-running process (gateway) only re-parse files that changed."""
    hit = _PARSE_CACHE.get(text)
    if hit is not None:
        return hit
    parsed = []
    words: list[str] = []
    for line, sent in split_sentences(text):
        wl = word_list(sent)
        if wl:
            parsed.append((line, sent, frozenset(wl)))
            words.extend(wl)
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[text] = (parsed, words)
    return parsed, words


@dataclass
class Sentence:
    doc: int
    line: int
    text: str
    toks: frozenset


@dataclass
class Doc:
    label: str  # e.g. "SOUL.md" or "skills/devops/foo/SKILL.md"
    kind: str  # "soul" | "skill" | "entry" | other
    text: str
    lower: str = ""
    toks: frozenset = frozenset()
    words: list = field(default_factory=list)
    sentence_ids: list[int] = field(default_factory=list)
    _shingles: set | None = None

    @property
    def shingles(self) -> set:
        if self._shingles is None:  # built lazily: only a few docs are ever compared shingle-wise
            self._shingles = shingles(self.words)
        return self._shingles


@dataclass
class Match:
    score: float
    sentence: Sentence | None


class Corpus:
    """Documents split into sentences with an inverted token index."""

    MAX_DF_SHARE = 0.01  # tokens in more than 1% of sentences are too common to seed candidates

    def __init__(self) -> None:
        self.docs: list[Doc] = []
        self.sentences: list[Sentence] = []
        self.index: dict[str, list[int]] = defaultdict(list)
        self.doc_freq: Counter = Counter()  # token -> number of docs containing it

    def add(self, label: str, kind: str, text: str) -> int:
        doc_id = len(self.docs)
        parsed, words = _parse(text)
        doc = Doc(label=label, kind=kind, text=text, lower=normalize(text), toks=frozenset(words), words=words)
        self.docs.append(doc)
        for tok in doc.toks:
            self.doc_freq[tok] += 1
        for line, sent, toks in parsed:
            sid = len(self.sentences)
            self.sentences.append(Sentence(doc_id, line, sent, toks))
            doc.sentence_ids.append(sid)
            for tok in toks:
                self.index[tok].append(sid)
        return doc_id

    def docs_of(self, kind: str) -> set[int]:
        return {i for i, d in enumerate(self.docs) if d.kind == kind}

    def idf(self, token: str) -> float:
        return math.log((1 + len(self.docs)) / (1 + self.doc_freq.get(token, 0))) + 1.0

    def _neighbour(self, sid: int) -> frozenset:
        s = self.sentences[sid]
        nxt = sid + 1
        if nxt < len(self.sentences) and self.sentences[nxt].doc == s.doc:
            return s.toks | self.sentences[nxt].toks
        return s.toks

    def best_sentence(self, toks: Iterable[str], docs: set[int] | None = None, top: int = 12) -> Match:
        """Best containment of ``toks`` inside one source sentence (optionally + its neighbour)."""
        toks = frozenset(toks)
        if not toks or not self.sentences:
            return Match(0.0, None)
        max_df = max(200, int(len(self.sentences) * self.MAX_DF_SHARE))
        seeds = [t for t in toks if 0 < len(self.index.get(t, ())) <= max_df]
        if not seeds:
            seeds = sorted((t for t in toks if t in self.index), key=lambda t: len(self.index[t]))[:2]
        counts: Counter = Counter()
        for tok in seeds:
            for sid in self.index[tok]:
                if docs is None or self.sentences[sid].doc in docs:
                    counts[sid] += 1
        best = Match(0.0, None)
        for sid, _ in counts.most_common(top):
            s = self.sentences[sid]
            score = max(len(toks & s.toks), len(toks & self._neighbour(sid))) / len(toks)
            if score > best.score:
                best = Match(score, s)
        return best

    def special_coverage(self, items: set[str], docs: set[int]) -> tuple[float, list[str]]:
        """Share of ``items`` that appear literally in any of ``docs``; returns (share, missing)."""
        if not items:
            return 1.0, []
        missing = [s for s in sorted(items) if not any(s in self.docs[d].lower for d in docs)]
        return 1 - len(missing) / len(items), missing


@dataclass
class Coverage:
    """How much of an entry is already said by a set of documents."""

    score: float  # token-weighted sentence containment (0..1)
    exact: float  # near-exact score: shingle containment, capped by special coverage
    specials: float
    missing_specials: list[str]
    evidence: list[dict]
    uncovered: list[str]  # entry sentences not found in the documents


SENTENCE_HIT = 0.6  # a sentence counts as "said there" from this containment upward


def coverage(entry: str, corpus: Corpus, docs: set[int], evidence_limit: int = 3) -> Coverage:
    if not docs:
        return Coverage(0.0, 0.0, 0.0, sorted(specials(entry)), [], [entry])
    total = 0.0
    covered = 0.0
    hits: list[tuple[float, str, Sentence]] = []
    uncovered: list[str] = []
    for _, sent in split_sentences(entry):
        toks = frozenset(word_list(sent))
        if not toks:
            continue
        weight = len(toks)
        total += weight
        m = corpus.best_sentence(toks, docs)
        if m.sentence is not None and m.score >= SENTENCE_HIT:
            covered += weight * m.score
            hits.append((m.score, sent, m.sentence))
        elif len(toks) <= 2 and all(any(t in corpus.docs[d].toks for d in docs) for t in toks):
            covered += weight  # tiny fragment whose words all appear there
        else:
            uncovered.append(sent)
    score = covered / total if total else 0.0
    sp = specials(entry)
    sp_share, missing = corpus.special_coverage(sp, docs)
    words = word_list(entry)
    sh = shingles(words)
    if sh:
        found = sum(1 for g in sh if any(g in corpus.docs[d].shingles for d in docs))
        exact = min(found / len(sh), sp_share)
    else:
        exact = 0.0
    hits.sort(key=lambda h: -h[0])
    evidence = [
        {
            "entry_text": snippet(sent, 120),
            "matched": snippet(s.text),
            "where": f"{corpus.docs[s.doc].label}:{s.line}",
            "containment": round(sc, 3),
        }
        for sc, sent, s in hits[:evidence_limit]
    ]
    return Coverage(round(score, 3), round(exact, 3), round(sp_share, 3), missing, evidence, uncovered)


def pair_overlap(a: str, b: str) -> dict:
    """Symmetric overlap measures between two entries."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return {"jaccard": 0.0, "containment": 0.0, "shared": 0, "shingle": 0.0}
    inter = ta & tb
    sa, sb = shingles(word_list(a)), shingles(word_list(b))
    sh = len(sa & sb) / min(len(sa), len(sb)) if sa and sb else 0.0
    return {
        "jaccard": round(len(inter) / len(ta | tb), 3),
        "containment": round(len(inter) / min(len(ta), len(tb)), 3),
        "shared": len(inter),
        "shingle": round(sh, 3),
    }
