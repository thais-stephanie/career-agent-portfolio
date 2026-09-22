"""Normalisation must preserve meaning, not just strip tags.

The tests that matter here are the structural ones: M2 has to be able to tell
"Salesforce is nice-to-have" from "Salesforce is required", and that distinction
lives entirely in section breaks and bullets.
"""

from career_agent.domain.normalize import collapse_whitespace, content_hash, html_to_text

REQUIREMENTS_HTML = (
    "<h3>Requirements</h3><ul><li>HubSpot administration</li>"
    "<li>API integrations</li></ul>"
    "<h3>Nice to have</h3><ul><li>Salesforce</li></ul>"
)


def test_sections_and_bullets_survive() -> None:
    """The example from the brief. If this flattens, M2 cannot tell a hard
    requirement from a preference, which is the product's core distinction."""
    text = html_to_text(REQUIREMENTS_HTML)
    lines = [line for line in text.split("\n") if line]

    assert lines == [
        "Requirements",
        "- HubSpot administration",
        "- API integrations",
        "Nice to have",
        "- Salesforce",
    ]


def test_requirements_and_nice_to_have_stay_separated() -> None:
    text = html_to_text(REQUIREMENTS_HTML)
    requirements_at = text.index("Requirements")
    nice_at = text.index("Nice to have")
    salesforce_at = text.index("Salesforce")
    hubspot_at = text.index("HubSpot")

    assert requirements_at < hubspot_at < nice_at < salesforce_at
    assert "\n" in text[hubspot_at:nice_at], "the section boundary must survive"


def test_paragraphs_become_blank_line_separated() -> None:
    text = html_to_text("<p>First paragraph.</p><p>Second paragraph.</p>")
    assert text == "First paragraph.\n\nSecond paragraph."


def test_line_breaks_are_kept() -> None:
    assert html_to_text("Line one<br>Line two") == "Line one\nLine two"


def test_entities_are_decoded() -> None:
    # punctuation-check: allow: the decoded &mdash; entity
    assert html_to_text("<p>R&amp;D &mdash; 50&#37; travel</p>") == "R&D — 50% travel"


def test_escaped_markup_is_unescaped_first() -> None:
    """Greenhouse returns `content` as entity-escaped HTML inside the JSON
    string, so it arrives as &lt;p&gt; rather than <p>."""
    escaped = "&lt;p&gt;We are hiring a &lt;strong&gt;Systems Analyst&lt;/strong&gt;.&lt;/p&gt;"
    assert html_to_text(escaped) == "We are hiring a Systems Analyst."


def test_double_escaped_markup_is_handled() -> None:
    assert html_to_text("&amp;lt;p&amp;gt;Hello&amp;lt;/p&amp;gt;") == "Hello"


def test_script_and_style_content_is_dropped() -> None:
    html = "<p>Real text</p><script>var x = 1;</script><style>.a{color:red}</style>"
    text = html_to_text(html)
    assert text == "Real text"


def test_excess_blank_lines_collapse_but_structure_remains() -> None:
    assert collapse_whitespace("a\n\n\n\n\nb") == "a\n\nb"
    assert collapse_whitespace("a\n\nb") == "a\n\nb"
    assert collapse_whitespace("a\nb") == "a\nb"


def test_horizontal_whitespace_collapses_without_touching_newlines() -> None:
    assert collapse_whitespace("a    b\n\tc   d") == "a b\nc d"


def test_non_breaking_spaces_are_normalised() -> None:
    assert html_to_text("<p>Remote - Worldwide</p>") == "Remote - Worldwide"


def test_empty_input_is_empty_output() -> None:
    assert html_to_text("") == ""
    assert html_to_text("   ") == ""


def test_normalisation_is_deterministic() -> None:
    """M2 verifies quotes by finding them in this exact text, so the same input
    must always produce the same output."""
    first = html_to_text(REQUIREMENTS_HTML)
    second = html_to_text(REQUIREMENTS_HTML)
    assert first == second
    assert content_hash(first) == content_hash(second)


def test_content_hash_changes_when_text_changes() -> None:
    a = content_hash("You will own our HubSpot instance.")
    b = content_hash("You will own our Salesforce instance.")
    assert a != b
    assert len(a) == 64


def test_table_rows_do_not_run_together() -> None:
    text = html_to_text("<table><tr><td>Salary</td></tr><tr><td>Location</td></tr></table>")
    lines = [line for line in text.split("\n") if line]
    assert lines == ["Salary", "Location"], "rows must not be concatenated"


def test_table_cells_in_one_row_are_separated() -> None:
    """Compensation bands are often tables; '80,000100,000' would be worse than
    useless."""
    text = html_to_text("<table><tr><td>80,000</td><td>100,000</td></tr></table>")
    assert "80,000 100,000" in text


# --- M1C.1: a list marker belongs to its item's first content ---------------
#
# The invariant: a list marker and the first meaningful content of its list item
# are on the same logical line. `<li>text</li>` and `<li><p>text</p></li>` are
# the same document; they must normalise the same way.
#
# Found via Ashby, whose rich-text editor wraps every item's content in a block
# element on 99.5% of postings -- but measured on the stored corpus it strands
# markers at all three providers, so it is a defect in this module rather than a
# vendor quirk. Fixing it in an adapter would have left the next provider to
# rediscover it.


def lines_of(html: str) -> list[str]:
    return [line for line in html_to_text(html).split("\n") if line.strip()]


def test_a_bare_list_item_keeps_its_marker() -> None:
    """The behaviour that already worked, pinned so the fix cannot break it."""
    assert html_to_text("<ul><li>Direct text</li></ul>") == "- Direct text"


def test_a_block_wrapped_list_item_keeps_its_marker() -> None:
    """The defect. Before the fix this rendered as "-\\nWrapped text"."""
    assert html_to_text("<ul><li><p>Wrapped text</p></li></ul>") == "- Wrapped text"


def test_both_spellings_of_one_list_normalise_identically() -> None:
    bare = html_to_text("<ul><li>First</li><li>Second</li></ul>")
    wrapped = html_to_text("<ul><li><p>First</p></li><li><p>Second</p></li></ul>")

    # The blank line between items is pre-existing behaviour for every list --
    # `</li>` closes a line and `<li>` opens another -- and it is deliberately
    # left alone here. M1C.1 fixes stranded markers, not list spacing.
    assert bare == "- First\n\n- Second"
    assert wrapped == bare, "the same document written two ways, byte for byte"


def test_the_rule_is_structural_not_a_p_tag_special_case() -> None:
    """Any block element immediately inside a list item, not just <p>. A vendor
    using <div> deserves the same result, and so does the next one."""
    for wrapper in ("p", "div", "h3", "section", "blockquote", "pre"):
        html = f"<ul><li><{wrapper}>Wrapped text</{wrapper}></li></ul>"
        assert html_to_text(html) == "- Wrapped text", wrapper


def test_whitespace_between_the_item_and_its_block_does_not_strand_the_marker() -> None:
    """Pretty-printed HTML puts a newline between `<li>` and `<p>`. That
    whitespace is markup formatting, not content, and must not push the item's
    text onto its own line."""
    assert html_to_text("<ul>\n  <li>\n    <p>Wrapped text</p>\n  </li>\n</ul>") == (
        "- Wrapped text"
    )


def test_an_empty_leading_block_does_not_strand_the_marker() -> None:
    assert html_to_text("<ul><li><p></p><p>Real text</p></li></ul>") == "- Real text"


def test_inline_formatting_inside_a_wrapped_item_survives() -> None:
    html = "<ul><li><p><strong>HubSpot</strong> administration, <em>daily</em></p></li></ul>"
    assert html_to_text(html) == "- HubSpot administration, daily"


def test_a_list_item_with_two_blocks_puts_the_marker_on_the_first() -> None:
    """Valid HTML, and the invariant only claims the FIRST meaningful content.
    The second block stays its own line rather than being merged into the item."""
    assert lines_of("<ul><li><p>First half</p><p>Second half</p></li></ul>") == [
        "- First half",
        "Second half",
    ]


def test_a_mixed_list_renders_both_spellings_the_same_way() -> None:
    html = "<ul><li>Bare item</li><li><p>Wrapped item</p></li><li>Another bare</li></ul>"
    assert lines_of(html) == ["- Bare item", "- Wrapped item", "- Another bare"]


def test_ordered_lists_get_the_same_treatment() -> None:
    assert lines_of("<ol><li><p>Step one</p></li><li><p>Step two</p></li></ol>") == [
        "- Step one",
        "- Step two",
    ]


def test_nested_lists_keep_every_marker_attached() -> None:
    """Nesting is flattened rather than indented -- existing behaviour, not
    redesigned here. What must not happen is a stranded marker at either level."""
    html = "<ul><li><p>Outer</p><ul><li><p>Inner</p></li></ul></li></ul>"
    assert lines_of(html) == ["- Outer", "- Inner"]


WRAPPED_REQUIREMENTS_HTML = (
    "<h3>Requirements</h3><ul>"
    '<li><p style="min-height:1.5em">HubSpot administration</p></li>'
    '<li><p style="min-height:1.5em">API integrations</p></li></ul>'
    "<h3>Nice to have</h3><ul>"
    '<li><p style="min-height:1.5em">Salesforce</p></li></ul>'
)


def test_the_product_distinction_survives_in_the_wrapped_spelling_too() -> None:
    """The same assertion as `test_sections_and_bullets_survive`, against the
    markup Ashby actually produces. If this flattens, M2 cannot tell a hard
    requirement from a preference -- which is the whole reason this module
    preserves structure."""
    assert lines_of(WRAPPED_REQUIREMENTS_HTML) == [
        "Requirements",
        "- HubSpot administration",
        "- API integrations",
        "Nice to have",
        "- Salesforce",
    ]


def test_headings_still_separate_the_two_wrapped_sections() -> None:
    text = html_to_text(WRAPPED_REQUIREMENTS_HTML)
    hubspot_at = text.index("HubSpot")
    nice_at = text.index("Nice to have")
    salesforce_at = text.index("Salesforce")

    assert text.index("Requirements") < hubspot_at < nice_at < salesforce_at
    assert "\n" in text[hubspot_at:nice_at], "the section boundary must survive"


def test_a_real_ashby_snippet_renders_as_a_list() -> None:
    """Copied from a live payload, attributes and all."""
    html = (
        '<ul><li><p style="min-height:1.5em">Advance the state of the art in '
        "medical NLP, in areas including conversation summarization.</p></li>"
        '<li><p style="min-height:1.5em">Actively contribute to the wider '
        "research community by sharing and publishing.</p></li></ul>"
    )
    lines = lines_of(html)

    assert len(lines) == 2
    assert all(line.startswith("- ") for line in lines)
    assert lines[0].endswith("conversation summarization.")
    assert not any(line.strip() == "-" for line in html_to_text(html).split("\n"))
