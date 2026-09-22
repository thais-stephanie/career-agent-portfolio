"""ISO 3166-1 alpha-2 country codes, and the aliases people actually type.

This exists because of a specific failure mode. Writing ``UK`` instead of the
ISO code ``GB`` produces no error anywhere: the market preference simply never
matches, silently, for the whole of a job search. The same is true of ``BRZ``
for Brazil or a lowercase ``br``.

Validating country codes at config-load time turns a silent no-match into a
message naming the offending line. That is the same principle the extraction
layer applies to job postings, applied to configuration.
"""

# ISO 3166-1 alpha-2, officially assigned codes.
#
# Kept as a grouped string rather than a list literal: grouping by first letter
# makes it possible to eyeball whether a code is present, which is the only
# maintenance this constant ever needs. SIM905 would rewrite it into a single
# 1500-character list, which is correct and unreadable.
_ISO_CODES = """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ
    EC EE EG EH ER ES ET
    FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP
    KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ
    OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY
    QA
    RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ
    VA VC VE VG VI VN VU
    WF WS
    YE YT
    ZA ZM ZW
"""

ISO_3166_1_ALPHA_2: frozenset[str] = frozenset(_ISO_CODES.split())  # noqa: SIM905

#: Codes and names people write that are not the ISO code. Normalised on load,
#: so ``UK`` keeps working while ``GB`` is what the system actually stores.
COUNTRY_ALIASES: dict[str, str] = {
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "UNITED KINGDOM": "GB",
    "EN": "GB",
    "USA": "US",
    "U.S.": "US",
    "UNITED STATES": "US",
    "BRA": "BR",
    "BRAZIL": "BR",
    "BRASIL": "BR",
    "DEU": "DE",
    "GERMANY": "DE",
    "CHE": "CH",
    "SWITZERLAND": "CH",
    "JPN": "JP",
    "JAPAN": "JP",
}


class UnknownCountryError(ValueError):
    """A country code that is neither ISO alpha-2 nor a recognised alias."""


def normalise_country(value: str) -> str:
    """Return the canonical ISO alpha-2 code, or raise with a useful message."""
    candidate = value.strip().upper()
    candidate = COUNTRY_ALIASES.get(candidate, candidate)

    if candidate not in ISO_3166_1_ALPHA_2:
        hint = ""
        if len(candidate) != 2:
            hint = " (ISO 3166-1 alpha-2 codes are exactly two letters)"
        raise UnknownCountryError(
            f"{value!r} is not a known country code{hint}. "
            "Use the ISO 3166-1 alpha-2 code, for example BR, US, DE, CH, JP, GB."
        )
    return candidate


# =========================================================================
# SUBDIVISIONS
#
# A hiring scope is sometimes narrower than a country, and the failure mode is
# expensive and silent: a posting open to 26 US states, recorded as "US",
# passes a candidate in one of the other 24 straight through the geography
# gate with no signal that anything was lost.
#
# Corpus prevalence is 12 of 18,549 postings (0.06%) -- rare enough that a
# geography parser would be absurd, and severe enough that pretending the whole
# country is open is not acceptable. So the existing free-form `countries` list
# carries ISO 3166-2 subdivision codes ("US-AZ") beside alpha-2 country codes
# ("US"), and this module says which is which. No new field, no new grammar,
# no schema cost.
# =========================================================================

#: "US-AZ", "CA-ON", "GB-ENG" -- an alpha-2 country, a hyphen, then 1-3
#: alphanumerics. Deliberately not a table of every valid subdivision: the
#: country half is validated, the subdivision half is preserved as written.
#: Validating the second half would mean vendoring and maintaining ~5,000 codes
#: to catch a typo in a field a human rarely writes.
_SUBDIVISION_LENGTH = range(1, 4)


def is_subdivision(value: str) -> bool:
    """True for an ISO 3166-2-shaped code whose country half is real."""
    country, sep, subdivision = value.partition("-")
    if not sep or not subdivision:
        return False
    if len(subdivision) not in _SUBDIVISION_LENGTH or not subdivision.isalnum():
        return False
    return country in ISO_3166_1_ALPHA_2


def country_of(value: str) -> str:
    """The country a code belongs to. ``US-AZ`` -> ``US``; ``US`` -> ``US``."""
    return value.partition("-")[0] if is_subdivision(value) else value
