"""The candidate's half of the product, over HTTP.

`api.py` answers questions about POSTINGS. Everything here answers questions
about the PERSON: what she has confirmed about herself, what a posting asks for
beside that, and where she is with an application. They are separated because
the two halves have different truth rules and mixing them in one file is how
those rules get mixed too.

FOUR THINGS THIS FILE WILL NOT DO, and each is enforced below rather than
merely intended:

**No route submits an application.** There is no client here that can POST to
an employer. The workspace prepares; the person applies, in her own browser,
through the link the posting carries.

**No route invents a fact about the candidate.** `verified=True` is set in one
place in this program -- `cv.propose.to_claim` -- and the only handler that
reaches it is `decide_proposal`, on an explicit accept. A requirement verdict
creates nothing. A gap creates nothing. `EVIDENCE_MISSING` in particular
creates nothing: it routes the person to the ledger, where she writes the claim
herself, in her own words.

**No route hides an unmet requirement.** `preparation` returns every
requirement the posting fired, in every state, and the counts include GAP.
A caller cannot ask for only the matches, because there is no parameter for it.

**No route sends anything anywhere.** Nothing in this file opens a socket. The
CV arrives as bytes on the loopback connection, is parsed in memory, and is
never written to disk in any form.
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import CareerStage, ClaimSource, ClaimType
from career_agent.intake.models import SourceKind
from career_agent.web.server import ApiError, validate_job_id
from career_agent.web.server import closing as _closing

#: What a person may type into one field. Long enough for a real
#: accomplishment, short enough that a paste accident is refused rather than
#: stored. `VerifiedClaim.text` allows 2000 and this matches it deliberately.
MAX_CLAIM_TEXT = 2000

#: How far back "recent" reaches in the digest when nobody has a checkpoint.
#: The same default `career-agent daily` uses, and sent to the client so the
#: heading can be written in the reader's language rather than in the
#: server's.
DIGEST_DAYS = 7

#: A note on a requirement verdict. Shorter than a claim: it is a remark about
#: a mapping, not a statement about a career.
MAX_NOTE = 2000

#: The claim types somebody may create by hand. Every one of `ClaimType`
#: except `METRIC`, which is deliberately absent: a metric on its own is a
#: number without the sentence that gives it a subject, and this product's
#: whole position on figures is that they stay inside the sentence they were
#: written in.
MANUAL_CLAIM_TYPES = tuple(t.value for t in ClaimType if t is not ClaimType.METRIC)

#: How a claim was arrived at, in the words a person would use. The enum value
#: is kept beside it -- this is a label, not a rename.
SOURCE_LABELS = {
    "RESUME": "From your CV",
    "LINKEDIN": "From LinkedIn",
    "SELF_ATTESTED": "You wrote this",
    "DOCUMENT": "From a document",
}

#: What each readiness means, in one sentence, defined ONCE and served to the
#: interface rather than restated in JavaScript. Section 6 of the V1.3 brief
#: asks for these to be precise in code, docs and tests; this is the code half,
#: and `tests/unit/test_workspace_api.py` asserts all four are present.
READINESS_MEANING = {
    "MATCHED": (
        "A configured phrase links this requirement to a confirmed claim. "
        "Review the quotes to decide whether the evidence supports the work."
    ),
    "PARTIAL": (
        "You have named this -- a tool or a skill -- without confirmed evidence of "
        "having done the work. Having used something and having done the job are "
        "different answers."
    ),
    "GAP": (
        "No configured phrase linked this requirement to a confirmed claim. "
        "This does not establish that you lack the experience."
    ),
    "UNRESOLVED": (
        "This system cannot tell. The requirement fired on a signal with no phrases "
        "to compare a claim against, so no verdict here would be honest."
    ),
}

#: The four answers somebody may give about a requirement/evidence pair.
VERDICT_MEANING = {
    "SUPPORTS": "This evidence does support the requirement.",
    "PARTIALLY_SUPPORTS": "This evidence only partly supports it.",
    "DOES_NOT_SUPPORT": "This evidence does not support it.",
    "EVIDENCE_MISSING": (
        "I have relevant experience that is not in my profile. "
        "Nothing is claimed by saying so -- add it to your evidence."
    ),
}


def _now() -> str:
    """This instant, ISO-8601 UTC, the same shape every stored timestamp uses."""
    from career_agent.clock import now_utc

    return now_utc()


#: How many documents one intake may carry.
#:
#: Bounded because each one is read in memory and a person with forty files is
#: not describing a career, they have selected a folder. Three is the shape the
#: flow actually offers -- a CV, a LinkedIn export and one other thing -- and
#: the limit is above it so the honest case never hits a wall.
MAX_INTAKE_DOCUMENTS = 6


def _text(body: dict, key: str, *, limit: int, required: bool = True) -> str | None:
    raw = body.get(key)
    if raw is None:
        if required:
            raise ApiError(400, f"{key} is required")
        return None
    if not isinstance(raw, str):
        raise ApiError(400, f"{key} must be text")
    value = raw.strip()
    if required and not value:
        raise ApiError(400, f"{key} cannot be empty")
    if len(value) > limit:
        raise ApiError(400, f"{key} is limited to {limit} characters")
    return value or None


def _column(row: object, name: str) -> object | None:
    """One column, or None when this database predates it.

    `sqlite3.Row` raises `IndexError` for a name it does not carry, and a
    database migrated before 0025 has no `superseded_by`. Migrations run when a
    command opens a database, so that window is narrow -- and a 500 inside it
    would be a crash on the one screen that exists to explain the situation.
    """
    try:
        return row[name]  # type: ignore[index]
    except (IndexError, KeyError):
        return None


def _claim_key(raw: str) -> str:
    """A claim key from a URL segment, validated before it reaches SQL.

    Keys are generated by `cv.propose._key` or by `_manual_key` below, both of
    which produce this alphabet. Every query binds parameters as well; this is
    the belt to that pair of braces, exactly as `validate_job_id` is.
    """
    if not raw or len(raw) > 64 or not all(c.isalnum() or c in "-_" for c in raw):
        raise ApiError(400, "invalid claim key")
    return raw


def _period(body: dict, key: str) -> str | None:
    raw = body.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw) != 7 or raw[4] != "-":
        raise ApiError(400, f"{key} must be YYYY-MM")
    year, month = raw[:4], raw[5:]
    if not (year.isdigit() and month.isdigit() and 1 <= int(month) <= 12):
        raise ApiError(400, f"{key} must be YYYY-MM")
    return raw


def _tools(body: dict) -> list[str]:
    raw = body.get("tools", [])
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ApiError(400, "tools must be a list")
    out: list[str] = []
    for item in raw[:40]:
        if not isinstance(item, str):
            raise ApiError(400, "tools must be a list of text")
        value = item.strip()[:80]
        if value:
            out.append(value)
    return out


def _manual_key(text: str, existing: set[str]) -> str:
    """A stable key for a claim somebody typed.

    Content-derived, like the CV reader's, so that writing the same sentence
    twice is visible as the same fact rather than as two. A suffix is added
    only on a genuine collision with a DIFFERENT claim.
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:44] or "claim"
    candidate = f"own-{slug}"
    if candidate not in existing:
        return candidate
    for n in range(2, 100):
        alt = f"{candidate}-{n}"[:64]
        if alt not in existing:
            return alt
    raise ApiError(409, "too many claims with this wording", for_reader=True)


def claim_payload(claim: VerifiedClaim, *, revisions: int = 1) -> dict:
    """One claim, as the interface shows it.

    `evidence_ref` is sent under the name `evidence` and is shown WHENEVER it
    differs from the text. That difference is the whole point of an edit: the
    stored claim may be the person's corrected wording, and the line her CV
    actually carried stays beside it rather than being rewritten to agree.
    """
    return {
        "claim_key": claim.claim_key,
        "revision": claim.revision,
        "revisions": revisions,
        "claim_type": claim.claim_type.value,
        "text": claim.text,
        "employer": claim.employer,
        "period_start": claim.period_start,
        "period_end": claim.period_end,
        "source": claim.source.value,
        "source_label": SOURCE_LABELS.get(claim.source.value, claim.source.value),
        "verified": claim.verified,
        "evidence": claim.evidence_ref,
        "evidence_differs": bool(
            claim.evidence_ref and claim.evidence_ref.strip() != claim.text.strip()
        ),
        "tools": list(claim.tools),
        "tags": [tag.value for tag in claim.tags],
        # A certification line read as its fields, for display only: the claim
        # text and its source line are unchanged. None when the line does not
        # have a shape `cv.certifications` reads without guessing.
        "certificate": _certificate_of(claim),
    }


def _certificate_of(claim: VerifiedClaim) -> dict | None:
    from career_agent.cv.certifications import read_certificate

    if claim.claim_type.value != "CERTIFICATION":
        return None
    found = read_certificate(claim.text)
    return found.as_dict() if found else None


def proposal_payload(row: sqlite3.Row) -> dict:
    """One staged proposal, with the line it was read from.

    `evidence` is always sent, even when it equals `text`. A review whose
    source quote appears only sometimes teaches somebody to stop looking for
    it, and looking for it is the entire mechanism that makes this reviewable.
    """
    return {
        "claim_key": str(row["claim_key"]),
        "claim_type": str(row["claim_type"]),
        "section": str(row["section"]),
        "text": str(row["text"]),
        "evidence": str(row["evidence"]),
        "evidence_differs": str(row["evidence"]).strip() != str(row["text"]).strip(),
        "has_measurement": bool(row["has_measurement"]),
        "decision": str(row["decision"]),
        "decided_text": row["decided_text"],
        "decided_at": row["decided_at"],
        # Which job it sits under, and the RAW provenance: the line number and
        # the line exactly as the document had it. Sent as text; the interface
        # never renders an imported line as HTML.
        "entry_key": _column(row, "entry_key"),
        "source_line": _column(row, "source_line"),
        "source_text": _column(row, "source_text"),
    }


#: The mixin's base, and it differs between running and checking on purpose.
#:
#: At RUNTIME this is `object`. `JobsApi(WorkspaceRoutes, LocalApp)` puts these
#: handlers first in the method resolution order, so any method defined here
#: SHADOWS `LocalApp`'s -- an earlier version declared `register` as a stub
#: raising `NotImplementedError` for the type checker's benefit and the server
#: could not register a single route.
#:
#: At CHECK time it is `LocalApp`, which is what it actually gets mixed into,
#: so `self.register`, `self.connect` and `self.search_config` are typed from
#: their real definitions rather than from a hand-written promise about them.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from career_agent.web.server import LocalApp as _MixinBase
else:
    _MixinBase = object


#: How many claims one request may return. A group of a person's real career
#: is tens; this is the ceiling that stops a pathological package becoming a
#: single enormous response, and it is REPORTED rather than silent.
PAGE_OF_CLAIMS = 200


def _group_id_of(row: object, payload: dict) -> str:
    """Which screen this claim belongs on.

    **Anything naming an employer is grouped by that employer**, whatever kind
    of claim it is. That is the unit a person thinks in: "what does this
    package say about my time at Acme" is a question somebody can answer, and
    "here are 287 sentences" is not. A bullet, an achievement and a project
    from the same job belong on one screen because they are one memory.

    Everything else -- skills, tools, certifications, education -- is grouped
    by kind, which is how a CV already lays them out.

    The first version grouped only EMPLOYMENT by employer and everything else
    by kind. On a package of 315 claims that produced a `PROJECT` group of 60
    spanning ten employers, headed with whichever employer sorted first: a
    heading that was both a wall and a lie.

    Derived, never stored. It is a decision about presentation, and a column
    would freeze today's answer to it into the schema.
    """
    employer = (payload.get("employer") or "").strip()
    if employer:
        return f"EMPLOYER:{employer.casefold()}"
    # The COLUMN, not `payload["type"]`. They hold the same value -- the column
    # is written from it -- and `payload.get('type')` is the shape
    # `tests/unit/test_provider_neutrality.py` refuses, because generic code
    # reading a key off a payload is how vendor knowledge gets in. This payload
    # is our own contract rather than a vendor's, and the guard is structural
    # for a good reason: it cannot tell, and neither can the next reader.
    return str(row["claim_type"])  # type: ignore[index]


def _widen(period: dict, into: dict) -> dict:
    """The widest period any claim in a group states.

    A heading says "Acme Logistics, Jan 2015 to Mar 2017" about the whole run,
    so it takes the earliest start and the latest end across the group rather
    than whichever sentence happened to sort first. Both halves of each date
    travel together -- the wording the document used and the reading of it --
    because the reading alone is the half she cannot check.
    """
    if not period:
        return into
    out = dict(into)
    start = period.get("start")
    if start and (
        not out.get("start")
        or str(start.get("normalized") or "") < str(out["start"].get("normalized") or "")
    ):
        out["start"] = start
    if period.get("current"):
        # Still there beats any end date: an end that is still running has not
        # happened, and a heading saying otherwise would be stating a fact
        # about her that no document does.
        out["current"] = True
        out.pop("end", None)
    elif not out.get("current"):
        end = period.get("end")
        if end and (
            not out.get("end")
            or str(end.get("normalized") or "") > str(out["end"].get("normalized") or "")
        ):
            out["end"] = end
    return out


def _priority_claims(conn: object, package_id: str) -> tuple[list, dict[str, str], frozenset[str]]:
    """The little the queue needs: every claim reduced, plus the open conflicts.

    Read in Python for the reason `_intake_groups` gives -- the employer and
    the dates live inside `payload_json` -- and it is the same size of work:
    three hundred rows is a summary, not a scan.
    """
    import json as _json

    from career_agent.intake.priority import claim_from

    rows = conn.execute(  # type: ignore[attr-defined]
        "SELECT * FROM intake_claim WHERE package_id = ? ORDER BY claim_key",
        (package_id,),
    ).fetchall()
    claims = []
    texts: dict[str, str] = {}
    for row in rows:
        payload = _json.loads(str(row["payload_json"]))
        claims.append(claim_from(row, payload))
        texts[str(row["claim_key"])] = str(payload.get("text") or "")

    settled = {
        str(r["conflict_group"])
        for r in conn.execute(  # type: ignore[attr-defined]
            "SELECT conflict_group FROM intake_conflict_resolution WHERE package_id = ?",
            (package_id,),
        )
    }
    unresolved = frozenset(
        {claim.conflict_group for claim in claims if claim.conflict_group} - settled
    )
    return claims, texts, unresolved


def _intake_groups(conn: object, package_id: str) -> list[dict]:
    """One heading per group, with what is waiting in it.

    Read in Python rather than grouped in SQL because the employer lives
    inside `payload_json`. Three hundred rows is a summary, not a scan: 4 ms
    on a 315-claim package.
    """
    import json as _json

    from career_agent.intake.models import ReviewState

    rows = conn.execute(  # type: ignore[attr-defined]
        "SELECT * FROM intake_claim WHERE package_id = ? ORDER BY claim_key",
        (package_id,),
    ).fetchall()

    groups: dict[str, dict] = {}
    for row in rows:
        payload = _json.loads(str(row["payload_json"]))
        gid = _group_id_of(row, payload)
        entry = groups.setdefault(
            gid,
            {
                "group_id": gid,
                # EMPLOYER is a group kind, not a claim kind. The screen it
                # names holds several claim kinds about one job, and calling
                # it EMPLOYMENT would say the projects in it are employment.
                "kind": "EMPLOYER" if gid.startswith("EMPLOYER:") else str(row["claim_type"]),
                "employer": payload.get("employer"),
                "period": {},
                "kinds": [],
                "total": 0,
                "waiting": 0,
                "conflicted": 0,
            },
        )
        entry["total"] += 1
        if str(row["review_state"]) in ReviewState.ANSWERABLE:
            entry["waiting"] += 1
        if row["conflict_group"]:
            entry["conflicted"] += 1
        if str(row["claim_type"]) not in entry["kinds"]:
            entry["kinds"].append(str(row["claim_type"]))
        entry["period"] = _widen(payload.get("period") or {}, entry["period"])

    # A job first, and the most recent job first inside that: a review should
    # start where the work she is likeliest to remember is, not wherever the
    # alphabet points. A group with no dates at all sorts last among jobs.
    def order(entry: dict) -> tuple:
        start = (entry.get("period") or {}).get("start") or {}
        return (
            0 if entry["kind"] == "EMPLOYER" else 1,
            # Descending by start date, which is why it is negated by
            # inverting the comparison rather than by a minus sign.
            _reverse(str(start.get("normalized") or "")),
            entry["group_id"],
        )

    return sorted(groups.values(), key=order)


class _reverse:  # noqa: N801  (a sort key, not a type somebody constructs)
    """Sort a string descending inside an ascending tuple key."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __lt__(self, other: _reverse) -> bool:
        return self.value > other.value


class WorkspaceRoutes(_MixinBase):
    """The candidate-side handlers, mixed into `JobsApi`.

    A mixin rather than a second server: these routes share the connection, the
    origin checks and the loaded search configuration with everything else, and
    a second app would have to duplicate all three.
    """

    # `search_config` and `_identity` are defined on `JobsApi`, not on
    # `LocalApp`, so they stay declared rather than inherited.
    if TYPE_CHECKING:  # pragma: no cover - typing only

        def search_config(self) -> Any: ...

        def _identity(self) -> tuple[str, int]: ...

        #: The band edges and the recency window, read once from the loaded
        #: configuration and cached on `JobsApi`. The digest renders cards, so
        #: it needs both.
        _bands: dict[str, dict[str, int]]
        _recency: dict[str, int]

    # =================================================================
    def register_workspace_routes(self) -> None:
        from career_agent.web.career_api import register_career_routes

        register_career_routes(self)
        self.register("GET", r"/api/evidence", self.evidence)
        self.register("POST", r"/api/evidence", self.create_claim)
        self.register("PATCH", r"/api/evidence/(?P<claim_key>[^/]+)", self.edit_claim)
        self.register("POST", r"/api/evidence/(?P<claim_key>[^/]+)/retire", self.retire_claim)
        self.register("POST", r"/api/evidence/(?P<claim_key>[^/]+)/confirm", self.confirm_claim)
        self.register("GET", r"/api/evidence/(?P<claim_key>[^/]+)/history", self.claim_history)

        self.register("GET", r"/api/cv/imports", self.cv_imports)
        self.register("POST", r"/api/cv/import", self.cv_import)
        self.register("GET", r"/api/cv/imports/(?P<import_id>[^/]+)", self.cv_review)
        self.register("POST", r"/api/cv/imports/(?P<import_id>[^/]+)/decide", self.decide_proposal)
        # "Discard" was a hard delete. It is ARCHIVE now, which is what the
        # word promised; deleting is its own route and asks first.
        self.register("POST", r"/api/cv/imports/(?P<import_id>[^/]+)/discard", self.discard_import)
        from career_agent.web.cv_api import register_cv_routes

        register_cv_routes(self)
        from career_agent.web.documents_api import register_documents_routes

        register_documents_routes(self)

        # The Candidate Intake Package. Five routes, and the shape of them is
        # the product decision: a SUMMARY that never returns three hundred
        # rows, a PAGE of one group, one ANSWER at a time, and a disagreement
        # answered ONCE.
        self.register("GET", r"/api/intake", self.intake_packages)
        # THE WAY IN FOR A BROWSER. Until this existed, producing an intake
        # package meant a terminal (`career-agent intake-build`) or sending
        # your documents to an AI provider. Neither is an onboarding step.
        self.register("POST", r"/api/intake", self.intake_create)
        # WHAT A FIRST RUN STILL HAS TO DO, counted rather than guessed.
        self.register("GET", r"/api/firstrun", self.firstrun)
        self.register("POST", r"/api/firstrun/stage", self.set_career_stage)
        self.register("GET", r"/api/intake/(?P<package_id>[^/]+)", self.intake_overview)
        self.register("GET", r"/api/intake/(?P<package_id>[^/]+)/claims", self.intake_claims)
        self.register("GET", r"/api/intake/(?P<package_id>[^/]+)/priority", self.intake_priority)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/answer", self.intake_answer)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/resolve", self.intake_resolve)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/discard", self.intake_discard)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/select", self.intake_select)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/restore", self.intake_restore)
        self.register("POST", r"/api/intake/(?P<package_id>[^/]+)/delete", self.intake_delete)

        self.register("GET", r"/api/home", self.home)
        self.register("GET", r"/api/daily", self.daily)
        self.register("POST", r"/api/daily/reviewed", self.mark_reviewed)

        self.register("GET", r"/api/jobs/(?P<job_id>[^/]+)/preparation", self.preparation)
        self.register("GET", r"/api/jobs/(?P<job_id>[^/]+)/resume", self.resume)
        self.register(
            "PATCH",
            r"/api/jobs/(?P<job_id>[^/]+)/requirements/(?P<signal_id>[^/]+)",
            self.review_requirement,
        )

    # =================================================================
    # the evidence ledger
    # =================================================================
    def evidence(self, *, query: dict, body: dict) -> dict:
        """Everything confirmed about the candidate, and everything retired.

        Both, in one response, with `verified` telling them apart. A ledger
        that showed only the current confirmed set would make retiring a claim
        look like deleting one, and the whole reason retirement is a revision
        rather than a DELETE is that the history is worth keeping.
        """
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                return {
                    "candidate": False,
                    "claims": [],
                    "counts": {},
                    "confirmed": 0,
                    "retired": 0,
                    "drafts": 0,
                    "types": list(MANUAL_CLAIM_TYPES),
                    "sources": SOURCE_LABELS,
                }
            repo = ClaimRepo(conn)
            claims = repo.current(candidate_id)
            states = repo.states(candidate_id)
            revisions = {
                str(row["claim_key"]): int(row["n"])
                for row in conn.execute(
                    "SELECT claim_key, COUNT(*) AS n FROM verified_claim"
                    " WHERE candidate_id = ? GROUP BY claim_key",
                    (candidate_id,),
                ).fetchall()
            }

        payload = [
            {
                **claim_payload(claim, revisions=revisions.get(claim.claim_key, 1)),
                # CONFIRMED, RETIRED (withdrawn) or DRAFT (never confirmed):
                # `verified` alone cannot tell the last two apart.
                "state": states.get(claim.claim_key, "CONFIRMED" if claim.verified else "RETIRED"),
            }
            for claim in claims
        ]
        counts: dict[str, int] = {}
        for claim in claims:
            if claim.verified:
                counts[claim.claim_type.value] = counts.get(claim.claim_type.value, 0) + 1
        return {
            "candidate": True,
            "claims": payload,
            "counts": counts,
            "confirmed": sum(1 for c in claims if c.verified),
            "retired": sum(1 for state in states.values() if state == "RETIRED"),
            "drafts": sum(1 for state in states.values() if state == "DRAFT"),
            "types": list(MANUAL_CLAIM_TYPES),
            "sources": SOURCE_LABELS,
        }

    def create_claim(self, *, query: dict, body: dict) -> dict:
        """A fact the person states about herself, in her own words.

        `SELF_ATTESTED`, and that is not a lesser source -- it is the accurate
        one. A sentence somebody typed into this box was confirmed by the only
        authority there is on her career, and recording it as though it had
        come off a CV would be this program inventing a provenance.

        `evidence_ref` is deliberately left empty. There is no document behind
        it, and copying the text into the evidence field would manufacture a
        citation that cites itself -- exactly the failure section 7 of the V1.3
        brief names.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import ensure_candidate

        text = _text(body, "text", limit=MAX_CLAIM_TEXT)
        assert text is not None
        raw_type = body.get("claim_type")
        if raw_type not in MANUAL_CLAIM_TYPES:
            allowed = ", ".join(MANUAL_CLAIM_TYPES)
            raise ApiError(400, f"claim_type must be one of: {allowed}")
        employer = _text(body, "employer", limit=200, required=False)
        start = _period(body, "period_start")
        end = _period(body, "period_end")
        if end and not start:
            raise ApiError(400, "a period end needs a period start")
        if start and end and end < start:
            raise ApiError(400, "the period ends before it starts")

        experience_id = body.get("experience_id")
        if experience_id is not None and not isinstance(experience_id, str):
            raise ApiError(400, "Choose an existing experience.")
        with _closing(self.connect()) as conn, transaction(conn):
            candidate_id = ensure_candidate(conn)
            repo = ClaimRepo(conn)
            if experience_id is not None:
                row = conn.execute(
                    "SELECT id FROM career_experience WHERE id = ?"
                    " AND candidate_id = ? AND archived = 0",
                    (experience_id, candidate_id),
                ).fetchone()
                if row is None:
                    raise ApiError(404, "Choose an existing experience.")
            existing = {claim.claim_key for claim in repo.current(candidate_id)}
            claim = VerifiedClaim(
                claim_key=_manual_key(text, existing),
                claim_type=ClaimType(raw_type),
                text=text,
                employer=employer,
                period_start=start,
                period_end=end,
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
                evidence_ref=None,
                tools=_tools(body),
            )
            repo.add(candidate_id, claim)
            if experience_id is not None:
                from career_agent.storage.career_repo import CareerRepo

                CareerRepo(conn, candidate_id).link(claim.claim_key, experience_id)
        return self.evidence(query={}, body={})

    def edit_claim(self, *, claim_key: str, query: dict, body: dict) -> dict:
        """Correct a claim. Creates a revision; destroys nothing.

        THE EVIDENCE DOES NOT MOVE. `evidence_ref` is carried forward from the
        claim being replaced and is never taken from the request. That is what
        keeps an edit honest: the corrected sentence is the person's, the line
        her CV carried is the document's, and rewriting the second to agree
        with the first would leave a citation that no longer cites anything.

        NEITHER DOES THE CONFIRMATION. `verified` is carried forward too, so
        editing a retired claim leaves it retired. Bringing one back is saying
        "this is true after all", which is an act and has its own route -- an
        edit that silently re-confirmed would let a typo fix restore a fact
        somebody had deliberately stopped standing behind.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        key = _claim_key(claim_key)
        text = _text(body, "text", limit=MAX_CLAIM_TEXT)
        assert text is not None
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such claim")
            repo = ClaimRepo(conn)
            row = repo.current_row(candidate_id, key)
            if row is None:
                raise ApiError(404, "no such claim")
            current = repo._to_claim(row)
            employer = (
                _text(body, "employer", limit=200, required=False)
                if "employer" in body
                else current.employer
            )
            start = (
                _period(body, "period_start") if "period_start" in body else current.period_start
            )
            end = _period(body, "period_end") if "period_end" in body else current.period_end
            if end and not start:
                raise ApiError(400, "a period end needs a period start")
            if start and end and end < start:
                raise ApiError(400, "the period ends before it starts")
            revised = current.next_revision(
                text=text,
                employer=employer,
                period_start=start,
                period_end=end,
                tools=_tools(body) if "tools" in body else list(current.tools),
            )
            with transaction(conn):
                repo.supersede(candidate_id, revised)
        return self.evidence(query={}, body={})

    def retire_claim(self, *, claim_key: str, query: dict, body: dict) -> dict:
        """Stop drawing on a claim, without pretending it was never made.

        A revision with `verified=False`, not a DELETE. Preparation consults
        only confirmed claims, so a retired one stops answering requirements
        immediately, and the ledger still shows it with its history intact.

        Deleting the row would be the wrong shape for two reasons. The claim
        may already have been used to prepare an application somebody sent, and
        the record of what she believed at the time is the record. And a
        revision chain whose earlier links can vanish is not a provenance.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        key = _claim_key(claim_key)
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such claim")
            repo = ClaimRepo(conn)
            row = repo.current_row(candidate_id, key)
            if row is None:
                raise ApiError(404, "no such claim")
            current = repo._to_claim(row)
            if not current.verified:
                raise ApiError(409, "this claim is already retired", for_reader=True)
            with transaction(conn):
                repo.supersede(candidate_id, current.next_revision(verified=False))
        return self.evidence(query={}, body={})

    def confirm_claim(self, *, claim_key: str, query: dict, body: dict) -> dict:
        """Stand behind a retired claim again. A revision, like everything here.

        The inverse of `retire`, and a route of its own rather than a flag on
        the editor, because confirming a fact is an act. A person fixing a typo
        in a sentence she had withdrawn has not thereby withdrawn the
        withdrawal, and an editor assuming otherwise would put a claim back
        into her applications without her ever saying so.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        key = _claim_key(claim_key)
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such claim")
            repo = ClaimRepo(conn)
            row = repo.current_row(candidate_id, key)
            if row is None:
                raise ApiError(404, "no such claim")
            current = repo._to_claim(row)
            if current.verified:
                raise ApiError(409, "this claim is already confirmed", for_reader=True)
            with transaction(conn):
                repo.supersede(candidate_id, current.next_revision(verified=True))
        return self.evidence(query={}, body={})

    def claim_history(self, *, claim_key: str, query: dict, body: dict) -> dict:
        """Every revision of one fact, oldest first."""
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        key = _claim_key(claim_key)
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such claim")
            revisions = ClaimRepo(conn).history(candidate_id, key)
        if not revisions:
            raise ApiError(404, "no such claim")
        return {
            "claim_key": key,
            "revisions": [claim_payload(claim) for claim in revisions],
        }

    # =================================================================
    # CV intake
    # =================================================================

    # =================================================================
    # The Candidate Intake Package
    #
    # WHY THESE ROUTES ARE SHAPED THIS WAY
    # ------------------------------------
    # The owner's real package holds 309 proposals. The CLI answer to that is
    # a list, and a list of 309 is a wall -- the thing people close the tab on.
    #
    # So there is no route that returns them all. `intake_overview` returns
    # COUNTS and GROUP HEADINGS; `intake_claims` returns one group at a time.
    # A reader meets "Acme, 2020 to 2021, 9 things to look at" before she meets
    # a sentence, and opens the one she wants to work on.
    # =================================================================

    def intake_packages(self, *, query: dict, body: dict) -> dict:
        """Every staged package, newest first, and which one is in force.

        `active_package_id` is returned beside the list rather than left to be
        inferred from the statuses. The interface has to open a sentence with
        "you are reviewing THIS reading of your documents", and a client that
        worked that out for itself would be a second implementation of the
        invariant -- one that cannot be tested where the first one is.

        A package carries the documents it was built from, when it was built,
        what it holds, how far the review has got, and WHY it is not in force
        when it is not. That last one is `superseded_by`: "a newer one exists"
        does not answer it, and naming the successor does.
        """
        del query, body
        import json as _json

        from career_agent.intake.store import active_package, summary

        with _closing(self.connect()) as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM intake_package ORDER BY created_at DESC"
                ).fetchall()
            except Exception:
                # A database that predates migration 0023 has no such table,
                # and "you have no packages" is the truthful answer rather
                # than a 500.
                return {"packages": [], "active_package_id": None}
            packages = []
            for row in rows:
                if _column(row, "deleted_at") is not None:
                    # Deleted, kept only as the provenance of what she confirmed.
                    continue
                counts = summary(conn, str(row["id"]))
                packages.append(
                    {
                        "package_id": str(row["id"]),
                        "filename": row["filename"],
                        "generator": row["generator"],
                        "status": str(row["status"]),
                        "created_at": str(row["created_at"]),
                        "claim_count": int(row["claim_count"]),
                        "counts": counts,
                        # The documents, by KIND and reference. Two packages
                        # built from the same CV and the same export look
                        # identical without this, and choosing between them is
                        # then a choice between two timestamps.
                        "sources": _json.loads(str(row["declared_sources"])),
                        "superseded_by": _column(row, "superseded_by"),
                        "conflicted": int(counts.get("CONFLICT", 0)),
                        "answered": sum(
                            counts[state]
                            for state in (
                                "CONFIRMED",
                                "CORRECTED_BY_USER",
                                "REJECTED",
                                "UNRESOLVED",
                            )
                        ),
                    }
                )
            active = active_package(conn)
        return {"packages": packages, "active_package_id": active}

    def intake_overview(self, *, package_id: str, query: dict, body: dict) -> dict:
        """What this package holds, WITHOUT returning what it holds.

        Counts, the documents it declares, the disagreements, and one heading
        per group. Everything a reader needs to decide where to start, and
        nothing that makes a wall.
        """
        del query, body
        import json as _json

        from career_agent.intake.store import conflict_groups, summary

        key = _claim_key(package_id)
        with _closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM intake_package WHERE id = ?", (key,)).fetchone()
            if row is None:
                raise ApiError(404, "no such package")
            counts = summary(conn, key)
            conflicts = conflict_groups(conn, key)
            groups = [
                {
                    "group_id": str(g["group_id"]),
                    "kind": str(g["kind"]),
                    "employer": g["employer"],
                    "period": g["period"],
                    # WHICH KINDS are inside, so a heading can say "work,
                    # things you achieved, projects" rather than making one
                    # number stand for three different sorts of statement.
                    "kinds": list(g["kinds"]),
                    "total": int(g["total"]),
                    "waiting": int(g["waiting"]),
                    "conflicted": int(g["conflicted"]),
                }
                for g in _intake_groups(conn, key)
            ]
            sources = _json.loads(str(row["declared_sources"]))

        answered = sum(
            counts[state] for state in ("CONFIRMED", "CORRECTED_BY_USER", "REJECTED", "UNRESOLVED")
        )
        return {
            "package_id": key,
            "filename": row["filename"],
            "generator": row["generator"],
            "status": str(row["status"]),
            # Whether THIS package is the one in force, answered here so a
            # reader who arrived by URL is not looking at a superseded reading
            # believing it is the live one.
            "is_active": str(row["status"]) == "ACTIVE",
            "superseded_by": _column(row, "superseded_by"),
            "created_at": str(row["created_at"]),
            "sources": sources,
            "counts": counts,
            "total": int(row["claim_count"]),
            # How far she has got, as a fraction of what arrived. Never framed
            # as a target: the wording on screen says what is left rather than
            # how much of a job she has "completed", because nothing here
            # should push somebody into confirming a sentence to finish a bar.
            "answered": answered,
            "conflicts": conflicts,
            "groups": groups,
        }

    # =====================================================================
    # THE FIRST RUN
    # =====================================================================
    #
    # "I just installed this. What do I do now?" was, until these three routes,
    # a question the product answered with a terminal: `career-agent
    # intake-build`, then `intake-import`, then `setup`. That is not an
    # onboarding, it is a runbook, and a person who does not write software
    # stops at the first command.
    #
    # WHAT THESE ROUTES ARE NOT
    # -------------------------
    # They are not a wizard the product refuses to work without. Every screen
    # already works with none of this answered; what changes is what the
    # product can CONCLUDE. So the flow is resumable, every step is skippable,
    # and each one writes as soon as it is answered rather than at the end.
    #
    # They are not a progress bar over a person either. `GET /api/firstrun`
    # returns STEPS WITH STATES, each naming a fact that is either recorded or
    # is not. There is no percentage, because there is no honest denominator
    # for a career -- the same refusal `home.profile_gaps` makes.

    def intake_create(self, *, query: dict, body: dict) -> dict:
        """Read one or more documents and stage what they appear to say.

        THE BROWSER'S WAY IN, and the reason this route exists at all. An
        intake package could be produced two ways before: a terminal command,
        or copying a prompt into an AI provider and sending that provider your
        CV. The second is a real choice and it must stay a choice rather than
        the only path through, which is what it was for anybody who does not
        open a terminal.

        NOTHING LEAVES THIS MACHINE. The documents are read by
        `cv.extract` / `cv.propose` / `intake.build` -- the same deterministic
        readers the CLI uses, none of which opens a socket or asks a model
        anything.

        NOTHING IS CONFIRMED. Every claim lands UNREVIEWED, or CONFLICT when
        two documents disagree about a date, and CONFLICT is an unanswered
        state rather than a verdict. `store.confirm` is the only thing that can
        make a claim true and only a person can call it.

        The files arrive base64-encoded inside a JSON body, for the same
        security reason `/api/cv/import` does: `server._check_origin` requires
        `application/json` on every body precisely because a cross-origin form
        CAN send multipart without a preflight and CANNOT send JSON.
        """
        del query
        from career_agent.cv.extract import CvError
        from career_agent.intake.build import IntakeDocument, build_package
        from career_agent.intake.store import import_package, preview

        documents = body.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ApiError(
                400,
                "Choose at least one document. A CV on its own is enough.",
                for_reader=True,
            )
        if len(documents) > MAX_INTAKE_DOCUMENTS:
            raise ApiError(
                400,
                f"At most {MAX_INTAKE_DOCUMENTS} documents at once.",
                for_reader=True,
            )

        read: list[IntakeDocument] = []
        for index, entry in enumerate(documents, start=1):
            if not isinstance(entry, dict):
                raise ApiError(400, "each document must be an object")
            filename = _text(entry, "filename", limit=300)
            assert filename is not None
            kind = str(entry.get("kind") or "DOCUMENT").strip().upper()
            if kind not in SourceKind.ALL:
                raise ApiError(
                    400,
                    f"kind must be one of {', '.join(sorted(SourceKind.ALL))}",
                )
            raw = entry.get("content_base64")
            if not isinstance(raw, str) or not raw:
                raise ApiError(400, f"{filename}: content_base64 is required")
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ApiError(400, f"{filename}: content_base64 is not valid base64") from exc
            # The REF is what every claim cites, so it has to be stable and
            # distinct per document. Derived from the declared kind and the
            # position rather than from the filename: two files called
            # `profile.pdf` are two documents, and a ref that collided would
            # silently merge their provenance.
            ref = f"{kind.lower()}-{index}"
            read.append(IntakeDocument(name=filename, data=data, ref=ref, kind=kind))

        try:
            package = build_package(read)
        except CvError as exc:
            # The extractor names the file and never quotes it. Far more useful
            # than "could not read", and safe to pass through.
            raise ApiError(400, str(exc), for_reader=True) from exc

        if not package.claims:
            raise ApiError(
                422,
                "Those documents were read and no section this recognises came out of "
                "them. It looks for headings: Experience, Skills, Education, "
                "Certifications, Projects, Volunteering, Internships, Activities and "
                "Awards, in English or Portuguese. A scanned CV is images rather than "
                "text, and there is nothing in one to read.",
                for_reader=True,
            )

        with _closing(self.connect()) as conn:
            # The dry run FIRST, over the same reconciliation, so the response
            # can say what arrived and what disagrees with what. `preview`
            # writes nothing and `import_package` is idempotent by digest:
            # sending the same CV twice returns the review already in progress
            # rather than a second copy of it.
            dry = preview(conn, package)
            package_id = import_package(conn, package)

        return {
            "package_id": package_id,
            "documents_read": len(read),
            "claims_found": dry.claims_total,
            "claims_new": dry.claims_new,
            "already_confirmed": dry.claims_already_confirmed,
            "disagreements": dry.conflict_groups,
            # Reopening an earlier review rather than starting a new one is a
            # fact the screen has to be able to state: somebody who uploads the
            # same file twice needs to know their answers survived.
            "reopened": dry.existing_package_id == package_id,
            # The promise, in the response, so a client cannot forget to say it.
            "confirmed_nothing": True,
        }

    def firstrun(self, *, query: dict, body: dict) -> dict:
        """What a fresh install still has to do, as facts rather than as a score.

        Each step is either RECORDED or is not, and the reason is a specific
        thing that is absent. No percentage and no total: section 20 of the
        review, and the same refusal `home.profile_gaps` makes. A bar over
        somebody's career invents a denominator -- how many facts IS a whole
        person -- and then reports the answer as progress.

        Read-only, and it creates no candidate row: a GET that did would make
        "nothing has been confirmed about you" indistinguishable from "this
        database has never had anybody in it", and the first screen has to be
        able to tell those apart.
        """
        del query, body
        from career_agent.storage.review_counts import review_counts
        from career_agent.storage.workspace_repo import (
            CAREER_STAGE,
            CandidateStateRepo,
            candidate_id_of,
        )

        config = self.search_config()
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            stage = (
                CandidateStateRepo(conn).get(candidate_id, CAREER_STAGE) if candidate_id else None
            )
            confirmed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM verified_claim"
                    " WHERE superseded_by_id IS NULL AND verified = 1"
                ).fetchone()[0]
            )
            # ONE definition of what is waiting, shared with Home, Career
            # Evidence and the terminal. An archived import is work she put
            # down: it neither waits here nor keeps the setup unfinished.
            counts = review_counts(conn, candidate_id)
            scored = int(conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0])

        # The three configuration facts, read from the SAME config object the
        # matcher uses. `home.profile_gaps` asks the same questions for the Home
        # page and is deliberately not reused: it returns a flat list of names
        # for a different surface, and a step here has to carry the VALUE too --
        # "you said BR" is an answer and "residence: not missing" is not.
        eligibility = getattr(config, "eligibility", None)
        lives_somewhere = bool(getattr(eligibility, "candidate_country", "") or "")
        scopes_named = bool(getattr(eligibility, "eligible_scopes", ()) or ()) or bool(
            getattr(eligibility, "eligible_countries", ()) or ()
        )
        work_described = bool(getattr(config, "lexicon", {}) or {})
        roles, skills = _first_search_phrases(config)
        home_code = (getattr(eligibility, "candidate_country", "") or "").strip().upper()

        # CAREER STAGE IS NOT A STEP. It is stored and nothing reads it -- no
        # score, no filter, no explanation changes with it (docs/ONBOARDING.md)
        # -- so asking it as setup would be a question the product ignores.
        # The answer, if one was given, is kept and still returned below.
        steps = [
            {
                "key": "documents",
                # Added, whether or not it is being worked on now. Archiving a
                # document is putting it away, not un-adding it.
                "done": counts.documents + counts.cv_archived + counts.packages_archived > 0,
                "documents": counts.cv_imports,
                "packages": counts.packages,
                "archived": counts.cv_archived + counts.packages_archived,
            },
            {
                "key": "evidence",
                # CONFIRMED, not "imported". A proposal read off a CV is a
                # draft, and counting one as evidence is what would make the
                # whole review step decorative.
                "done": confirmed > 0,
                "confirmed": confirmed,
                "waiting": counts.waiting,
            },
            {
                "key": "where",
                "done": lives_somewhere and scopes_named,
                "country": getattr(eligibility, "candidate_country", "") or None,
                # The hiring regions that contain where she lives, for showing
                # an answer back...
                "home_regions": _regions_containing(home_code) if home_code else [],
                # ...and the ones worth ASKING about. `gates._region_verdict`
                # admits a region that contains any confirmed country by
                # itself, and a region answer adds evidence only through her
                # residence -- so a region that already holds a confirmed
                # country would be a question whose answer changes nothing.
                "regions": _regions_worth_asking(
                    home_code, getattr(eligibility, "eligible_countries", ()) or ()
                )
                if home_code
                else [],
            },
            {
                "key": "work",
                "done": work_described,
                "phrases": len(getattr(config, "lexicon", {}) or {}),
                # The words she gave the setup, so going back shows them.
                "roles": roles,
                "skills": skills,
            },
            {
                "key": "jobs",
                # A corpus with no scores is a product with nothing to show,
                # and on a fresh install that is the ordinary state rather than
                # a fault. The step says so instead of the Jobs page being
                # mysteriously empty.
                "done": scored > 0,
                "scored": scored,
            },
        ]
        return {
            "steps": steps,
            # Whether this looks like a first run at all, so the interface knows
            # whether to offer the flow unprompted. Deliberately NOT "has the
            # user dismissed a banner": the question is whether the product can
            # conclude anything yet.
            "fresh": confirmed == 0 and not work_described,
            "career_stage": stage,
            "career_stages": [stage.value for stage in CareerStage],
        }

    def set_career_stage(self, *, query: dict, body: dict) -> dict:
        """Record where the person says they are in their career.

        CONTEXT, never identity, and never an ingestion constraint. It changes
        what the product explains and which filters it puts in front of
        somebody. It changes no score, no gate and no collection: it is stored
        in `candidate_state` rather than in `search.local.yaml` precisely
        because every section of that file changes how a POSTING IS READ, and a
        stored score is only true relative to the configuration that produced
        it.

        Sending `null` clears it, which is a different answer from
        `PREFER_NOT_TO_SAY`: one is "I have not answered", the other is "I have
        answered, and my answer is that I would rather not say".
        """
        del query
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import (
            CAREER_STAGE,
            CandidateStateRepo,
            ensure_candidate,
        )

        raw = body.get("stage")
        if raw is None:
            with _closing(self.connect()) as conn:
                candidate_id = ensure_candidate(conn)
                with transaction(conn):
                    conn.execute(
                        "DELETE FROM candidate_state WHERE candidate_id = ? AND key = ?",
                        (candidate_id, CAREER_STAGE),
                    )
            return {"stage": None}

        if not isinstance(raw, str) or raw not in {stage.value for stage in CareerStage}:
            raise ApiError(
                400,
                "stage must be one of: " + ", ".join(stage.value for stage in CareerStage),
            )
        with _closing(self.connect()) as conn:
            candidate_id = ensure_candidate(conn)
            with transaction(conn):
                CandidateStateRepo(conn).set(candidate_id, CAREER_STAGE, raw)
        return {"stage": raw}

    def intake_discard(self, *, package_id: str, query: dict, body: dict) -> dict:
        """Put a package away. It is not a delete, and the wording says so.

        Two packages built from the same two documents are both open on the
        owner's machine, one superseded by the other, and nothing in the
        product could say which. `store.discard` flips the status; every row
        stays, because a REJECTED row is the only thing that stops its line
        being proposed again by the next import of the same documents, and
        every claim she confirmed left this package the moment she confirmed
        it.
        """
        del query, body
        from career_agent.intake import store

        key = _claim_key(package_id)
        with _closing(self.connect()) as conn:
            if conn.execute("SELECT 1 FROM intake_package WHERE id = ?", (key,)).fetchone() is None:
                raise ApiError(404, "no such package")
            store.discard(conn, key)
        return {"package_id": key, "status": "DISCARDED"}

    def intake_select(self, *, package_id: str, query: dict, body: dict) -> dict:
        """Put this reading of her documents in force. Reversible, and confirms
        nothing.

        The one act that answers "which package am I reviewing". It retires
        whatever was in force to SUPERSEDED -- not DISCARDED, because the
        product did this and she did not -- and every answer in both packages
        stays exactly where it was given.

        A 409 rather than a 400 when the package cannot be put in force: the
        request is well formed and the package is real; it is the STATE that
        refuses, and the message says which state and what to do about it.
        """
        del query, body
        from career_agent.intake import store
        from career_agent.intake.store import IntakeReviewError

        key = _claim_key(package_id)
        with _closing(self.connect()) as conn:
            try:
                store.select(conn, key)
            except IntakeReviewError as exc:
                if "no such package" in str(exc):
                    raise ApiError(404, str(exc)) from exc
                raise ApiError(409, str(exc), for_reader=True) from exc
            active = store.active_package(conn)
        return {"package_id": key, "status": "ACTIVE", "active_package_id": active}

    def intake_restore(self, *, package_id: str, query: dict, body: dict) -> dict:
        """Take a package back out of the drawer.

        It lands ACTIVE when nothing is in force and SUPERSEDED when something
        is -- see `store.restore` for why those are different answers rather
        than one convenient one. The status it landed in is returned, because
        a client that assumed ACTIVE would be wrong exactly when it matters.
        """
        del query, body
        from career_agent.intake import store
        from career_agent.intake.store import IntakeReviewError

        key = _claim_key(package_id)
        with _closing(self.connect()) as conn:
            try:
                landed = store.restore(conn, key)
            except IntakeReviewError as exc:
                if "no such package" in str(exc):
                    raise ApiError(404, str(exc)) from exc
                raise ApiError(409, str(exc), for_reader=True) from exc
            active = store.active_package(conn)
        return {"package_id": key, "status": landed, "active_package_id": active}

    def intake_delete(self, *, package_id: str, query: dict, body: dict) -> dict:
        """Remove a package for good. Without `confirm: true`, only the plan.

        The plan is what the interface shows before anything happens: how many
        unconfirmed claims go, and how many confirmed ones keep their rows as
        the provenance their evidence cites. `store.delete` does the rest.
        """
        del query
        from career_agent.intake import store
        from career_agent.intake.store import IntakeReviewError

        key = _claim_key(package_id)
        with _closing(self.connect()) as conn:
            try:
                if body.get("confirm") is not True:
                    return {"deleted": False, "plan": store.delete_plan(conn, key)}
                plan = store.delete(conn, key)
            except IntakeReviewError as exc:
                raise ApiError(404, str(exc)) from exc
            active = store.active_package(conn)
        return {"deleted": True, "plan": plan, "active_package_id": active}

    def intake_claims(self, *, package_id: str, query: dict, body: dict) -> dict:
        """ONE GROUP of claims, or one review state, never the whole package."""
        del body
        import json as _json

        from career_agent.intake.models import ReviewState
        from career_agent.intake.priority import ORDER
        from career_agent.web.api import _one

        key = _claim_key(package_id)
        group_id = _one(query, "group")
        state = _one(query, "state")
        step = _one(query, "step")
        if state is not None and state not in ReviewState.ALL:
            raise ApiError(400, f"unknown review state {state!r}")
        if step is not None and step not in ORDER:
            raise ApiError(400, f"unknown step {step!r}")
        if group_id is None and state is None and step is None:
            # THE REFUSAL STAYS, and a third selector does not weaken it. Each
            # of the three names a BOUNDED slice; what there is deliberately
            # no way to ask for is all 309 at once.
            raise ApiError(
                400,
                "ask for one group, one state or one step. There is deliberately no "
                "route that returns every claim at once.",
            )

        clauses = ["package_id = ?"]
        params: list[object] = [key]
        if state is not None:
            clauses.append("review_state = ?")
            params.append(state)

        with _closing(self.connect()) as conn:
            if conn.execute("SELECT 1 FROM intake_package WHERE id = ?", (key,)).fetchone() is None:
                raise ApiError(404, "no such package")
            rows = conn.execute(
                "SELECT * FROM intake_claim WHERE " + " AND ".join(clauses) + " ORDER BY claim_key",
                params,
            ).fetchall()
            # The step is DERIVED for the same reason the group is: it is a
            # decision about where a claim is MET, and storing it would freeze
            # one reading order into the schema and go stale the moment a
            # disagreement is settled.
            if step is not None:
                from career_agent.intake.priority import assign

                all_claims, _texts, unresolved = _priority_claims(conn, key)
                in_step = {
                    claim_key
                    for claim_key, assigned in assign(all_claims, unresolved=unresolved).items()
                    if assigned == step
                }

        claims: list[dict] = []
        matched = 0
        for row in rows:
            payload = _json.loads(str(row["payload_json"]))
            # The group is DERIVED rather than stored. It is a presentation
            # decision -- which claims belong on one screen -- and storing it
            # would freeze one answer to that into the schema.
            if group_id is not None and _group_id_of(row, payload) != group_id:
                continue
            if step is not None and str(row["claim_key"]) not in in_step:
                continue
            matched += 1
            # A page, and it SAYS it is a page. The cap is here because one
            # response is not a place to put a thousand rows; a cap nothing
            # reports is a screen quietly holding claims back, which is the
            # defect this whole surface exists to avoid.
            if len(claims) >= PAGE_OF_CLAIMS:
                continue
            period = payload.get("period") or {}
            claims.append(
                {
                    "claim_key": str(row["claim_key"]),
                    "claim_type": str(row["claim_type"]),
                    "text": payload.get("text"),
                    "corrected_text": row["corrected_text"],
                    "employer": payload.get("employer"),
                    "review_state": str(row["review_state"]),
                    "conflict_group": row["conflict_group"],
                    "sources": str(row["source_ref"]).split(","),
                    # PROVENANCE, in full. The quote or the locator the package
                    # cited, the wording the document used for a date and the
                    # reading of it, and the sentence a figure came in. A
                    # review whose citation appears only sometimes teaches
                    # people to stop looking for it.
                    "evidence": payload.get("evidence") or {},
                    "period": period,
                    "tools": payload.get("tools") or [],
                    "metrics": payload.get("metrics") or [],
                }
            )
        # A READING ORDER, not a hash order. `claim_key` is a digest, so
        # sorting by it interleaves "release 2, release 0, release 1" -- which
        # looks like the list is shuffling itself while she reads.
        #
        # What needs her most comes first: a disputed period, then the role
        # itself, then everything said about it, alphabetically so the order
        # is stable between page loads.
        rank = {"EMPLOYMENT": 0, "ACHIEVEMENT": 1, "PROJECT": 2}
        claims.sort(
            key=lambda claim: (
                0 if claim["conflict_group"] else 1,
                rank.get(claim["claim_type"], 3),
                (claim["text"] or "").casefold(),
            )
        )
        return {
            "package_id": key,
            "claims": claims,
            "matched": matched,
            "truncated": matched > len(claims),
        }

    def intake_priority(self, *, package_id: str, query: dict, body: dict) -> dict:
        """WHERE TO START. Every step, with counts nobody had to invent.

        The grouped overview already stops the screen being a wall of 309
        sentences. This answers the question underneath it: of these three
        hundred, which ones matter before the product is usable at all.

        It returns COUNTS and STEP KEYS, never claims. A route that returned
        "the important ones" would be the wall again with a smaller number on
        it, and it would be this program deciding which of her statements are
        worth reading. `intake_claims?step=` fetches one step, one page at a
        time, exactly as `group` and `state` already do.

        `focus` is a LENS rather than a step: the claims that mention a
        requirement she arrived from are already recent work, or outcomes, or
        skills, and stealing them into a step of their own would break the
        partition the counts depend on.
        """
        del body
        from career_agent.intake.priority import focus_matches, plan
        from career_agent.web.api import _one

        key = _claim_key(package_id)
        term = _one(query, "term")
        with _closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM intake_package WHERE id = ?", (key,)).fetchone()
            if row is None:
                raise ApiError(404, "no such package")
            claims, texts, unresolved = _priority_claims(conn, key)

        payload = plan(claims, unresolved=unresolved)
        payload["package_id"] = key
        payload["is_active"] = str(row["status"]) == "ACTIVE"
        # WHAT SHE CAME FOR, when she came from a requirement. A count and the
        # term itself, so the screen can say what it is filtering by rather
        # than quietly showing a subset.
        matches = focus_matches(claims, texts, term) if term else ()
        payload["focus"] = {
            "term": term,
            "total": len(matches),
            "waiting": sum(
                1 for claim in claims if claim.claim_key in set(matches) and claim.waiting
            ),
        }
        return payload

    def intake_answer(self, *, package_id: str, query: dict, body: dict) -> dict:
        """One claim, one answer. The only way a proposal becomes a fact.

        Committed on its own, exactly as `decide_proposal` is: a review that
        saved only at the end would lose an hour of decisions to a closed tab.
        """
        del query
        from career_agent.intake import store

        key = _claim_key(package_id)
        claim_key = _claim_key(str(body.get("claim_key") or ""))
        answer = body.get("answer")
        if answer not in {"CONFIRM", "CORRECT", "REJECT", "UNRESOLVED", "REOPEN"}:
            raise ApiError(400, "answer must be CONFIRM, CORRECT, REJECT, UNRESOLVED or REOPEN")
        text = _text(body, "text", limit=MAX_CLAIM_TEXT, required=answer == "CORRECT")

        with _closing(self.connect()) as conn:
            try:
                if answer == "REJECT":
                    store.reject(conn, key, claim_key)
                elif answer == "UNRESOLVED":
                    store.mark_unresolved(conn, key, claim_key)
                elif answer == "REOPEN":
                    store.reopen(conn, key, claim_key)
                else:
                    store.confirm(conn, key, claim_key, text=text)
            except store.IntakeReviewError as exc:
                # A rule she can act on -- "that is already a claim you stand
                # behind" -- reaching the catch-all would surface as "internal
                # error, see the terminal". A user's mistake and a server's bug
                # must not look the same.
                raise ApiError(400, str(exc)) from exc
            counts = store.summary(conn, key)
        return {"package_id": key, "claim_key": claim_key, "counts": counts}

    def intake_resolve(self, *, package_id: str, query: dict, body: dict) -> dict:
        """A disagreement, answered ONCE, releasing every claim it held."""
        del query
        from career_agent.intake import store

        key = _claim_key(package_id)
        group = str(body.get("group") or "").strip()
        if not group:
            raise ApiError(400, "group is required")

        reopen = bool(body.get("reopen"))
        chosen = _claim_key(str(body.get("claim_key") or "")) if not reopen else ""

        with _closing(self.connect()) as conn:
            try:
                if reopen:
                    store.reopen_conflict(conn, key, group)
                    released = 0
                else:
                    released = store.resolve_conflict(conn, key, group, chosen)
            except store.IntakeReviewError as exc:
                raise ApiError(400, str(exc)) from exc
            counts = store.summary(conn, key)
            conflicts = store.conflict_groups(conn, key)
        return {"package_id": key, "released": released, "counts": counts, "conflicts": conflicts}

    def cv_imports(self, *, query: dict, body: dict) -> dict:
        from career_agent.storage.review_counts import review_counts
        from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of

        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            staged = CvReviewRepo(conn).imports(candidate_id) if candidate_id else []
            counts = review_counts(conn, candidate_id)
        return {
            "imports": [
                {
                    "import_id": item.import_id,
                    "source_name": item.source_name,
                    "kind": item.kind,
                    "pages": item.pages,
                    "characters": item.characters,
                    "status": item.status,
                    "created_at": item.created_at,
                    "total": item.total,
                    "pending": item.pending,
                    "accepted": item.accepted,
                    "edited": item.edited,
                    "rejected": item.rejected,
                    "confirmed": item.confirmed,
                    "entries": item.entries,
                    # Put away, reversibly. Its suggestions wait nowhere.
                    "archived": item.archived,
                    "archived_at": item.archived_at,
                }
                for item in staged
            ],
            "counts": counts.as_dict(),
            "supported": [".pdf", ".docx", ".txt", ".md"],
            "privacy": (
                "Your CV is read by Career Agent on this computer. It is not uploaded "
                "anywhere, no model of any kind sees it, and the file itself is never "
                "stored -- only the lines you confirm."
            ),
        }

    def cv_import(self, *, query: dict, body: dict) -> dict:
        """Read one CV and stage what it appears to say. Confirms nothing.

        The file arrives base64-encoded inside a JSON body, and that is a
        security decision rather than a convenience. `server._check_origin`
        requires `application/json` on every body precisely because a
        cross-origin form CAN send multipart without a preflight and CANNOT
        send JSON. Accepting a multipart upload here would have reopened the
        hole that check exists to close.

        The bytes are parsed in memory and dropped. Nothing is written to disk
        except the jobs the reader found, the proposals, and the lines they
        were read from.
        """
        from career_agent.cv.extract import CvError, extract_bytes
        from career_agent.cv.propose import read_cv
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import CvReviewRepo, ensure_candidate

        filename = _text(body, "filename", limit=300)
        assert filename is not None
        raw = body.get("content_base64")
        if not isinstance(raw, str) or not raw:
            raise ApiError(400, "content_base64 is required")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError(400, "content_base64 is not valid base64") from exc

        try:
            found = extract_bytes(data, filename)
        except CvError as exc:
            # The extractor's messages name the file and never quote it. Safe
            # to pass through, and far more useful than "could not read".
            raise ApiError(400, str(exc), for_reader=True) from exc

        if found.looks_empty:
            raise ApiError(
                422,
                f"Almost no text came out of {found.source_name}. If this is a scanned "
                "CV its pages are images, and there is nothing here to read.",
                for_reader=True,
            )

        read = read_cv(found.text)
        if not read.proposals and not read.entries:
            raise ApiError(
                422,
                f"{found.source_name} was read, and no section this recognises came out "
                "of it. It looks for headings: Experience, Skills, Education, "
                "Certifications, Projects, Volunteering, Internships, Activities and "
                "Awards, in English or Portuguese.",
                for_reader=True,
            )

        with _closing(self.connect()) as conn:
            candidate_id = ensure_candidate(conn)
            repo = CvReviewRepo(conn)
            earlier = repo.seen_before(candidate_id, found.text)
            with transaction(conn):
                import_id = repo.stage(
                    candidate_id,
                    source_name=found.source_name,
                    kind=found.kind,
                    pages=found.pages,
                    characters=found.characters,
                    text=found.text,
                    proposals=read.proposals,
                    entries=read.entries,
                )

        payload = self.cv_review(import_id=import_id, query={}, body={})
        payload["seen_before"] = [
            {
                "import_id": str(row["id"]),
                "created_at": str(row["created_at"]),
                "archived": _column(row, "archived_at") is not None,
            }
            for row in earlier
        ]
        payload["unread_lines"] = len(read.unread_lines)
        return payload

    def cv_review(self, *, import_id: str, query: dict, body: dict) -> dict:
        """One staged read: a summary, its jobs, and every proposal under them.

        THE SHAPE IS THE REVIEW DESIGN. A long CV is a hundred or more
        suggestions, and a hundred cards is a wall nobody finishes. So the read
        comes back as JOBS -- company, role, dates, how many wait in each --
        with the suggestions under the job they belong to, and the ones that
        belong to no job (skills, education) under their own section. The
        interface opens on the summary and the list of jobs, never on the
        cards.
        """
        from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of

        key = _claim_key(import_id)
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such import")
            repo = CvReviewRepo(conn)
            record = repo.get_import(candidate_id, key)
            if record is None:
                raise ApiError(404, "no such import")
            rows = repo.proposals(key)
            entry_rows = repo.entries(key)

        from career_agent.web.cv_api import cv_review_payload

        return cv_review_payload(record, rows, entry_rows)

    def decide_proposal(self, *, import_id: str, query: dict, body: dict) -> dict:
        """Accept, edit, or reject one proposal.

        ACCEPT stores the line as it stands. EDIT stores the person's own
        wording and keeps the CV line as the evidence -- `to_claim` takes the
        edited text and the ORIGINAL proposal, so the two never merge. REJECT
        records that this line of this document was not wanted and creates
        nothing anywhere.

        A confirmed claim carries the company and the dates of the job it sits
        under, as the review showed them (and as she corrected them). Only
        dates stated to the month reach the claim; a year-only span stays on
        the job, because a claim's period is a month and inventing January
        would be this code writing a fact.

        One proposal per request, committed on its own. A review that saved
        only at the end would lose an hour of decisions to a closed tab, and
        section 11 of the V1.3 brief is explicit that accepted items must
        survive.
        """
        from career_agent.cv.propose import Proposal, to_claim
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of

        key = _claim_key(import_id)
        claim_key = _claim_key(str(body.get("claim_key") or ""))
        decision = body.get("decision")
        if decision not in {"ACCEPTED", "EDITED", "REJECTED", "PENDING"}:
            raise ApiError(400, "decision must be ACCEPTED, EDITED, REJECTED or PENDING")
        edited = _text(body, "text", limit=MAX_CLAIM_TEXT, required=decision == "EDITED")

        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such import")
            repo = CvReviewRepo(conn)
            record = repo.get_import(candidate_id, key)
            if record is None:
                raise ApiError(404, "no such import")
            if record["archived_at"] is not None:
                raise ApiError(
                    409, "This CV read is archived. Restore it before answering.", for_reader=True
                )
            row = repo.proposal(key, claim_key)
            if row is None:
                raise ApiError(404, "no such proposal")
            if str(row["decision"]) in {"ACCEPTED", "EDITED"} and decision in {
                "REJECTED",
                "PENDING",
            }:
                # Withdrawing evidence is retiring it, in Career Evidence, where
                # it is a revision with its history. A review answer that
                # quietly un-confirmed a fact would leave the claim verified.
                raise ApiError(
                    409,
                    "That suggestion is confirmed evidence now. Retire it in Career "
                    "Evidence if it is no longer true.",
                    for_reader=True,
                )

            with transaction(conn):
                if decision in {"REJECTED", "PENDING"}:
                    repo.record_decision(key, claim_key, decision=decision)
                else:
                    proposal = Proposal(
                        claim_key=str(row["claim_key"]),
                        claim_type=ClaimType(str(row["claim_type"])),
                        text=str(row["text"]),
                        section=str(row["section"]),
                        evidence=str(row["evidence"]),
                        has_measurement=bool(row["has_measurement"]),
                    )
                    entry = (
                        repo.entry(key, str(row["entry_key"]))
                        if _column(row, "entry_key")
                        else None
                    )
                    stored_text = edited if decision == "EDITED" else None
                    claim = to_claim(
                        proposal,
                        text=stored_text,
                        employer=entry["company"] if entry is not None else None,
                        period_start=entry["period_start"] if entry is not None else None,
                        period_end=entry["period_end"] if entry is not None else None,
                    )
                    claim_id = ClaimRepo(conn).supersede(candidate_id, claim)
                    repo.record_decision(
                        key,
                        claim_key,
                        decision=decision,
                        decided_text=claim.text,
                        claim_id=claim_id,
                    )
                repo.close_if_finished(key)

        return self.cv_review(import_id=key, query={}, body={})

    def discard_import(self, *, import_id: str, query: dict, body: dict) -> dict:
        """ARCHIVE a read. Reversible, and every row stays.

        This route hard-deleted the read and all its proposals, including the
        rows her confirmed claims cite as provenance, while the button promised
        only "discard". It archives now; `/delete` is the permanent act and it
        shows what it will remove before it does.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of

        key = _claim_key(import_id)
        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if candidate_id is None:
                raise ApiError(404, "no such import")
            with transaction(conn):
                if not CvReviewRepo(conn).archive(candidate_id, key):
                    raise ApiError(404, "no such import")
        return self.cv_imports(query={}, body={})

    # =================================================================
    # the daily digest
    # =================================================================
    def home(self, *, query: dict, body: dict) -> dict:
        """Where the job search stands today.

        Counts and two short lists, and every figure declares whether it is a
        STOCK -- how many things are in a state now -- or an EVENT COUNT --
        how many times something happened since she last looked. Mixing the
        two under one heading is how "5 interviews" comes to mean either five
        conversations or five moves depending on who wrote the query.

        The lists are the digest's own sections, so the dashboard cannot
        develop a second opinion about what is worth reading. Nothing here
        ranks anything.
        """
        from career_agent.digest import default_filter, sections
        from career_agent.home import metrics, profile_gaps
        from career_agent.storage.mvp_repo import ScoredJobQuery
        from career_agent.storage.workspace_repo import (
            LAST_REVIEWED_AT,
            CandidateStateRepo,
            candidate_id_of,
        )
        from career_agent.web.presenter import job_card, utc_today

        show = 5
        config = self.search_config()
        config_id, config_version = self._identity()

        with _closing(self.connect()) as conn:
            repo = ScoredJobQuery(conn)
            candidate_id = candidate_id_of(conn)
            seen_at = (
                CandidateStateRepo(conn).get(candidate_id, LAST_REVIEWED_AT)
                if candidate_id
                else None
            )
            cards = metrics(
                conn,
                last_reviewed_at=seen_at,
                config_id=config_id,
                config_version=config_version,
            )
            gaps = profile_gaps(conn, config)
            total = repo.count(config_id, config_version, default_filter(show))
            open_jobs = conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[
                0
            ]

            # Two of the digest's sections, not a third list invented here.
            wanted = {"since_last_review", "recent", "best"}
            built = []
            for section in sections(config, show=show, last_reviewed_at=seen_at):
                if section.key not in wanted:
                    continue
                rows = repo.page(config_id, config_version, section.job_filter)
                built.append(
                    {
                        "key": section.key,
                        "title": section.title,
                        "lead": section.lead,
                        "count": len(rows),
                        "items": [
                            job_card(
                                row,
                                bands=self._bands,
                                today=utc_today(),
                                recency=self._recency,
                            )
                            for row in rows[:show]
                        ],
                    }
                )

        return {
            "total": total,
            "job_count": int(open_jobs),
            "last_reviewed_at": seen_at,
            "metrics": [metric.as_dict() for metric in cards],
            "sections": built,
            "profile_gaps": gaps,
        }

    def daily(self, *, query: dict, body: dict) -> dict:
        """What is worth looking at today. The same sections the terminal has.

        Built from `career_agent.digest`, so the two cannot drift into
        disagreeing about what "new" means -- and the disagreement would have
        been invisible, because each would have been internally consistent.

        Nothing here ranks anything. Every section is a filter over the score
        the cards already show, and the rows come back in that order.
        """
        from career_agent.digest import default_filter, sections
        from career_agent.storage.mvp_repo import MatchRepo, ScoredJobQuery
        from career_agent.storage.workspace_repo import (
            LAST_REVIEWED_AT,
            CandidateStateRepo,
            candidate_id_of,
        )
        from career_agent.web.presenter import job_card, utc_today

        show = 8
        config = self.search_config()
        config_id, config_version = self._identity()

        with _closing(self.connect()) as conn:
            repo = ScoredJobQuery(conn)
            candidate_id = candidate_id_of(conn)
            seen_at = (
                CandidateStateRepo(conn).get(candidate_id, LAST_REVIEWED_AT)
                if candidate_id
                else None
            )
            total = repo.count(config_id, config_version, default_filter(show))
            # How much of the corpus this digest could see at all. Without
            # these two numbers an empty digest after a settings change reads
            # as "nothing is worth looking at today", when the truth is that
            # the old scores answered the old question and nothing has been
            # recalculated yet -- which the job list already says and the
            # digest did not.
            open_jobs = conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[
                0
            ]
            scored = MatchRepo(conn).count_for(config_id, config_version)
            built = []
            for section in sections(config, days=DIGEST_DAYS, show=show, last_reviewed_at=seen_at):
                rows = repo.page(config_id, config_version, section.job_filter)
                built.append(
                    {
                        "key": section.key,
                        "title": section.title,
                        "lead": section.lead,
                        "count": len(rows),
                        "items": [
                            job_card(
                                row,
                                bands=self._bands,
                                today=utc_today(),
                                recency=self._recency,
                            )
                            for row in rows[:show]
                        ],
                    }
                )

        return {
            "total": total,
            "last_reviewed_at": seen_at,
            "days": DIGEST_DAYS,
            "job_count": int(open_jobs),
            "scored_count": int(scored),
            "sections": built,
        }

    def mark_reviewed(self, *, query: dict, body: dict) -> dict:
        """Record that the person has read the list, now.

        Written only when she says so. A digest that marked itself read every
        time it was drawn would make "since you last looked" mean "since this
        page last loaded", which is a fact about a render and not about a
        reader -- and it would empty the section she opened the page for.

        THIS IS NOT AN EMPLOYER'S DATE. `job.posted_at` is what a board
        published and `job.first_seen_at` is when this machine first held it;
        neither moves when somebody reads a screen.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import (
            LAST_REVIEWED_AT,
            CandidateStateRepo,
            ensure_candidate,
        )

        with _closing(self.connect()) as conn:
            candidate_id = ensure_candidate(conn)
            with transaction(conn):
                CandidateStateRepo(conn).set(candidate_id, LAST_REVIEWED_AT, _now())
        return self.daily(query={}, body={})

    # =================================================================
    # preparation
    # =================================================================
    def preparation(self, *, job_id: str, query: dict, body: dict) -> dict:
        """What this posting asks for, beside what has been confirmed.

        Every requirement the posting fired, in every state. There is no
        parameter that filters to the matches, because a view that flatters is
        the failure this whole surface exists to avoid.
        """
        from career_agent.match.preparation import Readiness, prepare
        from career_agent.storage.mvp_repo import MatchRepo
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import RequirementReviewRepo, candidate_id_of

        validate_job_id(job_id)
        config = self.search_config()
        config_id, config_version = self._identity()

        with _closing(self.connect()) as conn:
            result = MatchRepo(conn).get(job_id, config_id, config_version)
            if result is None:
                raise ApiError(
                    409,
                    "This posting has no score under the settings in force. "
                    "Recalculate matches and try again.",
                    for_reader=True,
                )
            candidate_id = candidate_id_of(conn)
            claims = ClaimRepo(conn).current(candidate_id) if candidate_id else []
            reviews = (
                RequirementReviewRepo(conn).for_job(candidate_id, job_id) if candidate_id else {}
            )

        plan = prepare(config, result, claims)
        confirmed = [claim for claim in claims if claim.verified]
        # Where each confirmed line came from, so a row asserting a match can
        # say WHOSE sentence answered it. Section 5 of the V1.3 brief: an
        # evidence match that does not show what the evidence actually is, and
        # where it came from, is an assertion asking to be trusted.
        provenance: dict[str, tuple[str | None, str | None]] = {
            claim.claim_key: (claim.source.value, claim.evidence_ref) for claim in claims
        }
        nowhere: tuple[str | None, str | None] = (None, None)

        requirements = []
        for requirement in plan.requirements:
            review = reviews.get(requirement.signal_id)
            requirements.append(
                {
                    "signal_id": requirement.signal_id,
                    "label": requirement.label,
                    "readiness": requirement.readiness.value,
                    "posting_quote": requirement.posting_quote,
                    "evidence_text": requirement.evidence_text,
                    "evidence_key": requirement.evidence_key,
                    "evidence_source": provenance.get(requirement.evidence_key or "", nowhere)[0],
                    # The document line behind the claim, when the claim came
                    # from one and says something different. An edited claim is
                    # the person's wording over the CV's line, and a reader
                    # preparing for an interview should see both.
                    "evidence_origin": provenance.get(requirement.evidence_key or "", nowhere)[1],
                    "matched_on": requirement.matched_on,
                    "review": (
                        {
                            "verdict": str(review["verdict"]),
                            "note": review["note"],
                            "updated_at": str(review["updated_at"]),
                        }
                        if review is not None
                        else None
                    ),
                }
            )

        counts = {readiness.value: len(plan.of(readiness)) for readiness in Readiness}
        return {
            "job_id": job_id,
            "requirements": requirements,
            "counts": counts,
            "total": len(plan.requirements),
            "concerns": [
                {
                    "kind": concern.kind,
                    "detail": concern.detail,
                    "quote": concern.quote,
                    "code": concern.code,
                    "params": concern.params or {},
                }
                for concern in plan.concerns
            ],
            "evidence_available": len(confirmed),
            "has_candidate": candidate_id is not None,
            "readiness_meaning": READINESS_MEANING,
            "verdict_meaning": VERDICT_MEANING,
        }

    def resume(self, *, job_id: str, query: dict, body: dict) -> dict:
        """Which of her own sentences this posting argues for, and in what order.

        SELECTION AND ORDER, NEVER NEW WORDS. Every string in this response
        came from a claim the candidate confirmed or from a requirement label
        in her own configuration. Nothing is rewritten, summarised or
        strengthened, and there is no model of either kind anywhere near it.

        The gaps come back in the same object as the suggestions. A resume
        workspace that returned only the strengths would be the flattering
        view the whole preparation surface exists to refuse.
        """
        from career_agent.match.preparation import prepare
        from career_agent.match.resume import plan
        from career_agent.match.resume_export import as_text
        from career_agent.storage.mvp_repo import MatchRepo
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        validate_job_id(job_id)
        config = self.search_config()
        config_id, config_version = self._identity()

        with _closing(self.connect()) as conn:
            result = MatchRepo(conn).get(job_id, config_id, config_version)
            if result is None:
                raise ApiError(
                    409,
                    "This posting has no score under the settings in force. "
                    "Recalculate matches and try again.",
                    for_reader=True,
                )
            candidate_id = candidate_id_of(conn)
            claims = ClaimRepo(conn).current(candidate_id) if candidate_id else []
            # The employer's own words, for the file's heading. Read here
            # rather than passed in, so the export names the posting it is
            # about and cannot be handed a different one.
            job = conn.execute(
                "SELECT j.title, c.name AS company FROM job j"
                " JOIN company c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,),
            ).fetchone()

        built = plan(prepare(config, result, claims), claims)
        return {
            "job_id": job_id,
            "counts": built.counts,
            "lead_with": [
                {
                    "claim_key": suggestion.claim.claim_key,
                    "text": suggestion.claim.text,
                    "claim_type": suggestion.claim.claim_type.value,
                    "source": suggestion.claim.source.value,
                    "evidence": suggestion.claim.evidence_ref,
                    "answers": list(suggestion.answers),
                    "strength": suggestion.strength.value,
                }
                for suggestion in built.lead_with
            ],
            "not_relevant": [
                {
                    "claim_key": claim.claim_key,
                    "text": claim.text,
                    "claim_type": claim.claim_type.value,
                }
                for claim in built.not_relevant
            ],
            "gaps": list(built.gaps),
            "unresolved": list(built.unresolved),
            # THE SAME SELECTION, AS SOMETHING SHE CAN TAKE AWAY. Sent inside
            # the JSON rather than as its own `text/plain` route: the browser
            # already has the payload, and a second route would be a second
            # place for the two to disagree about what the plan is.
            #
            # It is not a resume and the file says so in its own last line.
            "export_text": as_text(
                built,
                title=str(job["title"]) if job else job_id,
                company=str(job["company"]) if job else "",
            ),
        }

    def review_requirement(self, *, job_id: str, signal_id: str, query: dict, body: dict) -> dict:
        """Record what the person thinks of one requirement/evidence pair.

        Stored apart from both sides and consulted by neither. It changes no
        score, no claim and no quote -- it is a margin note on a mapping this
        system proposed, and the mapping stays visible beside it so a reader
        can see what was disagreed with.

        Sending `null` withdraws the verdict.
        """
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import (
            VERDICTS,
            RequirementReviewRepo,
            ensure_candidate,
        )

        validate_job_id(job_id)
        key = _claim_key(signal_id)
        if "verdict" not in body:
            raise ApiError(400, "verdict is required; send null to withdraw one")
        verdict = body["verdict"]
        if verdict is not None and verdict not in VERDICTS:
            allowed = ", ".join(sorted(VERDICTS))
            raise ApiError(400, f"verdict must be null or one of: {allowed}")
        note = _text(body, "note", limit=MAX_NOTE, required=False)

        with _closing(self.connect()) as conn:
            candidate_id = ensure_candidate(conn)
            repo = RequirementReviewRepo(conn)
            with transaction(conn):
                if verdict is None:
                    repo.clear(candidate_id, job_id, key)
                else:
                    repo.set(candidate_id, job_id, key, verdict=verdict, note=note)
        return self.preparation(job_id=job_id, query={}, body={})


def _first_search_phrases(config: object) -> tuple[list[str], list[str]]:
    """The kinds of work and the skills the setup wrote, as she typed them.

    The setup files a kind of work as a lexicon entry under
    `responsibility_other` and a skill as an entry weighted in the
    technologies component; anything else in the lexicon came from somewhere
    else and is not an answer she gave here.
    """
    lexicon = getattr(config, "lexicon", {}) or {}
    scoring = getattr(config, "scoring", None)
    weights = scoring.components.technologies.weights if scoring is not None else {}
    roles: list[str] = []
    skills: list[str] = []
    for key, entry in lexicon.items():
        label = str(getattr(entry, "label", "") or key)
        responsibility = getattr(entry, "responsibility", None)
        if str(getattr(responsibility, "value", responsibility) or "") == "responsibility_other":
            roles.append(label)
        elif key in weights:
            skills.append(label)
    return roles[:20], skills[:20]


def _regions_worth_asking(home: str, confirmed: Iterable[str]) -> list[str]:
    """Regions containing `home` that contain none of the confirmed countries."""
    from career_agent.match.places import region_contains

    codes = [str(code).upper() for code in confirmed]
    return [
        region
        for region in _regions_containing(home)
        if not any(region_contains(region, code) is True for code in codes)
    ]


def _regions_containing(country: str) -> list[str]:
    from career_agent.config.candidate_writer import FIELDS
    from career_agent.match.places import region_contains

    return [
        region
        for region in FIELDS["eligible_scopes"]["choices"]
        if region_contains(region, country) is True
    ]
