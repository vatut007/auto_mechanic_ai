"""Ingest orchestration: lock -> scan inbox -> parse -> store -> move -> report.

Flow per the plan ("Конвейер пополнения базы"):
  sha256 first; duplicate -> skip; same name different hash -> re-parse new doc_id;
  parse -> artifacts under data/processed/<doc_id>/; complete metadata -> move to
  library/<brand>/<model>/<generation>/<doc_type>/, else needs_review (file stays).
Double-run lock: flock on data/pipeline.lock (second trigger fails with a message).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import PARSER_VERSION
from .classify import classify_filename
from .manifest import Manifest
from .parser import parse_pdf, write_artifacts

DEFAULT_DATA = Path(__file__).resolve().parent.parent.parent / "data"


@dataclass
class RunReport:
    scanned: int = 0
    new: int = 0
    duplicates: int = 0
    indexed: int = 0
    needs_review: int = 0
    failed: int = 0
    items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["items"] = self.items
        return d


class PipelineBusy(RuntimeError):
    pass


class _Lock:
    """Coarse whole-pipeline lock; a second concurrent run refuses to start."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self) -> "_Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            raise PipelineBusy("pipeline is already running (data/pipeline.lock is held)")
        return self

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _slug(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip()) or "untitled"


def _library_relpath(meta, original_name: str) -> str:
    title = f"{_slug(meta.title or Path(original_name).stem)}.pdf"
    parts = ["library", meta.brand, meta.model, meta.generation, meta.doc_type]
    if meta.section:
        parts.append(meta.section)
    parts.append(title)
    return "/".join(parts)


def run_ingest(
    inbox: Path = DEFAULT_DATA / "inbox",
    data_root: Path = DEFAULT_DATA,
    manifest: Manifest | None = None,
) -> RunReport:
    inbox = Path(inbox)
    data_root = Path(data_root)
    processed_root = data_root / "processed"
    library_root = data_root / "library"
    manifest = manifest or Manifest(data_root / "pipeline.db")

    report = RunReport()
    with _Lock(data_root / "pipeline.lock"):
        run_id = manifest.start_run()
        pdfs = sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        report.scanned = len(pdfs)

        for pdf in pdfs:
            sha = _sha256(pdf)
            item: dict = {"file": pdf.name, "sha256": sha[:12], "status": None}
            if manifest.get_by_sha(sha):
                report.duplicates += 1
                item["status"] = "duplicate"
                item["reason"] = "hash already in manifest — skipped"
                report.items.append(item)
                continue

            doc_id = sha[:16]
            meta = classify_filename(pdf.name)
            out_dir = processed_root / doc_id
            status, reason = None, None

            try:
                parsed = parse_pdf(pdf, doc_id, meta.model, out_dir)
                write_artifacts(parsed, out_dir)
            except Exception as e:  # noqa: BLE001 — one broken PDF must not stop the batch
                status, reason = "failed", f"parse error: {e}"
                manifest.upsert_document(
                    doc_id=doc_id, sha256=sha, original_name=pdf.name, status=status,
                    reason=reason, out_dir=str(out_dir), parser_version=PARSER_VERSION,
                )
                report.failed += 1
                item.update({"status": status, "reason": reason})
                report.items.append(item)
                continue

            if not parsed.has_text_layer:
                status = "needs_review"
                reason = "no text layer — OCR required"
            elif not meta.complete:
                status = "needs_review"
                reason = "metadata incomplete: " + ", ".join(meta.missing) + \
                         (f"; {meta.draft_catalog}" if meta.draft_catalog else "")
            else:
                status = "indexed"

            relpath = None
            if status == "indexed":
                relpath = _library_relpath(meta, pdf.name)
                target = data_root / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    relpath = str(Path(relpath).with_stem(f"{Path(relpath).stem}__{doc_id}"))
                    target = data_root / relpath
                shutil.move(str(pdf), target)

            manifest.upsert_document(
                doc_id=doc_id, sha256=sha, original_name=pdf.name, relpath=relpath,
                brand=meta.brand, model=meta.model, generation=meta.generation,
                doc_type=meta.doc_type, section=meta.section, title=meta.title,
                market=meta.market, status=status, reason=reason,
                pages=parsed.pages, chunks_count=len(parsed.chunks),
                tables_count=len(parsed.tables), images_count=len(parsed.images),
                out_dir=str(out_dir), parser_version=PARSER_VERSION,
            )
            (out_dir / "meta.json").write_text(
                json.dumps({"doc_id": doc_id, "meta": meta.as_dict(),
                            "status": status, "reason": reason,
                            "parser_version": PARSER_VERSION},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

            report.new += 1
            if status == "indexed":
                report.indexed += 1
            elif status == "needs_review":
                report.needs_review += 1
            else:
                report.failed += 1
            item.update({"status": status, "doc_id": doc_id,
                         "meta": {k: meta.as_dict()[k] for k in
                                  ("brand", "model", "generation", "doc_type")},
                         "chunks": len(parsed.chunks), "tables": len(parsed.tables),
                         "images": len(parsed.images), "relpath": relpath})
            if reason:
                item["reason"] = reason
            report.items.append(item)

        manifest.finish_run(
            run_id, scanned=report.scanned, new=report.new, duplicates=report.duplicates,
            indexed=report.indexed, needs_review=report.needs_review, failed=report.failed,
        )
    return report


def status(manifest: Manifest | None = None, data_root: Path = DEFAULT_DATA) -> dict:
    manifest = manifest or Manifest(Path(data_root) / "pipeline.db")
    last = manifest.last_run()
    return {
        "counts_by_status": manifest.counts_by_status(),
        "needs_review": [dict(r) for r in manifest.needs_review()],
        "last_run": dict(last) if last else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
