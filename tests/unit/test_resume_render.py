"""The one resume renderer: golden output, safety, geometry, design vs content.

Synthetic documents only (`tests/support_resume.py`). To refresh the golden
files after an intended change to the markup or the stylesheet:

    UPDATE_GOLDEN=1 uv run python -m pytest tests/unit/test_resume_render.py
"""

from __future__ import annotations

import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from tests.support_resume import long, rich, rid, sparse

from career_agent.resume_doc.models import ResumeDocument
from career_agent.resume_doc.render import render_html

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "resume_doc" / "golden"
REF = re.compile(
    r"^(document|identity(/(name|email|phone|location|link/[0-9A-Z]{26}))?"
    r"|headline/[0-9A-Z]{26}|summary/[0-9A-Z]{26}"
    r"|section/(summary|experience|projects|education|certifications|skills|custom:[0-9A-Z]{26})"
    r"|(experience|projects|education)/[0-9A-Z]{26}(/bullet/[0-9A-Z]{26}|/url)?"
    r"|certifications/[0-9A-Z]{26}|skills/[0-9A-Z]{26}(/item/[0-9A-Z]{26})?"
    r"|custom/[0-9A-Z]{26}/bullet/[0-9A-Z]{26})$"
)


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_style = tag in ("style", "title")

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if not self._in_style:
            self.text.append(data)


def parsed(html: str) -> _Tags:
    tags = _Tags()
    tags.feed(html)
    return tags


def visible_text(html: str) -> str:
    return " ".join("".join(parsed(html).text).split())


@pytest.mark.parametrize("template", ["clean", "modern"])
@pytest.mark.parametrize("fixture", [rich, sparse])
def test_golden_html(template: str, fixture) -> None:  # noqa: ANN001
    html = render_html(fixture(template=template)).html
    path = GOLDEN / f"{template}_{fixture.__name__}.html"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8", newline="\n")
    assert html == path.read_text(encoding="utf-8"), (
        f"{path.name} changed; see the module docstring"
    )


def test_the_output_is_deterministic_and_semantic() -> None:
    doc = rich()
    first, again = render_html(doc).html, render_html(doc).html
    assert first == again
    names = [tag for tag, _ in parsed(first).tags]
    for tag in ("header", "section", "h1", "h2", "h3", "ul", "li", "p"):
        assert tag in names, tag
    for tag in ("table", "img", "canvas", "svg", "script", "iframe", "link"):
        assert tag not in names, tag


def test_every_reference_names_an_object_never_a_position() -> None:
    doc = rich()
    tags = parsed(render_html(doc).html).tags
    refs = {attrs["data-ref"] for _, attrs in tags if attrs.get("data-ref")}
    bad = sorted(r for r in refs if r is None or not REF.match(r))
    assert not bad, bad
    for entry in doc.experience:
        for bullet in entry.bullets:
            assert f"experience/{entry.id}/bullet/{bullet.id}" in refs


def test_hidden_content_is_not_drawn_and_stays_in_the_document() -> None:
    doc = rich()
    entry = doc.experience[0]
    hidden = entry.bullets[0].model_copy(update={"hidden": True})
    doc = doc.model_copy(
        update={
            "experience": [
                entry.model_copy(update={"bullets": [hidden, *entry.bullets[1:]]}),
                *doc.experience[1:],
            ],
            "layout": doc.layout.model_copy(update={"hidden_sections": ["projects"]}),
        }
    )
    html = render_html(doc).html
    assert hidden.text not in html and "Open data connector" not in html
    assert entry.bullets[1].text in html


def test_layout_order_and_heading_overrides_are_obeyed() -> None:
    doc = rich()
    doc = doc.model_copy(
        update={
            "layout": doc.layout.model_copy(
                update={
                    "section_order": ["skills", "experience"],
                    "headings": {"experience": "Where I worked"},
                }
            )
        }
    )
    text = visible_text(render_html(doc).html)
    assert text.index("Tools:") < text.index("Where I worked") < text.index("Education")


HOSTILE = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "<style>body{display:none}</style>",
    "<iframe src=//evil.example></iframe>",
    "Tom &amp; Jerry \"quoted\" 'single' &lt;tag&gt;",
    "Unicode: \N{LATIN SMALL LETTER E WITH ACUTE}\N{SNOWMAN}",
]


@pytest.mark.parametrize("mode", ["preview", "print"])
def test_user_text_is_text_never_markup(mode: str) -> None:
    doc = rich()
    entry = doc.experience[0]
    bullets = [
        b.model_copy(update={"text": h}) for b, h in zip(entry.bullets, HOSTILE, strict=False)
    ]
    doc = doc.model_copy(
        update={
            "identity": doc.identity.model_copy(update={"full_name": HOSTILE[0]}),
            "headline": doc.headline.model_copy(update={"text": HOSTILE[1]}),  # type: ignore[union-attr]
            "experience": [
                entry.model_copy(update={"bullets": bullets, "employer": HOSTILE[3]}),
                *doc.experience[1:],
            ],
        }
    )
    html = render_html(doc, mode=mode).html  # type: ignore[arg-type]
    tags = parsed(html)
    names = [tag for tag, _ in tags.tags]
    assert names.count("style") == 1 and "script" not in names
    assert not {"img", "iframe", "object", "embed", "link"} & set(names)
    assert not [a for _, attrs in tags.tags for a in attrs if a.startswith("on")]
    text = "".join(tags.text)
    for hostile in HOSTILE[:4]:
        assert hostile in text, "shown as the characters typed"


def test_bold_is_the_only_markup_a_field_can_make() -> None:
    doc = rich()
    html = render_html(doc).html
    assert "<strong>reconciliation</strong>" in html


def test_nothing_can_load_from_the_network() -> None:
    for mode in ("preview", "print"):
        html = render_html(rich(), mode=mode).html  # type: ignore[arg-type]
        assert "default-src 'none'" in html
        style = html[html.index("<style>") : html.index("</style>")]
        assert "url(" not in style and "@import" not in style and "http" not in style
    preview = parsed(render_html(rich(), mode="preview").html).tags
    assert not [a for _, a in preview if "href" in a], "a preview link is not followable"
    printed = parsed(render_html(rich(), mode="print").html).tags
    hrefs = [a["href"] for _, a in printed if a.get("href")]
    assert hrefs and all(h and h.startswith("https://") for h in hrefs)


@pytest.mark.parametrize(("size", "mm"), [("A4", (210.0, 297.0)), ("LETTER", (215.9, 279.4))])
def test_page_geometry_is_physical(size: str, mm: tuple[float, float]) -> None:
    rendered = render_html(rich(page={"size": size, "margins_mm": 12}))
    assert (rendered.page["width_mm"], rendered.page["height_mm"]) == mm
    assert f"@page {{ size: {mm[0]}mm {mm[1]}mm; margin: 12.0mm; }}" in rendered.html


def test_a_template_changes_how_it_looks_never_what_it_says() -> None:
    texts = {
        t: visible_text(render_html(rich(template=t)).html) for t in ("clean", "modern", "compact")
    }
    assert len(set(texts.values())) == 1
    styles = {t: render_html(rich(template=t)).html.split("<style>")[1] for t in texts}
    assert len(set(styles.values())) == 3


def test_a_blank_name_is_a_hint_on_screen_and_nothing_in_print() -> None:
    doc = sparse()
    doc = doc.model_copy(update={"identity": doc.identity.model_copy(update={"full_name": ""})})
    preview = render_html(doc).html
    assert 'data-empty="Seu nome"' in preview and "Seu nome" not in visible_text(preview)
    assert "You" not in visible_text(render_html(doc, mode="print").html).split()


def test_rendering_is_fast() -> None:
    for fixture, budget_ms in ((rich, 50), (long, 50)):
        doc: ResumeDocument = fixture()
        started = time.perf_counter()
        for _ in range(20):
            render_html(doc)
        assert (time.perf_counter() - started) / 20 * 1000 < budget_ms


def test_fixture_ids_are_stable() -> None:
    assert rich().id == rid("rich") and render_html(rich()).html == render_html(rich()).html
