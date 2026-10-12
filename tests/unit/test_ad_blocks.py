"""The ad as blocks: structure from the employer's markup, nothing else changes.

What must hold: lists stay lists (a paragraph per item was the defect), a
`Label: value` run becomes rows, headings come from the employer's own marks,
repeated blank lines are not extra spacing, text without markup is not turned
into one paragraph and not given invented titles, markup that executes or loads
is dropped, links keep only safe addresses, and no word is added, dropped or
reordered.
"""

from __future__ import annotations

import re

from career_agent.web.ad_blocks import ad_blocks

HTML_AD = (
    "<h3><strong>Automation Specialist - Remote</strong></h3>"
    "<p><strong>Position Type:</strong> Full-Time, Remote<br> <strong>Location:</strong> Remote"
    " (LATAM)<br> <strong>Hours:</strong> U.S. business hours</p><p></p>"
    "<h3>About the role</h3><p>We are hiring an <strong>Automation Specialist</strong> to build"
    ' workflows. See <a href="https://example.test/jobs">the page</a>.</p>'
    "<p><strong>What you will own</strong></p>"
    "<ul><li>Analyze workflows</li><li>Build with:<ul><li>Zapier</li><li>Make</li></ul></li>"
    "<li>Document the work</li></ul><ol><li>First</li><li>Second</li></ol>"
)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def flat(blocks) -> str:
    out: list[str] = []

    def runs(items) -> str:
        return " ".join(r if isinstance(r, str) else r["x"] for r in items)

    def walk(block) -> None:
        if block["t"] == "h":
            out.append(block["x"])
        elif block["t"] == "p":
            out.append(runs(block["r"]))
        elif block["t"] == "meta":
            for row in block["rows"]:
                out.append(row["k"])
                out.append(runs(row["r"]))
        else:
            for item in block["i"]:
                out.append(runs(item["r"]))
                if item.get("s"):
                    walk(item["s"])

    for block in blocks:
        walk(block)
    return " ".join(out)


def kinds(blocks) -> list[str]:
    return [b["t"] for b in blocks]


def test_list_items_stay_in_one_list_and_nested_lists_nest() -> None:
    blocks = ad_blocks(HTML_AD, "")
    lists = [b for b in blocks if b["t"] in ("ul", "ol")]
    assert [b["t"] for b in lists] == ["ul", "ol"]
    assert [i["r"][0] for i in lists[0]["i"]] == [
        "Analyze workflows",
        "Build with:",
        "Document the work",
    ]
    nested = lists[0]["i"][1]["s"]
    assert nested["t"] == "ul" and [i["r"][0] for i in nested["i"]] == ["Zapier", "Make"]


def test_label_value_lines_become_rows_and_headings_come_from_the_employers_marks() -> None:
    blocks = ad_blocks(HTML_AD, "")
    meta = next(b for b in blocks if b["t"] == "meta")
    assert [r["k"] for r in meta["rows"]] == ["Position Type", "Location", "Hours"]
    assert meta["rows"][1]["r"] == ["Remote (LATAM)"]
    headings = [b["x"] for b in blocks if b["t"] == "h"]
    assert headings == ["Automation Specialist - Remote", "About the role", "What you will own"]


def test_empty_paragraphs_and_repeated_breaks_add_no_blocks() -> None:
    blocks = ad_blocks("<p>One</p><p></p><p> </p><br><br><p>Two</p>", "")
    assert kinds(blocks) == ["p", "p"]
    text = "First paragraph.\n\n\n\n\nSecond paragraph."
    assert kinds(ad_blocks(None, text)) == ["p", "p"]


def test_no_word_is_added_dropped_or_reordered() -> None:
    blocks = ad_blocks(HTML_AD, "")
    source = re.sub(r"<[^>]+>", " ", HTML_AD)
    assert words(flat(blocks)) == words(source)


def test_plain_text_is_neither_one_paragraph_nor_given_invented_titles() -> None:
    text = (
        "Senior Engineer\n\n"
        "We build tools for operations teams and we are looking for someone who likes order.\n\n"
        "Requirements:\n- Python\n- SQL\n- Patience\n\n"
        "Tools\n\nZapier\n\nMake\n\nn8n\n\nAirtable\n\n"
        "Position Type: Full-Time\nLocation: Remote\n"
    )
    blocks = ad_blocks(None, text)
    assert "ul" in kinds(blocks) and "meta" in kinds(blocks) and len(blocks) >= 5
    headings = [b["x"] for b in blocks if b["t"] == "h"]
    assert headings == ["Requirements"], "only a line the ad itself marked (a colon) is a heading"
    plain = next(b for b in blocks if b["t"] == "ul" and b.get("plain"))
    assert [i["r"][0] for i in plain["i"]] == ["Tools", "Zapier", "Make", "n8n", "Airtable"]
    assert words(flat(blocks)) == words(text)


def test_nothing_executes_and_nothing_loads() -> None:
    hostile = (
        "<p>Safe text</p><script>alert(1)</script><style>p{x:y}</style>"
        '<iframe src="https://evil.test"></iframe><img src="https://evil.test/x.png" onerror="1">'
        '<p onclick="x()">Click <a href="javascript:alert(1)">here</a>'
        ' and <a href="https://ok.test/p?a=1&b=2">there</a>.</p>'
        '<object data="x">obj</object>'
    )
    blocks = ad_blocks(hostile, "")
    dumped = repr(blocks)
    for banned in ("alert", "evil.test", "onerror", "onclick", "javascript:", "p{x:y}", "obj"):
        assert banned not in dumped, banned
    links = [r for b in blocks if b["t"] == "p" for r in b["r"] if isinstance(r, dict) and "a" in r]
    assert [link["a"] for link in links] == ["https://ok.test/p?a=1&b=2"]
    assert flat(blocks).split()[:2] == ["Safe", "text"]


def test_an_empty_shell_of_markup_falls_back_to_the_text() -> None:
    blocks = ad_blocks("<div></div>", "The real text of the ad is here and is long enough to read.")
    assert flat(blocks).startswith("The real text")
    assert ad_blocks(None, "") == [] and ad_blocks("", "   ") == []
