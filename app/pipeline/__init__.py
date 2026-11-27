"""Document pipeline (Stage 2): inbox -> manifest -> parse -> library.

Modules:
  classify — filename -> metadata via the model catalog
  manifest — SQLite: documents + runs, statuses, dedupe by sha256
  parser   — PyMuPDF: text chunks (overlap), tables -> JSON, images + cards
  ingest   — orchestration: lock, scan, parse, store, move, report
"""
from __future__ import annotations

PARSER_VERSION = "v1-pymupdf2.2"

STATUSES = ("indexed", "needs_review", "failed")
