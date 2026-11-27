"""Pipeline tests with a synthetic PDF (no real manuals needed, fully offline)."""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from app.pipeline.classify import classify_filename
from app.pipeline.ingest import run_ingest
from app.pipeline.manifest import Manifest

PARAGRAPHS = [
    "Замена моторного масла в двигателе CFNA. Работу проводите на остывшем двигателе.",
    "Поддомкратьте автомобиль и установите на надёжные опоры. Снимите защиту картера.",
    "Выверните сливную пробку и слейте масло в ёмкость объёмом не менее четырёх литров.",
    "Заменяйте уплотнительное кольцо сливной пробки при каждом обслуживании.",
    "Момент затяжки сливной пробки указан в таблице регламента и строго обязателен.",
    "Залейте новое масло через заливную горловину и проверьте уровень щупом.",
    "Запустите двигатель, убедитесь в отсутствии течей и погасании лампы давления.",
] * 3  # long enough for several chunks

# base-14 fonts cannot encode Cyrillic — embed a real TTF (skip tests without it)
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
pytestmark = pytest.mark.skipif(not FONT.exists(), reason="no Cyrillic TTF font available")


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """2 pages: long text + torque table + one real image (rect render)."""
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    y = 60
    for para in PARAGRAPHS:
        page.insert_text((50, y), para, fontsize=11, fontfile=str(FONT), fontname="dejavu")
        y += 20
    # second page: bordered torque table + an image (like a real service manual)
    page2 = doc.new_page()
    page2.insert_text((50, 60), "Таблица 1. Моменты затяжки резьбовых соединений, Н·м",
                      fontsize=12, fontfile=str(FONT), fontname="dejavu")
    table_rows = [
        ("Соединение", "Момент, Н·м"),
        ("Сливная пробка масляного поддона", "30"),
        ("Болты крепления колеса", "120"),
        ("Свечи зажигания", "25"),
    ]
    x0, x_mid, x1 = 50, 300, 420
    y0, row_h = 80, 20
    for i in range(len(table_rows) + 1):                     # horizontal ruling
        y = y0 + i * row_h
        page2.draw_line((x0, y), (x1, y))
    for x in (x0, x_mid, x1):                                # vertical ruling
        page2.draw_line((x, y0), (x, y0 + len(table_rows) * row_h))
    for i, (name, value) in enumerate(table_rows):
        ty = y0 + i * row_h + 14
        page2.insert_text((x0 + 4, ty), name, fontsize=10, fontfile=str(FONT), fontname="dejavu")
        page2.insert_text((x_mid + 4, ty), value, fontsize=10, fontfile=str(FONT), fontname="dejavu")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200))
    pix.clear_with(90)  # solid gray rectangle as a figure
    page2.insert_image(pymupdf.Rect(60, 200, 360, 400), pixmap=pix)
    page2.insert_text((60, 415), "Рис. 1 — Сливная пробка масляного поддона", fontsize=10,
                      fontfile=str(FONT), fontname="dejavu")
    path = tmp_path / "skoda-rapid-2012-2018-ekspl-ru.pdf"
    doc.save(path)
    doc.close()
    return path


def test_classify_full_meta():
    meta = classify_filename("skoda-rapid-2012-2018-ekspl-ru.pdf")
    assert (meta.brand, meta.model, meta.doc_type) == ("skoda", "rapid", "эксплуатация")
    assert meta.generation == "2012-2018"
    assert meta.complete


def test_classify_incomplete():
    meta = classify_filename("руководство.pdf")
    assert not meta.complete
    assert "brand" in meta.missing


def test_ingest_end_to_end(tmp_path: Path, sample_pdf: Path):
    import hashlib
    import shutil as sh

    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    sh.copyfile(sample_pdf, inbox / sample_pdf.name)
    sha = hashlib.sha256(sample_pdf.read_bytes()).hexdigest()

    manifest = Manifest(data / "pipeline.db")
    report = run_ingest(inbox=inbox, data_root=data, manifest=manifest)

    assert report.new == 1 and report.indexed == 1 and report.failed == 0
    doc = manifest.get_by_sha(sha)
    assert doc["status"] == "indexed"
    assert doc["relpath"] == "library/skoda/rapid/2012-2018/эксплуатация/skoda-rapid-2012-2018-ekspl-ru.pdf"
    assert (data / doc["relpath"]).exists()          # moved out of inbox
    assert not (inbox / sample_pdf.name).exists()

    out = Path(doc["out_dir"])
    chunks = [json.loads(l) for l in (out / "chunks.jsonl").read_text().splitlines()]
    assert len(chunks) >= 2
    assert any("момент" in c["text"].lower() for c in chunks)
    # overlap: the paragraph ending chunk[0] reappears at the start of chunk[1]
    tail_para = chunks[0]["text"].split("\n")[-1][:50]
    assert tail_para in chunks[1]["text"]

    tables_meta = [json.loads(l) for l in (out / "tables.jsonl").read_text().splitlines()]
    assert tables_meta and tables_meta[0]["type_guess"] == "torque"
    assert tables_meta[0]["strategy"] == "lines"          # bordered table, primary strategy
    assert tables_meta[0]["needs_manual_check"] is True   # torque table: always verify
    assert any("Сливная пробка" in str(cell) for row in tables_meta[0]["rows"] for cell in row)

    images_meta = [json.loads(l) for l in (out / "images.jsonl").read_text().splitlines()]
    assert images_meta and images_meta[0]["figure"] == "1"
    assert (out / images_meta[0]["file"]).exists()


def test_duplicate_is_skipped(tmp_path: Path, sample_pdf: Path):
    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    import shutil as sh
    target = inbox / sample_pdf.name
    sh.copyfile(sample_pdf, target)
    manifest = Manifest(data / "pipeline.db")

    run_ingest(inbox=inbox, data_root=data, manifest=manifest)
    # same bytes back into inbox (name differs, hash same)
    sh.copyfile(sample_pdf, inbox / "another-name.pdf")
    report = run_ingest(inbox=inbox, data_root=data, manifest=manifest)

    assert report.scanned == 1 and report.duplicates == 1 and report.new == 0


def test_needs_review_stays_in_inbox(tmp_path: Path, sample_pdf: Path):
    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    import shutil as sh
    sh.copyfile(sample_pdf, inbox / "руководство.pdf")  # no brand/model info

    report = run_ingest(inbox=inbox, data_root=data, manifest=Manifest(data / "pipeline.db"))
    assert report.needs_review == 1
    assert (inbox / "руководство.pdf").exists()  # not moved
    row = report.items[0]
    assert row["status"] == "needs_review" and "brand" in row["reason"]


def test_failed_pdf_recorded(tmp_path: Path):
    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    (inbox / "broken.pdf").write_bytes(b"not a pdf at all")

    report = run_ingest(inbox=inbox, data_root=data, manifest=Manifest(data / "pipeline.db"))
    assert report.failed == 1
    assert report.items[0]["status"] == "failed"
