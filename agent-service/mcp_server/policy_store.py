"""Loads the markdown policy fixtures into an in-memory clause index.

This is the shared data layer behind two MCP tools: `search_policy` (keyword
retrieval over this index) and `get_clause` (exact lookup by ID). Both tools
are read-only wrappers around this module — no LLM involvement in loading,
indexing, or scoring.

Retrieval is deliberately plain keyword/term-overlap scoring, not embeddings.
This is a design choice (see DESIGN.md fork list): it keeps retrieval fully
deterministic and reproducible run-to-run, which matters for the consistency-
variance and stability evaluations, and it avoids spending LLM-adjacent API
budget on indexing 4 small fixture policies where an embedding model would be
overkill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "policies"

_CLAUSE_HEADER_RE = re.compile(
    r"^### (§[\d.]+) (.+?)\s*$",
    re.MULTILINE,
)
# Any markdown heading (H1/H2/H3) terminates a clause body. This matters at
# the end of an H2 section, where the next H3 clause header may be several
# lines away (in the *next* section) — without this, the last clause in a
# section would swallow the following "## N. Section Title" line as if it
# were part of its own text.
_ANY_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)


@dataclass(frozen=True)
class Clause:
    policy_id: str
    clause_id: str
    title: str
    text: str

    def searchable_text(self) -> str:
        return f"{self.title}\n{self.text}"


@dataclass
class PolicyIndex:
    clauses_by_policy: dict[str, list[Clause]] = field(default_factory=dict)
    clauses_by_id: dict[tuple[str, str], Clause] = field(default_factory=dict)

    def all_for_policy(self, policy_id: str) -> list[Clause]:
        return self.clauses_by_policy.get(policy_id, [])

    def get(self, policy_id: str, clause_id: str) -> Clause | None:
        return self.clauses_by_id.get((policy_id, clause_id))


_TOKEN_RE = re.compile(r"[a-zA-Z0-9₹]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _parse_policy_file(path: Path) -> list[Clause]:
    policy_id = path.stem
    raw = path.read_text(encoding="utf-8")
    matches = list(_CLAUSE_HEADER_RE.finditer(raw))
    clauses: list[Clause] = []
    for i, m in enumerate(matches):
        clause_id, title = m.group(1), m.group(2)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body_raw = raw[body_start:body_end]
        next_heading = _ANY_HEADING_RE.search(body_raw)
        if next_heading:
            body_raw = body_raw[: next_heading.start()]
        body = body_raw.strip()
        clauses.append(Clause(policy_id=policy_id, clause_id=clause_id, title=title, text=body))
    return clauses


def load_policy_index(policies_dir: Path = POLICIES_DIR) -> PolicyIndex:
    index = PolicyIndex()
    for path in sorted(policies_dir.glob("*.md")):
        clauses = _parse_policy_file(path)
        index.clauses_by_policy[path.stem] = clauses
        for c in clauses:
            index.clauses_by_id[(c.policy_id, c.clause_id)] = c
    return index


def score_clause(query_tokens: list[str], clause: Clause) -> float:
    """Plain term-overlap score: fraction of query tokens present in the
    clause's title+text, with a title-match bonus. Deterministic, no ML."""
    clause_tokens = set(_tokenize(clause.searchable_text()))
    title_tokens = set(_tokenize(clause.title))
    if not query_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in clause_tokens)
    title_hits = sum(1 for t in query_tokens if t in title_tokens)
    return (hits / len(query_tokens)) + 0.5 * (title_hits / len(query_tokens))


def search(index: PolicyIndex, query: str, policy_id: str, top_k: int = 5) -> list[tuple[Clause, float]]:
    query_tokens = _tokenize(query)
    candidates = index.all_for_policy(policy_id)
    scored = [(c, score_clause(query_tokens, c)) for c in candidates]
    scored = [(c, s) for c, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
