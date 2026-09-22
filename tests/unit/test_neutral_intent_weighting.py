"""Frozen generic setup weighting contract; no occupation-specific allocation."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from career_agent.config.search_config import SearchConfig
from career_agent.config.setup import Answers, apply_answers
from career_agent.match.engine import JobFacts, match_job

ROOT = Path(__file__).resolve().parents[2]


def starter():
    return yaml.safe_load((ROOT / "config/search.starter.yaml").read_text(encoding="utf-8"))


def component(config, name="responsibilities"):
    return config["scoring"]["components"][name]


@pytest.mark.parametrize(
    "category,field", [("responsibilities", "role_examples"), ("technologies", "skills")]
)
@pytest.mark.parametrize("count", [1, 2, 3, 7, 13, 20])
def test_equal_bounded_allocation_and_real_contribution(category, field, count):
    phrases = tuple(f"phrase {i}" for i in range(count))
    config, _ = apply_answers(starter(), Answers(**{field: phrases}))
    c = component(config, category)
    assert len(c["weights"]) == count
    assert len(set(c["weights"].values())) == 1
    assert 0 < sum(c["weights"].values()) <= c["max"]
    assert sum(c["weights"].values()) == pytest.approx(c["max"])
    loaded = SearchConfig.model_validate(config)
    result = match_job(
        loaded,
        JobFacts(
            title="Team member",
            description="Responsibilities\n" + "\n".join(f"You will perform {p}." for p in phrases),
        ),
        computed_at="2026-09-22T00:00:00Z",
    )
    scored = next(c for c in result.components if c.component_id == category)
    assert scored.points > 0
    assert scored.points <= scored.max_points
    assert scored.points == pytest.approx(scored.max_points)
    reverse, _ = apply_answers(starter(), Answers(**{field: tuple(reversed(phrases))}))
    assert reverse == config
    again, _ = apply_answers(yaml.safe_load(yaml.safe_dump(config)), Answers(**{field: phrases}))
    assert again == config


def test_add_remove_rename_empty_and_unanswered():
    first, _ = apply_answers(starter(), Answers(role_examples=("alpha",)))
    added, _ = apply_answers(first, Answers(role_examples=("alpha", "beta")))
    assert component(added)["weights"] == {"alpha": 12.5, "beta": 12.5}
    removed, _ = apply_answers(added, Answers(role_examples=("beta",)))
    assert component(removed)["weights"] == {"beta": 25}
    renamed, _ = apply_answers(removed, Answers(role_examples=("gamma",)))
    assert component(renamed)["weights"] == {"gamma": 25}
    same, _ = apply_answers(renamed, Answers())
    assert same == renamed
    empty, report = apply_answers(same, Answers(role_examples=()))
    assert "intent weights" in report.changed_sections
    assert component(empty)["weights"] == {}
    assert "setup_weights" not in component(empty)


def test_explicit_and_manually_edited_weights_are_preserved():
    base = starter()
    base["lexicon"]["explicit"] = {"label": "Explicit", "patterns": ["explicit"]}
    component(base)["weights"]["explicit"] = 3.25
    config, _ = apply_answers(base, Answers(role_examples=("explicit", "alpha")))
    assert component(config)["weights"] == {"explicit": 3.25, "alpha": 25}
    component(config)["weights"]["alpha"] = 4.5
    result, _ = apply_answers(config, Answers(role_examples=("beta",)))
    assert component(result)["weights"] == {"explicit": 3.25, "alpha": 4.5, "beta": 25}
    assert component(result)["setup_weights"] == {"beta": 25}


def test_question_alone_controls_category_even_for_identical_words():
    config, _ = apply_answers(
        starter(),
        Answers(
            role_examples=("tool sounding words",),
            skills=("work sounding words", "tool sounding words"),
        ),
    )
    assert component(config)["weights"] == {"tool_sounding_words": 25}
    assert component(config, "technologies")["weights"] == {
        "tool_sounding_words": 10,
        "work_sounding_words": 10,
    }


def test_worked_example_preserves_every_explicit_weight_and_legacy_digest():
    base = yaml.safe_load((ROOT / "config/search.worked-example.yaml").read_text(encoding="utf-8"))
    original = copy.deepcopy(base)
    config, _ = apply_answers(
        base, Answers(role_examples=("some new work",), skills=("some new tool",))
    )
    for name, c in original["scoring"]["components"].items():
        for signal, value in c.get("weights", {}).items():
            assert component(config, name)["weights"][signal] == value
        assert component(config, name)["max"] == c["max"]
    loaded = SearchConfig.model_validate(base)
    legacy = loaded.model_dump(mode="json")
    for c in legacy["scoring"]["components"].values():
        c.pop("setup_weights", None)
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert loaded.digest == expected
    assert base == original


def test_neutral_empty_no_generated_defaults():
    base = starter()
    result, _ = apply_answers(base, Answers(role_examples=(), skills=()))
    assert result == base
    assert not result["lexicon"]


def test_explicit_empty_answer_is_an_answer_for_cli_clear():
    assert Answers().is_empty()
    assert not Answers(role_examples=()).is_empty()
    assert not Answers(skills=()).is_empty()


@pytest.mark.parametrize(
    "field,category", [("role_examples", "responsibilities"), ("skills", "technologies")]
)
def test_cli_clear_round_trip(tmp_path, field, category):
    from typer.testing import CliRunner

    from career_agent.cli import app

    (tmp_path / "search.starter.yaml").write_text(yaml.safe_dump(starter()), encoding="utf-8")
    answers = tmp_path / "answers.yaml"
    runner = CliRunner()
    command = ["setup", "--config-dir", str(tmp_path), "--answers", str(answers)]
    answers.write_text(yaml.safe_dump({field: ["alpha"]}), encoding="utf-8")
    assert runner.invoke(app, command).exit_code == 0
    answers.write_text(yaml.safe_dump({field: []}), encoding="utf-8")
    cleared = runner.invoke(app, command)
    assert cleared.exit_code == 0
    assert "intent weights" in cleared.stdout
    assert "nothing changed" not in cleared.stdout
    config = yaml.safe_load((tmp_path / "search.local.yaml").read_text(encoding="utf-8"))
    assert component(config, category)["weights"] == {}
    assert runner.invoke(app, command).exit_code == 0
    assert yaml.safe_load((tmp_path / "search.local.yaml").read_text(encoding="utf-8")) == config
