"""Product-authored prose must go through the catalogue.

A gate for mixed language, and it checks the CAUSE rather than the symptom.

Detecting "English on a Portuguese screen" by looking at rendered text cannot
work: employer content is genuinely English, and a job title, a company name
and an evidence quote must stay exactly as the employer wrote them. What CAN be
checked deterministically is whether a string this project wrote reaches the
DOM without passing through `t()`. Every mixed-language defect the owner
reported was one of those.

WHAT COUNTS AS PROSE
--------------------
A literal assigned to something the browser shows -- `text:`, `placeholder`,
`title`, `aria-label`, or `.textContent = ` -- that contains at least three
consecutive letters and is not obviously an identifier, a URL, a CSS class or a
single mark.

QUOTED OR IN BACKTICKS. The first version of this gate read only quoted
strings, and passed clean while `${count} jobs` and "2 things worth knowing"
rendered English on a Portuguese header. A template literal is how a sentence
acquires a number, and a sentence with a number in it is the kind nobody
bothers to add a key for.

WHAT IS ALLOWED, AND WHY EACH ONE
----------------------------------
`ALLOWED_MODULES` are files with no user-facing prose to begin with.

`ALLOWED_LITERALS` are strings that are the same in both languages: a product
name, a mathematical symbol, a currency code, a mark. Translating `n8n` would
be a defect, not a feature.

`ALLOWED_LINES` carries the handful of places where a literal is a fallback
for a value the SERVER supplies from the person's own configuration -- a word
the configuration chose is the product working, not a string to translate.

Run directly, or through `tests/unit/test_localisation_gate.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JS = REPO_ROOT / "src" / "career_agent" / "web" / "static" / "js"

#: A literal assigned to something the DOM will render.
RENDERED = re.compile(
    r"""(?:\btext|\bplaceholder|\btitle|\bariaLabel|'aria-label'|"aria-label")\s*:\s*"""
    r"""(['"])([^'"]*)\1""",
)
TEXTCONTENT = re.compile(r"""\.textContent\s*=\s*(['"])([^'"]*)\1""")

#: THE SAME PLACES, WRITTEN WITH BACKTICKS.
#:
#: A template literal is how a sentence acquires a number, which is exactly
#: the kind of sentence somebody writes inline instead of adding a key. This
#: gate passed clean for a whole session while `${count} jobs`, `${n} scores
#: predate the filters` and "2 things worth knowing" rendered in English on a
#: Portuguese page -- because it only ever looked between quotes.
#:
#: The interpolations are blanked before the value is judged, so a template
#: holding ONLY `${...}` and punctuation is not prose, while a sentence built
#: around a substitution still is.
#: EVERY template literal, wherever it stands.
#:
#: The named positions above are not enough, because a sentence assembled from
#: a number is rarely written at the point it is rendered. It is built into a
#: `const`, returned from a helper, or chosen by a ternary two lines above the
#: `text:` that receives it -- and each of those forms carried a real English
#: sentence onto a Portuguese screen.
#:
#: The exclusions below are what keep this from being noise, and each is a
#: SHAPE rather than a list of files.
TEMPLATE = re.compile(r"`([^`]*)`")

#: A template that builds a CLASS NAME. `prep__state prep__state--${...}` is a
#: selector, and translating it would break the stylesheet.
CLASS_LINE = re.compile(r"""\bclassName\s*[:=]|\bclass\s*[:=]|setAttribute\(\s*['"]class['"]""")

#: A comment. Prose is exactly what a comment is for.
COMMENT_LINE = re.compile(r"^\s*(?://|\*|/\*)")

#: `${...}` and what it stands for. A value substituted into a sentence is not
#: itself prose, and leaving it in would make every template look translated.
INTERPOLATION = re.compile(r"\$\{[^}]*\}")

#: `button(label, ...)` and `extLink(url, label, ...)` take the visible words
#: POSITIONALLY. The first version of this gate looked only at named fields, so
#: the profile's Save button and every other positional label sat outside it --
#: which is how "Save" survived a pass that moved ninety-nine other strings.
POSITIONAL = re.compile(r"""\bbutton\(\s*(['"])([^'"]*)\1""")
EXT_LINK = re.compile(r"""\bextLink\([^,]+,\s*(['"])([^'"]*)\1""")

#: Modules with no user-facing prose of their own.
ALLOWED_MODULES = frozenset(
    {
        "i18n.js",  # the catalogue itself
        "state.js",  # query keys and defaults
        "api.js",  # transport; its error sentences are shown through callers
        "theme.js",  # a classic script that runs before the catalogue exists
    }
)

#: The same string in every language.
ALLOWED_LITERALS = frozenset(
    {
        "...",
        "n8n",
        "HubSpot",
        "Career Agent",
        "EN",
        "PT",
    }
)


def prose(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 3 or stripped in ALLOWED_LITERALS:
        return False
    if not re.search(r"[A-Za-z]{3}", stripped):
        return False
    # An identifier, a class list, a selector or a URL rather than a sentence.
    if re.fullmatch(r"[a-z0-9_.:\- ]+", stripped) and " " not in stripped.strip():
        return False
    # A URL, an endpoint, a fragment, a selector or a CSS custom property.
    return not stripped.startswith(("http", "/api", "#", ".", "--"))


def template_prose(value: str) -> bool:
    """Is this template literal a SENTENCE rather than an identifier?

    A LITERAL SPACE is what separates the two, and it has to be looked for
    OUTSIDE the interpolations. `daily.section.${key}.${part}` is a catalogue
    key and holds no space of its own; `${days} days ago` is a sentence and
    holds two. Blanking the interpolations first would have given the key one
    and made every computed key a finding.
    """
    outside = INTERPOLATION.sub("\x00", value)
    if " " not in outside:
        return False
    return prose(INTERPOLATION.sub(" ", value))


def findings() -> dict[str, list[tuple[int, str]]]:
    found: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(JS.glob("*.js")):
        if path.name in ALLOWED_MODULES:
            continue
        hits: list[tuple[int, str]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "localisation-check: allow" in line:
                continue
            for pattern in (RENDERED, TEXTCONTENT, POSITIONAL, EXT_LINK):
                for _, value in pattern.findall(line):
                    if prose(value):
                        hits.append((number, value))
            if COMMENT_LINE.match(line) or CLASS_LINE.search(line):
                continue
            for value in TEMPLATE.findall(line):
                if template_prose(value):
                    hits.append((number, value))
        if hits:
            found[path.name] = hits
    return found


#: The catalogue's three entry points. Calling one without importing it is a
#: `ReferenceError` at the first render, and the only visible symptom is an
#: error state that names a variable nobody recognises.
CATALOGUE_CALLS = ("t", "tState", "tVocab")

IMPORT_LINE = re.compile(r"import\s*\{([^}]*)\}\s*from\s*'\./i18n\.js'")


def missing_imports() -> dict[str, list[str]]:
    """Modules that call a catalogue function they never imported."""
    broken: dict[str, list[str]] = {}
    for path in sorted(JS.glob("*.js")):
        if path.name in {"i18n.js", "theme.js"}:
            continue
        source = path.read_text(encoding="utf-8")
        imported = set()
        for match in IMPORT_LINE.finditer(source):
            imported.update(name.strip() for name in match.group(1).split(","))
        wanted = [
            name
            for name in CATALOGUE_CALLS
            if re.search(r"(?<![\w.])" + name + r"\(", source) and name not in imported
        ]
        if wanted:
            broken[path.name] = wanted
    return broken


def main() -> int:
    absent = missing_imports()
    for name, wanted in sorted(absent.items()):
        print(f"localisation: {name} calls {', '.join(wanted)} without importing it")
    if absent:
        print("\nAdd them to the `from './i18n.js'` import. A module that calls one of")
        print("these without importing it throws at the first render, and the interface")
        print("shows an error naming a variable the reader has never heard of.")
        return 1

    found = findings()
    total = sum(len(hits) for hits in found.values())
    if not total:
        modules = len([p for p in JS.glob("*.js") if p.name not in ALLOWED_MODULES])
        print(f"localisation: clean ({modules} modules scanned)")
        print(f"localisation: {len(ALLOWED_MODULES)} modules allowed, each with a reason")
        return 0

    print(f"localisation: {total} product-authored literals bypass the catalogue\n")
    for name, hits in sorted(found.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name}  ({len(hits)})")
        for number, value in hits:
            safe = value[:96].encode("ascii", "replace").decode("ascii")
            print(f"    {number:>5}  {safe}")
        print()
    print(
        "Route each through `t('some.key')` and add the key to BOTH catalogues in\n"
        "js/i18n.js. Where a literal is genuinely the same in every language, add a\n"
        "line-level `localisation-check: allow <reason>` marker."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
