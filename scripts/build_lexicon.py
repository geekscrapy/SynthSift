"""Build the compact WordNet noun lexicon shipped with SynthSift.

WordNet's hypernym hierarchy lets us put common nouns into domain categories
("garlic" -> food, "sedan" -> vehicle, "surgeon" -> role) without any LLM and
without any runtime download.  This script walks every noun lemma, looks at its
first few senses and records which category (if any) each sense falls under.

Output: src/synthsift/nlp/data/wordnet_lexicon.tsv.gz with lines
    lemma<TAB>cat_sense1|cat_sense2|cat_sense3
(an empty slot means "no category for that sense").

Usage:
    uv run python scripts/build_lexicon.py [--nltk-data DIR]

If --nltk-data is omitted the WordNet zip is downloaded from the NLTK data
repository into a temporary directory.
"""

from __future__ import annotations

import argparse
import gzip
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

WORDNET_URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/wordnet.zip"

# Ordered: when several senses of a word map to different categories, the
# runtime prefers the category that appears earliest in this list.
CATEGORY_ROOTS: list[tuple[str, list[str]]] = [
    ("food", [
        "food.n.01", "food.n.02", "foodstuff.n.02", "beverage.n.01", "dish.n.02",
        "flavorer.n.01", "edible_fruit.n.01", "vegetable.n.01", "herb.n.02",
        "nutriment.n.01", "baked_goods.n.01", "dairy_product.n.01", "meat.n.01",
        "course.n.07", "ingredient.n.03", "edible_fat.n.01",
    ]),
    ("vehicle", ["vehicle.n.01", "craft.n.02", "conveyance.n.03", "wheeled_vehicle.n.01"]),
    ("software", [
        "software.n.01", "program.n.07", "code.n.03", "computer_file.n.01",
        "database.n.01", "algorithm.n.01", "data_structure.n.01",
        "operating_system.n.01", "computer_network.n.01", "programming_language.n.01",
    ]),
    ("device", [
        "computer.n.01", "electronic_equipment.n.01", "machine.n.01", "device.n.01",
        "equipment.n.01", "hardware.n.03",
    ]),
    ("tool", ["tool.n.01", "implement.n.01", "utensil.n.01", "cutter.n.06"]),
    ("clothing", ["clothing.n.01", "footwear.n.01", "headdress.n.01"]),
    ("weapon", ["weapon.n.01"]),
    ("medical", [
        "disease.n.01", "symptom.n.01", "drug.n.01", "medicine.n.02",
        "medical_care.n.01", "pathological_state.n.01", "injury.n.01",
    ]),
    ("body", ["body_part.n.01", "organ.n.01"]),
    ("animal", ["animal.n.01"]),
    ("plant", ["plant.n.02", "plant_part.n.01"]),
    ("substance", [
        "chemical_element.n.01", "compound.n.02", "material.n.01", "substance.n.01",
        "fuel.n.01",
    ]),
    ("place", [
        "geographical_area.n.01", "body_of_water.n.01", "building.n.01",
        "structure.n.01", "location.n.01", "room.n.01",
    ]),
    ("organization", ["organization.n.01", "institution.n.01", "company.n.01"]),
    ("role", ["person.n.01"]),
    ("document", ["document.n.01", "publication.n.01", "written_communication.n.01"]),
    ("finance", ["money.n.01", "monetary_unit.n.01", "financial_loss.n.01", "payment.n.01"]),
    ("time", ["time_period.n.01", "time_unit.n.01", "calendar_day.n.01"]),
    ("measure", ["unit_of_measurement.n.01"]),
    ("activity", ["sport.n.01", "game.n.01", "diversion.n.01"]),
    ("emotion", ["feeling.n.01"]),
    ("color", ["chromatic_color.n.01", "color.n.01"]),
    ("event", ["social_event.n.01", "meeting.n.01", "festival.n.01", "contest.n.01"]),
]

MAX_SENSES = 3


def ensure_wordnet(nltk_data: str | None) -> str:
    if nltk_data:
        return nltk_data
    tmp = tempfile.mkdtemp(prefix="synthsift-wn-")
    corpora = Path(tmp) / "corpora"
    corpora.mkdir(parents=True)
    print(f"downloading WordNet -> {corpora}", file=sys.stderr)
    with urllib.request.urlopen(WORDNET_URL) as resp:  # honours HTTPS_PROXY
        (corpora / "wordnet.zip").write_bytes(resp.read())
    return tmp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nltk-data", help="directory containing corpora/wordnet(.zip)")
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "src/synthsift/nlp/data/wordnet_lexicon.tsv.gz"),
    )
    args = ap.parse_args()

    import nltk

    nltk.data.path.insert(0, ensure_wordnet(args.nltk_data))
    from nltk.corpus import wordnet as wn

    roots: list[tuple[str, set]] = []
    for cat, names in CATEGORY_ROOTS:
        synsets = set()
        for name in names:
            try:
                synsets.add(wn.synset(name))
            except Exception:  # noqa: BLE001 - report and continue
                print(f"warning: synset {name} not found", file=sys.stderr)
        roots.append((cat, synsets))

    cache: dict = {}

    def categorize(synset) -> str:
        if synset in cache:
            return cache[synset]
        closure = set(synset.closure(lambda s: s.hypernyms() + s.instance_hypernyms()))
        closure.add(synset)
        result = ""
        for cat, rs in roots:
            if closure & rs:
                result = cat
                break
        cache[synset] = result
        return result

    rows = []
    for lemma in sorted(set(wn.all_lemma_names(pos="n"))):
        senses = wn.synsets(lemma, pos="n")[:MAX_SENSES]
        cats = [categorize(s) for s in senses]
        if any(cats):
            rows.append(f"{lemma.replace('_', ' ').lower()}\t{'|'.join(cats)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the file byte-for-byte reproducible
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(("\n".join(sorted(set(rows))) + "\n").encode())
    print(f"wrote {len(rows)} lemmas to {out} ({os.path.getsize(out) // 1024} KiB)", file=sys.stderr)


if __name__ == "__main__":
    main()
