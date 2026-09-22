"""Loading prompts, and the rule for when a prompt version changes.

Prompts live in `llm/prompts/*.md` as reviewable files rather than inside Python
string concatenation. They are the most-edited part of an extraction system and
the part whose diffs matter most; burying them in code makes both review and
blame useless.

THE VERSIONING RULE
-------------------
A prompt version is part of the cache key, so bumping it re-runs every cached
extraction and changing one without bumping serves stale answers to a new
question. Neither error is cheap, so the rule is written down:

**Bump the version** when a change could alter what the model reports: adding,
removing or redefining a dimension; changing a vocabulary or its examples;
changing what counts as evidence; changing the completeness or status rules;
adding or removing an instruction about how to decide something.

**Do not bump** for changes that cannot: fixing a typo in prose, reflowing a
paragraph, correcting a heading, adjusting whitespace or markdown formatting.

When in doubt, bump. Serving a stale extraction for a changed question is a
silent wrong answer; a needless re-run is a visible bill.

Versions are file names (`description_v1.md`), so the history of a prompt is
the history of a file and old versions stay readable next to new ones.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"

#: The prompt each family currently uses. Changing one of these is a deliberate
#: act with a cost attached, which is why they are named here rather than
#: assembled from a variable somewhere.
#: v2 rewrites the geography section around one distinction the rest of the
#: system is built on: work model and hiring scope are different questions, and
#: "remote" answers only the first. v1 said so in a sentence; v2 says it in a
#: table of worked examples, names the sentences that prove work model and
#: nothing else, and makes the honest answer for a posting that states no
#: hiring geography a `NOT_STATED` **status** rather than an EXPLICIT scope
#: whose value names nowhere.
#:
#: `UNSTATED` as a *value* now belongs to the provider family alone, where it
#: means something different and real: a location field that exists and names
#: nowhere. The description family has no field to be silent about, so its
#: silence is a status.
#: v3 publishes the dimension roster and every value vocabulary.
#:
#: The Cowork-assisted run on the golden set found this at zero cost, and found
#: it twice: four independent runners each reported that the required dimensions
#: were prose rather than a list and that the value vocabularies were never
#: printed -- and then 7 of 24 extractions failed on exactly that. Every failure
#: was a value the prompt asked for and never defined: `OCCASIONAL`, `ANNUAL`,
#: `REGULAR` and `AS_REQUIRED` for a five-member `travel_frequency`; `EXECUTIVE`
#: for a `Seniority` that has no such member; `US-PACIFIC` for a closed region
#: list.
#:
#: The prompt grew to fix it, and the arithmetic is not close: a failed
#: extraction costs a full retry, so a 29% failure rate is far more expensive
#: than the tokens the roster adds.
#: v4 separates worksite from hiring geography.
#:
#: A required office is not a hiring restriction: an employer may hire
#: internationally and require relocation, and postings saying exactly that
#: exist. v3 had no factual place to record a required work location at all --
#: `office_locations` was a bare list that could not tell "you must be here
#: three days a week" from "an office exists and you may use it", which are
#: opposite facts for anyone deciding whether a job is reachable.
#:
#: `work_environment.worksite_requirement` replaces it, carrying a binding
#: level alongside the places. The dimension count is unchanged at 31.
#:
#: v5 closes four representation gaps an independent review of the golden set
#: found, and closes them for the same reason each time: the fingerprint could
#: not hold a fact the posting had actually stated.
#:
#:   * `hiring_scope` gains REQUIRED / PREFERRED. "Should preferably be based
#:     in Sao Paulo" is an EXPLICIT statement of a soft preference, and the
#:     only way to signal softness before was to lie about the status.
#:   * `hiring_scope` gains ISO 3166-2 subdivisions. 12 postings in 18,549
#:     restrict below country level; recording those as `US` passes a candidate
#:     in any of the other 24 states through the geography gate.
#:   * `timezone_requirement` gains OVERLAP / RESIDENCE. "Cover EST hours" is a
#:     clock, not a border, and a bare anchor left that to a later guess.
#:   * `travel_required` and `relocation_allowed` are new dimensions. "Travel
#:     for meetings" with no frequency, and "reside here OR be willing to
#:     relocate", each state something the old model could only round off.
#:
#: The two new dimensions cost NOTHING: the transport carries dimensions as
#: rows, so the description schema is byte-identical at 8,768 characters and
#: still 24/24 optional, 13/16 union parameters. 33 dimensions now.
#:
#: v6 teaches four things v5 assumed. Stage 0 Take 2 ran on v5 against 27 real
#: postings and 11 of them produced no fingerprint at all, so this is the first
#: prompt version written from observed model behaviour rather than from
#: review. Nothing durable changed: every rule below was already true, and the
#: schema, the dimension roster and every vocabulary are untouched.
#:
#:   * **Absence is not the value `NONE`.** Six vocabularies open with `NONE`,
#:     and the model reached for it whenever a posting was silent -- 17 of the
#:     24 unsupported claims, and it marked them `EXPLICIT` with no sentence to
#:     cite, which the evidence rule then refused. v5 said "silence is
#:     NOT_STATED" and illustrated it only with travel and visa sponsorship,
#:     dimensions whose silence has no value to reach for. The gap was the
#:     illustration, not the rule.
#:   * **A level on a scale needs the sentence that set it.** The other 7 were
#:     `LOW` / `HIGH` read off the shape of the job and labelled `EXPLICIT`.
#:   * **A restriction in another dimension is not a hiring geography.** Take 2
#:     derived `COUNTRY_LIST:US` from "requires an active Top Secret Clearance"
#:     and `COUNTRY_LIST:US,CA,GB` from a benefits section listing three
#:     countries' health plans. One invariant, written once, with a table of
#:     the adjacent facts that are not geography.
#:   * **Examples of one thing are not several things.** "government financial
#:     systems (e.g., Oracle, PeopleSoft, Workday, SAP)" came back as four
#:     independent `PREFERRED` tools. v5's only `ALTERNATIVE` example used the
#:     word "or", and its nearest example to "e.g." taught `MENTIONED`.
#:
#: Plus one correction of a misleading example: `onsite_frequency` is
#: documented as free text "as stated", and its only illustration was a day
#: COUNT -- which is what the model wrote for a posting that named the days.
#:
#: What v6 deliberately does NOT change: relocation, where v5 already carries
#: that sentence and the right answer and the model contradicted it anyway; and
#: responsibility prominence, where one scored case is not a pattern and
#: tailoring the prompt to a single golden answer is how a benchmark stops
#: measuring anything.
#:
#: **It costs 668 tokens, not 1,353.** The first v6 draft ran to 7,176 and was
#: compressed in place before its first live call, which is the only moment a
#: prompt version may be rewritten without a bump. Three things paid for it:
#: the eight-row table of adjacent facts became one invariant and two lines;
#: v5's "Silence is NOT_STATED" paragraph merged into the absence rule instead
#: of sitting beside it; and the Evidence section stopped restating a rule the
#: status section now makes at the point of decision.
#:
#: Every illustration is generic. Three of the draft's examples were verbatim
#: sentences from scored golden postings -- a prompt that hands the benchmark
#: its own answer key measures nothing.
#:
#: v7 states the output contract the prompt had never stated.
#:
#: Every version through v6 described WHAT to report and left the SHAPE of the
#: response entirely to the vendor's schema enforcement. That worked while every
#: arm ran under STRICT_SCHEMA and stopped working the moment one did not: a
#: free ladder of five models produced four different contract violations, and
#: every one of them was a rule the prompt does not contain.
#:
#: Read the words v6 never uses: `observations`, `function_signals`,
#: `source_kind`, `JOB_DESCRIPTION`, `raw_mention`, `language_code`,
#: `not_applicable_because`. It says "return one observation row per dimension"
#: and never names the collection those rows go in. Under a vendor schema the
#: model is told; under JSON mode or plain JSON it is guessing, and one model
#: guessed that 33 dimensions were 33 top-level properties -- which is not
#: disobedience, it is the only reading the instructions supported.
#:
#: So v7 adds one section and changes nothing else. It restates rules that were
#: already true and already enforced:
#:
#:   * **the document boundary** -- one JSON document, `{` first, `}` last, no
#:     fence, no preamble, no second document;
#:   * **the topology** -- the seven authorised top-level properties, and the
#:     fact that a dimension name is a `dimension` FIELD rather than a key;
#:   * **the cardinality** -- exactly one row per dimension, which
#:     `index_observations` has always enforced;
#:   * **four semantic invariants** in the form they are checked: EXPLICIT needs
#:     `evidence_id`, evidence needs a verifiable `quote` and
#:     `source_kind: JOB_DESCRIPTION`, ALTERNATIVE needs `alternative_group`,
#:     and nothing is worth inventing evidence for.
#:
#: NOT a repair and not a relaxation. No field changed meaning, no dimension was
#: added or removed, no vocabulary moved, no threshold moved, and the parser,
#: the validator and the evidence verifier are untouched. Invalid output stays
#: invalid; what changes is that the contract it violates is now written down
#: where the model can read it.
#:
#: Nothing case-specific. The skeleton carries angle-bracket descriptors and no
#: values, no posting text and no answer from any scored case -- a prompt that
#: hands the benchmark its answer key measures nothing, which is the rule v6
#: was compressed to obey and v7 inherits.
#:
#: +4,709 bytes over v6. Whether it changes a model's behaviour is unmeasured;
#: no inference has been run against it.
#: v8 corrects a terminal-boundary failure, and nothing else.
#:
#: v7 wrote the output contract down and then, in the same section, demonstrated
#: the opposite. The canonical skeleton it added was the only fenced block in the
#: file carrying a language tag and the only one holding a JSON document, so the
#: prompt ended up containing exactly one place where a document's outer closing
#: brace is followed by a closing Markdown fence -- twelve characters after a
#: bullet reading "no Markdown fence, and no language tag".
#:
#: The first live v7 request completed a valid 14,117-character document, emitted
#: a newline, and then emitted that closing fence. Its final 17 characters are a
#: verbatim copy of the skeleton block's final 17. The whole-response parser
#: rejected it as `Extra data`, correctly, and no fingerprint was produced. The
#: reading inside the document was sound -- 33 dimensions, one row each, zero
#: unsupported EXPLICIT claims -- and none of it survived three characters.
#:
#: So v8 changes three things and no fourth:
#:
#:   * the version header;
#:   * the skeleton is no longer fenced, which removes the only continuation in
#:     the prompt that ends a JSON document with a delimiter;
#:   * one terminal rule is now the LAST instruction in the file, after the
#:     skeleton and after every other instruction, saying where output ends.
#:
#: The ordering is the point. v7 stated the boundary rule at the top of the
#: section and demonstrated its violation at the bottom; whichever of the two a
#: model weights more heavily, the last thing it read was the wrong one.
#:
#: NOT a repair and not a relaxation. Every semantic rule, dimension, vocabulary,
#: status rule, evidence rule and topology rule is byte-identical to v7. The
#: schema, parser, validator, evidence verifier and assembler are untouched, and
#: the stored v7 artifact still fails the unchanged parser -- no output is
#: normalised, stripped or extracted anywhere.
#:
#: +405 bytes and +80 tokens over v7 (7,740). Unfencing gave back 12 bytes; the
#: terminal rule cost 417. A boundary problem three characters wide does not
#: justify a section, and this is not one.
DESCRIPTION_PROMPT_VERSION = "description_v8"

#: v2 narrowed the provider family to geography. v1 asked the model to normalise
#: employment type and work model -- both closed sets a lookup handles -- and to
#: pass compensation through unchanged, which is an echo rather than an
#: interpretation. Measuring showed the family was 99.4% static overhead, so the
#: prompt was carrying instructions for work code now does.
#:
#: v3 corrects the described input format. v2 documented two columns while
#: `cache.observation_block` -- which is simultaneously the model's input and
#: the family's cache identity -- renders a vendor header line and three
#: tab-separated columns. Describing an input the model does not receive is the
#: kind of drift that produces a plausible wrong answer and no error, so the
#: prompt was corrected to the real format rather than the block reshaped to a
#: prompt written before it existed.
#:
#: v1 and v2 stay on disk: prompt versions are files, and an old one should
#: remain readable beside the new one.
PROVIDER_PROMPT_VERSION = "provider_v3"


#: THE VERSION NAME IS A CLAIM ABOUT BYTES, AND THIS IS WHAT MAKES IT TRUE
#: ----------------------------------------------------------------------
#: A cache key names a prompt version; it does not carry the prompt. That is the
#: right shape -- hashing 25kB of instructions into every key buys nothing a
#: name does not -- but it rests entirely on the name being honest, and nothing
#: was checking. Edit a prompt file without bumping its version and every stored
#: answer to the OLD question is served, silently, as an answer to the new one.
#:
#: The compression pass on `description_v6` walked straight into it: the file
#: changed by 685 tokens and its cache identity did not move by a byte. That was
#: safe only because v6 had never been sent to a vendor. Nothing in the code
#: knew that, or would have objected if it had been false.
#:
#: So the mapping is pinned here and verified on **every load**, not in a test.
#: A prompt whose bytes no longer match its entry does not produce a bad cache
#: hit; it produces a refusal, before a request exists. The version name is then
#: a true content identity, which is what the cache key has always assumed.
#:
#: To change a prompt: write a new file, add its digest, bump the version. To
#: change one of these digests: you are editing history, and the only correct
#: reason is that the prompt has never been used for a live call.
#:
#: THE DIGEST IS OVER THE CANONICAL BYTES, WHICH ARE LF (2026-09-18)
#: -----------------------------------------------------------------
#: `.gitattributes` has stored every `*.md` with LF since 2026-08-25, before
#: any prompt file existed, so the bytes the REPOSITORY holds for a version
#: are its LF bytes and every clone reproduces them. Two entries here were
#: pinned on 2026-09-03 over CRLF bytes: the working copy that ran the pin
#: still carried the line endings the files were first written with, git
#: had normalised them on commit, and the pin captured one machine's disk
#: rather than the repository's content. A fresh clone then refused
#: `description_v2` and `description_v3` at load, on files that had never
#: changed. `canonical_bytes` folds CRLF to LF before hashing and before
#: decoding, so a version's identity is the same on every machine and the
#: text a model is sent is too.
#:
#: The two re-pinned entries were re-pinned under the rule above and not
#: against it: the production ledger (`llm_call`, read on 2026-09-18) holds
#: 24 rows for `description_v2` and 7 for `description_v3`, every one
#: produced by the assisting-agent harness (the golden-01 / golden-02
#: batches of 2026-08-26) and zero `PRODUCTION_API`; both were keyed by
#: filename before
#: `static_digest` existed and were never re-keyed, so no reachable cache key
#: names their bytes. Every other entry already matched its LF bytes.
PROMPT_DIGESTS: dict[str, str] = {
    "description_v1": "sha256:b1c2987c8a5c1b0d6f0138b497d2317c9bd1120a0fd71539e23766256cd477a3",
    "description_v2": "sha256:ab826e47ce93db9303224f369b9623660fa675a860e89618543a8ca051911f93",
    "description_v3": "sha256:81d414b8a9fbade0a2ea790180d3cd89253fb363649173f4c89a404207f9cf27",
    "description_v4": "sha256:f373109be590a2ab8c00e4d02949b19d490878198f39c9f3f8aae036ae87df5a",
    "description_v5": "sha256:a8322ad0af86a56119772e79f4cfbf26dfbd9cc20a08b4c2491f0b5305933242",
    "description_v6": "sha256:c1e558dbbf3c2828eb4f13d56fea2ab7fb2209089e1988f7e75fee8af5a43c18",
    "description_v7": "sha256:f1871d8d064a46706f64177aed3143bbf82f01dca2fab68d0655be05d775306b",
    "description_v8": "sha256:18bcb569c03b196fcfb438af8ee43b693914e96bdd285f4392148763040a297c",
    "provider_v1": "sha256:5136623529e4aa749ec53a113d87d622aadfb0c0ac73f8f2487a24a31d43a3a8",
    "provider_v2": "sha256:1200d6e6d332230352efaa7934e3db038685cf834b1570fe8c2d41a5bc19a618",
    "provider_v3": "sha256:81a0e71195c12ede6cc0af87ad99b9a857348c1f20a20f5ceb13610f1bf16aec",
}


class PromptNotFound(FileNotFoundError):
    """A named prompt version does not exist on disk."""


class PromptChanged(RuntimeError):
    """A prompt file no longer matches the digest its version is pinned to."""


def canonical_bytes(raw: bytes) -> bytes:
    """The bytes a prompt is identified by: CRLF folded to LF.

    Line endings are a property of a checkout, not of the instructions. The
    repository stores LF (`.gitattributes`), so this is the identity every
    clone agrees on, and it is also what is decoded and sent: a model on a
    Windows working copy with a CRLF file sees the same text as one on Linux.
    """
    return raw.replace(b"\r\n", b"\n")


@lru_cache(maxsize=32)
def load_prompt(version: str) -> str:
    """Read one prompt by version name, and refuse a version that has drifted.

    Cached because a batch run loads the same two prompts thousands of times,
    and because a prompt that changed mid-run would make the cache key a lie.

    The digest check runs once per version per process, on the canonical
    bytes (`canonical_bytes`), before any request is built. It is the whole of
    the guarantee that a cache key naming `description_v6` refers to one
    specific set of instructions.
    """
    path = PROMPT_DIR / f"{version}.md"
    if not path.exists():
        available = sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
        raise PromptNotFound(f"no prompt {version!r} in {PROMPT_DIR}; available: {available}")

    raw = canonical_bytes(path.read_bytes())
    pinned = PROMPT_DIGESTS.get(version)
    if pinned is None:
        raise PromptChanged(
            f"{version} has no pinned digest. A prompt version is a cache identity, and an "
            "unpinned one is a name that could mean anything. Add its digest to "
            "PROMPT_DIGESTS in the same commit that adds the file."
        )
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != pinned:
        raise PromptChanged(
            f"{version} does not match its pinned digest.\n"
            f"  pinned : {pinned}\n"
            f"  on disk: {actual}\n"
            "Every answer already stored under this version was given to the OLD bytes. "
            "Serving those as answers to the new ones is a silent wrong result, which is "
            "why this refuses rather than warns. If the instructions must change, add a new "
            "version; only re-pin a version that has never been used for a live call."
        )
    return raw.decode("utf-8")


def prompt_digest(version: str) -> str:
    """The pinned digest of one prompt version, verified against disk."""
    load_prompt(version)
    return PROMPT_DIGESTS[version]


def available_prompts() -> list[str]:
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
