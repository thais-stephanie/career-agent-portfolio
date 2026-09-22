"""Review provenance without mistaking an inherited value for a user choice.

The sidecar records explicit review actions against value hashes. It is not a
scoring input. Editing a value invalidates its prior review naturally; reading
this report never writes or replaces the candidate's search.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from career_agent.config.candidate_writer import HEADER, _validates, _write_atomically
from career_agent.config.preferences import PreferenceError, _load_effective, local_search_path

REVIEW_FILE = "search-review.local.yaml"
LEGACY_FILE = "search.legacy-example.yaml"
NEUTRAL_FILE = "search.starter.yaml"
ROOTS = frozenset(
    {
        "lexicon",
        "taxonomy",
        "ambiguous_titles",
        "screening",
        "scoring",
        "preferences",
        "eligibility",
        "normalisation",
        "negation",
        "prominence",
        "confidence",
    }
)
POLICY_ROOTS = frozenset({"normalisation", "negation", "prominence", "confidence"})


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreferenceError(f"{path.name} must contain a mapping.")
    return value


def _leaves(value: Any, path: tuple[str, ...] = ()) -> dict[str, Any]:
    """Scalar values and phrase lists are review units; objects are traversed."""
    if isinstance(value, dict) and value:
        return {
            key: child
            for name, item in value.items()
            for key, child in _leaves(item, (*path, str(name))).items()
        }
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return {
            key: child
            for index, item in enumerate(value)
            for key, child in _leaves(item, (*path, str(index))).items()
        }
    return {".".join(path): value}


def _place(document: dict, path: str, value: Any) -> None:
    node: Any = document
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(parts[-1])] = value
    else:
        node[parts[-1]] = value


def _at(document: dict, path: str) -> Any:
    node: Any = document
    for part in path.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def review_search(config_dir: Path) -> dict:
    current = _load_effective(config_dir)
    legacy = _leaves(_read(config_dir / LEGACY_FILE))
    neutral = _leaves(_read(config_dir / NEUTRAL_FILE))
    reviewed = _read(config_dir / REVIEW_FILE).get("decisions", {})
    rows = []
    for path, value in _leaves(current).items():
        root = path.split(".")[0]
        if root not in ROOTS:
            continue
        fingerprint = _hash(value)
        decision = reviewed.get(path, {})
        if decision.get("value_hash") == fingerprint:
            origin = "reviewed_by_user"
        elif path in neutral and value == neutral[path]:
            origin = "neutral_product_policy"
        elif path in legacy and value == legacy[path]:
            origin = "inherited_legacy_example"
        elif path in legacy:
            origin = "changed_locally"
        else:
            origin = "unknown_provenance"
        # Descriptive labels and identifiers are not occupational assumptions.
        # They are still classified, but the review cannot edit an ID and break
        # references throughout the configuration.
        editable = root not in POLICY_ROOTS and path.split(".")[-1] not in {
            "id",
            "label",
            "responsibility",
            "ambiguity_rule",
        }
        rows.append(
            {
                "path": path,
                "value": value,
                "value_hash": fingerprint,
                "origin": origin,
                "review": decision if origin == "reviewed_by_user" else None,
                "legacy_value": legacy.get(path),
                "neutral_value": neutral.get(path),
                "editable": editable,
                "removable": editable and isinstance(value, (list, dict)),
            }
        )
    return {
        "config_version": current["config_version"],
        "document_hash": _hash(current),
        "rows": rows,
        "note": "Origin comparisons are inferred unless an explicit review is recorded. "
        "No current value is replaced by opening this review.",
    }


def plan_review(
    config_dir: Path,
    *,
    path: str,
    action: str,
    expected_hash: str,
    value: Any = None,
) -> tuple[dict, dict]:
    report = review_search(config_dir)
    if expected_hash != report["document_hash"]:
        raise PreferenceError(
            "The search changed since this review opened. Reload its current values."
        )
    row = next((row for row in report["rows"] if row["path"] == path), None)
    if row is None or action not in {"keep", "edit", "remove"}:
        raise PreferenceError("Choose a current setting and Keep, Edit or Remove.")
    if action != "keep" and not row["editable"]:
        raise PreferenceError("This is a product policy or identifier, not an editable assumption.")
    if action == "remove" and not row["removable"]:
        raise PreferenceError(
            "Edit this scalar preference explicitly instead of removing its type."
        )
    before = _load_effective(config_dir)
    document = copy.deepcopy(before)
    if action == "remove":
        value = [] if isinstance(row["value"], list) else {}
    if action != "keep":
        _place(document, path, value)
    changed = document != before
    if changed:
        document["config_version"] = int(before["config_version"]) + 1
        _validates(document)
    proposed = _leaves(document)
    prior = _leaves(before)
    diff = [
        {"path": key, "before": prior.get(key), "after": proposed.get(key)}
        for key in sorted(set(prior) | set(proposed))
        if prior.get(key) != proposed.get(key)
    ]
    plan = {
        "path": path,
        "action": action,
        "diff": diff,
        "config_version": document["config_version"],
        "rescore_required": changed,
        "confirmation": _hash(
            {"before": before, "after": document, "action": action, "path": path}
        ),
    }
    return document, plan


def apply_review(
    config_dir: Path,
    *,
    path: str,
    action: str,
    expected_hash: str,
    confirmation: str,
    value: Any = None,
) -> dict:
    document, plan = plan_review(
        config_dir,
        path=path,
        action=action,
        expected_hash=expected_hash,
        value=value,
    )
    if confirmation != plan["confirmation"]:
        raise PreferenceError("Confirm the current preview before applying this review.")
    if plan["rescore_required"]:
        _write_atomically(
            local_search_path(config_dir),
            HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
        )
    metadata = _read(config_dir / REVIEW_FILE)
    metadata.setdefault("decisions", {})[path] = {
        "action": action,
        "value_hash": _hash(_at(document, path)),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "actor": "user",
    }
    _write_atomically(
        config_dir / REVIEW_FILE,
        "# Explicit search review decisions; private, not a scoring input.\n"
        + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
    )
    return plan
