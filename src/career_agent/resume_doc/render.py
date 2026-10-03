"""The one resume renderer: ResumeDocument -> one self-contained HTML document.

The live preview shows this document and the PDF export (a later change)
prints it; there is no second template in JavaScript or for print. A pure
function: no database, no clock, no randomness, no network.

* Semantic, single-column HTML with real text: header, section, h1-h3, p,
  ul/li, a. No tables, images, canvas or absolute positioning for content.
* Every rendered piece of content carries `data-ref`, the path to the
  ResumeDocument object it shows (`experience/<id>/bullet/<id>`), never a
  position, so a click in the preview can find its field.
* Every keep-together unit carries `data-block`; a unit that must stay with
  the next one (a heading, an entry header) also carries `data-keep`. The
  print CSS and the preview paginator read the same two attributes.
* Every value is HTML-escaped. The only markup a field may produce is the
  model's inline `**bold**`, applied after escaping.
* The document carries a Content-Security-Policy that forbids every network
  load. In `preview` mode links are drawn as text and are not followable.

Templates are design only. They change appearance through CSS variables over
one shared stylesheet, and never what the resume says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Literal

from career_agent.resume_doc.models import (
    DEFAULT_ORDER,
    Bullet,
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    PartialDate,
    ProjectEntry,
    ResumeDocument,
    SkillGroup,
    TextBlock,
)

Mode = Literal["preview", "print"]

#: Physical page sizes in millimetres.
PAGE_MM = {"A4": (210.0, 297.0), "LETTER": (215.9, 279.4)}
#: Families present on Windows that print and embed alike (design section 12).
FONTS = {
    "sans": '"Calibri", "Arial", sans-serif',
    "serif": '"Cambria", "Georgia", serif',
}
ACCENTS = {
    "black": "#1a1a1a",
    "navy": "#1f3a5f",
    "teal": "#0f5c5c",
    "burgundy": "#6b1f2a",
    "forest": "#2d5a3d",
}
SPACING = {"tight": 0.7, "normal": 1.0, "airy": 1.35}
#: Each template is a set of values for the shared stylesheet's variables.
_CLEAN = {
    "--name-size": "1.9em",
    "--name-color": "var(--ink)",
    "--h2-color": "var(--ink)",
    "--h2-rule": "1px solid var(--accent)",
    "--h2-case": "uppercase",
    "--h2-tracking": "0.08em",
    "--h2-size": "0.92em",
    "--density": "1",
}
TEMPLATES = {
    "clean": _CLEAN,
    "modern": {
        **_CLEAN,
        "--name-size": "2.1em",
        "--name-color": "var(--accent)",
        "--h2-color": "var(--accent)",
        "--h2-rule": "2px solid var(--accent)",
        "--h2-case": "none",
        "--h2-tracking": "0",
        "--h2-size": "1.08em",
        "--density": "0.9",
    },
    "compact": {
        **_CLEAN,
        "--name-size": "1.6em",
        "--h2-tracking": "0.06em",
        "--h2-size": "0.88em",
        "--density": "0.7",
    },
}
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none';"
    " form-action 'none'"
)

HEADINGS = {
    "en": {
        "summary": "Summary",
        "experience": "Experience",
        "projects": "Projects",
        "education": "Education",
        "certifications": "Certifications",
        "skills": "Skills",
    },
    "pt": {
        "summary": "Resumo",
        "experience": "Experiência",
        "projects": "Projetos",
        "education": "Formação",
        "certifications": "Certificações",
        "skills": "Competências",
    },
}
MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
}
PRESENT = {"en": "Present", "pt": "Atual"}
NAME_HINT = {"en": "Your name", "pt": "Seu nome"}

STYLESHEET = """
@page { size: %(page_w)smm %(page_h)smm; margin: %(margin)smm; }
* { box-sizing: border-box; }
html { background: #ffffff; }
body {
  margin: 0; color: var(--ink); font-family: var(--font); font-size: var(--base);
  line-height: var(--lh); text-align: left; -webkit-print-color-adjust: exact;
}
.rv-doc { position: relative; }
.rv-mode--preview .rv-doc { width: var(--page-w); padding: var(--margin); }
.rv-head { margin: 0 0 calc(0.6em * var(--gap)); }
.rv-name { margin: 0; font-size: var(--name-size); line-height: 1.15; color: var(--name-color); }
.rv-contact { margin: 0.25em 0 0; font-size: 0.92em; }
.rv-headline { margin: 0 0 calc(0.7em * var(--gap)); font-size: 1.08em; font-weight: 600; }
.rv-section { margin: 0 0 calc(0.9em * var(--gap) * var(--density)); }
.rv-h2 {
  margin: 0 0 calc(0.45em * var(--gap)); padding-bottom: 0.15em; border-bottom: var(--h2-rule);
  color: var(--h2-color); font-size: var(--h2-size); text-transform: var(--h2-case);
  letter-spacing: var(--h2-tracking);
}
.rv-entry { margin: 0 0 calc(0.6em * var(--gap) * var(--density)); }
.rv-entry__head { margin: 0 0 0.15em; }
.rv-h3 { margin: 0; font-size: 1em; }
.rv-meta { margin: 0; font-size: 0.92em; color: var(--muted); }
.rv-text { margin: 0 0 0.3em; }
.rv-list { margin: 0.15em 0 0; padding-left: 1.2em; }
.rv-list li { margin: 0 0 calc(0.18em * var(--gap) * var(--density)); }
.rv-skills { margin: 0 0 0.2em; }
.rv-link { color: inherit; text-decoration: none; }
[data-block] { break-inside: avoid; }
[data-keep] { break-after: avoid; }
/* The preview frame is sized to the whole document: it never scrolls itself. */
.rv-mode--preview { overflow: hidden; }
.rv-mode--preview [data-empty]::before { content: attr(data-empty); color: #9a9a9a; }
/* Drawn by the preview's paginator: the gap between two pages sits BEHIND
   the text, so a block taller than a page stays readable across it. */
.rv-spacer { list-style: none; margin: 0; padding: 0; }
.rv-gap { position: absolute; left: 0; right: 0; z-index: -1; background: #d9d6cf; }
"""


@dataclass(frozen=True)
class RenderedResume:
    html: str
    #: Physical page geometry the preview frames pages with, in millimetres.
    page: dict[str, float | str]


def _lang(doc: ResumeDocument) -> str:
    return "pt" if doc.language.startswith("pt") else "en"


def _text(value: str) -> str:
    """Escaped text; the model's inline **bold** is the only markup kept."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(value, quote=True))


def _date(value: PartialDate | None, fmt: str, lang: str) -> str:
    if value is None:
        return ""
    if fmt == "yyyy" or value.month is None:
        return str(value.year)
    if fmt == "mm_yyyy":
        return f"{value.month:02d}/{value.year}"
    return f"{MONTHS[lang][value.month - 1]} {value.year}"


def _period(
    start: PartialDate | None, end: PartialDate | None, current: bool, fmt: str, lang: str
) -> str:
    first = _date(start, fmt, lang)
    last = PRESENT[lang] if current else _date(end, fmt, lang)
    if first and last and first != last:
        return f"{first} - {last}"
    return first or last


def _joined(parts: list[str]) -> str:
    """Pieces of one line, separated by a visible bar that is real text."""
    return '<span class="rv-sep" aria-hidden="true"> | </span>'.join(p for p in parts if p)


class _Renderer:
    def __init__(self, doc: ResumeDocument, mode: Mode) -> None:
        self.doc, self.mode = doc, mode
        self.lang = _lang(doc)
        self.fmt = doc.design.date_format
        self.customs = {f"custom:{c.id}": c for c in doc.custom_sections}

    def heading(self, ref: str) -> str:
        custom = (self.doc.layout.headings.get(ref) or "").strip()
        if custom:
            return custom
        if ref in self.customs:
            return self.customs[ref].heading
        return HEADINGS[self.lang][ref]

    def link(self, url: str, label: str, ref: str) -> str:
        shown = _text(label)
        # Only a web address is ever followable, whatever a field holds.
        if self.mode == "print" and re.match(r"^https?://", url, re.IGNORECASE):
            return f'<a class="rv-link" href="{escape(url)}" data-ref="{escape(ref)}">{shown}</a>'
        return f'<span class="rv-link" data-ref="{escape(ref)}">{shown}</span>'

    def bullets(self, items: list[Bullet], prefix: str) -> str:
        shown = [b for b in items if not b.hidden]
        if not shown:
            return ""
        rows = "".join(
            f'<li data-ref="{prefix}/bullet/{b.id}" data-block>{_text(b.text)}</li>' for b in shown
        )
        return f'<ul class="rv-list">{rows}</ul>'

    def identity(self) -> str:
        who = self.doc.identity
        show = who.show
        parts = []
        if show.email and who.email:
            parts.append(f'<span data-ref="identity/email">{_text(who.email)}</span>')
        if show.phone and who.phone:
            parts.append(f'<span data-ref="identity/phone">{_text(who.phone)}</span>')
        place = ", ".join(p for p in (who.city, who.region, who.country) if p)
        if show.location and place:
            parts.append(f'<span data-ref="identity/location">{_text(place)}</span>')
        if show.links:
            for link in who.links:
                label = link.label or re.sub(r"^https?://(www\.)?", "", link.url).rstrip("/")
                parts.append(self.link(link.url, label, f"identity/link/{link.id}"))
        name = who.full_name.strip()
        empty = f' data-empty="{escape(NAME_HINT[self.lang])}"' if not name else ""
        contact = f'<p class="rv-contact">{_joined(parts)}</p>' if parts else ""
        return (
            '<header class="rv-head" data-ref="identity" data-block>'
            f'<h1 class="rv-name" data-ref="identity/name"{empty}>{_text(name)}</h1>'
            f"{contact}</header>"
        )

    def section(self, ref: str, body: str) -> str:
        if not body:
            return ""
        return (
            f'<section class="rv-section" data-ref="section/{escape(ref)}">'
            f'<h2 class="rv-h2" data-ref="section/{escape(ref)}" data-block data-keep>'
            f"{_text(self.heading(ref))}</h2>{body}</section>"
        )

    def entry(self, ref: str, title: str, meta: list[str], bullets: str) -> str:
        keep = " data-keep" if bullets else ""
        return (
            f'<article class="rv-entry" data-ref="{ref}">'
            f'<header class="rv-entry__head" data-ref="{ref}" data-block{keep}>'
            f'<h3 class="rv-h3">{title}</h3>'
            + (f'<p class="rv-meta">{_joined(meta)}</p>' if any(meta) else "")
            + f"</header>{bullets}</article>"
        )

    def experience(self, e: ExperienceEntry) -> str:
        ref = f"experience/{e.id}"
        title = _joined([_text(e.display_title), _text(e.employer)])
        meta = [_text(e.location or ""), _period(e.start, e.end, e.current, self.fmt, self.lang)]
        return self.entry(ref, title, meta, self.bullets(e.bullets, ref))

    def project(self, p: ProjectEntry) -> str:
        ref = f"projects/{p.id}"
        title = _joined([_text(p.name), _text(p.role or "")])
        url = self.link(p.url, re.sub(r"^https?://", "", p.url), f"{ref}/url") if p.url else ""
        meta = [url, _period(p.start, p.end, False, self.fmt, self.lang)]
        return self.entry(ref, title, meta, self.bullets(p.bullets, ref))

    def education(self, e: EducationEntry) -> str:
        ref = f"education/{e.id}"
        degree = ", ".join(x for x in (e.degree, e.field_of_study) if x)
        title = _joined([_text(degree), _text(e.institution)]) if degree else _text(e.institution)
        meta = [_text(e.location or ""), _period(e.start, e.end, False, self.fmt, self.lang)]
        return self.entry(ref, title, meta, self.bullets(e.bullets, ref))

    def certification(self, c: CertificationEntry) -> str:
        line = ", ".join(
            x for x in (c.name, c.issuer or "", _date(c.issued, self.fmt, self.lang)) if x
        )
        return f'<li data-ref="certifications/{c.id}" data-block>{_text(line)}</li>'

    def skills(self, g: SkillGroup) -> str:
        items = ", ".join(
            f'<span data-ref="skills/{g.id}/item/{i.id}">{_text(i.label)}</span>' for i in g.items
        )
        if not items:
            return ""
        label = f"<strong>{_text(g.name)}:</strong> " if g.name else ""
        return f'<p class="rv-skills" data-ref="skills/{g.id}" data-block>{label}{items}</p>'

    def text(self, block: TextBlock | None, kind: str, css: str) -> str:
        if block is None or not block.text.strip():
            return ""
        return f'<p class="{css}" data-ref="{kind}/{block.id}" data-block>{_text(block.text)}</p>'

    def body(self, ref: str) -> str:
        d = self.doc
        if ref == "headline":
            return self.text(d.headline, "headline", "rv-headline")
        if ref == "summary":
            return self.section(ref, self.text(d.summary, "summary", "rv-text"))
        lists = {
            "experience": [self.experience(e) for e in d.experience if not e.hidden],
            "projects": [self.project(p) for p in d.projects if not p.hidden],
            "education": [self.education(e) for e in d.education if not e.hidden],
            "skills": [self.skills(g) for g in d.skills if not g.hidden],
        }
        if ref in lists:
            return self.section(ref, "".join(lists[ref]))
        if ref == "certifications":
            rows = "".join(self.certification(c) for c in d.certifications if not c.hidden)
            return self.section(ref, f'<ul class="rv-list">{rows}</ul>' if rows else "")
        custom = self.customs[ref]
        return "" if custom.hidden else self.section(ref, self.bullets(custom.items, ref))

    def order(self) -> list[str]:
        """The layout's order, then any section it does not name, minus hidden."""
        refs = list(self.doc.layout.section_order)
        known = [*DEFAULT_ORDER, *(f"custom:{c.id}" for c in self.doc.custom_sections)]
        refs += [r for r in known if r not in refs]
        hidden = set(self.doc.layout.hidden_sections)
        return [r for r in refs if r not in hidden]


def stylesheet(doc: ResumeDocument) -> str:
    """The shared stylesheet with this document's design as its variables."""
    design = doc.design
    width, height = PAGE_MM[design.page.size]
    margin = design.page.margins_mm
    variables = {
        "--page-w": f"{width}mm",
        "--page-h": f"{height}mm",
        "--margin": f"{margin}mm",
        "--font": FONTS[design.typography.font],
        "--base": f"{design.typography.base_pt}pt",
        "--lh": str(design.typography.line_height),
        "--gap": str(SPACING[design.spacing]),
        "--accent": ACCENTS[design.accent],
        "--ink": "#1a1a1a",
        "--muted": "#4a4a4a",
        **TEMPLATES[design.template],
    }
    root = ":root { " + " ".join(f"{k}: {v};" for k, v in variables.items()) + " }"
    # `@page` cannot read custom properties, so its size is written literally.
    return root + STYLESHEET % {"page_w": width, "page_h": height, "margin": margin}


def render_html(doc: ResumeDocument, *, mode: Mode = "preview") -> RenderedResume:
    """The whole resume as one HTML document, ready to show or to print."""
    r = _Renderer(doc, mode)
    content = r.identity() + "".join(r.body(ref) for ref in r.order())
    title = doc.identity.full_name.strip() or doc.title
    width, height = PAGE_MM[doc.design.page.size]
    html = (
        "<!doctype html>"
        f'<html lang="{escape(doc.language)}" class="rv-mode--{mode}">'
        "<head>"
        '<meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f"<title>{escape(title)}</title>"
        f"<style>{stylesheet(doc)}</style>"
        "</head>"
        "<body>"
        f'<article class="rv-doc" data-ref="document">{content}</article>'
        "</body></html>"
    )
    page: dict[str, float | str] = {
        "size": doc.design.page.size,
        "width_mm": width,
        "height_mm": height,
        "margin_mm": doc.design.page.margins_mm,
    }
    return RenderedResume(html=html, page=page)
