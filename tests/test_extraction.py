"""Fast input checks using a generated PDF; no Docling models required."""

import pypdfium2 as pdfium
import pytest

from app.extraction import extract_pdf, validate_pages


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "sample.pdf"
    with pdfium.PdfDocument.new() as pdf:
        for _ in range(3):
            page = pdf.new_page(100, 100)
            page.close()
        pdf.save(path)
    return path


def test_page_range_defaults_to_document_end(pdf_path):
    assert validate_pages(pdf_path, 2, None) == (3, 3)


@pytest.mark.parametrize("start,end", [(0, 1), (3, 2), (1, 4)])
def test_rejects_invalid_ranges(pdf_path, start, end):
    with pytest.raises(ValueError, match="Page range"):
        validate_pages(pdf_path, start, end)


def test_existing_output_is_not_overwritten(pdf_path, tmp_path):
    with pytest.raises(ValueError, match="already exists"):
        extract_pdf(pdf_path, 2025, tmp_path)
