"""Two files that describe the same person, and what to do when they disagree.

`profile.local.yaml` has owned the candidate's facts since M0: where they live,
which hiring scopes they can accept, how they may be engaged, what they are paid.
M3 then built a matcher, and the matcher took its gate inputs from
`search.*.yaml` instead -- `eligibility.candidate_country`, `eligible_scopes`,
`eligible_countries`, and a whole `preferences` section of remote, contract and
compensation. Both files still exist. Nothing checks that they agree.

That is a real hazard and a small one, in that order. Real, because the two can
say different things about where somebody lives and different commands will
answer differently with no symptom -- exactly the silent-wrong-answer failure
`config/loader.py` was written to prevent, reappearing one layer up. Small,
because the shipped product reads only one of them, so a divergence changes no
score today.

**This module warns. It does not choose.** Picking a winner would be a
migration, and a migration before there is any profile history to migrate is
paying the cost before the benefit. `search_profile_version` holds zero rows.

The intended end state is written down so the interim cannot quietly become the
design:

    The Candidate Profile owns facts and preferences about the PERSON.
    The search and match configuration owns product-level MACHINERY.

Two rules keep the warnings worth reading.

**Only two EXPLICIT values can conflict.** An empty list, a zero target or an
absent file is not a claim, and reporting it as a disagreement would bury the
real ones. Absence is never permission, and it is not a contradiction either.

**Every finding names both sides.** A warning that says "these disagree" without
saying what each said is a warning nobody can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from career_agent.config.search_config import SearchConfig
    from career_agent.domain.profile import SearchProfile

#: The future invariant, in one line, quoted by `doctor` so it is impossible to
#: read the warnings without reading the direction they point in.
OWNERSHIP_RULE = (
    "Candidate Profile owns facts about the person; "
    "search configuration owns the matching machinery."
)


@dataclass(frozen=True, slots=True)
class Divergence:
    """One fact, stated twice, differently."""

    #: What the two files disagree about, in the words a person would use.
    subject: str
    #: Where each value lives, as a path somebody can open and edit.
    profile_path: str
    config_path: str
    #: What each one says, rendered for reading rather than for comparison.
    profile_value: str
    config_value: str

    @property
    def sentence(self) -> str:
        return (
            f"{self.subject}: {self.profile_path} says {self.profile_value}, "
            f"{self.config_path} says {self.config_value}. "
            f"The matcher reads {self.config_path}."
        )


def _listed(values: object) -> str:
    if isinstance(values, list | tuple | set):
        return ", ".join(sorted(str(v) for v in values)) or "nothing"
    return str(values)


def divergences(profile: SearchProfile, config: SearchConfig) -> tuple[Divergence, ...]:
    """Every candidate fact the two files state differently.

    Empty when they agree, and empty when only one of them says anything --
    which is the common case, because most of these have a natural "unset" and
    the shipped example leaves several of them there.
    """
    found: list[Divergence] = []

    # --- where the candidate lives ---------------------------------------
    home = profile.candidate_geography.residence_country
    stated = (config.eligibility.candidate_country or "").strip().upper()
    if home and stated and home != stated:
        found.append(
            Divergence(
                subject="Where you live",
                profile_path="profile.local.yaml candidate_geography.residence_country",
                config_path="search.yaml eligibility.candidate_country",
                profile_value=home,
                config_value=stated,
            )
        )

    # --- which hiring scopes are acceptable ------------------------------
    #
    # The two vocabularies are not identical: the profile holds one list of
    # scopes, regions and countries together, while the configuration splits
    # them in two. They are compared as one SET on each side, because the
    # question -- "which places can hire me" -- is one question.
    accepted = {str(s).upper() for s in profile.job_hiring_geography.acceptable_hiring_scopes}
    configured = {str(s).upper() for s in config.eligibility.eligible_scopes} | {
        str(c).upper() for c in config.eligibility.eligible_countries
    }
    if accepted and configured and accepted != configured:
        found.append(
            Divergence(
                subject="Which places may hire you",
                profile_path="profile.local.yaml job_hiring_geography.acceptable_hiring_scopes",
                config_path="search.yaml eligibility.eligible_scopes + eligible_countries",
                profile_value=_listed(accepted),
                config_value=_listed(configured),
            )
        )

    # --- how you may be engaged ------------------------------------------
    contracts = {str(c).upper() for c in profile.engagement.contract_types_accepted}
    preferred = {str(c).upper() for c in config.preferences.contract.preferred}
    if contracts and preferred and contracts != preferred:
        found.append(
            Divergence(
                subject="How you may be engaged",
                profile_path="profile.local.yaml engagement.contract_types_accepted",
                config_path="search.yaml preferences.contract.preferred",
                profile_value=_listed(contracts),
                config_value=_listed(preferred),
            )
        )

    # --- what you will not accept, about where the work happens ----------
    #
    # `work_environment` is a free vocabulary on the profile side and a closed
    # one on the configuration side, so only the flat contradiction is reported:
    # a work model the configuration accepts and the profile refuses outright.
    refused = {str(v).upper() for v in profile.intent.work_environment.NEVER}
    for model in config.preferences.remote.accepted_work_models:
        if f"{model.upper()}_REQUIRED" in refused or model.upper() in refused:
            found.append(
                Divergence(
                    subject="Where the work happens",
                    profile_path="profile.local.yaml intent.work_environment.NEVER",
                    config_path="search.yaml preferences.remote.accepted_work_models",
                    profile_value=_listed(refused),
                    config_value=model.upper(),
                )
            )

    # --- what you are paid ------------------------------------------------
    #
    # Compared per YEAR, in one currency, and only when both sides state an
    # amount AND agree on the currency. Nothing here converts: a cross-currency
    # comparison needs a dated rate, and inventing one to produce a WARNING
    # would be the same manufactured fact the scorer refuses to produce.
    target = profile.compensation.annual_target
    money = config.preferences.compensation
    comparable = (
        target is not None
        and target.amount > 0
        and money.target_monthly_amount > 0
        and target.currency.upper() == money.currency.upper()
    )
    if comparable and target is not None:
        annual = money.target_monthly_amount * (12 if money.period.upper() == "MONTH" else 1)
        if abs(annual - target.amount) > 1:
            found.append(
                Divergence(
                    subject="What you are aiming to earn",
                    profile_path="profile.local.yaml compensation.annual_target",
                    config_path="search.yaml preferences.compensation.target_monthly_amount",
                    profile_value=f"{target.currency} {target.amount:,.0f} per year",
                    config_value=f"{money.currency} {annual:,.0f} per year",
                )
            )

    return tuple(found)
