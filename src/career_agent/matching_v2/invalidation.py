"""Which postings a change can actually move, before recomputing anything.

A version bump makes every artefact of a component stale by its versions
map. For a change to the LANGUAGE TABLE that is too broad: adding
`estoque = inventory` can only move postings that contain `estoque`. `diff`
compares two tables and names the words whose canonical root moved;
`affected_jobs` names the postings that contain one, and every other posting
would come out identical and can be re-stamped without being read
(`restamp`). The same selection serves a change of ONE person's phrases
(`phrase_candidates`): only postings with the words of a new phrase can gain
a match from it.

THE INDEX IS THE ONE THE CATALOGUE ALREADY HAS. Canonical tokens are not
stored (`features`), so a term index of our own would be a second copy of
every word of the corpus. The full-text index `job_search` (migration 0015)
already holds the folded words of every title and advert; a canonical root
is a PREFIX of every word that reaches it except listed translations and
fixed expressions, which are added to the query by name. The selection is a
SUPERSET (a prefix finds `inventories` and `inventor` alike) and never short.

Two cases read every posting instead, and say so (`scan`): a change to a
morphology rule (a suffix rule can move any word ending in it, which no
index answers) and an index that is not current (`search_index.is_current`:
a posting changed after the index was built is not in it).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from career_agent.matching_v2.language import LanguageTable, concept_readings, load_table, tokens
from career_agent.storage.search_index import is_current


@dataclass(frozen=True)
class LanguageChange:
    #: Canonical roots, as stored by the OLD table, whose meaning moved; a
    #: root `*sfx` stands for a morphology rule on that suffix.
    old_roots: frozenset[str]
    #: First roots of fixed expressions that appeared, disappeared or moved,
    #: as the OLD table tokenised them (a sequence starts with one).
    sequence_heads: frozenset[str]
    #: Folded words named by the change (listed forms, first words of fixed
    #: expressions), for the full-text query.
    words: frozenset[str] = frozenset()

    def touches(self, stored_roots: set[str]) -> bool:
        plain = {r for r in self.old_roots if not r.startswith("*")}
        suffixes = {r[1:] for r in self.old_roots if r.startswith("*")}
        return bool(stored_roots & (plain | self.sequence_heads)) or any(
            r.endswith(s) for r in stored_roots for s in suffixes
        )

    @property
    def needs_scan(self) -> bool:
        return any(r.startswith("*") for r in self.old_roots)


def diff(old: LanguageTable, new: LanguageTable) -> LanguageChange:
    """The stored roots a move from `old` to `new` can change."""
    moved: set[str] = set()
    words: set[str] = set()
    for key in set(old.words) | set(new.words):
        if old.words.get(key) != new.words.get(key):
            moved.add(old.canonical(key)[0])
            moved.add(key)
    for form in set(old.surface) | set(new.surface):
        if old.surface.get(form) != new.surface.get(form):
            moved.add(old.surface.get(form, form))
            words.add(form)
    if old.morphology != new.morphology:
        # A rule on roots can move any root ending in its suffix.
        suffixes = {s for _, s in set(old.morphology) ^ set(new.morphology)}
        moved |= {f"*{s}" for s in suffixes}
    heads: set[str] = set()
    for seq in set(old.fixed) | set(new.fixed):
        if old.fixed.get(seq) != new.fixed.get(seq):
            heads.add(old.canonical(seq[0])[0])
            heads.add(seq[0])
    return LanguageChange(frozenset(moved), frozenset(heads), frozenset(words))


def _quoted(term: str) -> str:
    return '"' + term.replace('"', "") + '"'


def _fts(conn: sqlite3.Connection, query: str) -> set[str]:
    rows = conn.execute(
        "SELECT job_id FROM job_search WHERE job_search MATCH ?",
        (f"{{title description}} : ({query})",),
    )
    return {str(r[0]) for r in rows}


def _all_jobs(conn: sqlite3.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute("SELECT job_id FROM job_features")]


def scan(
    conn: sqlite3.Connection, hit: Callable[[set[str]], bool], table: LanguageTable
) -> set[str]:
    """Every posting with stored facts whose words, tokenised under `table`,
    satisfy `hit`. Reads every advert: the fallback, not the path."""
    out: set[str] = set()
    for job_id, title, text in conn.execute(
        "SELECT f.job_id, j.title, r.description_text FROM job_features f"
        " JOIN job j ON j.id = f.job_id JOIN job_raw r ON r.content_hash = f.content_hash"
    ):
        roots = {t.root for t in tokens(title or "", table)} | {
            t.root for t in tokens(text or "", table)
        }
        if hit(roots):
            out.add(str(job_id))
    return out


def affected_jobs(
    conn: sqlite3.Connection, change: LanguageChange, old: LanguageTable | None = None
) -> tuple[list[str], list[str], str]:
    """(postings the change can move, postings it cannot, how it was found:
    `index` or `scan`), over the postings with stored facts."""
    old = old or load_table()
    every = _all_jobs(conn)
    if change.needs_scan or not is_current(conn):
        moved = scan(conn, change.touches, old)
        how = "scan"
    else:
        terms = [f"{_quoted(r)}*" for r in change.old_roots | change.sequence_heads]
        terms += [_quoted(w) for w in change.words]
        moved = _fts(conn, " OR ".join(sorted(terms))) if terms else set()
        how = "index"
    moved &= set(every)
    return sorted(moved), sorted(set(every) - moved), how


def _root_terms(root: str, table: LanguageTable) -> list[str]:
    """Every full-text term that can reach canonical `root`: the root as a
    prefix, its listed forms, the roots of its listed family as prefixes and
    the first word of each fixed expression that produces it."""
    terms = {f"{_quoted(root)}*"}
    terms |= {_quoted(form) for form, target in table.surface.items() if target == root}
    terms |= {f"{_quoted(key)}*" for key, (target, _) in table.words.items() if target == root}
    for seq, target in table.fixed.items():
        if root in target:
            terms.add(f"{_quoted(seq[0])}*")
    return sorted(terms)


def phrase_candidates(
    conn: sqlite3.Connection, patterns: Iterable[str], table: LanguageTable | None = None
) -> tuple[set[str], str]:
    """Postings that can match any of `patterns` (a person's new or edited
    phrases): every root of one reading present. A superset, never short."""
    table = table or load_table()
    readings = {r for p in patterns for r in concept_readings(p, table)}
    if not is_current(conn):
        wanted = [set(r) for r in readings]
        return scan(conn, lambda roots: any(w <= roots for w in wanted), table), "scan"
    clauses = [
        " AND ".join(f"({' OR '.join(_root_terms(root, table))})" for root in sorted(set(r)))
        for r in readings
        if r
    ]
    if not clauses:
        return set(), "index"
    return _fts(conn, " OR ".join(f"({c})" for c in clauses)) & set(_all_jobs(conn)), "index"


def restamp(
    conn: sqlite3.Connection, job_ids: list[str], versions_from: str, versions_to: str
) -> int:
    """Give unaffected postings the new versions map without recomputing
    them: their facts are what the new version would have written."""
    done = 0
    for i in range(0, len(job_ids), 500):
        chunk = job_ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        done += conn.execute(
            f"UPDATE job_features SET versions = ? WHERE versions = ? AND job_id IN ({marks})",
            (versions_to, versions_from, *chunk),
        ).rowcount
    return done
