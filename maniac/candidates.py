"""Derive whether a page is a MANIAC candidate, from ADR-0012's stored facts.

See docs/adr/0014-candidates-flag-not-threshold.md: the verdict is a pure
function of `ManpageFacts` and an internal threshold, computed at use time
and stored nowhere -- caching it would discard the whole fact cache on a
rename (ADR-0012).
"""

from enum import Enum

from .classification import ManpageFacts
from .sources.manpages import Dialect


class CandidateSelection(Enum):
    """Three-state verdict over one page's classification facts.

    `NO_EVIDENCE` is distinct from `NOT_SELECTED`: the words-per-flag
    measure requires a dialect it understands and a nonzero flag-entry
    count, and a page that fails either gives the measure nothing to
    divide by. Collapsing that into `NOT_SELECTED` would hide the
    under-claiming bias ADR-0012 depends on being visible to the caller.
    """

    SELECTED = "selected"
    NOT_SELECTED = "not_selected"
    NO_EVIDENCE = "no_evidence"


def select_candidate(
    facts: ManpageFacts, min_words_per_flag: int
) -> CandidateSelection:
    """Decide candidacy for one tool's facts against a words-per-flag threshold.

    Rules apply in order: a missing page has nothing to lose from
    generation; ownership decides a MANIAC-authored page, not score; an
    unrecognised dialect or zero countable flag entries leave the measure
    undefined, so both report no evidence rather than a false negative.
    Otherwise selected when word_count / tp_count falls under the threshold.
    """
    if not facts.exists:
        return CandidateSelection.SELECTED
    if facts.is_maniac_authored:
        return CandidateSelection.NOT_SELECTED
    if facts.dialect is Dialect.UNKNOWN:
        return CandidateSelection.NO_EVIDENCE
    if facts.tp_count == 0:
        return CandidateSelection.NO_EVIDENCE
    words_per_flag = facts.word_count / facts.tp_count
    return (
        CandidateSelection.SELECTED
        if words_per_flag < min_words_per_flag
        else CandidateSelection.NOT_SELECTED
    )
