"""A blind evaluation package for work matching, and the import of its labels.

    uv run python scripts/matching_v2_blind_package.py build --sample BLIND.db \
        --config-dir DIR --out PACKAGE_DIR
    uv run python scripts/matching_v2_blind_package.py score --package PACKAGE_DIR \
        --labels PACKAGE_DIR/REVIEW.tsv

BUILD reads a bounded copy of postings whose ids are of class 2
(`matching_v2_sample.id_class`): never in the first evaluation, never in the
development sets of 2026-10-14. Production (readers-N) and v2 match it for
the owner's configuration and wizard-built professions written in several
languages, one-word preferences included (`matching_v2_evaluate.items`). It
then draws a stratified package: every kind of item (both readers matched,
they disagree, v2 held it uncertain or set it aside, near misses with every
word present and neither matching), per language and per profession, up to
`--per-cell` each, and writes:

* REVIEW.tsv     what a person sees: an id, the language, the preference, the
                 posting's title, the section heading above the sentence as
                 the advert wrote it (if any) and the sentence. Nothing about
                 either reader, no score, no expected answer, shuffled.
                 The reviewer fills `label` with Y, N or ?.
* INSTRUCOES.md  how to judge, in Portuguese, and how long it takes.
* key.jsonl      the readers' decisions per id, SEALED: its SHA-256 is written
                 in INSTRUCOES.md and checked on import. Do not open it while
                 reviewing.

SCORE joins the labels to the key (refusing a key that changed) and reports
precision, recall and F1 for each reader, overall, per language, per
profession and for one-word preferences, with Wilson 95% intervals, weighted
by each stratum's sampling rate. The package holds employer text: keep it
local, never commit it. Opens the copy read-only, opens no socket, calls no
model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts import matching_v2_evaluate as evaluate  # noqa: E402

from career_agent.matching_v2.context import sentences  # noqa: E402

KINDS = ("disagree", "both_match", "v2_held", "near_miss")
MINUTES_PER_ITEM = 0.25
#: A "sentence" longer than this is a section an advert wrote without breaks;
#: the reviewer sees a window around the matched words, marked `[...]`. The
#: key keeps the sentence's offsets, so nothing is lost for the import.
WINDOW = 500


def _shown(text: str, start: int, end: int, anchor: int | None, phrase: str) -> str:
    """The sentence as the reviewer sees it: whole, or a window of WINDOW
    characters around `anchor` (the matched words; else the first word of the
    preference found in it) when it is longer."""
    sentence = " ".join(text[start:end].split())
    if len(sentence) <= WINDOW:
        return sentence
    if anchor is None:
        lowered = text[start:end].lower()
        found = [lowered.find(w[:5]) for w in phrase.lower().split() if len(w) > 2]
        found = [f for f in found if f >= 0]
        anchor = start + min(found) if found else None
    centre = (anchor if anchor is not None else start) - start
    lo = max(0, centre - WINDOW // 2)
    piece = " ".join(text[start + lo : start + lo + WINDOW].split())
    return ("[...] " if lo > 0 else "") + piece + (" [...]" if start + lo + WINDOW < end else "")


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(a: argparse.Namespace) -> None:
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; give a new directory")
    a.out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        items_path = Path(tmp) / "items.jsonl"
        evaluate.build_items(
            argparse.Namespace(
                sample=a.sample, config_dir=a.config_dir, out=items_path,
                to_label=Path(tmp) / "unused.tsv", half=a.half, profiles="all",
                near_misses=10**6, both=10**6, held=10**6,
            )
        )  # fmt: skip
        items = [json.loads(x) for x in items_path.read_text(encoding="utf-8").splitlines()]
    rng = random.Random(a.seed)
    cells: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for it in items:
        lang = it["lang"] if it["lang"] in ("en", "pt", "es") else "other"
        cells[(it["profile"], lang, it["stratum"])].append(it)
    # Per cell first; then each profile topped up to `--per-profile-min`,
    # one more item per cell in turn, so a profession with little material is
    # still measurable where the corpus allows. A cell's weight is its size
    # over what was drawn from it, whichever pass drew it.
    order = {cell: rng.sample(members, len(members)) for cell, members in sorted(cells.items())}
    boost = {b.split("=")[0]: float(b.split("=")[1]) for b in a.boost}
    taken = {
        cell: min(round(a.per_cell * boost.get(cell[0], 1.0)), len(members))
        for cell, members in order.items()
    }
    for profile in sorted({cell[0] for cell in order}):
        mine = [cell for cell in order if cell[0] == profile]
        while sum(taken[c] for c in mine) < a.per_profile_min:
            grew = False
            for cell in mine:
                if taken[cell] < len(order[cell]) and sum(taken[c] for c in mine) < (
                    a.per_profile_min
                ):
                    taken[cell] += 1
                    grew = True
            if not grew:
                break
    chosen: list[dict] = []
    for cell, members in order.items():
        take = members[: taken[cell]]
        for it in take:
            it["weight"] = len(members) / max(1, len(take))
        chosen += take
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    titles = dict(conn.execute("SELECT id, title FROM job"))
    texts = dict(
        conn.execute(
            "SELECT j.id, r.description_text FROM job j"
            " JOIN job_raw r ON r.content_hash = j.content_hash"
        )
    )
    headings: dict[tuple[str, int], str | None] = {}
    for job_id in {it["job_id"] for it in chosen}:
        for s in sentences(texts[job_id] or ""):
            headings[(job_id, s.start)] = s.heading
    rng.shuffle(chosen)
    review = ["id\tlanguage\tpreference\tjob title\tsection\tsentence\tlabel\tnote"]
    key = []
    for i, it in enumerate(chosen):
        item_id = f"b{i:04d}"
        heading = headings.get((it["job_id"], it["start"])) or ""
        text = texts[it["job_id"]] or ""
        sentence = _shown(text, it["start"], it["end"], it.get("m_start"), it["phrase"])
        title = " ".join((titles.get(it["job_id"]) or "").split())
        review.append(
            f"{item_id}\t{it['lang']}\t{it['phrase']}\t{title}\t{heading}\t{sentence}\t\t"
        )
        key.append(
            {
                "id": item_id, "profile": it["profile"], "lang": it["lang"],
                "job_id": it["job_id"], "signal": it["signal"], "stratum": it["stratum"],
                "weight": it["weight"], "current": it["current"], "v2": it["v2"],
                "cause": it["cause"], "context": it["context"],
                "one_word": it["profile"].startswith("one_"),
                "start": it["start"], "end": it["end"],
            }
        )  # fmt: skip
    (a.out / "REVIEW.tsv").write_text("\n".join(review) + "\n", encoding="utf-8")
    key_path = a.out / "key.jsonl"
    key_path.write_text("".join(json.dumps(k) + "\n" for k in key), encoding="utf-8")
    minutes = len(key) * MINUTES_PER_ITEM
    by_lang = Counter(k["lang"] for k in key)
    by_kind = Counter(k["stratum"] for k in key)
    profs = Counter(k["profile"] for k in key)
    (a.out / "INSTRUCOES.md").write_text(
        f"""# Avaliação cega do Work Matching (v2 e leitor atual)

{len(key)} frases de anúncios reais. Para cada linha de `REVIEW.tsv`, leia a
preferência e a frase (com o título da vaga e a seção, quando houver) e
preencha a coluna `label`:

* **Y**: a frase diz que a pessoa contratada FAZ ou PRECISA SABER fazer o
  trabalho que a preferência nomeia;
* **N**: não diz (é outro sentido da palavra, a empresa, um benefício, uma
  equipe com quem se trabalha, um aviso do processo seletivo, palavras que
  só se encontram);
* **?**: não dá para dizer só com isso.

Não há resposta "certa" esperada: o arquivo não mostra o que nenhum leitor
decidiu. Não abra `key.jsonl` durante a revisão.

Alguns anúncios chegaram sem quebras de linha e uma "frase" vira um trecho
longo: nesses casos a frase aparece cortada em volta das palavras da
preferência, marcada com `[...]`. Julgue o trecho mostrado.

Tempo estimado: cerca de {MINUTES_PER_ITEM * 60:.0f} segundos por frase, ou
{minutes / 60:.1f} horas no total. Pode ser feito em partes; a importação
aceita linhas sem rótulo.

Composição: por idioma {dict(by_lang)}; por tipo de item {dict(by_kind)};
{len(profs)} perfis (a configuração da proprietária e profissões criadas
pelo assistente de configuração, incluindo preferências de uma palavra).

Selo da chave (SHA-256 de `key.jsonl`): `{_sha(key_path)}`

Importar os rótulos:

    uv run python scripts/matching_v2_blind_package.py score --package <esta pasta> \\
        --labels <esta pasta>/REVIEW.tsv
""",
        encoding="utf-8",
    )
    weights: dict[str, list[float]] = defaultdict(list)
    for k in key:
        weights[k["stratum"]].append(k["weight"])
    from career_agent.matching_v2 import components as comp

    manifest = {
        "commit": _git("rev-parse", "HEAD"),
        "worktree_clean": _git("status", "--porcelain", "--untracked-files=no") == "",
        "component_versions": {name: c.version for name, c in comp.REGISTRY.items()},
        "seed": a.seed,
        "selection": {
            "sample": a.sample.name, "half": a.half, "per_cell": a.per_cell,
            "per_profile_min": a.per_profile_min, "boost": boost,
            "cells": "profile x language (en, pt, es, other) x kind", "window_chars": WINDOW,
        },
        "exclusions": a.exclusion,
        "items": len(key),
        "by_language": dict(by_lang),
        "by_kind": dict(by_kind),
        "by_profile": dict(profs.most_common()),
        "weights_by_kind": {
            kind: {"n": len(ws), "min": min(ws), "max": max(ws), "sum": round(sum(ws), 1)}
            for kind, ws in sorted(weights.items())
        },
        "sha256": {
            name: _sha(a.out / name) for name in ("REVIEW.tsv", "INSTRUCOES.md", "key.jsonl")
        },
    }  # fmt: skip
    (a.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"package: {a.out}; items {len(key)}; ~{minutes / 60:.1f} h to review")
    print(f"  by language {dict(by_lang)}")
    print(f"  by kind {dict(by_kind)}")
    print(f"  by profile {dict(profs.most_common())}")


def _weighted_wilson(
    hits: float, rows: list[dict], total: float | None = None
) -> tuple[float, float, float]:
    """A weighted proportion with a Wilson interval on the EFFECTIVE sample
    size (Kish: (sum w)^2 / sum w^2), so a stratum's sampling weight never
    narrows the interval as if every weight were a labelled sentence."""
    weights = [r["weight"] for r in rows]
    denominator = total if total is not None else sum(weights)
    if not weights or denominator <= 0:
        return (float("nan"), float("nan"), float("nan"))
    n_eff = sum(weights) ** 2 / sum(w * w for w in weights)
    p, lo, hi = evaluate.wilson(hits / denominator * n_eff, n_eff)
    return p, lo, hi


def score(a: argparse.Namespace) -> None:
    key_path = a.package / "key.jsonl"
    sealed = (a.package / "INSTRUCOES.md").read_text(encoding="utf-8")
    if _sha(key_path) not in sealed:
        raise SystemExit("key.jsonl does not match the seal in INSTRUCOES.md; refusing")
    key = {k["id"]: k for k in map(json.loads, key_path.read_text("utf-8").splitlines())}
    labels: dict[str, str] = {}
    lines = a.labels.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    col = header.index("label")
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) > col and parts[col].strip().upper() in ("Y", "N", "?"):
            labels[parts[0]] = parts[col].strip().upper()
    rows = [dict(key[i], label=v) for i, v in labels.items() if v in ("Y", "N")]
    print(f"labelled Y/N: {len(rows)} of {len(key)} ({sum(v == '?' for v in labels.values())} ?)")

    def report(name: str, subset: list[dict]) -> None:
        positives = sum(r["weight"] for r in subset if r["label"] == "Y")
        out = []
        for reader, said in (
            ("current", lambda r: r["current"]),
            ("v2", lambda r: r["v2"] == "MATCH"),
        ):
            yes = [r for r in subset if said(r)]
            tp = sum(r["weight"] for r in yes if r["label"] == "Y")
            p = _weighted_wilson(tp, yes)
            rc = _weighted_wilson(tp, [r for r in subset if r["label"] == "Y"], positives)
            f1 = 2 * p[0] * rc[0] / (p[0] + rc[0]) if p[0] + rc[0] else float("nan")
            out.append(
                f"{reader}: P {p[0]:.3f} [{p[1]:.3f}-{p[2]:.3f}] n={len(yes)}"
                f"  R {rc[0]:.3f} [{rc[1]:.3f}-{rc[2]:.3f}]  F1 {f1:.3f}"
            )
        print(f"{name} (n={len(subset)}):\n    " + "\n    ".join(out))

    report("ALL", rows)
    report("one-word preferences", [r for r in rows if r["one_word"]])
    report("phrases of two words or more", [r for r in rows if not r["one_word"]])
    for field in ("lang", "profile"):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r[field]].append(r)
        for value, subset in sorted(groups.items()):
            if len(subset) >= 30:
                report(f"{field}={value}", subset)
            else:
                print(f"{field}={value}: {len(subset)} labelled, too few for an estimate")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sample", type=Path, required=True)
    b.add_argument("--config-dir", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--per-cell", type=int, default=4)
    b.add_argument("--per-profile-min", type=int, default=0)
    b.add_argument("--half", choices=("blind", "any"), default="blind")
    b.add_argument("--boost", nargs="*", default=[], help="PROFILE=FACTOR on --per-cell")
    b.add_argument("--exclusion", nargs="*", default=[], help="recorded in MANIFEST.json")
    b.add_argument("--seed", type=int, default=20261014)
    s = sub.add_parser("score")
    s.add_argument("--package", type=Path, required=True)
    s.add_argument("--labels", type=Path, required=True)
    a = p.parse_args()
    build(a) if a.cmd == "build" else score(a)


if __name__ == "__main__":
    main()
