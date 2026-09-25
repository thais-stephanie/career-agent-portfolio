"""First run: what the wizard writes, what it refuses, and what it leaves alone.

Everything here drives `apply_answers` and `run_setup` directly. There is no
terminal in this file, which is the point of splitting the transform from the
prompts: a wizard that can only be tested by typing at it is a wizard nobody
tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from career_agent.config.search_config import load_search_config, local_search_path
from career_agent.config.setup import (
    Answers,
    SetupError,
    apply_answers,
    base_config,
    export_profile,
    import_profile,
    run_setup,
    slugify,
    write_local,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "config" / "search.worked-example.yaml"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A configuration directory holding the shipped files and nothing private.

    Both of them: the worked example is no longer where a plain `setup` begins,
    so a directory carrying only that one now tests a path nobody takes.
    """
    shutil.copy(EXAMPLE, tmp_path / "search.worked-example.yaml")
    shutil.copy(EXAMPLE.parent / "search.starter.yaml", tmp_path / "search.starter.yaml")
    return tmp_path


def load_written(config_dir: Path) -> dict:
    return yaml.safe_load(local_search_path(config_dir).read_text(encoding="utf-8"))


# =========================================================================
# the transform is pure
# =========================================================================


def test_it_does_not_mutate_the_base(config_dir: Path) -> None:
    """A caller holding the base still holds the base."""
    base, _ = base_config(config_dir)
    before = yaml.safe_dump(base, sort_keys=True)
    apply_answers(base, Answers(role_examples=("Revenue Operations",)))
    assert yaml.safe_dump(base, sort_keys=True) == before


def test_answering_nothing_changes_nothing(config_dir: Path) -> None:
    """Enter through the whole wizard is a valid answer, and a no-op.

    The example IS a working configuration. Forcing somebody to invent
    preferences before they have seen a posting would be the wrong trade.
    """
    base, _ = base_config(config_dir)
    config, result = apply_answers(base, Answers())
    assert config == base
    assert result.config_version == base["config_version"]
    assert not result.changed_sections
    assert Answers().is_empty()


def test_the_version_is_bumped_only_when_something_changed(config_dir: Path) -> None:
    """A bump says "the scores you have describe the previous question".

    Bumping it for a run that changed nothing would invalidate a whole scored
    corpus for free.
    """
    base, _ = base_config(config_dir)
    _, unchanged = apply_answers(base, Answers())
    assert unchanged.config_version == base["config_version"]

    _, changed = apply_answers(base, Answers(shortlist_min_score=61))
    assert changed.config_version == base["config_version"] + 1


# =========================================================================
# role examples are signals, not a title whitelist
# =========================================================================


def test_a_role_example_becomes_a_lexicon_entry_not_a_title_rule(config_dir: Path) -> None:
    """The founding complaint of this product, asserted.

    The same job is posted as "Business Systems Analyst", "RevOps Engineer" and
    "Sales Systems Manager". A title whitelist finds one of the three; a phrase
    matched against the whole posting finds all three.
    """
    base, _ = base_config(config_dir)
    taxonomy_before = yaml.safe_dump(base["taxonomy"], sort_keys=True)
    titles_before = yaml.safe_dump(base.get("ambiguous_titles", {}), sort_keys=True)

    # A phrase the shipped example does not already score. "Revenue
    # Operations" is one of its patterns, so answering it correctly adds
    # nothing at all, which is a different property with its own test below.
    config, result = apply_answers(base, Answers(role_examples=("Fractional CFO work",)))

    assert "fractional_cfo_work" in config["lexicon"]
    assert config["lexicon"]["fractional_cfo_work"]["patterns"] == ["fractional cfo work"]
    assert "fractional_cfo_work" in result.signals_added
    # And nothing that decides what a TITLE means was touched.
    assert yaml.safe_dump(config["taxonomy"], sort_keys=True) == taxonomy_before
    assert yaml.safe_dump(config.get("ambiguous_titles", {}), sort_keys=True) == titles_before


def test_a_role_example_uses_the_closed_responsibility_vocabulary(config_dir: Path) -> None:
    """`ResponsibilityCategory` is shared with the extraction contract.

    A configuration inventing a fortieth category would name something no model
    observation can ever carry, and the loader rejects it. `responsibility_other`
    exists for exactly this.
    """
    base, _ = base_config(config_dir)
    config, _ = apply_answers(base, Answers(role_examples=("Something Nobody Has Named",)))
    entry = config["lexicon"]["something_nobody_has_named"]
    assert entry["responsibility"] == "responsibility_other"


def test_a_signal_never_overwrites_one_that_already_exists(config_dir: Path) -> None:
    """Two DIFFERENT phrases that slugify the same way.

    This is the case `_unique_id` exists for, and the only one. "API/integration"
    and the example's `api_integration` are not the same phrase -- neither one
    is a pattern of the other -- so the second must be minted rather than
    dropped, and the first must not be touched.

    The test used to answer "API integration", which IS one of the example's
    patterns, and asserted that `api_integration_2` appeared. That entry was
    the defect: two entries carrying one phrase fire together and score it
    twice.
    """
    base, _ = base_config(config_dir, worked_example=True)
    existing = base["lexicon"]["api_integration"]["patterns"]
    config, _ = apply_answers(base, Answers(keywords=("API/integration",)))
    assert config["lexicon"]["api_integration"]["patterns"] == existing
    assert config["lexicon"]["api_integration_2"]["patterns"] == ["api/integration"]


def test_only_the_phrase_the_person_typed_becomes_a_pattern(config_dir: Path) -> None:
    """No invented synonyms.

    A wizard that expanded "HubSpot" into a list of guesses would be putting
    words in somebody's mouth and then scoring postings against them.
    """
    base, _ = base_config(config_dir)
    config, _ = apply_answers(base, Answers(skills=("Duckdb warehousing",)))
    assert config["lexicon"]["duckdb_warehousing"]["patterns"] == ["duckdb warehousing"]


def test_a_negative_signal_is_a_lexicon_entry_with_a_penalty_magnitude(config_dir: Path) -> None:
    """Not a separate list. `config/preferences.py` already records why.

    The weight is a POSITIVE magnitude the scorer subtracts. Setup used to
    write -3, and subtracting -3 added points to every posting that said the
    thing the person wanted less of.
    """
    base, _ = base_config(config_dir)
    config, _ = apply_answers(base, Answers(negative_keywords=("door to door",)))
    assert "door_to_door" in config["lexicon"]
    assert config["scoring"]["soft_penalties"]["weights"]["door_to_door"] == 3


def test_a_dislike_of_something_already_named_weights_that_entry(config_dir: Path) -> None:
    """ "Cold calling" is already a pattern of the example's `cold_outbound`.

    The penalty belongs on THAT entry. Minting a second one gives the corpus
    two signals matching the same phrase, both firing, both subtracting -- so
    the person is penalised twice for one dislike, and again on every re-run.
    """
    base, _ = base_config(config_dir, worked_example=True)
    config, result = apply_answers(base, Answers(negative_keywords=("cold calling",)))

    weights = config["scoring"]["soft_penalties"]["weights"]
    assert weights["cold_outbound"] == 3
    assert "cold_calling" not in config["lexicon"]
    assert result.signals_already_present == [("cold calling", "cold_outbound")]


# =========================================================================
# exclusions, and the sponsorship rules that stop applying
# =========================================================================


def test_a_hard_exclusion_is_a_phrase_the_posting_must_say(config_dir: Path) -> None:
    """Silence never excludes. There is no way to express the opposite here."""
    base, _ = base_config(config_dir)
    config, result = apply_answers(base, Answers(hard_exclusions=("must relocate to Dublin",)))
    added = [b for b in config["eligibility"]["blockers"] if b["id"] == "must_relocate_to_dublin"]
    assert added and added[0]["patterns"] == ["must relocate to dublin"]
    assert added[0]["negation_sensitive"] is True
    assert result.blockers_added == ["must_relocate_to_dublin"]


def test_not_needing_sponsorship_removes_the_sponsorship_blockers(config_dir: Path) -> None:
    """Somebody who can already work where they live is not blocked by
    a posting that declines to sponsor. Keeping those rules would hide jobs
    they can actually take."""
    base, _ = base_config(config_dir, worked_example=True)
    before = [
        b["id"] for b in base["eligibility"]["blockers"] if b.get("gate") == "work_authorization"
    ]
    assert before, "the example must carry at least one to make this meaningful"

    config, result = apply_answers(base, Answers(needs_visa_sponsorship=False))
    after = [
        b["id"] for b in config["eligibility"]["blockers"] if b.get("gate") == "work_authorization"
    ]
    assert after == []
    assert sorted(result.blockers_removed) == sorted(before)


def test_needing_sponsorship_leaves_them_exactly_as_they_were(config_dir: Path) -> None:
    base, _ = base_config(config_dir)
    config, result = apply_answers(base, Answers(needs_visa_sponsorship=True))
    assert config["eligibility"]["blockers"] == base["eligibility"]["blockers"]
    assert result.blockers_removed == []


# =========================================================================
# what it refuses
# =========================================================================


def test_a_pay_target_without_a_currency_is_refused(config_dir: Path) -> None:
    """Nothing here converts between currencies, so a bare number cannot be
    compared. The filter API already refuses this; refusing it at setup means
    the refusal happens where a person can still answer the question."""
    base, _ = base_config(config_dir)
    with pytest.raises(SetupError, match="needs a currency"):
        apply_answers(base, Answers(target_amount=9000))


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (Answers(residence_country="Brazil"), "two-letter"),
        (Answers(target_amount=1, currency="reais"), "three-letter"),
        (Answers(target_amount=1, currency="BRL", period="FORTNIGHT"), "pay period"),
        (Answers(target_amount=-1, currency="BRL"), "negative"),
        (Answers(shortlist_min_score=101), "between 0 and 100"),
        (Answers(fresh_days=0), "at least one day"),
        (Answers(accepted_work_models=("TELEPATHY",)), "unknown work model"),
        (Answers(preferred_contracts=("SLAVERY",)), "unknown contract type"),
        (Answers(preferred_seniorities=("WIZARD",)), "unknown level"),
    ],
)
def test_an_answer_the_config_cannot_express_is_refused(
    config_dir: Path, answers: Answers, message: str
) -> None:
    base, _ = base_config(config_dir)
    with pytest.raises(SetupError, match=message):
        apply_answers(base, answers)


def test_a_configuration_that_would_not_load_is_never_written(config_dir: Path) -> None:
    """Validated in memory, so a failure leaves the previous file untouched.

    The person who just answered twelve questions has no reason to suspect the
    file, so a broken write would leave the product broken with no way back.
    """
    (config_dir / "search.local.yaml").write_text("label: mine\n", encoding="utf-8")
    with pytest.raises(SetupError, match="does not load"):
        run_setup(config_dir, Answers(shortlist_min_score=60))
    # Untouched, exactly as it was.
    assert local_search_path(config_dir).read_text(encoding="utf-8") == "label: mine\n"


def test_setup_never_writes_the_committed_example(config_dir: Path) -> None:
    """A person's real pay target has no business in a public repository."""
    before = (config_dir / "search.worked-example.yaml").read_text(encoding="utf-8")
    run_setup(config_dir, Answers(target_amount=9000, currency="BRL"))
    assert (config_dir / "search.worked-example.yaml").read_text(encoding="utf-8") == before
    assert local_search_path(config_dir).exists()


def test_write_local_refuses_to_treat_a_file_as_a_directory(config_dir: Path) -> None:
    """The guard, asserted against a call that can actually make it fire.

    This test used to call `write_local(tmp_path, ...)` and assert the written
    name was `search.local.yaml` -- which `local_search_path` computes itself,
    so the assertion held whatever the guard did. An independent functional
    review called it a tautology and it was.

    The mistake worth catching is passing the example FILE where the config
    DIRECTORY belongs, which is one keystroke away and would have destroyed
    committed documentation. The example must come through untouched.
    """
    example = config_dir / "search.worked-example.yaml"
    before = example.read_text(encoding="utf-8")

    with pytest.raises(SetupError) as error:
        write_local(example, {"label": "x"})
    assert "is a file" in str(error.value)
    assert example.read_text(encoding="utf-8") == before

    # And the ordinary call still writes where it should.
    written = write_local(config_dir, {"label": "x"})
    assert written.name == "search.local.yaml"
    assert written.parent == config_dir


# =========================================================================
# it writes something the real loader accepts
# =========================================================================


def test_the_written_file_loads_through_the_real_loader(config_dir: Path) -> None:
    result = run_setup(
        config_dir,
        Answers(
            role_examples=("Revenue Operations",),
            skills=("HubSpot",),
            residence_country="pt",
            target_regions=("EU", "EMEA"),
            accepted_work_models=("REMOTE", "HYBRID"),
            require_remote=True,
            target_amount=4000,
            currency="eur",
            period="MONTH",
            shortlist_min_score=65,
            fresh_days=21,
            label="Systems work, remote from Portugal",
        ),
    )
    config, path = load_search_config(config_dir)
    assert path.name == "search.local.yaml"
    assert config.config_version == result.config_version
    assert config.label == "Systems work, remote from Portugal"
    assert config.eligibility.candidate_country == "PT"
    assert config.thresholds.shortlist_min_score == 65
    assert config.preferences.compensation.currency == "EUR"
    assert config.preferences.remote.require_remote is True


def test_the_written_file_carries_no_personal_value_into_the_example(config_dir: Path) -> None:
    run_setup(config_dir, Answers(target_amount=123456, currency="BRL"))
    example = (config_dir / "search.worked-example.yaml").read_text(encoding="utf-8")
    assert "123456" not in example


# =========================================================================
# re-running edits, and never resets
# =========================================================================


def test_a_second_run_starts_from_what_the_person_already_has(config_dir: Path) -> None:
    """This is what "preserve the existing local configuration" means.

    A first run that always started from the example would silently discard
    every preference tuned since the last one.
    """
    run_setup(config_dir, Answers(label="First", skills=("Duckdb warehousing",)))
    second = run_setup(config_dir, Answers(shortlist_min_score=70))

    assert second.started_from == "search.local.yaml"
    written = load_written(config_dir)
    assert written["label"] == "First", "the label from the first run survived"
    assert "duckdb_warehousing" in written["lexicon"], "the skill from the first run survived"
    assert written["thresholds"]["shortlist_min_score"] == 70


def test_the_base_is_the_neutral_starter_when_there_is_no_local_file(config_dir: Path) -> None:
    """SUPERSEDES `test_the_base_is_the_example_when_there_is_no_local_file`.

    It WAS the example, and that is the defect this replaced: a stranger with
    no local file began their search by inheriting the owner's.
    """
    _, started_from = base_config(config_dir)
    assert started_from == "search.starter.yaml"


# =========================================================================
# export and import
# =========================================================================


def test_export_then_import_round_trips(config_dir: Path, tmp_path: Path) -> None:
    run_setup(config_dir, Answers(label="Mine", shortlist_min_score=66))
    destination = tmp_path / "away" / "profile.yaml"
    export_profile(config_dir, destination)
    assert destination.exists()

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    shutil.copy(EXAMPLE, fresh / "search.worked-example.yaml")
    import_profile(fresh, destination)

    config, _ = load_search_config(fresh)
    assert config.label == "Mine"
    assert config.thresholds.shortlist_min_score == 66


def test_exporting_before_setup_says_so(config_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="no search.local.yaml to export"):
        export_profile(config_dir, tmp_path / "out.yaml")


def test_import_refuses_to_overwrite_without_being_told(config_dir: Path, tmp_path: Path) -> None:
    run_setup(config_dir, Answers(label="Mine"))
    source = tmp_path / "other.yaml"
    export_profile(config_dir, source)
    with pytest.raises(SetupError, match="already exists"):
        import_profile(config_dir, source)


def test_import_refuses_a_file_that_does_not_load(config_dir: Path, tmp_path: Path) -> None:
    """And says whose fault it is. The message used to blame "the answers",
    which sends somebody back to a wizard they never used."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("nonsense: true\n", encoding="utf-8")
    with pytest.raises(SetupError, match="not a valid search configuration"):
        import_profile(config_dir, broken, overwrite=True)


def test_import_of_a_missing_file_says_so(config_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="no such file"):
        import_profile(config_dir, tmp_path / "nowhere.yaml")


# =========================================================================
# small things
# =========================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Revenue Operations", "revenue_operations"),
        ("HubSpot / Salesforce", "hubspot_salesforce"),
        ("  spaced  out  ", "spaced_out"),
        ("C#", "c"),
        ("...", "unnamed"),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_phrases_are_folded_and_deduplicated(config_dir: Path) -> None:
    """Every matcher folds case before comparing, so two spellings of one
    phrase would be two patterns that can never behave differently."""
    base, _ = base_config(config_dir)
    config, result = apply_answers(
        base, Answers(keywords=("Nomad clusters", "nomad clusters", "  NOMAD CLUSTERS  "))
    )
    assert config["lexicon"]["nomad_clusters"]["patterns"] == ["nomad clusters"]
    # Three answers, one signal: the duplicates collapsed rather than minting
    # `nomad_clusters_2` and `_3`. The FIRST spelling wins the label, so
    # the person sees what they typed.
    assert "nomad_clusters_2" not in config["lexicon"]
    assert config["lexicon"]["nomad_clusters"]["label"] == "Nomad clusters"
    assert result.signals_added.count("nomad_clusters") == 1


# =========================================================================
# Re-running the wizard is an EDIT, and an edit that changes nothing is not
# a change. Every case here was measured by an independent functional review.
# =========================================================================


def test_the_same_answers_twice_change_nothing_the_second_time(config_dir: Path) -> None:
    """The promise in `apply_answers`' own docstring, asserted.

    Three consecutive runs with identical answers used to produce:

        run1  ['revenue_operations','hubspot','cold_calling']
        run2  ['revenue_operations_2','hubspot_2','cold_calling_2']
        run3  lexicon: revenue_operations{,_2,_3}
              weights: cold_calling: -3, cold_calling_2: -3
              config_version 2 -> 3 -> 4

    One phrase scored three times, one penalty applied twice, and three bumps
    of `config_version` -- each of which detaches every stored score in the
    corpus from the configuration that produced it.

    `_unique_id` was doing two jobs. Minting `base_2` is right when two
    DIFFERENT phrases slugify the same way. It is wrong when the phrase is
    already there, which on a re-run is every phrase, because the base is by
    then the person's own file.
    """
    answers = Answers(
        role_examples=("Fractional CFO work",),
        skills=("Snowflake",),
        keywords=("board reporting",),
        negative_keywords=("door to door",),
    )
    first = run_setup(config_dir, answers)
    assert first.signals_added, "the first run added nothing at all"
    after_first = load_written(config_dir)

    second = run_setup(config_dir, answers)
    assert second.signals_added == []
    assert second.changed_sections == []
    assert second.config_version == first.config_version, (
        "a run that changed nothing bumped the version, invalidating every stored score"
    )

    third = run_setup(config_dir, answers)
    assert third.config_version == first.config_version
    assert load_written(config_dir) == after_first

    lexicon = after_first["lexicon"]
    assert not [key for key in lexicon if key.endswith(("_2", "_3"))], sorted(lexicon)


def test_one_phrase_is_penalised_once_however_often_you_answer(config_dir: Path) -> None:
    """A negative keyword minted a SECOND entry carrying a second penalty.

    Two entries whose patterns are the same phrase both fire on the same
    posting, so the penalty doubled every run. Nobody asked to be penalised
    twice for one dislike.
    """
    answers = Answers(negative_keywords=("commission only",))
    run_setup(config_dir, answers)
    run_setup(config_dir, answers)
    run_setup(config_dir, answers)

    written = load_written(config_dir)
    matching = [
        key
        for key, entry in written["lexicon"].items()
        if "commission only" in [str(p).lower() for p in entry.get("patterns", [])]
    ]
    assert len(matching) == 1, matching
    weights = written["scoring"]["soft_penalties"]["weights"]
    assert weights[matching[0]] == 3


def test_a_stated_requirement_is_not_restated_on_every_run(config_dir: Path) -> None:
    """`security_clearance_required_2`, `_3`, `_4`, one per run."""
    answers = Answers(hard_exclusions=("must relocate to Zurich",))
    run_setup(config_dir, answers)
    run_setup(config_dir, answers)
    run_setup(config_dir, answers)

    blockers = load_written(config_dir)["eligibility"]["blockers"]
    matching = [
        b
        for b in blockers
        if "must relocate to zurich" in [str(p).lower() for p in b.get("patterns", [])]
    ]
    assert len(matching) == 1, [b["id"] for b in matching]


def test_the_sponsorship_answer_goes_both_ways(config_dir: Path) -> None:
    """It used to be one-way, and silently so.

    Answering `no` deleted the work_authorization blockers from the local
    file. Answering `yes` afterwards did nothing, because there was no branch
    for it and the base was by then the person's own file without them:

        work_auth blockers after answering yes: []

    An answer whose effect depends on what you answered last time is not an
    answer to a question. Both values now do something definite, and `yes`
    restores from the shipped example -- the only honest source, since these
    blockers are vocabulary the project ships rather than words the person
    wrote.
    """

    def work_auth() -> list[str]:
        return [
            b["id"]
            for b in load_written(config_dir)["eligibility"]["blockers"]
            if b.get("gate") == "work_authorization"
        ]

    run_setup(config_dir, Answers(label="starting point"), worked_example=True)
    original = work_auth()
    assert original, "the example ships no work_authorization blockers, so this proves nothing"

    removed = run_setup(config_dir, Answers(needs_visa_sponsorship=False), worked_example=True)
    assert work_auth() == []
    assert sorted(removed.blockers_removed) == sorted(original)

    restored = run_setup(config_dir, Answers(needs_visa_sponsorship=True), worked_example=True)
    assert sorted(work_auth()) == sorted(original)
    assert sorted(restored.blockers_added) == sorted(original)

    # And answering `yes` twice adds nothing the second time.
    again = run_setup(config_dir, Answers(needs_visa_sponsorship=True), worked_example=True)
    assert again.blockers_added == []
    assert again.changed_sections == []


def test_a_phrase_the_example_already_scores_is_not_scored_twice(config_dir: Path) -> None:
    """The first run has the same problem as the second, for the same reason.

    The shipped example already scores a number of phrases. Typing one of them
    into the wizard used to mint a second entry for it, which fires on the
    same postings and adds its points again.
    """
    base, _ = base_config(config_dir, worked_example=True)
    existing_id, entry = next(
        (key, value) for key, value in base["lexicon"].items() if value.get("patterns")
    )
    phrase = str(entry["patterns"][0])

    result = run_setup(config_dir, Answers(skills=(phrase,)), worked_example=True)
    assert result.signals_added == [], result.signals_added

    written = load_written(config_dir)
    matching = [
        key
        for key, value in written["lexicon"].items()
        if phrase.lower() in [str(p).lower() for p in value.get("patterns", [])]
    ]
    assert matching == [existing_id], matching


# =========================================================================
# A stranger does not inherit somebody else's career
# =========================================================================


@pytest.fixture
def full_config_dir(tmp_path: Path) -> Path:
    """A configuration directory holding BOTH shipped files."""
    shutil.copy(EXAMPLE, tmp_path / "search.worked-example.yaml")
    shutil.copy(REPO / "config" / "search.starter.yaml", tmp_path / "search.starter.yaml")
    return tmp_path


def test_the_shipped_starter_loads_through_the_real_loader(full_config_dir: Path) -> None:
    """A starting point that does not load is worse than no starting point.

    It is DERIVED from the example by `scripts/make_starter_config.py`, and
    deriving it is what caught two things a hand-written file would have
    shipped broken: `preferences` has six required structured sections, and
    `screening.required_any_groups[].any_of` names lexicon signals, which the
    loader refuses when the signal does not exist.
    """
    shutil.copy(full_config_dir / "search.starter.yaml", full_config_dir / "search.local.yaml")
    config = load_search_config(full_config_dir)
    loaded = config[0] if isinstance(config, tuple) else config

    assert loaded.lexicon == {}, "the starter carries somebody's phrases"
    assert loaded.eligibility.blockers == [], "the starter carries somebody's blockers"
    assert loaded.eligibility.candidate_country == "", "the starter says where somebody lives"


def test_the_starter_keeps_every_mechanism_the_example_has(full_config_dir: Path) -> None:
    """Emptied of the person, identical in machinery.

    The point of deriving it: the scoring components, their maxima, the
    prominence multipliers, the negation windows and the thresholds are design
    decisions this project made, not preferences somebody expressed.
    """
    example = yaml.safe_load(
        (full_config_dir / "search.worked-example.yaml").read_text(encoding="utf-8")
    )
    starter = yaml.safe_load((full_config_dir / "search.starter.yaml").read_text(encoding="utf-8"))

    assert set(starter) == set(example), "the starter lost or gained a section"

    for name in ("prominence", "negation", "normalisation", "confidence", "thresholds"):
        assert starter[name] == example[name], f"{name} is machinery and should be identical"

    maxima = {key: component["max"] for key, component in starter["scoring"]["components"].items()}
    # A budget, not a hundred: the score is a percentage of whatever the
    # components declare. See `match_score_from`.
    assert sum(maxima.values()) > 0, maxima
    assert maxima == {
        key: component["max"] for key, component in example["scoring"]["components"].items()
    }


def test_plain_setup_starts_blank_and_the_worked_example_is_asked_for(
    full_config_dir: Path,
) -> None:
    """SUPERSEDES `test_fresh_starts_from_the_starter_and_plain_setup_from_the_example`.

    The two are the same two starting points with the DEFAULT swapped, and the
    swap is the point. Starting from a worked example and editing it is easier
    than starting from nothing -- but the worked example is one person's real
    search, and inheriting it without being asked is not a convenience.
    """
    plain, from_plain = base_config(full_config_dir)
    worked, from_worked = base_config(full_config_dir, worked_example=True)

    assert from_plain == "search.starter.yaml"
    assert from_worked == "search.worked-example.yaml"
    assert plain["lexicon"] == {}, "a plain setup carries somebody else's phrases"
    assert worked["lexicon"], "the worked example lost its phrases"


def test_neither_flag_overrides_settings_somebody_already_has(full_config_dir: Path) -> None:
    """A flag says where to START, not what to discard.

    Somebody who has set up their search and then passes it is telling the
    wizard where to begin, not asking it to delete their work. `forget
    settings` is the command that discards, and it asks first.
    """
    run_setup(full_config_dir, Answers(label="Mine", keywords=("nomad clusters",)))
    assert local_search_path(full_config_dir).exists()

    for flag in (False, True):
        base, started_from = base_config(full_config_dir, worked_example=flag)
        assert started_from == "search.local.yaml"
        assert base["label"] == "Mine"
        assert any("nomad" in key for key in base["lexicon"]), sorted(base["lexicon"])


def test_the_starter_is_regenerated_rather_than_edited(full_config_dir: Path) -> None:
    """The committed file must be what the generator produces.

    A hand-edit here would drift from the example the moment either changed,
    and the drift would be invisible until somebody's first run.
    """
    import sys

    scripts = REPO / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from make_starter_config import derive

    example = yaml.safe_load(
        (REPO / "config" / "search.worked-example.yaml").read_text(encoding="utf-8")
    )
    committed = yaml.safe_load(
        (REPO / "config" / "search.starter.yaml").read_text(encoding="utf-8")
    )
    assert derive(example) == committed, (
        "config/search.starter.yaml is not what the generator produces. "
        "Run `uv run python scripts/make_starter_config.py`."
    )


def test_somebody_in_a_completely_different_field_gets_their_own_search(
    full_config_dir: Path,
) -> None:
    """The whole point of the neutral default, demonstrated rather than argued.

    A nurse educator in Portugal runs setup on a fresh clone. What they should
    end up with is three signals, their own country and their own label, and no
    trace of the forty-nine phrases about revenue operations and integration
    platforms the worked example carries. This used to require a flag; it is
    what happens now when they type nothing at all.
    """
    result = run_setup(
        full_config_dir,
        Answers(
            role_examples=("Nurse educator",),
            skills=("Clinical simulation",),
            keywords=("patient safety",),
            residence_country="pt",
            label="Nursing education, Portugal",
        ),
    )
    assert result.started_from == "search.starter.yaml"

    config = load_search_config(full_config_dir)
    loaded = config[0] if isinstance(config, tuple) else config

    assert loaded.label == "Nursing education, Portugal"
    assert loaded.eligibility.candidate_country == "PT"
    assert sorted(loaded.lexicon) == ["clinical_simulation", "nurse_educator", "patient_safety"]

    owner = {"hubspot_platform", "revops_infrastructure", "gtm_systems_work", "ipaas"}
    assert not owner & set(loaded.lexicon), "the owner's search leaked into a stranger's"
