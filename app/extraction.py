"""Extract a PDF with Docling; preserve its document structure for later chunking."""

import argparse
import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium


def validate_pages(source: Path, start: int, end: int | None) -> tuple[int, int]:
    """Validate against physical, 1-based PDF pages before loading models."""
    if not source.is_file():
        raise ValueError(f"PDF does not exist: {source}")
    with pdfium.PdfDocument(source) as pdf:
        total = len(pdf)
    end = total if end is None else end
    if not 1 <= start <= end <= total:
        raise ValueError(f"Page range must satisfy 1 <= start <= end <= {total}")
    return total, end


def extract_pdf(
    source: Path,
    year: int,
    output: Path,
    *,
    start: int = 1,
    end: int | None = None,
    ocr: bool = False,
) -> Path:
    """Save one extraction run. Output must not exist, to prevent accidental overwrites."""
    total, end = validate_pages(source, start, end)
    if output.exists():
        raise ValueError(f"Output already exists; choose a new directory: {output}")

    # Keep model downloads local and ignored by Git. Import after configuring caches.
    model_cache = Path("local/models").resolve()
    os.environ.setdefault("HF_HOME", str(model_cache / "huggingface"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, PdfFormatOption

    settings.cache_dir = model_cache / "docling"
    options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True)
    options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4)
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    print(f"Extracting {source.name}, pages {start}–{end} (OCR={ocr})", flush=True)
    result = converter.convert(source, page_range=(start, end))
    if result.status != ConversionStatus.SUCCESS:
        raise RuntimeError(f"Extraction did not complete successfully: {result.status}")
    document = result.document
    expected_pages = set(range(start, end + 1))
    if set(document.pages) != expected_pages:
        raise RuntimeError("Extracted page numbers do not match the requested PDF pages")

    warnings = []
    if not ocr:
        warnings.append("OCR disabled: visible text absent from the PDF text layer may be missing.")
    sparse_pages = []
    for page in sorted(expected_pages):
        characters = sum(
            len(item.text) for item in document.texts if any(p.page_no == page for p in item.prov)
        )
        characters += sum(
            len(cell.text)
            for table in document.tables
            if any(p.page_no == page for p in table.prov)
            for cell in table.data.table_cells
        )
        if characters < 50:
            sparse_pages.append(page)
    if sparse_pages:
        warnings.append(f"Pages with fewer than 50 extracted text characters: {sparse_pages}")
    if document.tables:
        warnings.append(
            "Table structure is uncorrected model output. Check headers and continuations: "
            "a split table may lack year headers or mark its first data row as a header. "
            "Keep same-page headings and preceding tables as context."
        )
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "filename": source.name,
        "year": year,
        "source_sha256": digest,
        "total_pages": total,
        "page_start": start,
        "page_end": end,
        "extracted_pages": sorted(expected_pages),
        "parser": "docling",
        "parser_version": version("docling"),
        "ocr_enabled": ocr,
        "table_count": len(document.tables),
        "picture_count": len(document.pictures),
        "warnings": warnings,
    }
    output.mkdir(parents=True, exist_ok=False)
    document.save_as_json(output / "document.json")
    document.save_as_markdown(output / "document.md")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--year", type=int, required=True, help="Report year, not table column year"
    )
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--start", type=int, default=1, help="First physical PDF page, inclusive")
    parser.add_argument(
        "--end", type=int, help="Last physical PDF page, inclusive; default: last page"
    )
    parser.add_argument(
        "--ocr", action="store_true", help="Enable OCR; may download additional models"
    )
    args = parser.parse_args()
    try:
        output = extract_pdf(
            args.pdf, args.year, args.output, start=args.start, end=args.end, ocr=args.ocr
        )
    except (ValueError, RuntimeError, OSError, pdfium.PdfiumError) as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Saved extraction to {output}")


if __name__ == "__main__":
    main()
