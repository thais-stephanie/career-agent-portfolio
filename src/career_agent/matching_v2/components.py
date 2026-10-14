"""Which component made a derived artefact, and what a change makes stale.

One version per component replaces, for the artefacts of this package, the
single `READER_IDENTITY` that made every reader change re-read every posting
(ADR-0037). A component names the components it depends on and the raw
inputs it reads; a change to either makes it stale, and so everything that
depends on it, and nothing else.

Raw inputs are names, not values:

* `job.text`      the title and the advert as collected (its content hash);
* `job.metadata`  what the board stated beside the text (location, workplace
                  type, a declared scope, a structured salary);
* `machinery.headings` the section headings the parser cuts an advert at;
* `profile.*`     one section of a person's preferences each. A profile
                  component reads only the sections it names, which is what
                  lets a salary preference move without re-reading work.

Bump a component's version in the same commit that changes what it
produces, exactly as `READER_IDENTITY` was bumped (`match/identity.py`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Component:
    name: str
    version: str
    #: `job`: the same for every profile, stored in the shared catalogue.
    #: `profile`: depends on one person, stored in that profile's database.
    scope: str
    depends_on: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()


#: The graph. Each version is the one this build produces.
REGISTRY: dict[str, Component] = {
    c.name: c
    for c in (
        # -- shared facts of a posting ------------------------------------
        # Section headings are machinery (identical in the starter and the
        # worked example), not a preference; they are an input all the same.
        Component("text_parser", "1", "job", inputs=("job.text", "machinery.headings")),
        Component("language", "1", "job", ("text_parser",)),
        Component("sentence_context", "1", "job", ("text_parser",)),
        Component("work_facts", "1", "job", ("language", "sentence_context")),
        Component("pay_facts", "1", "job", ("text_parser",), ("job.metadata",)),
        Component("location_facts", "1", "job", ("text_parser",), ("job.metadata",)),
        Component("seniority_facts", "1", "job", ("text_parser",)),
        Component("engagement_facts", "1", "job", ("text_parser",), ("job.metadata",)),
        # -- what a posting is for one person ------------------------------
        Component("work_match", "1", "profile", ("work_facts",), ("profile.lexicon",)),
        Component("tools_match", "1", "profile", ("work_facts",), ("profile.lexicon",)),
        Component(
            "eligibility",
            "1",
            "profile",
            # The gates still read the advert's text for configured blocker
            # phrases (ADR-0037, "not done"), hence `text_parser`.
            ("location_facts", "engagement_facts", "text_parser"),
            ("profile.eligibility",),
        ),
        Component(
            "compensation_match",
            "1",
            "profile",
            # The period default depends on where the posting hires.
            ("pay_facts", "eligibility"),
            ("profile.salary",),
        ),
        Component("seniority_match", "1", "profile", ("seniority_facts",), ("profile.seniority",)),
        Component(
            "score",
            "1",
            "profile",
            (
                "work_match",
                "tools_match",
                "eligibility",
                "compensation_match",
                "seniority_match",
                "engagement_facts",
            ),
            ("profile.weights", "profile.contract", "profile.work_model"),
        ),
    )
}

JOB_COMPONENTS = tuple(n for n, c in REGISTRY.items() if c.scope == "job")
PROFILE_COMPONENTS = tuple(n for n, c in REGISTRY.items() if c.scope == "profile")


def affected(changed: Iterable[str], registry: Mapping[str, Component] = REGISTRY) -> set[str]:
    """Every component made stale by a change to `changed`.

    `changed` holds component names (a version moved) and raw input names
    (`job.text`, `profile.salary`, ...). The answer is closed over the graph:
    a stale component makes every component that depends on it stale too.
    """
    changed = set(changed)
    stale = {
        name for name, c in registry.items() if name in changed or changed.intersection(c.inputs)
    }
    grew = True
    while grew:
        grew = False
        for name, c in registry.items():
            if name not in stale and stale.intersection(c.depends_on):
                stale.add(name)
                grew = True
    return stale


def lineage(name: str, registry: Mapping[str, Component] = REGISTRY) -> dict[str, str]:
    """`name` and every component it depends on, transitively, with versions."""
    out: dict[str, str] = {}
    todo = [name]
    while todo:
        current = registry[todo.pop()]
        if current.name in out:
            continue
        out[current.name] = current.version
        todo.extend(current.depends_on)
    return out


def inputs_of(name: str, registry: Mapping[str, Component] = REGISTRY) -> set[str]:
    """Every raw input `name` reads, directly or through what it depends on."""
    return {i for dep in lineage(name, registry) for i in registry[dep].inputs}


def stamp(
    name: str,
    input_digests: Mapping[str, str],
    registry: Mapping[str, Component] = REGISTRY,
) -> str:
    """The identity of an artefact: its lineage's versions plus the digests of
    exactly the raw inputs it reads. Two artefacts with the same stamp were
    made by the same code from the same inputs."""
    needed = sorted(inputs_of(name, registry))
    missing = [i for i in needed if i not in input_digests]
    if missing:
        raise KeyError(f"{name} reads {missing} and no digest was given for them")
    payload = {
        "component": name,
        "versions": sorted(lineage(name, registry).items()),
        "inputs": {i: input_digests[i] for i in needed},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def digest(value: object) -> str:
    """A content address for an input section, stable across key order."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def stale(
    names: Iterable[str],
    stored_versions: Mapping[str, str],
    stored_inputs: Mapping[str, str],
    current_inputs: Mapping[str, str],
    registry: Mapping[str, Component] = REGISTRY,
) -> set[str]:
    """Which of `names` an artefact written under `stored_versions` from
    `stored_inputs` no longer is: a version in its lineage moved, or one of
    the inputs it reads did. The same answer as comparing stamps, from what a
    compact row keeps (one versions map and the input digests)."""
    out = set()
    for name in names:
        moved_version = any(
            stored_versions.get(dep) != version for dep, version in lineage(name, registry).items()
        )
        moved_input = any(
            stored_inputs.get(i) != current_inputs.get(i) for i in inputs_of(name, registry)
        )
        if moved_version or moved_input:
            out.add(name)
    return out


def versions(names: Iterable[str], registry: Mapping[str, Component] = REGISTRY) -> dict[str, str]:
    return {n: registry[n].version for n in names}
