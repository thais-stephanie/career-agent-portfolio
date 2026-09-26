"""What Resume Tailor may know about the active local profile, and nothing more.

Resume Tailor runs in the same process as Career Agent, on its own port. It
used to know nothing about Career Agent: a person copied a posting into it,
kept a second "candidate" there under a second name, and tracked the same
applications twice. This is the one door between the two, bound to ONE
profile and that profile's app:

    profile()        which local profile this is (id and label)
    job(id)          one posting: title, company, link, description, status
    tracked_jobs()   the profile's postings with an application status
    set_status()     change a status, through Career Agent's own route
    evidence()       the Career Profile: experiences and CONFIRMED statements
    statuses()       the application vocabulary, with its on-screen words

Career Agent stays the source of truth for application state and evidence;
Resume Tailor reads them and writes only a status, through the same route the
Career Agent page uses. Nothing unconfirmed crosses: a suggestion still
waiting for review is not evidence. When the profile is switched away the
bridge refuses every call (`BridgeRetired`), so a Tailor page left open on an
old profile can never read or write the new one.

Resume Tailor never imports Career Agent: `create_app(bridge=...)` receives
this object and uses it through a small protocol of its own
(`resume_tailor.integration.career`).
"""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING, Any

from career_agent.domain.application import ApplicationStatus

if TYPE_CHECKING:
    from career_agent.runtime.profiles import Profile
    from career_agent.web.api import JobsApi

#: The words Career Agent's own interface uses (i18n `status.*`, English).
STATUS_WORDS: dict[str, str] = {
    "DISCOVERED": "Found",
    "SHORTLISTED": "Interested",
    "APPLIED": "Applied",
    "INTERVIEW": "Interviewing",
    "OFFER": "Offer",
    "HIRED": "Hired",
    "REJECTED": "Rejected",
    "WITHDRAWN": "Withdrawn",
    "ARCHIVED": "Archived",
}

#: A description longer than this is cut, and says so. Postings are rarely a
#: tenth of it; the bound only protects the page from a pathological row.
MAX_DESCRIPTION_CHARS = 60_000


class BridgeRetired(RuntimeError):
    """This profile is no longer the active one."""


class BridgeNotFound(LookupError):
    """No such posting in this profile."""


class CareerBridge:
    def __init__(self, api: JobsApi, profile: Profile) -> None:
        self._api = api
        self._profile = profile

    # -- identity ----------------------------------------------------------
    def _live(self) -> None:
        if getattr(self._api, "retired", False):
            raise BridgeRetired(
                "Career Agent switched to another local profile. Reload Resume Tailor."
            )

    def profile(self) -> dict[str, str]:
        self._live()
        return {"id": self._profile.id, "label": self._profile.label}

    def statuses(self) -> list[dict[str, str]]:
        return [
            {"value": status.value, "label": STATUS_WORDS.get(status.value, status.value)}
            for status in ApplicationStatus
        ]

    # -- postings ------------------------------------------------------------
    def _row(self, conn: Any, job_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT j.id, j.title, j.url, j.closed_at, c.name AS company,"
            "       r.description_text, a.status, a.applied_at, a.updated_at"
            " FROM job j JOIN company c ON c.id = j.company_id"
            " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
            " LEFT JOIN job_application a ON a.job_id = j.id"
            " WHERE j.id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _shape(row: dict[str, Any], *, with_description: bool) -> dict[str, Any]:
        status = str(row.get("status") or ApplicationStatus.DISCOVERED.value)
        out: dict[str, Any] = {
            "job_id": str(row["id"]),
            "title": str(row["title"] or ""),
            "company": str(row["company"] or ""),
            "url": str(row["url"] or ""),
            "closed": row.get("closed_at") is not None,
            "status": status,
            "status_label": STATUS_WORDS.get(status, status),
            "applied_at": row.get("applied_at"),
            "updated_at": row.get("updated_at"),
        }
        if with_description:
            text = str(row.get("description_text") or "")
            out["description"] = text[:MAX_DESCRIPTION_CHARS]
            out["description_cut"] = len(text) > MAX_DESCRIPTION_CHARS
        return out

    def job(self, job_id: str) -> dict[str, Any]:
        from career_agent.web.server import JOB_ID_PATTERN

        self._live()
        if not JOB_ID_PATTERN.match(job_id or ""):
            raise ValueError("That is not a Career Agent posting id.")
        with closing(self._api.connect()) as conn:
            row = self._row(conn, job_id)
        if row is None:
            raise BridgeNotFound("This posting is not in the active Career Agent profile.")
        return self._shape(row, with_description=True)

    def tracked_jobs(self) -> list[dict[str, Any]]:
        """Every posting this profile gave a status, newest change first.
        Found (never touched) is the corpus, not an application."""
        self._live()
        with closing(self._api.connect()) as conn:
            rows = conn.execute(
                "SELECT j.id, j.title, j.url, j.closed_at, c.name AS company,"
                "       a.status, a.applied_at, a.updated_at"
                " FROM job_application a JOIN job j ON j.id = a.job_id"
                " JOIN company c ON c.id = j.company_id"
                " WHERE a.status <> 'DISCOVERED'"
                " ORDER BY a.updated_at DESC, j.id"
            ).fetchall()
        return [self._shape(dict(row), with_description=False) for row in rows]

    def set_status(self, job_id: str, status: str) -> dict[str, Any]:
        """Through Career Agent's own route: its validation, dates and
        history apply exactly as when the person changes it there."""
        from career_agent.web.server import ApiError

        self._live()
        try:
            self._api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": status})
        except ApiError as exc:
            if exc.status == 404:
                raise BridgeNotFound(str(exc)) from exc
            raise ValueError(str(exc)) from exc
        return self.job(job_id)

    # -- evidence ------------------------------------------------------------
    def evidence(self) -> dict[str, Any]:
        """The Career Profile as Resume Tailor may use it: experiences with
        their CONFIRMED statements and skills, verbatim. Nothing waiting for
        review, nothing retired, nothing rephrased."""
        from career_agent.storage.career_repo import CareerRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        self._live()
        with closing(self._api.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            name = None
            if candidate_id is not None:
                row = conn.execute(
                    "SELECT display_name FROM candidate WHERE id = ?", (candidate_id,)
                ).fetchone()
                name = str(row[0]) if row is not None else None
            overview = CareerRepo(conn, candidate_id).overview() if candidate_id else {}
        experiences = []
        for entry in overview.get("experiences", []):
            highlights = [
                {"key": str(h["claim_key"]), "text": str(h["text"]), "origin": str(h["origin"])}
                for h in entry.get("highlights", [])
                if str(h.get("text") or "").strip()
            ]
            experiences.append(
                {
                    "id": str(entry["id"]),
                    "company": entry.get("company") or "",
                    "title": entry.get("title") or "",
                    "start": entry.get("period_start"),
                    "end": entry.get("period_end"),
                    "current": bool(entry.get("current_role")),
                    "kind": entry.get("kind") or "EMPLOYMENT",
                    "highlights": highlights,
                    "skills": list(entry.get("skills") or []),
                }
            )
        return {
            "profile": {"id": self._profile.id, "label": self._profile.label},
            "name": name or self._profile.label,
            "experiences": experiences,
        }


def tailor_app(home: Any, profile: Profile, api: JobsApi) -> Any:
    """Resume Tailor for `profile`: its own workspace, following this app."""
    from resume_tailor.api.app import create_app

    return create_app(home=home, bridge=CareerBridge(api, profile))
