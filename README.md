# Multimodal document ingestion

Ingest annual-report PDFs into SQLite, preserving text, tables, images, and
source page references. Upload a PDF through FastAPI's browser interface or
`curl`, then inspect the saved chunks and images in an HTML page.

The project implements ingestion and inspection. It does not yet implement
embeddings, vector search, or answer generation.

## Quick start with Docker

Install [Docker Desktop](https://docs.docker.com/get-started/get-docker/) or Docker
Engine with the Compose plugin, and start Docker. No local Python installation
or API key is required. Clone this repository, open a terminal in its root, and run:

```sh
docker compose up --build -d --wait
```

Open <http://127.0.0.1:8000/docs>. The first build downloads Python dependencies
and can take several minutes. Models download on the first ingestion request;
that request needs internet access and can take longer than later requests.
Model files are cached in a persistent volume. The health check verifies HTTP
liveness, not model availability or extraction quality.

The image runs on CPU, with four Docling threads. As a starting allocation for
Docker Desktop, allow 4 CPUs and 8 GB of memory; this is not a measured minimum
and large reports may need more. Start with a PDF containing a few pages.
The Dockerfile does not force CPU architecture; dependency availability and
runtime should be checked on your platform. CI is configured for Linux x86-64.

Useful commands:

```sh
docker compose ps
docker compose logs -f app
docker compose stop
docker compose start
```

If port 8000 is occupied, run `RAG_PUBLISHED_PORT=8001 docker compose up -d --wait`
and use port 8001 in the URLs below. Rebuild after changing application code:
`docker compose up --build -d --wait`.

## Upload and inspect a PDF

Source PDFs are supplied separately; they are not included in the repository or
Docker image. You can select a file anywhere on your computer in the browser:

1. Open <http://127.0.0.1:8000/docs>.
2. Expand **POST /documents**, then click **Try it out**.
3. Choose a PDF, enter its report year, and click **Execute**.
4. Wait for the response and note its `document_id`.
5. Open `http://127.0.0.1:8000/documents/ID/debug`, replacing `ID` with that number.

The inspection page displays original chunk text, headings, labels, page ranges,
and associated images. It has no upload form. Image files are served through
`/debug/images/{image_id}`; unknown IDs and missing files return 404.

Alternatively, run this on your host, replacing the file path as needed:

```sh
curl --fail-with-body \
  -F 'file=@data/raw/report_2022.pdf' \
  -F 'year=2022' \
  http://127.0.0.1:8000/documents
```

Both methods run the same pipeline:

```text
PDF → Docling extraction → HybridChunker → image association → SQLite + image files
```

The response contains `document_id`, `filename`, `year`, `total_pages`, and
`chunk_count`. Each upload inserts a new document; there is no deduplication.
The endpoint processes the **entire uploaded PDF**, with no page-range selector.
A filename such as `pages_8-11.pdf` does not restrict processing: the file itself
must contain only those pages. Page references refer to physical pages in the
uploaded file, so a four-page excerpt is numbered 1–4.

Uploads run synchronously and large reports may take many minutes. Avoid sending
multiple large uploads concurrently. The original upload and intermediate files
are temporary; committed database rows and exported images remain. Consult
`docker compose logs -f app` for parser output and errors. There is currently no
progress percentage or cancellation endpoint.

## Persistent storage

Compose creates three Docker-managed named volumes:

| Volume | Container path | Contents |
| --- | --- | --- |
| `database` | `/data/db` | `multimodal_rag.db` and SQLite sidecar files |
| `images` | `/data/images` | Exported PNGs, grouped by upload |
| `models` | `/app/local/models` | Docling and Hugging Face model/tokenizer caches |

Docker prefixes volume names with the Compose project name. Data survives
container recreation, rebuilds, and `docker compose down`. The container runs as
UID/GID 10001; the image prepares writable storage directories for new volumes.
Test containers do not mount application volumes.

```sh
# Remove containers and network; keep all three volumes.
docker compose down
```

`docker compose down --volumes` **deletes the database, images, and model cache**.
Use it only for an intentional complete reset. Docker starts with its own empty
database; it does not import your existing host `local/` directory.

To make a consistent backup, wait for uploads to finish, then stop the app and
copy both the database directory and image directory before restarting:

```sh
mkdir -p local/backups
docker compose stop app
docker compose cp app:/data/db local/backups/db
docker compose cp app:/data/images local/backups/images
docker compose start app
```

Use a fresh backup destination each time. Restore both directories together at
the same container paths and preserve UID/GID 10001 ownership. Model caches can
be downloaded again. The database stores image paths, not the image bytes.

## Tests and checks

Run the tests in Docker without starting the application:

```sh
docker compose --profile test run --build --rm tests
docker compose --profile test run --rm tests python -m ruff check --no-cache app tests
docker compose --profile test run --rm tests python -m ruff format --check --no-cache app tests
```

The separate test image contains pytest and Ruff; the runtime image does not.
Tests cover API validation, ingestion orchestration, page-range safeguards,
image association, database persistence/rollback, and debug routes, including
HTML escaping and missing resources. Tests generate small fixtures and do not
require source PDFs, downloaded models, or network access during execution.
Building the images does require internet access.

GitHub Actions runs these checks, builds and starts the runtime image, and checks
HTTP endpoints and volume write permissions. It does not download models or
validate real PDF extraction. For a manual smoke test, upload a small PDF, open
its debug page, run `docker compose down` followed by `docker compose up -d --wait`,
and confirm the same document and images still load.

**Validation status:** the Python tests and lint checks have been run locally.
Docker was unavailable in the development environment where this configuration
was added; a successful Docker build and real ingestion smoke test are still
required before claiming the container is verified.

## Stack and database schema

| Component | Tool / purpose |
| --- | --- |
| Runtime | Python 3.12 |
| HTTP API | FastAPI, Uvicorn, Pydantic settings |
| PDF parsing | Docling, with PDFium (`pypdfium2`) for PDF handling |
| Chunking | Docling Core `HybridChunker`, preserving headings and provenance |
| Tokenizer | `sentence-transformers/all-MiniLM-L6-v2`; target 480 tokens |
| Image handling | Docling picture regions, PNG export, association with chunks |
| Persistence | SQLite through SQLAlchemy 2; transactional document writes |
| Inspection | Server-rendered HTML and image endpoints |
| Validation | pytest, HTTPX TestClient, Ruff |
| Packaging | Docker, Docker Compose; GitHub Actions for checks |

`requirements.txt` pins direct application dependencies, and
`requirements-dev.txt` adds test/lint dependencies. Transitive dependencies,
including PyTorch and Docling Core, are not fully locked. The Docker build
installs CPU PyTorch wheels first and checks dependency consistency with
`pip check`. Model downloads are separate from the image build.

SQLite is initialized automatically on startup. No database server or manual
schema setup is needed. Relationships are:

```text
documents (1) ── (many) chunks (1) ── (many) images
```

| Table | Columns |
| --- | --- |
| `documents` | `id` PK, `filename`, `year`, `total_pages`, nullable `source_sha256`, `created_at` |
| `chunks` | `id` PK, `document_id` FK, `chunk_index`, `original_text`, `contextualized_text`, nullable `page_start`/`page_end`, JSON `headings`/`labels` |
| `images` | `id` PK, `chunk_id` FK, `image_index`, `page_number`, `path`, nullable `caption`, JSON `bbox` |

Chunk indexes are 1-based within a document. Missing page references remain null.
Document, chunk, and image rows are committed together or rolled back together.
Foreign keys and cascading deletes are enabled. Cascading deletes remove image
records, but do not delete their files from disk. There is no migration system;
future schema changes require an explicit migration or an intentional reset.

Inspect saved document metadata inside the running container:

```sh
docker compose exec app python -c "import sqlite3; c=sqlite3.connect('/data/db/multimodal_rag.db'); print(c.execute('SELECT id, filename, year, total_pages FROM documents').fetchall())"
```

## Local development without Docker

Use Python 3.12 and run from the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m app.main
```

The same API and inspection URLs apply. To enable automatic reload:

```sh
python -m uvicorn app.main:create_app --factory --reload
```

Run local checks:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check app tests
.venv/bin/python -m ruff format --check app tests
```

Copy `.env.example` to `.env` to customize local settings. Environment variables
have precedence over `.env` values.

| Variable | Local default | Docker default |
| --- | --- | --- |
| `RAG_HOST` | `127.0.0.1` | `0.0.0.0` inside the container |
| `RAG_PORT` | `8000` | `8000` inside the container |
| `RAG_DATABASE_URL` | `sqlite:///local/multimodal_rag.db` | `sqlite:////data/db/multimodal_rag.db` |
| `RAG_IMAGE_DIR` | `local/images` | `/data/images` |
| `RAG_APP_NAME` | `Multimodal RAG Ingestion` | Same |
| `RAG_MIN_IMAGE_AREA_RATIO` | `0.001` | Same; configurable through Compose |
| `HF_HOME` | `local/models/huggingface` (resolved absolute) | `/app/local/models/huggingface` |
| `RAG_PUBLISHED_PORT` | Not used | `8000` on the host, configurable through Compose |

Compose deliberately keeps container storage paths fixed and does not pass your
local `.env` wholesale into the container. Its supported interpolated settings
are `RAG_PUBLISHED_PORT` and `RAG_MIN_IMAGE_AREA_RATIO`. Local source files,
credentials, databases, PDFs, and model caches are excluded from the build context.
The API is published on host loopback only and has no authentication; this setup
is intended for local evaluation.

## Standalone extraction and chunking

With the local virtual environment activated, you can process selected physical
PDF pages without uploading. The output paths below must be new:

```sh
python -m app.extraction data/raw/report_2022.pdf --year 2022 \
  --start 8 --end 11 --output local/extractions/2022-sample
python -m app.chunking local/extractions/2022-sample \
  --output local/chunks/2022-sample.json
python -m app.images local/extractions/2022-sample local/chunks/2022-sample.json \
  --output local/chunks/2022-multimodal.json \
  --image-dir local/images/2022-sample
python -m app.database local/extractions/2022-sample local/chunks/2022-multimodal.json
```

Extraction creates `document.json`, `document.md`, `manifest.json`, and eligible
picture PNGs under `images/`. Page-range endpoints are 1-based and inclusive.
Unlike uploading a shortened PDF, this preserves the source PDF's page numbers.
The manifest records source identity, page range, parser version, and warnings.

Chunk JSON contains original and contextualized text, headings, labels, page
references, Docling metadata, and contextualized token counts. The summary field
`contextualized_chunks_over_max_tokens` identifies chunks exceeding the target;
480 is a target, not a guaranteed strict limit. Contextualized text is stored for
future embeddings. The image step copies PNGs into durable storage before the
database step inserts a new document.

OCR is disabled for the default selectable-text PDF workflow. The extraction CLI
accepts `--ocr`, but additional OCR dependencies/models may be needed and that
path is not validated here. Table extraction can misidentify headers or split
tables; inspect results against the source PDF. Full-report memory usage and
runtime have not been benchmarked.

## Repository layout

```text
app/                    # API, parsing, chunking, images, database, HTML debug view
tests/                  # Automated tests
Dockerfile              # Shared dependency, test, and runtime stages
compose.yaml            # Local application, test service, and persistent volumes
.dockerignore           # Allowlist of image build inputs
.github/workflows/      # Container build, tests, lint, and startup checks
.env.example            # Optional local configuration
requirements*.txt       # Application and development dependencies
pyproject.toml          # pytest and Ruff configuration
data/raw/               # User-supplied PDFs (ignored)
local/                  # Local outputs, databases, models, experiments (ignored)
```

Commit the source, tests, Docker files, dependency files, and README to the public
repository. PDFs, `.env`, virtual environments, databases, model caches, and local
experiments are excluded from Git. Public hosting is separate from running the
application; publishing the repository does not deploy the service.
