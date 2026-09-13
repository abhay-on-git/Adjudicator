"""Deterministic citation-content verification for explanation sentences.

Citation presence alone only proves that a clause was retrieved. This module
also asks whether the sentence containing the citation shares meaningful
entities/keywords with the clause title/text. It is intentionally lightweight:
no extra LLM call, no new latency, and identical behavior across providers.

Violation strings are typed prefixes for eval reporting:
  - ``citation_not_retrieved:<id>``
  - ``citation_not_in_narrative:<id>``
  - ``content_mismatch:<id>:overlap=<n>:assertion_terms=<n>``
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from graph.schemas import ClauseRef

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z₹])")
_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_STOP_WORDS = {
    "about", "after", "again", "against", "also", "amount", "and", "are",
    "because", "been", "before", "being", "but", "claim", "claimed", "decision",
    "does", "for", "from", "has", "have", "here", "into", "its", "may", "not",
    "include", "included", "includes", "including", "only", "payable", "payment",
    "policy", "rule", "should", "such", "that", "the",
    "their", "then", "there", "therefore", "this", "those", "under", "was", "were",
    "which", "with", "would", "your",
}


@dataclass(frozen=True)
class CitationSupport:
    supported: bool
    overlap: tuple[str, ...]
    assertion_terms: tuple[str, ...]
    sentence: str | None
    violation: str | None = None


def _stem(word: str) -> str:
    word = word.lower().strip("-")
    if word.startswith("exclu"):
        return "exclud"
    for suffix in ("ingly", "edly", "ation", "ments", "ment", "ing", "ied", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def meaningful_terms(text: str) -> set[str]:
    return {
        stemmed
        for raw in _WORD_RE.findall(text)
        if raw.lower() not in _STOP_WORDS
        if (stemmed := _stem(raw)) and stemmed not in _STOP_WORDS
    }


def citation_sentence(narrative: str, clause_id: str) -> str | None:
    sentences = _SENTENCE_SPLIT_RE.split(narrative.replace("\n", " "))
    matches = [sentence.strip() for sentence in sentences if clause_id in sentence]
    return " ".join(matches) if matches else None


def _local_citation_span(sentence: str, clause_id: str) -> str:
    # A sentence may list several clauses ("hospitalization at §4.1, the
    # deductible at §2.1, ..."). This narrower span is a fallback when the
    # full sentence fails because unrelated neighboring assertions dilute it.
    local_matches = []
    for match in _SENTENCE_SPLIT_RE.split(sentence):
        # Do not split thousands separators (₹20,000). If the ID leads the
        # clause ("Under §4.4, diagnostics..."), include the next segment so
        # the actual assertion—not the bare ID—is scored.
        segments = re.split(r"(?<!\d),|,(?!\d)|[;—]", match)
        index = next((i for i, segment in enumerate(segments) if clause_id in segment), None)
        if index is None:
            continue
        else:
            local = segments[index].strip()
            terms_without_id = meaningful_terms(local.replace(clause_id, ""))
            if not terms_without_id and index + 1 < len(segments):
                local = f"{local}, {segments[index + 1].strip()}"
        local_matches.append(local)
    return " ".join(local_matches) or sentence


def _support_terms(text: str, clause: ClauseRef) -> tuple[set[str], set[str], bool]:
    assertion_terms = meaningful_terms(text.replace(clause.clause_id, ""))
    clause_terms = meaningful_terms(f"{clause.title} {clause.text}")
    overlap = assertion_terms & clause_terms
    required = 1 if len(assertion_terms) <= 2 else 2
    ratio = len(overlap) / len(assertion_terms) if assertion_terms else 0.0
    supported = bool(assertion_terms) and (
        len(overlap) >= required
        or (len(overlap) >= 1 and ratio >= 0.4)
        # "No exclusions under §X apply" is a complete, meaningful assertion
        # even when several exclusion IDs are grouped in one sentence.
        or "exclud" in overlap
    )
    return assertion_terms, overlap, supported


def verify_citation_content(
    narrative: str,
    clause: ClauseRef,
) -> CitationSupport:
    sentence = citation_sentence(narrative, clause.clause_id)
    if sentence is None:
        return CitationSupport(
            supported=False,
            overlap=(),
            assertion_terms=(),
            sentence=None,
            violation=f"citation_not_in_narrative:{clause.clause_id}",
        )

    # A terse sentence ("Capped under §4.2.9.") can legitimately carry one
    # distinctive term. Longer assertions need two shared terms, or at least
    # 40% of their meaningful terms supported by the clause. Score the full
    # sentence first to preserve cross-comma support; only if that fails,
    # retry the clause-local span to avoid dilution by neighboring citations.
    assertion_terms, overlap, supported = _support_terms(sentence, clause)
    scored_sentence = sentence
    if not supported:
        local = _local_citation_span(sentence, clause.clause_id)
        local_terms, local_overlap, local_supported = _support_terms(local, clause)
        if local_supported:
            assertion_terms, overlap, supported = local_terms, local_overlap, True
            scored_sentence = local
    violation = None
    if not supported:
        violation = (
            f"content_mismatch:{clause.clause_id}:overlap={len(overlap)}:"
            f"assertion_terms={len(assertion_terms)}"
        )
    return CitationSupport(
        supported=supported,
        overlap=tuple(sorted(overlap)),
        assertion_terms=tuple(sorted(assertion_terms)),
        sentence=scored_sentence,
        violation=violation,
    )
