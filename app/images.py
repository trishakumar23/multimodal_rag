"""Export Docling picture regions and attach them to nearby text chunks."""

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

from docling_core.types.doc import DoclingDocument

DEFAULT_MIN_AREA_RATIO = 0.001  # 0.1% of the page; only obvious tiny artifacts


def picture_filename(index: int) -> str:
    return f"picture-{index:05d}.png"


def relative_area(bbox: Any, page_size: Any) -> float:
    return abs((bbox.r - bbox.l) * (bbox.t - bbox.b)) / (page_size.width * page_size.height)


def save_picture_regions(
    document: DoclingDocument, output: Path, *, min_area_ratio: float = DEFAULT_MIN_AREA_RATIO
) -> int:
    """Save retained picture crops using Docling's rendered picture data."""
    if not 0 <= min_area_ratio < 1:
        raise ValueError("min_area_ratio must be between 0 and 1")
    saved = 0
    for index, picture in enumerate(document.pictures):
        if not picture.prov:
            raise ValueError(f"Picture {index} has no page provenance")
        prov = picture.prov[0]
        if relative_area(prov.bbox, document.pages[prov.page_no].size) < min_area_ratio:
            picture.image = None
            continue
        image = picture.get_image(document)
        if image is None:
            raise RuntimeError(f"Docling did not render picture {index} on page {prov.page_no}")
        directory = output / "images"
        directory.mkdir(exist_ok=True)
        image.save(directory / picture_filename(index), format="PNG")
        # Keep structured JSON small; the PNG is stored alongside it.
        picture.image = None
        saved += 1
    return saved


def _box(bbox: dict[str, Any], page_height: float) -> tuple[float, float, float, float]:
    left, top, right, bottom = bbox["l"], bbox["t"], bbox["r"], bbox["b"]
    if bbox.get("coord_origin", "TOPLEFT").upper() == "BOTTOMLEFT":
        top, bottom = page_height - top, page_height - bottom
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def _distance(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


def _captions(document: DoclingDocument, picture: Any) -> tuple[set[str], str | None]:
    references = set()
    texts = []
    for reference in picture.captions:
        try:
            item = reference.resolve(document)
        except (AttributeError, IndexError, KeyError, RuntimeError, ValueError):
            continue
        if getattr(item, "text", "").strip():
            references.add(reference.cref)
            texts.append(item.text.strip())
    return references, " ".join(texts) or None


def _chunk_for_picture(
    document: DoclingDocument,
    picture: Any,
    chunks: list[dict[str, Any]],
    caption_refs: set[str],
    caption_text: str | None,
) -> tuple[int | None, str]:
    page = picture.prov[0].page_no
    for index, chunk in enumerate(chunks):
        refs = {item.get("self_ref") for item in chunk.get("metadata", {}).get("doc_items", [])}
        if caption_refs & refs:
            return index, "caption_ref"
    if caption_text:
        for index, chunk in enumerate(chunks):
            if caption_text.casefold() in chunk.get("original_text", "").casefold():
                return index, "caption_text"

    same_page = [
        (index, chunk)
        for index, chunk in enumerate(chunks)
        if page in chunk.get("page_numbers", [])
    ]
    picture_box = _box(
        picture.prov[0].bbox.model_dump(mode="json"), document.pages[page].size.height
    )
    distances = []
    for index, chunk in same_page:
        for item in chunk.get("metadata", {}).get("doc_items", []):
            for prov in item.get("prov", []):
                if prov.get("page_no") == page:
                    element_box = _box(prov["bbox"], document.pages[page].size.height)
                    distances.append((_distance(picture_box, element_box), index))
    if distances:
        return min(distances)[1], "spatial"
    if same_page:
        return same_page[0][0], "same_page_fallback"
    return None, "image_only_fallback"


def attach_pictures(
    document: DoclingDocument,
    chunk_output: dict[str, Any],
    image_dir: Path,
    *,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
) -> int:
    """Attach each retained picture to one chunk; mutate and return the chunk output."""
    if not 0 <= min_area_ratio < 1:
        raise ValueError("min_area_ratio must be between 0 and 1")
    chunks = chunk_output["chunks"]
    for chunk in chunks:
        chunk.setdefault("images", [])
    count = 0
    for index, picture in enumerate(document.pictures):
        if not picture.prov:
            raise ValueError(f"Picture {index} has no page provenance")
        prov = picture.prov[0]
        if relative_area(prov.bbox, document.pages[prov.page_no].size) < min_area_ratio:
            continue
        path = image_dir / picture_filename(index)
        if not path.is_file():
            raise FileNotFoundError(f"Missing rendered picture: {path}")
        caption_refs, caption = _captions(document, picture)
        target, method = _chunk_for_picture(document, picture, chunks, caption_refs, caption)
        if target is None:
            target = len(chunks)
            chunks.append(
                {
                    "chunk_id": f"image-only-{index:05d}",
                    "original_text": caption or "",
                    "contextualized_text": caption or f"Image on page {prov.page_no}",
                    "page_numbers": [prov.page_no],
                    "headings": [],
                    "labels": ["picture"],
                    "metadata": {"doc_items": [picture.model_dump(mode="json", exclude_none=True)]},
                    "images": [],
                }
            )
        chunks[target]["images"].append(
            {
                "image_id": f"{chunk_output['source']['source_sha256'][:12]}-{index:05d}",
                "image_index": index,
                "source_ref": picture.self_ref,
                "page_number": prov.page_no,
                "bbox": prov.bbox.model_dump(mode="json"),
                "caption": caption,
                "path": str(path),
                "association_method": method,
            }
        )
        count += 1
    chunk_output["chunk_count"] = len(chunks)
    chunk_output["image_count"] = count
    return count


def associate_images(
    extraction_dir: Path,
    chunk_json: Path,
    output_json: Path,
    image_dir: Path,
    *,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
) -> int:
    """Copy exported images to durable storage and write chunks with image lists."""
    if output_json.exists() or image_dir.exists():
        raise ValueError("Choose new image and JSON output paths")
    document = DoclingDocument.load_from_json(extraction_dir / "document.json")
    chunk_output = json.loads(chunk_json.read_text(encoding="utf-8"))
    source_dir = extraction_dir / "images"
    if source_dir.is_dir():
        shutil.copytree(source_dir, image_dir)
    count = attach_pictures(document, chunk_output, image_dir, min_area_ratio=min_area_ratio)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(chunk_output, indent=2, ensure_ascii=False), encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extraction_dir", type=Path)
    parser.add_argument("chunk_json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image-dir", required=True, type=Path)
    parser.add_argument("--min-area-ratio", type=float, default=DEFAULT_MIN_AREA_RATIO)
    args = parser.parse_args()
    count = associate_images(
        args.extraction_dir,
        args.chunk_json,
        args.output,
        args.image_dir,
        min_area_ratio=args.min_area_ratio,
    )
    print(f"Attached {count} images; saved chunks to {args.output}")


if __name__ == "__main__":
    main()
