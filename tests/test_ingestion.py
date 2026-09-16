"""Exercise the HTTP orchestration without loading Docling's models."""

import json
import sqlite3

import pypdfium2 as pdfium
from fastapi.testclient import TestClient

from app import main
from app.config import Settings


def make_pdf(tmp_path):
    path = tmp_path / "three-pages.pdf"
    with pdfium.PdfDocument.new() as pdf:
        for _ in range(3):
            pdf.new_page(100, 100).close()
        pdf.save(path)
    return path.read_bytes()


def test_upload_runs_full_pipeline_and_persists(tmp_path, monkeypatch):
    database = tmp_path / "ingestion.db"
    settings = Settings(database_url=f"sqlite:///{database}", _env_file=None)
    pdf_bytes = make_pdf(tmp_path)
    calls = []
    temporary_source = []

    def fake_extract(source, year, output, **kwargs):
        calls.append("extract")
        temporary_source.append(source)
        assert source.name == "report_2025.pdf"
        assert source.read_bytes() == pdf_bytes
        assert len(pdfium.PdfDocument(source)) == 3
        assert year == 2025
        output.mkdir()
        (output / "manifest.json").write_text(
            json.dumps({"filename": source.name, "year": year, "total_pages": 3})
        )
        (output / "document.json").write_text("{}")
        return output

    def fake_chunk(extraction_dir, output):
        calls.append("chunk")
        assert (extraction_dir / "document.json").exists()
        output.write_text(
            json.dumps(
                {
                    "chunks": [
                        {
                            "original_text": "original",
                            "contextualized_text": "heading\noriginal",
                            "page_numbers": [2, 3],
                            "headings": ["heading"],
                            "labels": ["text"],
                        }
                    ]
                }
            )
        )
        return 1

    original_persist = main.persist_document

    def checked_persist(sessions, manifest, chunks):
        calls.append("persist")
        return original_persist(sessions, manifest, chunks)

    monkeypatch.setattr(main, "extract_pdf", fake_extract)
    monkeypatch.setattr(main, "chunk_extraction", fake_chunk)
    monkeypatch.setattr(main, "persist_document", checked_persist)
    with TestClient(main.create_app(settings)) as client:
        response = client.post(
            "/documents",
            files={"file": ("report_2025.pdf", pdf_bytes, "application/pdf")},
            data={"year": "2025"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "document_id": 1,
        "filename": "report_2025.pdf",
        "year": 2025,
        "total_pages": 3,
        "chunk_count": 1,
    }
    assert calls == ["extract", "chunk", "persist"]
    assert not temporary_source[0].exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert connection.execute("SELECT page_start, page_end FROM chunks").fetchone() == (2, 3)


def test_invalid_pdf_and_missing_year_are_rejected(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'invalid.db'}", _env_file=None)
    with TestClient(main.create_app(settings)) as client:
        wrong_extension = client.post(
            "/documents",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"year": "2025"},
        )
        wrong_content = client.post(
            "/documents",
            files={"file": ("report.pdf", b"hello", "application/pdf")},
            data={"year": "2025"},
        )
        missing_year = client.post(
            "/documents", files={"file": ("report.pdf", b"%PDF-test", "application/pdf")}
        )
    assert wrong_extension.status_code == 400
    assert wrong_content.status_code == 400
    assert missing_year.status_code == 422


def test_ingestion_failure_returns_safe_error_without_database_rows(tmp_path, monkeypatch):
    database = tmp_path / "failed.db"
    settings = Settings(database_url=f"sqlite:///{database}", _env_file=None)
    source_path = []

    def fail_extraction(source, year, output, **kwargs):
        source_path.append(source)
        raise RuntimeError("sensitive internal failure")

    monkeypatch.setattr(main, "extract_pdf", fail_extraction)
    with TestClient(main.create_app(settings)) as client:
        response = client.post(
            "/documents",
            files={"file": ("report.pdf", make_pdf(tmp_path), "application/pdf")},
            data={"year": "2025"},
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "Document ingestion failed"}
    assert not source_path[0].exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
