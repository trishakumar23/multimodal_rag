"""FastAPI application for health checks and full-document ingestion."""

import json
import logging
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.chunking import chunk_extraction
from app.config import Settings
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
    persist_document,
)
from app.debug import (
    get_document_for_debug,
    get_image_path,
    render_document_html,
)
from app.extraction import extract_pdf
from app.images import associate_images

logger = logging.getLogger(__name__)


# Response models define the JSON fields returned by the routes below.
class HealthResponse(BaseModel):
    status: str


class DocumentResponse(BaseModel):
    document_id: int
    filename: str
    year: int
    total_pages: int
    chunk_count: int
    debug_url: str


class ChunkDetail(BaseModel):
    id: int
    document_id: int
    chunk_index: int
    textual_content: str
    page_start: int | None
    page_end: int | None


class DocumentDetail(BaseModel):
    id: int
    name: str
    year: int
    number_of_pages: int
    debug_url: str
    chunks: list[ChunkDetail]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API and its routes; callers can supply settings for isolated tests or storage."""
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Prepare database access at startup and release the engine at shutdown."""
        engine = create_database_engine(settings)
        initialize_database(engine)
        application.state.sessions = create_session_factory(engine)
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Ingestion service for multimodal annual reports.",
        lifespan=lifespan,
    )

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Report process liveness; this does not check parsing or database readiness."""
        return HealthResponse(status="ok")

    @application.post("/documents", response_model=DocumentResponse, tags=["documents"])
    def ingest_document(
        request: Request,
        file: Annotated[UploadFile, File()],
        year: Annotated[int, Form()],
    ) -> DocumentResponse:
        """Validate an upload, extract and chunk it, attach pictures, and save the document."""
        filename = Path((file.filename or "").replace("\\", "/")).name
        if not filename or Path(filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="Upload a PDF file with a .pdf filename")
        image_dir = settings.image_dir / uuid.uuid4().hex
        committed = False
        try:
            if file.file.read(5) != b"%PDF-":
                raise HTTPException(status_code=400, detail="The uploaded file is not a PDF")
            file.file.seek(0)
            # Intermediate PDF/JSON files are removed automatically after this request.
            with tempfile.TemporaryDirectory(prefix="rag-ingest-") as work:
                workdir = Path(work)
                source = workdir / filename
                with source.open("wb") as destination:
                    shutil.copyfileobj(file.file, destination)
                # Pipeline: PDF → structured extraction → text chunks → associated images.
                extraction_dir = extract_pdf(
                    source,
                    year,
                    workdir / "extraction",
                    min_image_area_ratio=settings.min_image_area_ratio,
                )
                chunks_path = workdir / "chunks.json"
                chunk_extraction(extraction_dir, chunks_path)
                manifest_path = extraction_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("saved_picture_count", 0):
                    # Move crops into durable storage before the temporary folder is deleted.
                    multimodal_path = workdir / "multimodal.json"
                    associate_images(
                        extraction_dir,
                        chunks_path,
                        multimodal_path,
                        image_dir,
                        min_area_ratio=settings.min_image_area_ratio,
                    )
                    chunks_path = multimodal_path
                chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
                document = persist_document(application.state.sessions, manifest, chunks)
                # Keep the image files only once their database records have been committed.
                committed = True
                return DocumentResponse(
                    document_id=document.id,
                    filename=document.filename,
                    year=document.year,
                    total_pages=document.total_pages,
                    chunk_count=len(document.chunks),
                    debug_url=str(request.url_for("debug_document", document_id=document.id)),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to ingest %s", filename)
            raise HTTPException(status_code=500, detail="Document ingestion failed") from exc
        finally:
            file.file.close()
            if not committed and image_dir.exists():
                shutil.rmtree(image_dir)

    @application.get(
        "/documents/{document_id}",
        response_model=DocumentDetail,
        tags=["documents"],
        summary="Get document data (JSON)",
        description="Returns saved document and chunk data. Open debug_url for the HTML page.",
    )
    def get_document(document_id: int, request: Request) -> DocumentDetail:
        """Return a persisted document and its ordered chunks with explicit page ranges."""
        document = get_document_for_debug(application.state.sessions, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")

        return DocumentDetail(
            id=document.id,
            name=document.filename,
            year=document.year,
            number_of_pages=document.total_pages,
            debug_url=str(request.url_for("debug_document", document_id=document.id)),
            chunks=[
                ChunkDetail(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    textual_content=chunk.original_text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
                for chunk in sorted(document.chunks, key=lambda item: item.chunk_index)
            ],
        )

    @application.get(
        "/documents/{document_id}/debug",
        response_class=HTMLResponse,
        tags=["documents"],
        summary="View document and images (HTML)",
        description=(
            "Enter the document ID and execute, then open the Request URL in your browser "
            "to view the HTML page. The URL must end in /debug."
        ),
    )
    def debug_document(document_id: int) -> HTMLResponse:
        """Render persisted chunks and images for human inspection."""
        document = get_document_for_debug(
            application.state.sessions,
            document_id,
        )

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found",
            )

        return HTMLResponse(content=render_document_html(document))

    @application.get(
        "/debug/images/{image_id}",
        response_class=FileResponse,
        tags=["debug"],
    )
    def debug_image(image_id: int) -> FileResponse:
        """Serve one persisted image used by the debug HTML."""
        image_path = get_image_path(
            application.state.sessions,
            image_id,
        )

        if image_path is None or not image_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Image not found",
            )

        return FileResponse(image_path)

    return application


def main() -> None:
    """Start the HTTP server with configured host/port via python -m app.main."""
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
