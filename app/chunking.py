"""Chunk a saved DoclingDocument with HybridChunker."""

import argparse
import json
import os
from pathlib import Path

DEFAULT_TOKENIZER = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MAX_TOKENS = 480


def chunk_extraction(
    extraction_dir: Path,
    output: Path,
    *,
    tokenizer_name: str = DEFAULT_TOKENIZER,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> int:
    """Write inspectable chunks from extraction JSON, preserving Docling metadata."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if output.exists():
        raise ValueError(f"Output already exists: {output}")

    document_path = extraction_dir / "document.json"
    manifest_path = extraction_dir / "manifest.json"
    if not document_path.is_file() or not manifest_path.is_file():
        raise ValueError("Expected document.json and manifest.json in the extraction directory")

    # The tokenizer is downloaded once, then cached outside Git.
    os.environ.setdefault("HF_HOME", str(Path("local/models/huggingface").resolve()))
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from docling_core.types.doc import DoclingDocument

    document = DoclingDocument.load_from_json(document_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tokenizer = HuggingFaceTokenizer.from_pretrained(tokenizer_name, max_tokens=max_tokens)
    chunker = HybridChunker(tokenizer=tokenizer)

    chunks = []
    for index, chunk in enumerate(chunker.chunk(document), start=1):
        metadata = chunk.meta.model_dump(mode="json", by_alias=True, exclude_none=True)
        doc_items = chunk.meta.doc_items
        pages = sorted({prov.page_no for item in doc_items for prov in item.prov})
        labels = list(dict.fromkeys(str(item.label.value) for item in doc_items))
        contextualized_text = chunker.contextualize(chunk)
        chunks.append(
            {
                "chunk_id": f"{manifest['source_sha256'][:12]}-{index:05d}",
                "contextualized_text": contextualized_text,
                "original_text": chunk.text,
                "contextualized_token_count": tokenizer.count_tokens(contextualized_text),
                "page_numbers": pages,
                "headings": chunk.meta.headings or [],
                "labels": labels,
                "metadata": metadata,
            }
        )

    result = {
        "source": {
            "filename": manifest["filename"],
            "year": manifest["year"],
            "source_sha256": manifest["source_sha256"],
            "extracted_pages": manifest["extracted_pages"],
        },
        "chunker": "docling-core HybridChunker",
        "tokenizer": tokenizer_name,
        "max_tokens": max_tokens,
        "chunk_count": len(chunks),
        "chunks_over_max_tokens": sum(
            chunk["contextualized_token_count"] > max_tokens for chunk in chunks
        ),
        "chunks": chunks,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extraction_dir", type=Path, help="Folder containing document.json")
    parser.add_argument("--output", type=Path, required=True, help="New JSON output file")
    parser.add_argument("--tokenizer", default=DEFAULT_TOKENIZER, help="Hugging Face tokenizer ID")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()
    try:
        count = chunk_extraction(
            args.extraction_dir,
            args.output,
            tokenizer_name=args.tokenizer,
            max_tokens=args.max_tokens,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Saved {count} chunks to {args.output}")


if __name__ == "__main__":
    main()
