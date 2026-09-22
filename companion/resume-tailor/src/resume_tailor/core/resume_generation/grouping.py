# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Presentation-only grouping of consecutive roles at the same employer.

The generated resume keeps one ``ExperienceEntry`` per Position (titles, dates,
bullets and evidence bindings stay attached to their own position). Exporters
call ``group_experience`` to render consecutive entries with the same employer
identity (company name and location) under one company heading with a single
descriptor, each role keeping its own title and dates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from resume_tailor.core.models import ExperienceEntry


@dataclass
class CompanyGroup:
    company: str
    location: str
    start: str
    end: str | None
    company_blurb: str
    roles: list[ExperienceEntry] = field(default_factory=list)

    @property
    def grouped(self) -> bool:
        return len(self.roles) > 1


def _identity(e: ExperienceEntry) -> tuple[str, str]:
    return (e.company.strip().lower(), e.location.strip().lower())


def group_experience(entries: list[ExperienceEntry]) -> list[CompanyGroup]:
    groups: list[CompanyGroup] = []
    for e in entries:
        if groups and _identity(groups[-1].roles[-1]) == _identity(e):
            g = groups[-1]
            g.roles.append(e)
            g.start = min(g.start, e.start)
            g.end = None if (g.end is None or e.end is None) else max(g.end, e.end)
            g.company_blurb = g.company_blurb or e.company_blurb
        else:
            groups.append(
                CompanyGroup(
                    company=e.company,
                    location=e.location,
                    start=e.start,
                    end=e.end,
                    company_blurb=e.company_blurb,
                    roles=[e],
                )
            )
    return groups
