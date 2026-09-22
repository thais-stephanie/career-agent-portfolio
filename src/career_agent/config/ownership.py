"""Who owns which fact, stated once, in a table a test can read.

`consistency.py` answers "do these two files disagree?". This answers the
question underneath it: **which of them should have been asked in the first
place**, and it does so as data rather than as a sentence in a docstring.

    The Candidate Profile owns facts and preferences about the PERSON.
    The search and match configuration owns product-level MACHINERY.

That rule has been written down since M3 and it has never been checkable. The
table below makes it checkable in three ways, and each has caught something:

**Every candidate-owned fact says whether a person can edit it without opening
a file.** `editable` is not a wish; `tests/unit/test_ownership.py` asserts that
every row marked editable is reachable through `candidate_writer.FIELDS` and
that every field the writer offers appears here. A field could otherwise be
added to the writer and never appear on any screen, or be described here as
editable while no control existed -- both happened during V1.3.

**Nothing in the MACHINERY half may become editable from a settings panel.**
`scoring.components`, `thresholds` and the lexicon patterns decide how a
posting is read; a browser being able to rewrite them is the hazard the
writer's whitelist exists for, and this table is where that intent is
recorded next to the fields it excludes.

**Where a fact lives today is separate from who owns it.** `home` is a fact
about this build; `owner` is a fact about the design. The two disagree for
every row where `home` is the search configuration and `owner` is CANDIDATE,
and that disagreement is the migration -- visible, counted, and not pretended
away.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
------------------------------------------
It does not move anything. The matcher reads `search.*.yaml` and it keeps
reading it. Repointing the gates at `profile.local.yaml` would invalidate every
score in the corpus, would require a file that is deliberately absent
(`career-agent doctor` reports `profile: INVALID or MISSING` and that is
expected), and would be an architecture change rather than a hardening. It is
an owner decision and `docs/architecture/candidate-ownership.md` states what it
would cost.

What V1.3 did instead was make every CANDIDATE-owned fact that lives in the
search configuration EDITABLE without opening the file. That is the half of the
migration a person actually feels, and it is safe: the same loader validates
it, the same version bump invalidates the same scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: The rule, in one line, quoted by `doctor` so it is impossible to read the
#: report without reading the direction it points in.
OWNERSHIP_RULE = (
    "Candidate Profile owns facts about the person; "
    "search configuration owns the matching machinery."
)


class Owner(StrEnum):
    """Who a fact belongs to, in the design rather than in this build."""

    #: A fact about the PERSON. Changing it says something new about her.
    CANDIDATE = "CANDIDATE"
    #: Product MACHINERY. Changing it says something new about how a posting is
    #: read, which is not a fact about anybody.
    MACHINERY = "MACHINERY"


class Home(StrEnum):
    """Which file holds it today."""

    SEARCH_CONFIG = "search.yaml"
    PROFILE = "profile.local.yaml"
    DATABASE = "the database"
    BROWSER = "the browser"


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing this product knows, and who it belongs to."""

    #: What a person would call it.
    subject: str
    owner: Owner
    home: Home
    #: The dotted path inside its home, so somebody can go and look.
    path: str
    #: The `candidate_writer` field id, when a screen can change it. `None`
    #: means read-only, and `reason` says why.
    field: str | None = None
    #: Why it is not editable, for the rows that are not. Shown on screen.
    reason: str | None = None

    @property
    def editable(self) -> bool:
        return self.field is not None

    @property
    def displaced(self) -> bool:
        """A candidate fact living in the machinery's file. The migration."""
        return self.owner is Owner.CANDIDATE and self.home is Home.SEARCH_CONFIG


#: Every fact this product holds about the person or about how it reads a
#: posting. Ordered as somebody would meet them, not as the files store them.
FACTS: tuple[Fact, ...] = (
    # -- who and where she is ------------------------------------------
    Fact(
        subject="Where you live",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="eligibility.candidate_country",
        field="candidate_country",
    ),
    Fact(
        subject="Where you can be hired from",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="eligibility.eligible_scopes",
        field="eligible_scopes",
    ),
    Fact(
        subject="Countries that may hire you directly",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="eligibility.eligible_countries",
        field="eligible_countries",
    ),
    # -- how she will work ---------------------------------------------
    Fact(
        subject="Ways of working you will accept",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.remote.accepted_work_models",
        field="work_models",
    ),
    Fact(
        subject="Only consider remote roles",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.remote.require_remote",
        field="require_remote",
    ),
    Fact(
        subject="Engagements you would accept",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.contract.preferred",
        field="contract_preferred",
    ),
    Fact(
        subject="Engagements you would not",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.contract.unwanted",
        field="contract_unwanted",
    ),
    Fact(
        subject="Levels you are looking for",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.seniority.preferred",
        field="seniority_preferred",
    ),
    # A SECOND seniority fact, and hers for the same reason the first one is.
    # `preferred` prices a level; this one hides it. The distinction is the
    # same one `include_user_hidden` draws against the automatic narrowings:
    # what the MACHINE sets aside and what SHE does are different facts, and
    # they do not share a control.
    Fact(
        subject="Levels to keep off the list",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.seniority.excluded",
        field="seniority_excluded",
    ),
    Fact(
        subject="Most travel you would accept",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.travel.max_tolerated_pct",
        field="travel_max_pct",
    ),
    # -- what it has to pay ---------------------------------------------
    Fact(
        subject="What you are aiming for",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.compensation.target_monthly_amount",
        field="compensation_target",
    ),
    Fact(
        subject="In which currency",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="preferences.compensation.currency",
        field="compensation_currency",
    ),
    # -- the work itself, as phrase groups -------------------------------
    #
    # These ARE candidate-owned and they ARE editable, through a different
    # writer: `config/preferences.py` edits one signal's phrases at a time,
    # which is the right shape for a list of forty. They are named here so the
    # matrix is complete, with `field` left None because the writer that
    # changes them is not `candidate_writer`.
    Fact(
        subject="Work you want (adds to the match)",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="lexicon",
        reason="Edited one phrase group at a time, in Search preferences.",
    ),
    Fact(
        subject="Work you would rather avoid (subtracts, never excludes)",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="lexicon + scoring.soft_penalties.weights",
        reason="Edited one phrase group at a time, in Search preferences.",
    ),
    Fact(
        subject="What rules a job out entirely",
        owner=Owner.CANDIDATE,
        home=Home.SEARCH_CONFIG,
        path="eligibility.blockers",
        reason="Edited one phrase group at a time, in Search preferences.",
    ),
    # -- what she has actually done --------------------------------------
    Fact(
        subject="Confirmed facts about your career",
        owner=Owner.CANDIDATE,
        home=Home.DATABASE,
        path="verified_claim",
        reason="Edited in Your career evidence, with a revision chain.",
    ),
    Fact(
        subject="Where you are with each application",
        owner=Owner.CANDIDATE,
        home=Home.DATABASE,
        path="job_application",
        reason="Edited on the job itself.",
    ),
    Fact(
        subject="Which language the interface is in",
        owner=Owner.CANDIDATE,
        home=Home.BROWSER,
        path="localStorage careerAgent.locale.v1",
        reason=(
            "A display choice, not a fact about you. Storing it in the search "
            "configuration would bump config_version and invalidate every score."
        ),
    ),
    # -- machinery, and none of it is editable from a screen ---------------
    Fact(
        subject="How much each signal is worth",
        owner=Owner.MACHINERY,
        home=Home.SEARCH_CONFIG,
        path="scoring.components",
        reason="Changing this changes how every posting is read. Not a fact about you.",
    ),
    Fact(
        subject="Where the score bands begin",
        owner=Owner.MACHINERY,
        home=Home.SEARCH_CONFIG,
        path="thresholds",
        reason="Changing this changes how every posting is read. Not a fact about you.",
    ),
    Fact(
        subject="How a title is classified",
        owner=Owner.MACHINERY,
        home=Home.SEARCH_CONFIG,
        path="taxonomy",
        reason="Names and admits a role. Buys no compatibility points -- ADR-0014.",
    ),
    Fact(
        subject="Which sources are collected",
        owner=Owner.MACHINERY,
        home=Home.SEARCH_CONFIG,
        path="sources",
        reason="Retrieval configuration. Some sources also need a credential.",
    ),
    Fact(
        subject="How text is normalised and negation is read",
        owner=Owner.MACHINERY,
        home=Home.SEARCH_CONFIG,
        path="normalisation + negation",
        reason="Changing this changes how every posting is read. Not a fact about you.",
    ),
)


def candidate_facts() -> tuple[Fact, ...]:
    return tuple(fact for fact in FACTS if fact.owner is Owner.CANDIDATE)


def editable_fields() -> frozenset[str]:
    """Every `candidate_writer` field this table says a screen may change."""
    return frozenset(fact.field for fact in FACTS if fact.field is not None)


def displaced() -> tuple[Fact, ...]:
    """Candidate facts living in the machinery's file: what a migration moves."""
    return tuple(fact for fact in FACTS if fact.displaced)


def summary() -> dict[str, int]:
    """The state of the boundary, in four numbers `doctor` can print."""
    return {
        "candidate_facts": len(candidate_facts()),
        "machinery_facts": len(FACTS) - len(candidate_facts()),
        "editable_without_a_file": sum(1 for fact in FACTS if fact.editable),
        "candidate_facts_in_the_search_file": len(displaced()),
    }
