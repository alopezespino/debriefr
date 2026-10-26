"""Per-project vocabulary: proper nouns and their known mishearings.

A project names its vocabulary file with the ``vocabulary:`` key (in
``project.yaml``, or per project in ``projects.yaml``), a path relative to the
project root. File format, one term per line::

    # comment
    CostBase <- Cost Base | cost bass
    Riverton

The text before `` <- `` is the correct spelling; the alternatives after it
are mishearings, separated by `` | ``. The vocabulary is used three ways:

1. :func:`whisper_prompt` builds the Whisper initial prompt (people and terms
   with recorded mishearings first, capped at about 224 tokens).
2. :func:`correct_segments` replaces listed mishearings in transcript text,
   whole-word: a mishearing written in lowercase matches any case, one with a
   capital letter matches only as written.
3. :func:`summary_block` lists every term for the summarizer.

People from the project registry (key name and ``full_name``) are merged into
the term list by :func:`build_vocabulary`; the file adds their mishearings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE = """\
# Proper nouns for meeting transcripts (read by debriefr; key `vocabulary` in project.yaml).
# One term per line. Optional mishearings after " <- ", separated by " | ":
#   Correct spelling <- misheard 1 | misheard 2
# Lines starting with # are comments. Add a term whenever a transcript misspells it.
# People in project.yaml are added automatically; list them here only to record mishearings.
"""

# Whisper keeps at most 224 prompt tokens (half its 448-token text context).
PROMPT_TOKEN_LIMIT = 224
PROMPT_PREFIX = "Glossary: "


@dataclass
class Term:
    spellings: list[str]                 # first one is the canonical spelling
    fixes: dict[str, str] = field(default_factory=dict)  # mishearing -> correct spelling
    person: bool = False
    rank: int = 2                        # 0 project people, 1 file terms, 2 guests

    @property
    def name(self) -> str:
        return self.spellings[0]


def parse_vocabulary(text: str) -> list[Term]:
    """Parse the file format above into ``Term`` objects (file order kept)."""
    terms: list[Term] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, rest = line.partition(" <- ")
        name = name.strip()
        if not name:
            continue
        fixes = {m.strip(): name for m in rest.split("|") if m.strip()} if rest else {}
        terms.append(Term([name], fixes, rank=1))
    return terms


def load_vocabulary_file(path: str | Path | None) -> list[Term]:
    """Read a vocabulary file; a missing file prints a notice and returns []."""
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        print(f"      [notice] vocabulary file not found: {p}; continuing without it")
        return []
    return parse_vocabulary(p.read_text())


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[^\w]+", s.lower()) if w}


def _same_person(term: Term, person: Term) -> bool:
    """A file term names a person when it equals one of the person's spellings,
    or it has two or more words and each occurs in the person's names
    ("Jordan Avery" for Jordan, full name Jordan Q. Avery; "Sam Okafor" for
    Sam, full name Samuel Okafor)."""
    t = term.name.lower()
    if any(t == s.lower() for s in person.spellings):
        return True
    words = _words(term.name)
    known = set().union(*(_words(s) for s in person.spellings))
    return len(words) > 1 and words <= known


def build_vocabulary(
    file_terms: list[Term],
    people: dict | None = None,
    guests: dict | None = None,
) -> list[Term]:
    """Merge registry people into the file's terms.

    ``people`` are the project's own people (and the run's participants),
    ``guests`` everyone else the registry knows. Each maps a name to its
    registry metadata (``full_name`` optional). A file term that names a
    person is folded into that person's entry: the file adds spellings and
    mishearings, never a second entry.
    """
    persons: list[Term] = []
    for rank, group in ((0, people or {}), (2, guests or {})):
        for key, meta in group.items():
            if any(key in p.spellings for p in persons):
                continue
            spellings = [key]
            full = meta.get("full_name") if isinstance(meta, dict) else None
            if full and full != key:
                spellings.append(full)
            persons.append(Term(spellings, person=True, rank=rank))

    others: list[Term] = []
    for t in file_terms:
        match = next((p for p in persons if _same_person(t, p)), None)
        if match is None:
            others.append(t)
            continue
        if t.name not in match.spellings:
            match.spellings.append(t.name)
        for m, target in t.fixes.items():
            match.fixes.setdefault(m, target)
        match.rank = min(match.rank, 1)
    return persons + others


def all_spellings(terms: list[Term]) -> list[str]:
    out: list[str] = []
    for t in terms:
        out += [s for s in t.spellings if s not in out]
    return out


def _count_tokens(text: str) -> int:
    """Whisper's tokenizer is GPT-2 byte-pair encoding; fall back to a
    conservative 2.5 characters per token without tiktoken."""
    try:
        import tiktoken
        return len(tiktoken.get_encoding("gpt2").encode(text))
    except Exception:
        return -(-len(text) * 2 // 5)


def whisper_prompt(terms: list[Term], limit: int = PROMPT_TOKEN_LIMIT) -> str | None:
    """Initial prompt for Whisper, within ``limit`` tokens.

    Order: entries with recorded mishearings, then project people, then other
    file terms, then registry guests. Spellings that do not fit are dropped
    with a notice (they still reach the corrections and the summary).
    """
    ordered = sorted(terms, key=lambda t: (not t.fixes, t.rank))
    kept: list[str] = []
    dropped: list[str] = []
    for s in all_spellings(ordered):
        trial = PROMPT_PREFIX + ", ".join(kept + [s]) + "."
        if _count_tokens(trial) <= limit:
            kept.append(s)
        else:
            dropped.append(s)
    if dropped:
        more = f" and {len(dropped) - 10} more" if len(dropped) > 10 else ""
        print(f"      [notice] Whisper prompt limit (~{limit} tokens): "
              f"{len(dropped)} term(s) left out of the prompt: "
              f"{', '.join(dropped[:10])}{more}")
    if not kept:
        return None
    return PROMPT_PREFIX + ", ".join(kept) + "."


def _pattern(text: str) -> str:
    body = r"\s+".join(re.escape(w) for w in text.split())
    if text == text.lower():
        body = f"(?i:{body})"
    return rf"(?<!\w){body}(?!\w)"


def corrector(terms: list[Term]):
    """Return ``fix(text) -> text`` that replaces mishearings, counting per term
    in ``fix.counts``. Correct spellings are matched too and left alone, so
    ``Northwind <- ...`` never turns "Northwind Fund" into "Northwind Fund Ventures"."""
    entries: list[tuple[str, str | None]] = [
        (m, target) for t in terms for m, target in t.fixes.items()
    ]
    counts: dict[str, int] = {}
    regex = None
    if entries:
        entries += [(s, None) for s in all_spellings(terms)]
        entries.sort(key=lambda e: -len(e[0]))
        regex = re.compile("|".join(f"(?P<g{i}>{_pattern(src)})"
                                    for i, (src, _) in enumerate(entries)))

    def sub(m: re.Match) -> str:
        target = entries[int(m.lastgroup[1:])][1]
        if target is None:
            return m.group(0)
        counts[target] = counts.get(target, 0) + 1
        return target

    def fix(text: str) -> str:
        return regex.sub(sub, text) if regex else text

    fix.counts = counts
    return fix


def correct_segments(segments: list[dict], terms: list[Term]) -> dict[str, int]:
    """Replace mishearings in each segment's ``text`` in place; print and
    return the count per corrected term."""
    fix = corrector(terms)
    for s in segments:
        s["text"] = fix(s["text"])
    if fix.counts:
        print("      vocabulary corrections: " + ", ".join(
            f"{name} x{n}" for name, n in fix.counts.items()))
    return fix.counts


def summary_block(terms: list[Term]) -> str | None:
    """Prompt block that lists the correct spelling of every term."""
    if not terms:
        return None
    lines = []
    for t in terms:
        line = f"- {t.name}"
        if len(t.spellings) > 1:
            line += f" (also: {', '.join(t.spellings[1:])})"
        if t.fixes:
            shown = [m if target == t.name else f"{m} (for {target})"
                     for m, target in t.fixes.items()]
            line += f"; transcripts may show it as {', '.join(shown)}"
        lines.append(line)
    return ("Vocabulary (spell these names and terms exactly as listed; correct any "
            "transcript variant to them):\n" + "\n".join(lines))


def init_vocabulary(path: str | Path) -> bool:
    """Create an empty vocabulary file with the format header. Returns False
    (and leaves the file alone) when it already exists."""
    p = Path(path)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(TEMPLATE)
    return True
