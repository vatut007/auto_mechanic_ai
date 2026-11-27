"""PDF parser (PyMuPDF): text chunks with overlap, tables -> JSON, images + cards.

Single-parser principle from the plan: one code path for every manual; OCR for
scans is deliberately out of this main path (a later, separate stage).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import PARSER_VERSION

# ~200-token overlap per the plan; tokens are estimated (RU ~2.5-3 chars/token),
# so we work in characters: ~800-char target chunks, ~200-char carried overlap.
TARGET_CHARS = 800
OVERLAP_CHARS = 200

_MIN_IMAGE_PT = 80          # smaller display size = icon/logo debris
_FIGURE_RE = re.compile(r"(?:рис(?:унок|\.\s*|\s*)?|fig(?:ure)?\.?\s*|图\s*)(\d+(?:[-–.]\d+)?)", re.I)
_TORQUE_RE = re.compile(r"н\s*[·∙•.]?\s*м\b|nm\b|扭矩|момент[аы]? затяжк", re.I)
_VOLUME_RE = re.compile(r"\bл\b|литр|мл\b|объ[её]м", re.I)
_PINOUT_RE = re.compile(r"распин|пин\b|pin\s*out|针脚|接脚", re.I)


@dataclass
class Parsed:
    chunks: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_text_layer(self) -> bool:
        return bool(self.chunks)


def _est_tokens(chars: int) -> int:
    """Crude mixed RU/CN estimate; real tokenizer counts come with Stage 3."""
    return max(1, round(chars / 2.6))


def _split_block(text: str, max_chars: int = 400) -> list[str]:
    """Split an oversized text block into pieces: by lines, then by sentences."""
    pieces: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        while len(line) > max_chars:            # no newlines inside — cut by sentences
            cut = max(
                line.rfind(".", 0, max_chars), line.rfind("!", 0, max_chars),
                line.rfind("?", 0, max_chars), line.rfind("。", 0, max_chars),
            )
            cut = cut + 1 if cut > 100 else max_chars
            pieces.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            pieces.append(line)
    return pieces


def _make_chunks(doc_id: str, blocks: list[tuple[int, str]]) -> list[dict]:
    """Group text blocks into chunks of ~TARGET chars, carrying the tail as overlap."""
    chunks: list[dict] = []
    cur: list[tuple[int, str]] = []
    cur_len = 0

    def emit() -> None:
        text = "\n".join(t for _, t in cur)
        pages = [p for p, _ in cur]
        chunks.append({
            "chunk_id": f"{doc_id}-c{len(chunks):04d}",
            "page_start": min(pages), "page_end": max(pages),
            "text": text, "chars": len(text),
            "est_tokens": _est_tokens(len(text)),
        })

    for page, text in blocks:
        for piece in _split_block(text):
            if cur and cur_len + len(piece) > TARGET_CHARS:
                emit()
                tail: list[tuple[int, str]] = []
                tail_len = 0
                for p, t in reversed(cur):
                    tail.insert(0, (p, t))
                    tail_len += len(t)
                    if tail_len >= OVERLAP_CHARS:
                        break
                cur, cur_len = tail, tail_len
            cur.append((page, piece))
            cur_len += len(piece)
    if cur:
        emit()
    return chunks


def _guess_table_type(cells: str) -> str:
    if _TORQUE_RE.search(cells):
        return "torque"
    if _PINOUT_RE.search(cells):
        return "pinout"
    if _VOLUME_RE.search(cells):
        return "volumes"
    return "generic"


def _extract_tables(doc: pymupdf.Document, doc_id: str) -> list[dict]:
    tables: list[dict] = []
    for page in doc:
        try:
            found = page.find_tables()                       # ruling lines first
        except Exception:
            continue
        strategy = "lines"
        if not found.tables:
            try:
                found = page.find_tables(strategy="text")    # borderless tables
                strategy = "text"
            except Exception:
                continue
        for t in found.tables:
            rows = t.extract()
            if not rows or len(rows) < 2:
                continue
            cells_flat = " ".join(str(c) for row in rows for c in row if c)
            header = [str(c).strip() if c else "" for c in rows[0]]
            empty_cells = sum(1 for row in rows for c in row if not (c and str(c).strip()))
            total_cells = sum(len(row) for row in rows)
            # title guess: nearest text block above the table
            title = ""
            bbox = pymupdf.Rect(t.bbox)
            best_dy = 1e9
            for x0, y0, x1, y1, text, block_no, btype, *_ in page.get_text("blocks"):
                if btype != 0 or not text.strip():
                    continue
                rect_text = pymupdf.Rect(x0, y0, x1, y1)
                if y1 <= bbox.y0 + 2 and bbox.y0 - y1 < best_dy:
                    best_dy = bbox.y0 - y1
                    title = " ".join(text.split())[:160]
            tables.append({
                "table_id": f"{doc_id}-t{len(tables):03d}",
                "page": page.number + 1,
                "strategy": strategy,
                "bbox": [round(v, 1) for v in t.bbox],
                "title_guess": title,
                "header": header,
                "n_rows": len(rows), "n_cols": len(header),
                "rows": rows,
                "type_guess": _guess_table_type(cells_flat),
                "empty_cell_ratio": round(empty_cells / max(total_cells, 1), 3),
                # torque tables are safety-critical: always flagged for manual
                # verification against the original (plan, Stage 2)
                "needs_manual_check": (
                    _TORQUE_RE.search(cells_flat) is not None
                    or strategy == "text"
                    or empty_cells / max(total_cells, 1) > 0.3
                ),
            })
    return tables


def _extract_images(doc: pymupdf.Document, doc_id: str, model: str | None, out_dir: Path) -> tuple[list[dict], int]:
    """Meaningful figures at 300 dpi; debris (icons/logos) filtered by display size."""
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    images: list[dict] = []
    skipped = 0
    for page in doc:
        page_rect = page.rect
        blocks = [
            (pymupdf.Rect(b[:4]), " ".join(b[4].split()))
            for b in page.get_text("blocks")
            if b[6] == 0 and b[4].strip()
        ]
        for xref in {info[0] for info in page.get_images(full=True)}:
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            if min(rect.width, rect.height) < _MIN_IMAGE_PT or rect.width > page_rect.width * 1.2:
                skipped += 1
                continue
            # caption: nearest text block below/above the figure
            caption, best_dy = "", 1e9
            for brect, text in blocks:
                dy = brect.y0 - rect.y1 if brect.y0 >= rect.y1 - 5 else rect.y0 - brect.y1
                if 0 <= dy < best_dy:
                    best_dy, caption = dy, text
            fig = None
            for source in (caption, page.get_text("text")):
                m = _FIGURE_RE.search(source)
                if m:
                    fig = m.group(1)
                    break
            idx = len(images)
            image_id = f"{doc_id}-i{idx:03d}"
            rel_file = f"images/{image_id}.png"
            pix = page.get_pixmap(clip=rect, dpi=300)
            pix.save(out_dir / rel_file)
            images.append({
                "image_id": image_id,
                "model": model,
                "page": page.number + 1,
                "bbox": [round(v, 1) for v in rect],
                "file": rel_file,
                "w_px": pix.width, "h_px": pix.height,
                "figure": fig,
                "caption": caption[:200],
                "type": "figure",
            })
    return images, skipped


def parse_pdf(pdf_path: Path, doc_id: str, model: str | None, out_dir: Path) -> Parsed:
    """Parse one PDF; artifacts are NOT written here — ingest decides where."""
    result = Parsed()
    with pymupdf.open(pdf_path) as doc:
        result.pages = doc.page_count
        blocks: list[tuple[int, str]] = []
        for page in doc:
            for x0, y0, x1, y1, text, block_no, btype, *_ in page.get_text("blocks"):
                if btype == 0 and text.strip():          # 0 = text block
                    blocks.append((page.number + 1, text))
        result.chunks = _make_chunks(doc_id, blocks)
        if not result.chunks:
            result.warnings.append("no text layer — OCR needed (separate path)")
        result.tables = _extract_tables(doc, doc_id)
        result.images, img_skipped = _extract_images(doc, doc_id, model, out_dir)
        if img_skipped:
            result.warnings.append(f"{img_skipped} small images skipped as debris")
    return result


def write_artifacts(parsed: Parsed, out_dir: Path) -> None:
    """Persist parsed artifacts: chunks.jsonl, tables/*.json, images/*.png + jsonl."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in parsed.chunks), encoding="utf-8"
    )
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(exist_ok=True)
    with (out_dir / "tables.jsonl").open("w", encoding="utf-8") as fh:
        for t in parsed.tables:
            (tables_dir / f"{t['table_id']}.json").write_text(
                json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    with (out_dir / "images.jsonl").open("w", encoding="utf-8") as fh:
        for im in parsed.images:
            fh.write(json.dumps(im, ensure_ascii=False) + "\n")
