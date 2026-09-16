# Multimodal document ingestion

A take-home project for ingesting TotalEnergies annual reports into a relational
database, preserving text, tables, images, and source page references.

**Current status:** the FastAPI application accepts complete PDF uploads and
persists text chunks with Docling, HybridChunker, and SQLite. Standalone extraction
and chunking commands are also available. Image export, the chunk inspection
interface, and Docker packaging are subsequent milestones.

## Setup

Use Python 3.12. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

`requirements.txt` pins application dependencies; `requirements-dev.txt` adds
test and lint tools. Docling and PDFium are pinned directly; Docling's transitive
dependencies are currently resolved by pip, not fully locked. `pyproject.toml`
contains only test and lint configuration; package versions
are maintained in the requirements files.

## Run

```sh
python -m app.main
```

- Health check: <http://127.0.0.1:8000/health>
- Interactive API documentation: <http://127.0.0.1:8000/docs>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>

```sh
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

The health endpoint reports process liveness only. It does not imply that a
database or parser is available.

Upload one complete PDF with a required integer year:

```sh
curl -F 'file=@data/raw/report_2025.pdf' -F 'year=2025' \
  http://127.0.0.1:8000/documents
```

`POST /documents` returns `document_id`, `filename`, `year`, `total_pages`, and
`chunk_count`. It always processes the **entire uploaded PDF** and saves the
document and chunks before replying. Large reports may take time. You can also
use `/docs` to send the request interactively. The PDF and intermediate files
are removed from the temporary work directory after the request; the database
record remains. The upload must have a `.pdf` filename and PDF file header.

For development with automatic reload:

```sh
python -m uvicorn app.main:create_app --factory --reload
```

The reload command uses Uvicorn's default host and port; pass `--host` and `--port`
explicitly to override them.

## Configuration

Defaults work without a configuration file. To customize `python -m app.main`,
copy `.env.example` to `.env` or set environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RAG_APP_NAME` | `Multimodal RAG Ingestion` | Title in the API documentation |
| `RAG_HOST` | `127.0.0.1` | Server bind address |
| `RAG_PORT` | `8000` | Server port, from 1 to 65535 |
| `RAG_DATABASE_URL` | `sqlite:///local/multimodal_rag.db` | SQLite database location |

Environment variables take precedence over `.env`. Do not commit `.env` or
credentials.

## Extract a PDF

Run from the project root with the virtual environment activated:

```sh
python -m app.extraction data/raw/report_2025.pdf --year 2025 \
  --start 455 --end 456 --output local/extractions/my-2025-sample
```

Page numbers are physical PDF pages, **1-based and inclusive**. The report year
is metadata supplied by you; it does not replace years printed inside tables.
Omit `--start` and `--end` to process the entire report. Start with a sample:
full-report runtime and memory use have not yet been validated.

The output directory must be new. It contains:

- `document.json`: Docling's structured document, including text, table cells,
  detected picture metadata, reading order, and original page/bounding-box references.
- `document.md`: readable extraction for visual checking.
- `manifest.json`: source filename/hash, report year, total page count, extracted
  page range, parser version, counts, and warnings.

This command extracts only; it does not create chunks or database records.
Detected picture locations are retained, but image files are not exported yet.
Keep the JSON and manifest together for later processing.

Docling runs locally on CPU with four threads. Its models download on first use
and are cached under `local/models/` by default (ignored by Git). Existing
`HF_HOME` settings are respected. No hosted LLM API key is required.

OCR is disabled by default for selectable-text PDFs. Pass `--ocr` to enable
Docling's automatic OCR selection; it may need additional models or dependencies
and has not yet been validated in this project. With OCR off, image/outlined text
may be missed. The manifest flags pages with fewer than 50 extracted characters.

Table extraction is not infallible: in the tested balance sheet, Docling splits
the content into two tables and marks the second table's first data row as a
header. The output deliberately retains the original structure and records a
warning instead of guessing replacements. Shared year/unit context must be
retained when we implement chunking. Compare financial values against the PDF.

## Chunk a saved extraction

Use the structured `document.json` produced by extraction:

```sh
python -m app.chunking local/extractions/my-sample \
  --output local/chunks/my-sample.json
```

The output JSON contains the source identity, tokenizer settings, and a list of
chunks. Each chunk has a stable ID within that run, original text for display,
`contextualized_text` for future embeddings, page numbers, headings, labels,
and Docling metadata including its source `doc_items` and provenance. Chunking
does not use the Markdown export and does not create embeddings or database records.

By default the command uses `sentence-transformers/all-MiniLM-L6-v2` for
tokenization and asks HybridChunker to target 480 tokens. The tokenizer downloads
on first use into the ignored `local/models/huggingface/` directory. Change the
settings when the embedding model is selected:

```sh
python -m app.chunking local/extractions/my-sample \
  --output local/chunks/with-new-tokenizer.json \
  --tokenizer MODEL_ID --max-tokens 480
```

The JSON includes each contextualized token count and `chunks_over_max_tokens`.
Docling can exceed the requested target by a few tokens after adding context;
the count makes that visible so the setting can leave room for the chosen model.
Existing output files are never overwritten.

## Save a document and its chunks

After extraction and chunking, save their existing outputs in one transaction:

```sh
python -m app.database local/extractions/my-sample local/chunks/my-run.json
```

This creates `local/multimodal_rag.db` by default. Set `RAG_DATABASE_URL` to use
another SQLite file, for example `sqlite:///local/test-run.db`. Run commands from
the repository root so relative paths resolve as shown. Each invocation inserts
a new document; running it again creates another record.

The schema has two tables:

- `documents`: ID, filename, report year, total **physical** PDF pages, optional
  SHA-256 source hash, and creation timestamp.
- `chunks`: ID, document foreign key, 1-based order index, original text,
  contextualized text, nullable first/last page, and JSON headings/labels.

The index comes from chunk order, not the display ID. Missing page numbers stay
null. Deleting a document through the ORM deletes its chunks. SQLite foreign keys
are enabled so database-level cascading is also enforced. If any chunk fails to
save, the document and all its chunks are rolled back together.

Inspect the database with Python's standard library:

```sh
python -c "import sqlite3; c=sqlite3.connect('local/multimodal_rag.db'); print(c.execute('SELECT id, filename, year, total_pages FROM documents').fetchall()); print(c.execute('SELECT chunk_index, page_start, page_end FROM chunks ORDER BY chunk_index LIMIT 5').fetchall())"
```

## Validate

```sh
python -m pytest
python -m ruff check app tests
python -m ruff format --check app tests
```

Tests cover the API, environment settings, extraction page-range validation,
overwrite prevention, SQLite persistence and rollback, and the upload request
flow. They need no source PDFs, model downloads, or network access; a tiny PDF is
generated for input-validation tests. These checks do not measure extraction
accuracy; inspect the real sample outputs separately.

## Project structure

```text
app/
  config.py             # Environment-based settings
  main.py               # Application factory, health and upload routes, server
  extraction.py         # Docling extraction function and command-line entry point
  chunking.py           # HybridChunker command and inspectable JSON output
  database.py           # SQLite schema, sessions, and transactional persistence
tests/
  test_app.py            # API and configuration checks
  test_extraction.py     # PDF range and output safeguards
  test_ingestion.py      # Upload validation and pipeline orchestration
.env.example            # Optional configuration template
pyproject.toml          # Test and lint configuration
requirements.txt        # Pinned runtime dependencies
requirements-dev.txt    # Pinned test and lint dependencies
data/raw/               # Local source PDFs (ignored by Git)
local/                  # Assessment, experiments, and inspection output (ignored)
```

## Local data and exploratory work

Source PDFs are supplied separately and are not committed. During development,
keep `report_2022.pdf`, `report_2023.pdf`, `report_2024.pdf`, and `report_2025.pdf`
in `data/raw/` (create this directory after cloning).

Exploratory work is grouped in the ignored `local/` directory: `scripts/` holds
inspection utilities, `docs/` holds working notes, `artifacts/` holds generated
outputs, and `reference/` holds the assessment. These files are local working
material and are not required to start or test the application.

The virtual environment, source PDFs, secrets, databases, generated artifacts,
and local inspection experiments are excluded from Git. The repository contains
the application source, dependency definitions, tests, and documentation. Docker
configuration and the architecture discussion will be added with implementation.
