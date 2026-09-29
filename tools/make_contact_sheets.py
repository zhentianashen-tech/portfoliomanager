#!/usr/bin/env python3
"""Create compact, uncropped contact sheets for human editorial review."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "photos" / "originals"
CATALOG = ROOT / "analysis" / "catalog.json"
OUTPUT = ROOT / "analysis" / "editorial_review" / "contact_sheets"
COLS = 5
ROWS = 4
CELL_W = 360
CELL_H = 300
IMAGE_H = 244
MARGIN = 20
BACKGROUND = "#f4f1ea"
FOREGROUND = "#24231f"


def fitted_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((CELL_W - 24, IMAGE_H - 20), Image.Resampling.LANCZOS)
    return image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--chapter", action="append", dest="chapters")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    catalog = args.catalog.resolve()
    output = args.output.resolve()
    rows = json.loads(catalog.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=15)
    index: list[dict[str, object]] = []

    available = sorted({row["chapter"] for row in rows})
    chapters = args.chapters or available

    for chapter in chapters:
        chapter_rows = [row for row in rows if row["chapter"] == chapter]
        slug = re.sub(r"[^a-z0-9]+", "-", chapter.lower()).strip("-") or "chapter"
        for sheet_no in range(math.ceil(len(chapter_rows) / (COLS * ROWS))):
            start = sheet_no * COLS * ROWS
            batch = chapter_rows[start:start + COLS * ROWS]
            canvas = Image.new(
                "RGB",
                (MARGIN * 2 + COLS * CELL_W, MARGIN * 2 + ROWS * CELL_H + 46),
                BACKGROUND,
            )
            draw = ImageDraw.Draw(canvas)
            draw.text((MARGIN, 12), f"{chapter} · sheet {sheet_no + 1}", fill=FOREGROUND, font=font)
            for offset, row in enumerate(batch):
                x = MARGIN + (offset % COLS) * CELL_W
                y = MARGIN + 46 + (offset // COLS) * CELL_H
                image = fitted_image(source / row["relative_path"])
                image_x = x + (CELL_W - image.width) // 2
                image_y = y + (IMAGE_H - image.height) // 2
                canvas.paste(image, (image_x, image_y))
                item_no = start + offset + 1
                label = f"{item_no:03d}  {row['filename']}"
                if len(label) > 39:
                    label = label[:36] + "…"
                draw.text((x + 8, y + IMAGE_H + 6), label, fill=FOREGROUND, font=font)
                index.append({
                    "chapter": chapter,
                    "number": item_no,
                    "sheet": sheet_no + 1,
                    "relative_path": row["relative_path"],
                })
            target = output / f"{slug}-{sheet_no + 1:02d}.jpg"
            canvas.save(target, "JPEG", quality=88, optimize=True)

    (output.parent / "contact_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Created contact sheets for {len(index)} photographs across {len(chapters)} chapters")


if __name__ == "__main__":
    main()
