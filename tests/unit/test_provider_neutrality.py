"""Nothing outside `providers/` may know which ATS a job came from.

This is the property M1B exists to verify. A single adapter can never prove it:
any interface fits one implementation. The test is whether a second, structurally
different vendor slots in without anyone else learning its name.

Comments and docstrings are exempt on purpose -- `domain/normalize.py` explains
*why* it double-unescapes by naming the board that does it, and that explanation
is worth keeping. What is forbidden is a provider name reaching runtime: a
branch, a lookup key, a format string, a default.
"""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"
PROVIDER_NAMES = frozenset({"greenhouse", "lever", "ashby", "workday", "smartrecruiters"})

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_WORDS = re.compile(r"[A-Za-z]+")


def hits(text: str) -> list[str]:
    """Provider names appearing as whole words in `text`.

    Word-level rather than substring, because `SearchProfileVersionRepo`
    contains the letters of "lever" -- profi|lever|sion -- and a substring test
    flags it. A guard that cries wolf gets deleted.
    """
    tokens = {word.lower() for word in _WORDS.findall(_CAMEL_BOUNDARY.sub(r"\1 \2", text))}
    return sorted(tokens & PROVIDER_NAMES)


NEUTRAL_MODULES = sorted(
    path for path in SRC.rglob("*.py") if "providers" not in path.relative_to(SRC).parts
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the Constant nodes that are docstrings, so they can be skipped."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


@pytest.mark.parametrize(
    "module_path", NEUTRAL_MODULES, ids=lambda p: str(p.relative_to(SRC)).replace("\\", "/")
)
def test_no_provider_name_reaches_runtime_outside_providers(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and hits(node.value):
                offenders.append(f"{hits(node.value)} in string {node.value[:60]!r}")
        elif isinstance(node, ast.Name) and hits(node.id):
            offenders.append(f"identifier {node.id!r}")
        elif isinstance(node, ast.Attribute) and hits(node.attr):
            offenders.append(f"attribute {node.attr!r}")
        elif isinstance(node, ast.ImportFrom) and node.module and hits(node.module):
            offenders.append(f"import {node.module!r}")
        elif isinstance(node, ast.Import):
            offenders += [f"import {a.name!r}" for a in node.names if hits(a.name)]

    assert not offenders, (
        f"{module_path.relative_to(SRC)} knows about a specific ATS: {offenders}. "
        "Absorb the difference inside providers/, or -- if the neutral types "
        "genuinely cannot express it -- report it rather than working around it."
    )


def test_the_neutral_surface_is_not_accidentally_empty() -> None:
    """A guard on the guard: if the glob ever stops matching, the test above
    would pass by testing nothing."""
    names = {p.name for p in NEUTRAL_MODULES}
    assert {"collect.py", "repositories.py", "fetcher.py", "normalize.py"} <= names
    assert len(NEUTRAL_MODULES) >= 15


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("greenhouse", ["greenhouse"]),
        ("LeverProvider", ["lever"]),
        ("provider == 'lever'", ["lever"]),
        ("https://boards.greenhouse.io/x", ["greenhouse"]),
        ("lever_capabilities", ["lever"]),
        ("SearchProfileVersionRepo", []),
        ("profile_version", []),
        ("clever", []),
        ("board_identifier", []),
    ],
)
def test_the_detector_catches_names_without_crying_wolf(text: str, expected: list[str]) -> None:
    """A guard on the guard. `SearchProfileVersionRepo` is the real case that
    made a substring test unusable: profi|lever|sion."""
    assert hits(text) == expected


# --- M1B.1: paths must not leak either -------------------------------------
#
# The guard above catches a provider NAME reaching runtime. The metadata
# transport introduces a second thing that must not leak: the vendor JSON paths
# themselves. `payload["workplaceType"]` in pipeline/ names no provider and
# would sail straight past the test above, while being exactly the coupling the
# whole mechanism exists to prevent.
#
# The path list is generated from the adapters themselves, so it cannot go
# stale: declare a new path in an adapter and this guard covers it immediately.
#
# It used to be generated from the FIELD MAPS alone, and that was a real hole.
# Two mechanisms read payloads -- the field map, which resolves paths carrying a
# canonical dimension, and the compensation reader, which reads numbers -- and
# the second one's paths were in no declared list anywhere.
# `compensation.compensationTiers`, `salaryRange` and Greenhouse's `metadata`
# were all invisible here, so generic code writing `payload["salaryRange"]`
# would have passed. `declared_payload_paths()` is the union of both.


def _declared_paths() -> set[str]:
    """Every path an adapter admits to reading, minus what cannot be guarded.

    `metadata` is declared by the Greenhouse reader and excluded here, and the
    exclusion is the honest half of closing this gap rather than a loophole in
    it. Single-word paths are matched by whole-string equality, and "metadata"
    is an ordinary English word that two neutral modules already use for their
    own purposes: `pipeline/collect.py` keys its run statistics with it, and
    `llm/vendors/openai_compatible.py` reads an OpenRouter error field of that
    name. Neither has anything to do with a job board. Guarding the bare word
    would fire on both, and -- as the docstring at the top of this file says --
    a guard that cries wolf gets deleted.

    What IS guarded for Greenhouse is `currency_range`, the vendor-specific
    discriminator that generic code would actually have to spell in order to
    interpret Greenhouse pay. That is the leak; the bare key is not.
    """
    from career_agent.providers.registry import declared_payload_paths

    return declared_payload_paths() - UNGUARDABLE_ORDINARY_WORDS


#: Declared paths that are ordinary English words already in innocent use in
#: neutral modules. Keep this as short as the evidence forces it to be, and
#: never add to it to silence a real finding.
UNGUARDABLE_ORDINARY_WORDS = frozenset({"metadata"})

#: Modules where one specific ordinary word is a name this system chose, and
#: the reason. PER MODULE and per word on purpose: a global exemption would
#: disarm the guard across ninety neutral modules to excuse two, and the whole
#: value of this test is that it fires on the module nobody was thinking about.
#:
#: `country` became a filter dimension of our own -- `job_match.countries`, the
#: `country` query parameter, the `country` facet -- resolved from
#: `location_raw` through `config/places.yaml`. It is not a provider payload
#: field and is never read from one; the word simply collides with a path some
#: vendor also declares. Everywhere else `country` in a neutral module still
#: fails, which is what the guard is for.
#: `employment_type` joined `country` in V3, for exactly the same reason and
#: with exactly the same shape of evidence.
#:
#: It has been this system's own vocabulary since M3: a `job_match` column, a
#: `JobFacts` attribute, a query parameter, a facet name and a filter label. It
#: became a guarded path only because the Speedrun adapter declares a payload
#: field that happens to carry the same name, which three ATS adapters spelled
#: differently (`categories.commitment`, `employmentType`).
#:
#: `salary` joined them when the Jooble adapter was added, and it is the
#: clearest case of the three. It is a `job_match` column, four of them
#: (`salary_min`, `salary_max`, `salary_currency`, `salary_period`), a query
#: parameter, a facet, a confidence item and a filter label -- and it is also
#: what Jooble happens to call its rendered pay string, which the other four
#: adapters spell `compensation`, `compensationTierSummary` and `comp_summary`.
#: An aggregator choosing an ordinary English word for a field does not make
#: that word the aggregator's property.
#:
#: `region` joined with the We Work Remotely adapter, whose `region` element
#: carries an employer's hiring scope. It is also a `Source` field, a facet, a
#: filter, a `job_match` column and a whole `regions` configuration section, and
#: has been since M3. One module DID have to change rather than be exempted:
#: `sources/catalogue.py` called a parsed entry of our own catalogue `raw`,
#: which is the name this guard reads as a vendor blob. The guard was right and
#: the variable was misnamed.
#:
#: No neutral module reads any of them from a payload. Every one below reaches
#: them through `JobFacts` or through a column, which is checkable:
#: `resolve_metadata` is the only path from a payload to a dimension, and it is
#: handed the field map rather than a name.
#: Words too generic for this guard to say anything about, anywhere.
#:
#: Separate from the per-module list because the honest thing to record is that
#: the guard has NO power over these, rather than to sprinkle the same
#: exception through every file that trips it and leave a reader thinking it
#: still watches them.
#:
#: `type` is the whole list, and it arrived with the Jooble adapter, which
#: calls its employment-type field exactly that. It is also the first key of
#: every JSON Schema this project writes, which is why four LLM vendor modules
#: and the local contract failed the moment it became a declared path.
#:
#: The cost is real and bounded: if generic code ever reads `payload["type"]`
#: from a Jooble response, this guard will not catch it. The mitigation is the
#: one that was already load-bearing -- `resolve_metadata` is the only path
#: from a payload to a dimension, and it takes a field map rather than a name.
#:
#: `remote` is the second, and it arrived with the Torre adapter, whose payload
#: carries a top-level boolean of exactly that name. It is also a value in this
#: product's own work-model vocabulary, so it appears as a plain string in the
#: matcher, the presenter, the demo seeder and the configuration writer -- six
#: modules failed the moment it became a declared path, and not one of them
#: reads a payload.
#:
#: **Get on Board declares `attributes.remote`, and that stays guarded.** The
#: dotted form is not a phrase that occurs by accident, so the protection this
#: guard actually provides survives for every provider that namespaces its
#: fields. What is lost is bounded to Torre's bare key, and the load-bearing
#: mitigation is unchanged: `resolve_metadata` is the only route from a payload
#: to a dimension and it is handed a field map, never a name.
TOO_GENERIC_TO_GUARD: frozenset[str] = frozenset({"type", "remote"})

ORDINARY_WORD_USES: dict[str, frozenset[str]] = {
    "llm/vendors/anthropic.py": frozenset({"salary"}),
    "llm/vendors/openai.py": frozenset({"salary"}),
    "llm/vendors/openai_compatible.py": frozenset({"salary"}),
    "local_ai/contract.py": frozenset({"salary"}),
    "match/engine.py": frozenset({"employment_type", "salary"}),
    "pipeline/demo_seed.py": frozenset({"employment_type", "salary"}),
    # The candidate's own country of residence, and the NAME of the validator
    # that checks it is two letters. Neither is a vendor payload path: this
    # module reads nothing a provider sent and writes only the person's own
    # answers into her own file.
    "config/candidate_writer.py": frozenset({"country"}),
    "sources/catalogue.py": frozenset({"region"}),
    # The same word, in the same sense, in the module that renders the source
    # matrix: `region` here is the catalogue's own column saying which part of
    # the world a SOURCE covers. `sources/catalogue.py` above is allowed it for
    # exactly that reason, and this reads the field it defines.
    "sources/matrix.py": frozenset({"region"}),
    # The candidate's own country of residence, on the first-run screen, in
    # exactly the sense `config/candidate_writer.py` is already allowed it: the
    # key names WHERE SHE SAID SHE LIVES, read from her own configuration.
    # Nothing in this module reads a provider payload.
    "web/workspace_api.py": frozenset({"country"}),
    # Where a job in the candidate's OWN CV says it was done ("Remote", "Sao
    # Paulo"), read by `cv/structure.py` off a date line and stored in
    # `cv_entry.location`. Her document, never a provider payload.
    "web/cv_api.py": frozenset({"location"}),
    "storage/mvp_repo.py": frozenset({"country", "employment_type", "salary", "region"}),
    "web/api.py": frozenset({"country", "employment_type", "salary", "region"}),
    "web/presenter.py": frozenset({"employment_type", "salary"}),
}


def path_hits(text: str) -> list[str]:
    """Declared payload paths appearing in `text`.

    Dotted paths are matched as substrings -- `categories.commitment` is not a
    phrase that occurs by accident. Single-word paths are matched only on exact
    equality, because `country` is an ordinary English word and `hq_country` in
    the company registry is not a leak.
    """

    def leaks(path: str) -> bool:
        return path in text if "." in path else text == path

    return sorted(path for path in _declared_paths() if leaks(path))


@pytest.mark.parametrize(
    "module_path", NEUTRAL_MODULES, ids=lambda p: str(p.relative_to(SRC)).replace("\\", "/")
)
def test_no_vendor_payload_path_appears_outside_providers(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)

    relative = str(module_path.relative_to(SRC)).replace("\\", "/")
    allowed = ORDINARY_WORD_USES.get(relative, frozenset()) | TOO_GENERIC_TO_GUARD
    offenders = [
        f"{hits} in {node.value[:60]!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and (hits := [p for p in path_hits(node.value) if p not in allowed])
    ]

    assert not offenders, (
        f"{module_path.relative_to(SRC)} spells a provider payload path: {offenders}. "
        "Generic code asks the provider for its field_map and hands it to "
        "resolve_metadata; it never names a path itself."
    )


def test_the_path_detector_is_actually_armed() -> None:
    """A guard on the guard, twice over: the path list must be non-empty, and it
    must distinguish a real leak from an ordinary word."""
    declared = _declared_paths()
    assert len(declared) >= 6
    assert "workplaceType" in declared
    assert "categories.commitment" in declared

    assert path_hits("workplaceType") == ["workplaceType"]
    assert path_hits('payload["categories.commitment"]') == ["categories.commitment"]
    assert path_hits("country") == ["country"]
    assert path_hits("hq_country") == [], "an ordinary column name is not a leak"
    assert path_hits("the country of residence") == []


def test_the_ordinary_word_exemptions_stay_narrow() -> None:
    """The exemption list is the one place this guard can be quietly disarmed.

    Two properties, both cheap: every exempted module exists, and every
    exempted word is one the detector would otherwise flag -- so an entry that
    has stopped being needed shows up as dead rather than sitting there giving
    cover to a word nobody has checked.
    """
    for module, words in ORDINARY_WORD_USES.items():
        assert (SRC / module).is_file(), f"{module} no longer exists"
        assert words, f"{module} has an empty exemption; remove the entry"
        assert words <= _declared_paths(), f"{module} exempts a word nothing declares: {words}"
        assert all("." not in word for word in words), (
            f"{module} exempts a DOTTED path: {words}. Only ordinary single "
            "words can collide by accident; a dotted path is always a leak."
        )


def test_the_compensation_paths_are_guarded_too() -> None:
    """The gap this file had: a whole second reading mechanism, undeclared.

    A field map declares paths that carry a canonical dimension. Pay is an
    amount rather than a dimension, so no compensation path was in any declared
    list, and `payload["salaryRange"]` in generic code would have passed both
    guards in this file -- the name guard because "salaryRange" names no vendor,
    and the path guard because it derived its list from the field maps alone.
    """
    declared = _declared_paths()

    assert "compensation.compensationTiers" in declared
    assert "salaryRange" in declared
    assert "currency_range" in declared

    assert path_hits('payload["salaryRange"]') == []
    assert path_hits("salaryRange") == ["salaryRange"]
    assert path_hits('tier["compensation.compensationTiers"]') == ["compensation.compensationTiers"]


def test_the_only_unguarded_declared_path_is_an_ordinary_word() -> None:
    """The exclusion above is allowed to exist and is not allowed to grow
    quietly. Every excluded path must be a single ordinary word -- a dotted
    vendor path is never an accident and is never excusable."""
    from career_agent.providers.registry import declared_payload_paths

    assert declared_payload_paths() >= UNGUARDABLE_ORDINARY_WORDS
    assert all("." not in word for word in UNGUARDABLE_ORDINARY_WORDS)
    assert len(UNGUARDABLE_ORDINARY_WORDS) <= 1


#: Names this codebase gives a VENDOR blob. Nothing else is exempt from the
#: word check, so this list only has to cover the shapes a leak would take.
#:
#: `payload` is the name `JobPosting.payload` carries and the one every adapter
#: uses; the rest are the names a raw response goes by on its way there. A
#: neutral module has no business subscripting any of them, whatever the key.
PAYLOAD_NAMES: frozenset[str] = frozenset(
    {"payload", "raw", "raw_payload", "body", "envelope", "entry", "blob", "response", "document"}
)


def _payload_reads(tree: ast.AST) -> list[str]:
    """`payload["x"]` and `payload.get("x")` for a declared path `x`.

    The word exemptions above disarm the string check for `country` and
    `employment_type` in five modules, and an independent functional review
    pointed out that the justification for them -- "no neutral module reads it
    from a payload" -- was asserted rather than verified. From here on it is
    verified: an exempted word may still appear as a bare string, because it is
    a column name and a query parameter and a facet, but it may NOT appear as a
    key read off something named like a vendor blob.

    Deliberately narrower than "any subscript". `result["country"]` in the
    facet builder and `facts.get("employment_type")` in the presenter read OUR
    OWN structures, and flagging those would be flagging the thing the
    exemption exists for.
    """
    declared = _declared_paths()
    found: list[str] = []

    def base_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in declared
            and base_name(node.value) in PAYLOAD_NAMES
        ):
            found.append(ast.unparse(node))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "setdefault", "pop"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in declared
            and base_name(node.func.value) in PAYLOAD_NAMES
        ):
            found.append(ast.unparse(node))
    return sorted(found)


@pytest.mark.parametrize(
    "module_path", NEUTRAL_MODULES, ids=lambda p: str(p.relative_to(SRC)).replace("\\", "/")
)
def test_an_exempted_word_is_still_never_read_off_a_payload(module_path: Path) -> None:
    """The half of the guard the exemption list cannot cover.

    `ORDINARY_WORD_USES` says "this word is ours, in this module". It cannot
    say "and it will stay ours", and five modules were exempted on a claim
    nobody could check. This checks it, and it applies to EVERY neutral module
    rather than only the exempted ones -- a module with no exemption would have
    been caught by the word check anyway, so making this universal costs
    nothing and removes a special case.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    reads = _payload_reads(tree)
    assert not reads, (
        f"{module_path.relative_to(SRC)} reads a provider payload path off a payload: {reads}. "
        "Generic code asks the provider for its field_map and hands it to resolve_metadata."
    )


def test_the_payload_read_detector_is_actually_armed() -> None:
    """The guard on the guard, again. A detector that finds nothing is not proof."""
    leak = ast.parse('x = payload["employment_type"]\ny = raw.get("workplaceType")')
    assert _payload_reads(leak) == ["payload['employment_type']", "raw.get('workplaceType')"]

    # And the two shapes it must NOT flag, which are why the word exemptions
    # exist in the first place.
    ours = ast.parse('a = result["country"]\nb = facts.get("employment_type")')
    assert _payload_reads(ours) == []
