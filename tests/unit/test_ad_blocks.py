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


def text_of(block) -> str:
    """One block's text exactly as shown: runs joined with nothing between."""
    join = lambda runs: "".join(r if isinstance(r, str) else r["x"] for r in runs)  # noqa: E731
    if block["t"] == "h":
        return block["x"]
    if block["t"] == "p":
        return join(block["r"])
    return " ".join(join(item["r"]) for item in block["i"])


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


# ------------------------------------------------------------------ markdown

#: Shaped like the 4 Day Week advert that showed its marks (Oyster, 2026-10-13).
MARKDOWN_AD = (
    "### **One platform, a whole world of opportunity**\n\n"
    "We are distributed across 60+ countries:\n\n"
    "- Ranked **#10 of 250** on [a public list](https://example.test/list)\n\n"
    "- Named one of [the best workplaces](https://example.test/award) by a magazine\n\n"
    "Work *asynchronously* and __own__ your time."
)


def test_markdown_marks_become_structure_not_text() -> None:
    blocks = ad_blocks("", MARKDOWN_AD)
    assert blocks[0] == {"t": "h", "x": "One platform, a whole world of opportunity"}
    lists = [b for b in blocks if b["t"] == "ul"]
    assert len(lists) == 1 and len(lists[0]["i"]) == 2, "one list, not one per item"
    first = lists[0]["i"][0]["r"]
    assert {"x": "#10 of 250", "b": 1} in first
    assert {"x": "a public list", "a": "https://example.test/list"} in first
    last = blocks[-1]["r"]
    assert {"x": "asynchronously", "i": 1} in last and {"x": "own", "b": 1} in last
    shown = " ".join(text_of(b) for b in blocks)
    assert "**" not in shown and "###" not in shown and "](" not in shown


def test_markdown_keeps_every_word_in_order() -> None:
    shown = " ".join(text_of(b) for b in ad_blocks("", MARKDOWN_AD))
    source = re.sub(r"\]\([^)]*\)", "]", MARKDOWN_AD).replace("__", "")
    assert words(shown) == words(source)


def test_a_dangerous_markdown_link_keeps_its_label_and_loses_its_address() -> None:
    blocks = ad_blocks(
        "", "## Apply\n\nSee [the form](javascript:alert(1)) and ![x](https://e.test/a.png)."
    )
    runs = blocks[-1]["r"]
    assert not any(isinstance(r, dict) and "a" in r for r in runs)
    assert "javascript" not in text_of(blocks[-1]) and "e.test" not in text_of(blocks[-1])
    assert "the form" in text_of(blocks[-1])


def test_escaped_marks_are_the_characters_they_escape() -> None:
    blocks = ad_blocks("", r"**Pay** is \*negotiable\* and \#1 in reviews.")
    assert text_of(blocks[0]) == "Pay is *negotiable* and #1 in reviews."


def test_plain_text_with_a_stray_asterisk_is_not_read_as_markdown() -> None:
    text = "Rates from 3 * 4 hours a day.\n\nUse snake_case_names and *nix tools."
    blocks = ad_blocks("", text)
    assert [text_of(b) for b in blocks] == [
        "Rates from 3 * 4 hours a day.",
        "Use snake_case_names and *nix tools.",
    ]


def test_markup_still_wins_over_markdown() -> None:
    html = "<p>Real <strong>markup</strong> from the employer, long enough to be the ad.</p>"
    blocks = ad_blocks(html, "**not this** but the text the board also stored")
    assert blocks[0]["t"] == "p" and {"x": "markup", "b": 1} in blocks[0]["r"]
