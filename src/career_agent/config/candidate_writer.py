"""Editing the candidate's own choices, without opening a YAML file.

The product already had a writer for one thing -- the phrase lists behind a
signal -- and nothing for the choices a person is most likely to want to
change: how they will work, what engagement they will accept, what level they
are looking for, what a job has to pay, where they live. Those lived only in
`search.local.yaml`, which meant "maintainable" meant "maintainable by somebody
who edits YAML".

Three rules the previous writer did not keep, and each is here because of what
happens without it.

**Validate before writing, through the real loader.** A file that fails to load
is worse than an unchanged one: every later command refuses, and the person who
made a typo in a dropdown has no way to know that is what happened. The
candidate document is assembled in memory, loaded by `load_search_config` from
a temporary directory, and only written if that succeeds.

**Write atomically, and keep the last good copy.** `Path.write_text` truncates
first. A crash between truncate and write leaves an empty configuration and
loses the person's search. This writes a sibling temporary file and renames it,
which is atomic on every filesystem this runs on, and keeps `.backup` so the
previous version is one copy away.

**Never touch the committed files.** `search.worked-example.yaml` is the shipped
baseline and the thing `git checkout` restores when an edit goes wrong.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from career_agent.config.preferences import PreferenceError, _load_effective, local_search_path

#: Every field this writer will change, and where it lives in the document.
#:
#: A whitelist, and that is the security property. The body of a PATCH arrives
#: from a browser; without this it could name `scoring.components` or
#: `eligibility.blockers` and rewrite how matching works from a settings panel.
#: A field absent from this table cannot be written, whatever the request says.
FIELDS: dict[str, dict[str, Any]] = {
    "work_models": {
        "path": ("preferences", "remote", "accepted_work_models"),
        "kind": "enum_list",
        "choices": ("REMOTE", "HYBRID", "ONSITE"),
        "label": "Ways of working you will accept",
    },
    "require_remote": {
        "path": ("preferences", "remote", "require_remote"),
        "kind": "bool",
        "label": "Only consider remote roles",
    },
    "contract_preferred": {
        "path": ("preferences", "contract", "preferred"),
        "kind": "enum_list",
        "choices": ("FULL_TIME_EMPLOYEE", "CONTRACTOR_B2B", "EOR"),
        "label": "Engagements you would accept",
    },
    "contract_unwanted": {
        "path": ("preferences", "contract", "unwanted"),
        "kind": "enum_list",
        "choices": ("FULL_TIME_EMPLOYEE", "CONTRACTOR_B2B", "EOR"),
        "label": "Engagements you would not",
        "allow_empty": True,
    },
    # STAFF and PRINCIPAL joined `Seniority` on 2026-09-07 and this list did
    # not follow, so the two levels that most needed a candidate's opinion were
    # the two she could not express one about from the screen.
    "seniority_preferred": {
        "path": ("preferences", "seniority", "preferred"),
        "kind": "enum_list",
        "choices": ("INTERN", "JUNIOR", "MID", "SENIOR", "STAFF", "PRINCIPAL", "LEAD"),
        "label": "Levels you are looking for",
    },
    # A SECOND QUESTION, AND NOT THE OPPOSITE OF THE FIRST.
    #
    # `preferred` prices a level: a posting outside it scores lower and stays
    # on screen. This one hides it. "Not what I am looking for" and "do not
    # show me these" are different sentences, and one control answering both
    # is how a preference quietly becomes a filter.
    #
    # Empty is the neutral value and the shipped default, so `allow_empty` is
    # required here in a way it deliberately is not on `eligible_scopes`:
    # emptying THIS hides nothing, which is exactly what most people want.
    #
    # It was readable in `search.local.yaml` and editable only by opening it.
    # For the owner that is the difference between a default list of fifty that
    # holds fourteen LEAD postings and one that holds none -- measured on her
    # real corpus on 2026-09-08 -- and the answer is hers to give.
    "seniority_excluded": {
        "path": ("preferences", "seniority", "excluded"),
        "kind": "enum_list",
        "choices": ("INTERN", "JUNIOR", "MID", "SENIOR", "STAFF", "PRINCIPAL", "LEAD"),
        "label": "Levels to keep off the list",
        "allow_empty": True,
    },
    "travel_max_pct": {
        "path": ("preferences", "travel", "max_tolerated_pct"),
        "kind": "percent",
        "label": "Most travel you would accept",
    },
    "compensation_target": {
        "path": ("preferences", "compensation", "target_monthly_amount"),
        "kind": "money",
        "label": "What you are aiming for, per month",
    },
    "compensation_currency": {
        "path": ("preferences", "compensation", "currency"),
        "kind": "currency",
        "label": "In which currency",
    },
    "candidate_country": {
        "path": ("eligibility", "candidate_country"),
        "kind": "country",
        "label": "Where you live",
    },
    # WHERE AN EMPLOYER MAY HIRE YOU FROM, which is a different question from
    # where you live and the one the geography gate actually asks. It was
    # readable on the profile screen and editable only by opening the file --
    # the single most consequential candidate fact in the configuration, and
    # the one a person is most likely to get wrong on the first run.
    #
    # `allow_empty` is deliberately absent on the scopes. Emptying both of
    # these leaves no way for any posting to pass the geography gate, so the
    # eligible column would read UNRESOLVED for the whole corpus with nothing
    # on screen saying why.
    "eligible_scopes": {
        "path": ("eligibility", "eligible_scopes"),
        "kind": "enum_list",
        # The six region ids `match.places.REGIONS` knows. `EUROPE` used to be
        # offered here and is not a region id anywhere else in the product:
        # `places.yaml` files `europe` under EMEA, so choosing it recorded a
        # scope no gate could ever read. Removed 2026-09-11.
        "choices": ("WORLDWIDE", "AMERICAS", "LATAM", "EMEA", "APAC", "NORTH_AMERICA"),
        "label": "Hiring scopes that include you",
    },
    # Countries, not scopes: `BR` here means an employer hiring specifically in
    # Brazil can hire you. Free-form two-letter codes rather than a list, for
    # the same reason the currency is: this program has no opinion about which
    # countries exist, and refusing an unusual one would be this file having
    # one about somebody's passport.
    "eligible_countries": {
        "path": ("eligibility", "eligible_countries"),
        "kind": "country_list",
        "label": "Countries that may hire you directly",
        "allow_empty": True,
    },
}

#: An ISO 4217 code is three letters. Not validated against a list of real
#: currencies: this system converts between none of them, so a code it does not
#: recognise is a label rather than a risk, and refusing an unusual one would
#: be this file having an opinion about somebody's country.
_CURRENCY_LENGTH = 3
#: An ISO 3166-1 alpha-2 code.
_COUNTRY_LENGTH = 2


def _coerce(field: str, spec: dict[str, Any], value: Any) -> Any:
    """One value, validated against what the field actually accepts.

    Raises `PreferenceError` with a sentence a person could act on, because
    this message reaches a settings panel and "invalid input" does not tell
    anybody which box to look at.
    """
    kind = spec["kind"]
    label = spec["label"]

    if kind == "bool":
        if not isinstance(value, bool):
            raise PreferenceError(f"{label} is a yes or no answer.")
        return value

    if kind == "enum_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PreferenceError(f"{label} is a list of choices.")
        choices = spec["choices"]
        unknown = [v for v in value if v not in choices]
        if unknown:
            raise PreferenceError(
                f"{label}: {', '.join(unknown)} is not one of {', '.join(choices)}."
            )
        # Order preserved, duplicates dropped. A list that says REMOTE twice is
        # not a different search from one that says it once.
        seen: list[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        if not seen and not spec.get("allow_empty"):
            raise PreferenceError(
                f"{label}: choosing nothing here would match nothing. "
                "Leave at least one, or this is not the control you want."
            )
        return seen

    if kind == "percent":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            raise PreferenceError(f"{label} is a whole number of per cent, from 0 to 100.")
        return value

    if kind == "money":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise PreferenceError(f"{label} cannot be negative.")
        # Zero means "not stated" throughout this configuration and is a
        # legitimate answer, so it is not rejected.
        return int(value)

    if kind == "currency":
        if not isinstance(value, str) or len(value.strip()) != _CURRENCY_LENGTH:
            raise PreferenceError(f"{label} is a three-letter code, such as BRL or USD.")
        return value.strip().upper()

    if kind == "country":
        if not isinstance(value, str) or len(value.strip()) != _COUNTRY_LENGTH:
            raise PreferenceError(f"{label} is a two-letter country code, such as BR.")
        return value.strip().upper()

    if kind == "country_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PreferenceError(f"{label} is a list of two-letter country codes.")
        codes: list[str] = []
        for item in value:
            code = item.strip().upper()
            if len(code) != _COUNTRY_LENGTH or not code.isalpha():
                raise PreferenceError(
                    f"{label}: {item!r} is not a two-letter country code, such as BR."
                )
            if code not in codes:
                codes.append(code)
        if not codes and not spec.get("allow_empty"):
            raise PreferenceError(f"{label}: leave at least one.")
        return codes

    raise PreferenceError(f"{field} cannot be edited here.")


def _place(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node: Any = document
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def _validates(document: dict[str, Any]) -> None:
    """Load the proposed document the way every command will, or refuse it.

    In a temporary directory with nothing else in it, so the loader cannot be
    satisfied by the file this write is about to replace.
    """
    from career_agent.config.search_config import load_search_config

    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        (staging / "search.local.yaml").write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        places = Path("config") / "places.yaml"
        if places.exists():
            shutil.copy(places, staging / "places.yaml")
        try:
            load_search_config(staging)
        except Exception as exc:  # noqa: BLE001 - the loader raises several types
            raise PreferenceError(
                f"That change would produce a configuration this system cannot read: {exc}"
            ) from exc


HEADER = (
    "# Your search. Written by Career Agent, and safe to edit by hand.\n"
    "#\n"
    "# Gitignored: it holds YOUR preferences and never leaves this machine.\n"
    "# `config_version` is bumped on every change, so scores computed under an\n"
    "# older version stay readable instead of being silently reinterpreted.\n"
    "# Re-run `career-agent rescore` to score against this version.\n"
    "#\n"
    "# The previous version is kept beside this one as search.local.yaml.backup.\n\n"
)


def _write_atomically(target: Path, text: str) -> None:
    """Replace a file without ever leaving a partial one behind.

    `Path.write_text` truncates first, so a crash between truncate and write
    leaves an empty configuration -- which for this file means the person's
    entire search is gone and every later command refuses to start.

    A sibling temporary file, flushed and fsynced, then renamed. `os.replace`
    is atomic within a filesystem, and a sibling guarantees the same one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, target.with_suffix(target.suffix + ".backup"))

    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".yaml")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def set_candidate_fields(config_dir: Path, changes: dict[str, Any]) -> tuple[Path, int, list[str]]:
    """Apply one or more candidate choices. Returns the file, version and fields.

    Every change in one write. A panel that saves four fields must not be able
    to leave two of them applied because the third was rejected, and four
    separate writes would bump the version four times and invalidate the corpus
    three times over for no reason.

    **AND A SAVE THAT CHANGES NOTHING BUMPS NOTHING.** That is the same
    sentence carried to its end. The version is not an edit counter; it is what
    a stored score is TRUE RELATIVE TO, so raising it detaches every row in
    `job_match` and the list opens empty until a rescore has run -- 19,469 rows
    and about seven minutes, on the corpus this was measured against.

    Pressing Save on a panel you only came to read should not cost that. It did:
    the version rose, the scores detached, and the screen said "these jobs have
    not been scored yet", which is true and gives no hint that the cause was a
    button that appeared to do nothing.
    """
    if not changes:
        raise PreferenceError("Nothing to save.")

    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise PreferenceError(f"Not something this panel can change: {', '.join(unknown)}.")

    before = copy.deepcopy(_load_effective(config_dir))
    document = copy.deepcopy(before)
    applied: list[str] = []
    for field, value in changes.items():
        spec = FIELDS[field]
        _place(document, tuple(spec["path"]), _coerce(field, spec, value))
        applied.append(field)

    target = local_search_path(config_dir)
    if document == before and target.exists():
        # Nothing moved. Not an error -- re-saving a value you did not change
        # is an ordinary thing to do -- so it reports the fields it was asked
        # about and the version that still stands. `target.exists()` is the
        # exception: a first save has to MATERIALISE the local file even when
        # its content equals the example it was resolved from.
        return target, int(before.get("config_version", 1)), sorted(applied)

    version = int(document.get("config_version", 1)) + 1
    document["config_version"] = version

    _validates(document)

    _write_atomically(
        target,
        HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
    )
    return target, version, sorted(applied)


def current_candidate_fields(config_dir: Path) -> dict[str, Any]:
    """What each editable field holds right now, for a panel to render."""
    document = _load_effective(config_dir)
    out: dict[str, Any] = {}
    for field, spec in FIELDS.items():
        node: Any = document
        for key in spec["path"]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        out[field] = node
    return out
