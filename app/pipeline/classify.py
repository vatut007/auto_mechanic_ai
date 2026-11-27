"""Filename -> document metadata, via config/models_catalog.json.

Rules from the plan ("Структура папок и именование"):
  minimum for indexing: brand + model + generation + doc_type, else needs_review.
Generation comes from a year range or single year in the filename.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = ROOT / "config" / "models_catalog.json"

_YEAR_RANGE = re.compile(r"(?<!\d)(19|20)\d{2}\s*[-–_/_.]\s*((19|20)\d{2})(?!\d)")
_YEAR_ONE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


@dataclass
class Meta:
    brand: str | None = None
    model: str | None = None
    generation: str | None = None
    doc_type: str | None = None
    section: str | None = None
    title: str | None = None
    market: str = "ru"                # cn explicit later (Stage 7)
    missing: list[str] = field(default_factory=list)
    draft_catalog: str | None = None  # suggestion for the one-click confirm

    @property
    def complete(self) -> bool:
        return all([self.brand, self.model, self.generation, self.doc_type])

    def as_dict(self) -> dict:
        return {
            "brand": self.brand, "model": self.model, "generation": self.generation,
            "doc_type": self.doc_type, "section": self.section, "title": self.title,
            "market": self.market, "missing": self.missing, "draft_catalog": self.draft_catalog,
        }


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[ \-_.()\[\]]+", name.lower()) if t}


def classify_filename(filename: str, catalog: dict | None = None) -> Meta:
    catalog = catalog or load_catalog()
    meta = Meta(title=Path(filename).stem.strip() or None)

    tokens = _tokens(filename)
    low = filename.lower().replace("-", " ").replace("_", " ").replace(".", " ")

    # brand + model
    for brand in catalog["brands"]:
        brand_hit = any(a in tokens or a in low for a in map(str.lower, brand["aliases"]))
        if not brand_hit:
            continue
        meta.brand = brand["slug"]
        for model in brand["models"]:
            if any(a in tokens or a in low for a in map(str.lower, model["aliases"])):
                meta.model = model["slug"]
                break
        if meta.model is None:
            meta.draft_catalog = f"brand {brand['slug']}: model not recognised — confirm/add in catalog"
        break

    if meta.brand is None:
        meta.missing.append("brand")
        meta.draft_catalog = "brand not recognised — confirm draft entry in catalog"

    # generation: "2012-2018" preferred, else single year
    m = _YEAR_RANGE.search(filename)
    if m:
        meta.generation = f"{m.group(0)[:4]}-{m.group(2)}"
    else:
        m1 = _YEAR_ONE.search(filename)
        if m1:
            meta.generation = m1.group(0)
    if not meta.generation:
        meta.missing.append("generation")

    # doc_type: stem-based, longest alias match wins
    stem_tokens = _tokens(Path(filename).stem)
    best: tuple[int, str] | None = None
    for dtype, stems in catalog["doc_types"].items():
        for stem in map(str.lower, stems):
            for tok in stem_tokens:
                if tok.startswith(stem):
                    if best is None or len(stem) > best[0]:
                        best = (len(stem), dtype)
    if best:
        meta.doc_type = best[1]
    else:
        meta.missing.append("doc_type")

    if meta.complete:
        meta.missing = []
    return meta
