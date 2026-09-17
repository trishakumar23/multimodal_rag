"""HTML debug view for inspecting persisted multimodal chunks."""

from html import escape
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload, sessionmaker

from app.database import Chunk, Document


def get_document_for_debug(sessions: sessionmaker, document_id: int) -> Document | None:
    """Load a document together with its chunks and associated images."""
    with sessions() as session:
        statement = (
            select(Document)
            .where(Document.id == document_id)
            .options(selectinload(Document.chunks).selectinload(Chunk.images))
        )
        return session.scalar(statement)


def render_document_html(document: Document) -> str:
    """Render one persisted document as a simple human-readable HTML page."""

    chunk_sections = []

    for chunk in sorted(document.chunks, key=lambda item: item.chunk_index):
        page_label = _page_label(chunk.page_start, chunk.page_end)

        headings = ", ".join(chunk.headings or []) or "None"
        labels = ", ".join(chunk.labels or []) or "None"

        images_html = []

        for image in sorted(chunk.images, key=lambda item: item.image_index):
            caption = escape(image.caption or "No caption")

            images_html.append(
                f"""
                <figure>
                    <img
                        src="/debug/images/{image.id}"
                        alt="{caption}"
                        loading="lazy"
                    >
                    <figcaption>
                        Page {image.page_number} · {caption}
                    </figcaption>
                </figure>
                """
            )

        if images_html:
            visual_content = f"""
                <div class="images">
                    {"".join(images_html)}
                </div>
            """
        else:
            visual_content = '<p class="empty">No images associated with this chunk.</p>'

        chunk_sections.append(
            f"""
            <section class="chunk">
                <div class="chunk-header">
                    <h2>Chunk {chunk.chunk_index}</h2>
                    <span>{escape(page_label)}</span>
                </div>

                <div class="metadata">
                    <strong>Headings:</strong> {escape(headings)}
                    <br>
                    <strong>Labels:</strong> {escape(labels)}
                    <br>
                    <strong>Images:</strong> {len(chunk.images)}
                </div>

                <h3>Text</h3>
                <pre>{escape(chunk.original_text)}</pre>

                <h3>Visual content</h3>
                {visual_content}
            </section>
            """
        )

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <title>Debug · {escape(document.filename)}</title>

        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 1200px;
                margin: 40px auto;
                padding: 0 20px;
                background: #f6f6f6;
                color: #222;
            }}

            header {{
                margin-bottom: 32px;
            }}

            .document-meta {{
                color: #555;
            }}

            .chunk {{
                background: white;
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 24px;
                margin-bottom: 24px;
            }}

            .chunk-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}

            .chunk-header h2 {{
                margin: 0;
            }}

            .metadata {{
                margin: 16px 0;
                padding: 12px;
                background: #f3f3f3;
                border-radius: 6px;
                line-height: 1.6;
            }}

            pre {{
                white-space: pre-wrap;
                word-wrap: break-word;
                font-family: inherit;
                line-height: 1.5;
            }}

            .images {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 16px;
            }}

            figure {{
                margin: 0;
                border: 1px solid #ddd;
                padding: 12px;
                border-radius: 6px;
            }}

            img {{
                width: 100%;
                height: auto;
                display: block;
            }}

            figcaption {{
                margin-top: 8px;
                font-size: 14px;
                color: #555;
            }}

            .empty {{
                color: #777;
                font-style: italic;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>{escape(document.filename)}</h1>

            <div class="document-meta">
                <strong>Document ID:</strong> {document.id}
                · <strong>Year:</strong> {document.year}
                · <strong>Total pages:</strong> {document.total_pages}
                · <strong>Chunks:</strong> {len(document.chunks)}
            </div>
        </header>

        {"".join(chunk_sections)}
    </body>
    </html>
    """


def _page_label(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return "Page unknown"

    if page_end is None or page_start == page_end:
        return f"Page {page_start}"

    return f"Pages {page_start}–{page_end}"


def get_image_path(sessions: sessionmaker, image_id: int) -> Path | None:
    """Return the stored path for one image."""
    from app.database import Image

    with sessions() as session:
        image = session.get(Image, image_id)

        if image is None:
            return None

        return Path(image.path)
