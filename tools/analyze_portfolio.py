#!/usr/bin/env python3
"""Local-only portfolio analysis with Ollama and deterministic image features.

The script never edits, moves, or deletes originals. It creates thumbnails and
analysis artifacts under photo_portfolio/analysis/ and resumes from labels.jsonl.
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import csv
import hashlib
import html
import io
import json
import math
import os
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image, ImageOps
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize
from scipy.sparse import csr_matrix, hstack


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
PROMPT_VERSION = "portfolio-v1"
PRINT_LOCK = threading.Lock()

PROMPT = """You are a photography editor analyzing one image for portfolio sequencing.
Return ONE strict JSON object and no prose. Describe only visible evidence. Do not identify or
guess the identity, ethnicity, health, beliefs, or private traits of any person.

Required schema:
{
  "content_summary": "one factual sentence",
  "subjects": ["2-6 concise lowercase visual nouns"],
  "setting": "urban|nature|interior|domestic|coastal|rural|studio|transit|abstract|other",
  "motif": "people|landscape|architecture|object|water|light-shadow|flora|gesture|texture|mixed|other",
  "composition": {
    "shot_scale": "extreme-wide|wide|medium|close|detail",
    "subject_position": "center|left|right|top|bottom|distributed|none",
    "balance": "symmetrical|asymmetrical|radial|all-over",
    "dominant_lines": "horizontal|vertical|diagonal|curved|mixed|none",
    "depth": "flat|layered|deep",
    "negative_space": "low|medium|high",
    "visual_density": "sparse|moderate|dense",
    "energy": "still|flowing|dynamic"
  },
  "color_scheme": {
    "palette_words": ["2-5 concise color names"],
    "temperature": "warm|cool|neutral|mixed|monochrome",
    "saturation": "muted|moderate|vivid|monochrome",
    "contrast": "low|medium|high",
    "luminosity": "dark|mid|bright"
  },
  "mood": ["1-3 concise atmosphere words"],
  "sequence_roles": ["choose 1-3: opener|anchor|bridge|detail|pause|closer"],
  "quality_flags": ["visible technical or presentation concerns only; empty if none"],
  "portfolio_strength": 1,
  "editor_note": "one short reason it may or may not belong in a portfolio"
}

portfolio_strength must be an integer from 1 (weak) to 5 (exceptional). A quiet or ambiguous
photograph is not automatically weak. Judge visual intention, coherence, distinctiveness, and
technical presentation. Use the exact enum values above."""


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=here / "photos" / "originals")
    p.add_argument("--output", type=Path, default=here / "analysis")
    p.add_argument("--model", default="gemma3:4b")
    p.add_argument("--ollama", default="http://127.0.0.1:11434")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--limit", type=int)
    p.add_argument("--group-seed", type=int, default=20260911)
    p.add_argument("--randomness", type=float, default=0.10)
    return p.parse_args()


def discover(source: Path) -> list[Path]:
    paths = [
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and not p.name.startswith(".")
    ]
    return sorted(paths, key=lambda p: (p.relative_to(source).parts[0].lower(), str(p).lower()))


def file_fingerprint(path: Path, source: Path) -> str:
    stat = path.stat()
    raw = f"{path.relative_to(source)}\0{stat.st_size}\0{stat.st_mtime_ns}\0{PROMPT_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("fingerprint"):
                out[row["fingerprint"]] = row
        except json.JSONDecodeError:
            continue
    return out


def save_jpeg(image: Image.Image, path: Path, max_px: int, quality: int) -> None:
    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    copy.save(path, "JPEG", quality=quality, optimize=True)


def dhash(gray: np.ndarray) -> str:
    im = Image.fromarray(gray.astype(np.uint8)).resize((9, 8), Image.Resampling.LANCZOS)
    arr = np.asarray(im)
    bits = arr[:, 1:] > arr[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def image_features(image: Image.Image) -> dict[str, Any]:
    sample = image.copy()
    sample.thumbnail((320, 320), Image.Resampling.LANCZOS)
    arr = np.asarray(sample, dtype=np.float32) / 255.0
    gray = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114)
    gx = np.abs(np.diff(gray, axis=1)).mean() if gray.shape[1] > 1 else 0.0
    gy = np.abs(np.diff(gray, axis=0)).mean() if gray.shape[0] > 1 else 0.0

    hsv = np.array([colorsys.rgb_to_hsv(*px) for px in arr.reshape(-1, 3)], dtype=np.float32)
    quant = sample.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    total = sum(count for count, _ in counts) or 1
    colors, weights = [], []
    for count, idx in counts[:5]:
        rgb = palette[idx * 3:idx * 3 + 3]
        colors.append("#" + "".join(f"{v:02x}" for v in rgb))
        weights.append(round(count / total, 4))

    w, h = image.size
    return {
        "width": w,
        "height": h,
        "orientation": "landscape" if w > h else "portrait" if h > w else "square",
        "aspect_ratio": round(w / h, 4),
        "brightness": round(float(gray.mean()), 4),
        "saturation": round(float(hsv[..., 1].mean()), 4),
        "contrast": round(float(gray.std()), 4),
        "warmth": round(float((arr[..., 0] - arr[..., 2]).mean()), 4),
        "edge_strength": round(float(gx + gy), 5),
        "clipped_black": round(float((gray < 0.015).mean()), 4),
        "clipped_white": round(float((gray > 0.985).mean()), 4),
        "dominant_colors": colors,
        "color_weights": weights,
        "dhash": dhash((gray * 255).astype(np.uint8)),
    }


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response was not an object")
    return value


def list_strings(value: Any, maximum: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip().lower() for v in value if str(v).strip()][:maximum]


def enum_value(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def normalize_label(raw: dict[str, Any]) -> dict[str, Any]:
    comp = raw.get("composition") if isinstance(raw.get("composition"), dict) else {}
    color = raw.get("color_scheme") if isinstance(raw.get("color_scheme"), dict) else {}
    try:
        strength = max(1, min(5, int(raw.get("portfolio_strength", 3))))
    except (TypeError, ValueError):
        strength = 3
    return {
        "content_summary": str(raw.get("content_summary") or "").strip(),
        "subjects": list_strings(raw.get("subjects"), 6),
        "setting": enum_value(raw.get("setting"), {"urban", "nature", "interior", "domestic", "coastal", "rural", "studio", "transit", "abstract", "other"}, "other"),
        "motif": enum_value(raw.get("motif"), {"people", "landscape", "architecture", "object", "water", "light-shadow", "flora", "gesture", "texture", "mixed", "other"}, "other"),
        "composition": {
            "shot_scale": enum_value(comp.get("shot_scale"), {"extreme-wide", "wide", "medium", "close", "detail"}, "medium"),
            "subject_position": enum_value(comp.get("subject_position"), {"center", "left", "right", "top", "bottom", "distributed", "none"}, "distributed"),
            "balance": enum_value(comp.get("balance"), {"symmetrical", "asymmetrical", "radial", "all-over"}, "asymmetrical"),
            "dominant_lines": enum_value(comp.get("dominant_lines"), {"horizontal", "vertical", "diagonal", "curved", "mixed", "none"}, "mixed"),
            "depth": enum_value(comp.get("depth"), {"flat", "layered", "deep"}, "layered"),
            "negative_space": enum_value(comp.get("negative_space"), {"low", "medium", "high"}, "medium"),
            "visual_density": enum_value(comp.get("visual_density"), {"sparse", "moderate", "dense"}, "moderate"),
            "energy": enum_value(comp.get("energy"), {"still", "flowing", "dynamic"}, "still"),
        },
        "color_scheme": {
            "palette_words": list_strings(color.get("palette_words"), 5),
            "temperature": enum_value(color.get("temperature"), {"warm", "cool", "neutral", "mixed", "monochrome"}, "neutral"),
            "saturation": enum_value(color.get("saturation"), {"muted", "moderate", "vivid", "monochrome"}, "moderate"),
            "contrast": enum_value(color.get("contrast"), {"low", "medium", "high"}, "medium"),
            "luminosity": enum_value(color.get("luminosity"), {"dark", "mid", "bright"}, "mid"),
        },
        "mood": list_strings(raw.get("mood"), 3),
        "sequence_roles": [x for x in list_strings(raw.get("sequence_roles"), 3) if x in {"opener", "anchor", "bridge", "detail", "pause", "closer"}],
        "quality_flags": list_strings(raw.get("quality_flags"), 6),
        "portfolio_strength": strength,
        "editor_note": str(raw.get("editor_note") or "").strip(),
    }


def analyze_one(path: Path, source: Path, output: Path, model: str, ollama: str) -> dict[str, Any]:
    rel = path.relative_to(source)
    fingerprint = file_fingerprint(path, source)
    thumb_name = f"{fingerprint}.jpg"
    thumb_rel = Path("thumbs") / rel.parts[0] / thumb_name
    thumb_path = output / thumb_rel

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    features = image_features(image)
    save_jpeg(image, thumb_path, 640, 84)

    vlm = image.copy()
    vlm.thumbnail((896, 896), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    vlm.save(buf, "JPEG", quality=84, optimize=True)
    started = time.time()
    response = requests.post(
        f"{ollama.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": PROMPT,
            "images": [base64.b64encode(buf.getvalue()).decode("ascii")],
            "format": "json",
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_predict": 420},
        },
        timeout=300,
    )
    response.raise_for_status()
    raw_text = response.json().get("response", "")
    label = normalize_label(parse_json(raw_text))
    return {
        "fingerprint": fingerprint,
        "prompt_version": PROMPT_VERSION,
        "relative_path": str(rel),
        "chapter": rel.parts[0],
        "filename": path.name,
        "thumbnail": str(thumb_rel),
        "features": features,
        "label": label,
        "model": model,
        "seconds": round(time.time() - started, 2),
        "analyzed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def numeric_percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.5
    return sum(v <= value for v in values) / len(values)


def apply_selection(rows: list[dict[str, Any]]) -> None:
    by_chapter: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_chapter.setdefault(row["chapter"], []).append(row)

    for chapter_rows in by_chapter.values():
        edges = [r["features"]["edge_strength"] for r in chapter_rows]
        for row in chapter_rows:
            label, feat = row["label"], row["features"]
            flags: list[str] = []
            if label["portfolio_strength"] <= 2:
                flags.append("low_model_strength")
            if numeric_percentile(edges, feat["edge_strength"]) <= 0.04:
                flags.append("relative_softness_outlier")
            if feat["clipped_black"] > 0.45:
                flags.append("heavy_shadow_clipping")
            if feat["clipped_white"] > 0.30:
                flags.append("heavy_highlight_clipping")
            row["selection"] = {"status": "keep", "flags": flags}

        ranked = sorted(
            chapter_rows,
            key=lambda r: (-r["label"]["portfolio_strength"], r["relative_path"]),
        )
        accepted: list[dict[str, Any]] = []
        for row in ranked:
            duplicate = next(
                (other for other in accepted if hamming_hex(row["features"]["dhash"], other["features"]["dhash"]) <= 3),
                None,
            )
            if duplicate:
                row["selection"]["status"] = "filter_candidate"
                row["selection"]["flags"].append(f"near_duplicate_of:{duplicate['relative_path']}")
            elif row["label"]["portfolio_strength"] <= 2:
                row["selection"]["status"] = "review"
                accepted.append(row)
            else:
                accepted.append(row)


def vectorize(rows: list[dict[str, Any]]) -> np.ndarray:
    orientations, composition_cats, composition_nums = [], [], []
    for row in rows:
        label, feat = row["label"], row["features"]
        comp = label["composition"]
        orientations.append({"orientation": feat["orientation"]})
        composition_cats.append({f"composition_{k}": v for k, v in comp.items()})
        composition_nums.append([np.log(max(feat["aspect_ratio"], 0.1)), feat["edge_strength"]])

    orientation_categories = DictVectorizer().fit_transform(orientations)
    composition_categories = DictVectorizer().fit_transform(composition_cats)
    composition_numeric = np.asarray(composition_nums, dtype=np.float32)
    composition_std = composition_numeric.std(axis=0)
    composition_numeric = (composition_numeric - composition_numeric.mean(axis=0)) / np.where(composition_std < 1e-6, 1, composition_std)

    # Group membership intentionally uses composition only. Color remains in
    # the catalog and visible palette strip, but does not influence grouping.
    matrix = hstack([
        orientation_categories * 1.8,
        composition_categories,
        csr_matrix(composition_numeric[:, 0:1]) * 1.15,
        csr_matrix(composition_numeric[:, 1:2]) * 0.45,
    ])
    return normalize(matrix).toarray()


def make_microgroups(
    rows: list[dict[str, Any]], group_seed: int, randomness: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    randomness = max(0.0, min(1.0, randomness))
    eligible = [r for r in rows if r["selection"]["status"] == "keep"]
    excluded = [r for r in rows if r["selection"]["status"] != "keep"]
    groups: list[dict[str, Any]] = []
    by_chapter: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        by_chapter.setdefault(row["chapter"], []).append(row)

    for chapter, chapter_rows in sorted(by_chapter.items()):
        if len(chapter_rows) < 5:
            excluded.extend(chapter_rows)
            continue
        vectors = vectorize(chapter_rows)
        sim = cosine_similarity(vectors)
        rng = random.Random(group_seed + sum((i + 1) * ord(c) for i, c in enumerate(chapter)))

        # Hold back composition outliers until the chapter total is divisible by
        # five. Randomness participates in tie-breaking so a reshuffle can
        # produce a meaningfully different but still composition-led edit.
        remaining = set(range(len(chapter_rows)))
        holdout_count = len(remaining) % 5
        if holdout_count:
            integration = []
            for idx in remaining:
                neighbors = sorted((float(sim[idx, j]) for j in remaining if j != idx), reverse=True)
                composition_fit = statistics.fmean(neighbors[:4]) if neighbors else 0.0
                mixed_fit = (1.0 - randomness) * composition_fit + randomness * rng.random()
                integration.append((mixed_fit, idx))
            for _, idx in sorted(integration)[:holdout_count]:
                remaining.remove(idx)
                chapter_rows[idx]["selection"]["status"] = "review"
                chapter_rows[idx]["selection"]["flags"].append("held_out_for_five_image_groups")
                excluded.append(chapter_rows[idx])

        member_groups: list[list[int]] = []
        while remaining:
            # Start with the hardest-to-place photograph so it cannot become a
            # loose leftover, then choose each next member against the whole
            # emerging group rather than only the seed.
            seed_scores = []
            for idx in sorted(remaining):
                neighbors = sorted((float(sim[idx, other]) for other in remaining if other != idx), reverse=True)
                fit = statistics.fmean(neighbors[:min(4, len(neighbors))]) if neighbors else 1.0
                seed_scores.append(((1.0 - randomness) * fit + randomness * rng.random(), idx))
            _, seed = min(seed_scores)
            remaining.remove(seed)
            members = [seed]
            while len(members) < 5:
                candidates = []
                for idx in sorted(remaining):
                    group_fit = statistics.fmean(float(sim[idx, member]) for member in members)
                    score = (1.0 - randomness) * group_fit + randomness * rng.random()
                    candidates.append((score, idx))
                _, selected = max(candidates)
                remaining.remove(selected)
                members.append(selected)
            member_groups.append(members)

        def cohesion(member_ids: list[int]) -> float:
            return statistics.fmean(
                float(sim[a, b])
                for pos, a in enumerate(member_ids)
                for b in member_ids[pos + 1:]
            )

        # A bounded local refinement swaps photographs between groups only
        # when the combined composition cohesion improves.
        for _ in range(min(100, len(member_groups) * 4)):
            best_swap: tuple[float, int, int, int, int] | None = None
            for left_index in range(len(member_groups)):
                for right_index in range(left_index + 1, len(member_groups)):
                    left, right = member_groups[left_index], member_groups[right_index]
                    before = cohesion(left) + cohesion(right)
                    for left_pos in range(5):
                        for right_pos in range(5):
                            candidate_left, candidate_right = left.copy(), right.copy()
                            candidate_left[left_pos], candidate_right[right_pos] = candidate_right[right_pos], candidate_left[left_pos]
                            gain = cohesion(candidate_left) + cohesion(candidate_right) - before
                            if gain > 1e-6 and (best_swap is None or gain > best_swap[0]):
                                best_swap = (gain, left_index, right_index, left_pos, right_pos)
            if best_swap is None:
                break
            _, left_index, right_index, left_pos, right_pos = best_swap
            member_groups[left_index][left_pos], member_groups[right_index][right_pos] = (
                member_groups[right_index][right_pos], member_groups[left_index][left_pos]
            )

        chapter_groups: list[dict[str, Any]] = []
        for members in member_groups:
            group_cohesion = cohesion(members)
            rng.shuffle(members)
            member_rows = [chapter_rows[i] for i in members]
            common_motifs = sorted({r["label"]["motif"] for r in member_rows})
            common_temps = sorted({r["label"]["color_scheme"]["temperature"] for r in member_rows})
            chapter_groups.append({
                "chapter": chapter,
                "size": 5,
                "layout_hint": "five-frame composition",
                "cohesion": round(group_cohesion, 4),
                "grouping_basis": "composition_only",
                "randomness": randomness,
                "seed": group_seed,
                "motifs": common_motifs,
                "temperatures": common_temps,
                "members": [r["relative_path"] for r in member_rows],
            })
        rng.shuffle(chapter_groups)
        for group_no, group in enumerate(chapter_groups, 1):
            group["id"] = f"{chapter}-{group_no:02d}"
            groups.append(group)
    return groups, excluded


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = ["chapter", "relative_path", "orientation", "content_summary", "subjects", "motif", "setting", "composition", "palette", "temperature", "mood", "strength", "selection", "flags", "editor_note"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            label = row["label"]
            writer.writerow({
                "chapter": row["chapter"], "relative_path": row["relative_path"],
                "orientation": row["features"]["orientation"], "content_summary": label["content_summary"],
                "subjects": "; ".join(label["subjects"]), "motif": label["motif"], "setting": label["setting"],
                "composition": json.dumps(label["composition"], ensure_ascii=False),
                "palette": "; ".join(label["color_scheme"]["palette_words"]),
                "temperature": label["color_scheme"]["temperature"], "mood": "; ".join(label["mood"]),
                "strength": label["portfolio_strength"], "selection": row["selection"]["status"],
                "flags": "; ".join(row["selection"]["flags"] + label["quality_flags"]),
                "editor_note": label["editor_note"],
            })


def render_html(rows: list[dict[str, Any]], groups: list[dict[str, Any]], output: Path) -> None:
    by_path = {r["relative_path"]: r for r in rows}
    sections = []
    for chapter in sorted({g["chapter"] for g in groups}):
        cards = []
        for group in [g for g in groups if g["chapter"] == chapter]:
            figures = []
            for member in group["members"]:
                row = by_path[member]
                label, feat = row["label"], row["features"]
                swatches = "".join(f'<i style="background:{c}"></i>' for c in feat["dominant_colors"][:5])
                figures.append(f'''<figure>
                  <img src="{html.escape(row['thumbnail'])}" alt="{html.escape(label['content_summary'])}" loading="lazy">
                  <figcaption><b>{html.escape(row['filename'])}</b><span>{html.escape(label['content_summary'])}</span>
                  <small>{html.escape(label['motif'])} · {html.escape(feat['orientation'])} · strength {label['portfolio_strength']}/5</small>
                  <span class="swatches">{swatches}</span></figcaption></figure>''')
            cards.append(f'''<article class="microgroup"><header><div><strong>{html.escape(group['id'])}</strong>
              <span>{html.escape(group['layout_hint'])}</span></div><small>cohesion {group['cohesion']:.2f}</small></header>
              <div class="frames size-{group['size']}">{''.join(figures)}</div></article>''')
        sections.append(f'<section><h2>{html.escape(chapter)}</h2>{"".join(cards)}</section>')

    review_rows = [r for r in rows if r["selection"]["status"] != "keep"]
    review = []
    for row in review_rows:
        label = row["label"]
        flags = row["selection"]["flags"] + label["quality_flags"]
        review.append(f'''<figure class="review-item"><img src="{html.escape(row['thumbnail'])}" loading="lazy">
          <figcaption><b>{html.escape(row['relative_path'])}</b><span>{html.escape(label['content_summary'])}</span>
          <small>{html.escape(' · '.join(flags) or 'manual review')}</small></figcaption></figure>''')

    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Portfolio grouping review</title><style>
    :root{{--paper:#f3f1eb;--ink:#161616;--muted:#68655f;--line:#d7d3ca}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1440px;margin:auto;padding:48px 28px}}h1{{font:500 clamp(36px,6vw,72px)/.95 Georgia,serif;margin:0 0 12px}}h2{{font:500 32px Georgia,serif;text-transform:capitalize;margin:64px 0 20px;border-bottom:1px solid var(--line);padding-bottom:8px}}.intro{{color:var(--muted);max-width:760px}}.microgroup{{margin:0 0 52px}}.microgroup>header{{display:flex;justify-content:space-between;gap:20px;margin-bottom:12px}}.microgroup header div{{display:flex;gap:12px}}.microgroup header span,.microgroup header small,figcaption small{{color:var(--muted)}}.frames{{display:grid;gap:10px;align-items:stretch}}.size-2{{grid-template-columns:repeat(2,1fr)}}.size-3{{grid-template-columns:repeat(3,1fr)}}.size-5{{grid-template-columns:2fr repeat(2,1fr);grid-template-rows:repeat(2,1fr)}}.size-5 figure:first-child{{grid-row:span 2}}figure{{margin:0;background:#fff;min-width:0}}figure img{{width:100%;height:clamp(240px,35vw,560px);display:block;object-fit:cover;background:#ddd}}.size-5 figure:not(:first-child) img{{height:280px}}figcaption{{padding:10px 12px;display:grid;gap:4px}}figcaption b{{font:11px ui-monospace,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}figcaption span{{font-size:13px}}.swatches{{display:flex!important;gap:3px;margin-top:3px}}.swatches i{{display:block;width:26px;height:5px;border-radius:4px}}.review-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}.review-item img{{height:220px}}@media(max-width:700px){{main{{padding:28px 14px}}.frames,.size-2,.size-3,.size-5{{display:grid;grid-template-columns:1fr;grid-template-rows:auto}}.size-5 figure:first-child{{grid-row:auto}}figure img,.size-5 figure:not(:first-child) img{{height:auto;max-height:75vh}}}}
    </style></head><body><main><h1>Portfolio grouping review</h1><p class="intro">Machine-assisted editorial suggestions, not final selections. Groups preserve the three original chapters; the current permanent arrangement may be restored from an earlier approved release. The analysis tool does not alter originals.</p>
    {''.join(sections)}<section><h2>Review / filter candidates</h2><div class="review-grid">{''.join(review) or '<p>None flagged.</p>'}</div></section></main></body></html>'''
    (output / "review.html").write_text(document, encoding="utf-8")


def write_summary(rows: list[dict[str, Any]], groups: list[dict[str, Any]], output: Path, model: str, group_seed: int, randomness: float) -> None:
    restored = bool(groups) and groups[0].get("grouping_basis") == "restored_loose_composition"
    grouping = "restored looser composition release" if restored else "composition only"
    lines = ["# Portfolio analysis summary", "", f"Model: `{model}`  ", f"Prompt: `{PROMPT_VERSION}`  ", f"Grouping: **{grouping}**  ", f"Randomness: **{randomness:.0%}**, seed `{group_seed}`  ", f"Images analyzed: **{len(rows)}**  ", f"Suggested five-image groups: **{len(groups)}**", "", "## Chapters", ""]
    for chapter in sorted({r["chapter"] for r in rows}):
        chapter_rows = [r for r in rows if r["chapter"] == chapter]
        statuses = {s: sum(r["selection"]["status"] == s for r in chapter_rows) for s in ("keep", "review", "filter_candidate")}
        chapter_groups = [g for g in groups if g["chapter"] == chapter]
        lines.append(f"- **{chapter}** — {len(chapter_rows)} images; {statuses['keep']} grouped, {statuses['review']} review, {statuses['filter_candidate']} filter candidates; five-image groups: {len(chapter_groups)}")
    lines += ["", "## How to use this", "", "Open `review.html` and evaluate each machine-suggested group. Treat `filter_candidate` as a request for human review, never as an instruction to delete. Final website sequencing should be chosen by the photographer.", ""]
    (output / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    labels_path = output / "labels.jsonl"
    cache = load_cache(labels_path)
    paths = discover(source)
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"No images found under {source}")

    cached_rows, pending = [], []
    for path in paths:
        fp = file_fingerprint(path, source)
        if fp in cache:
            cached_rows.append(cache[fp])
        else:
            pending.append(path)
    print(f"Found {len(paths)} images: {len(cached_rows)} cached, {len(pending)} pending")

    new_rows: list[dict[str, Any]] = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(analyze_one, p, source, output, args.model, args.ollama): p for p in pending}
            for count, future in enumerate(as_completed(futures), 1):
                path = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    with PRINT_LOCK:
                        print(f"ERROR {path.relative_to(source)}: {exc}", flush=True)
                    continue
                new_rows.append(row)
                with labels_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                with PRINT_LOCK:
                    print(f"[{count}/{len(pending)}] {row['relative_path']} ({row['seconds']:.1f}s)", flush=True)

    rows = sorted(cached_rows + new_rows, key=lambda r: r["relative_path"].lower())
    if len(rows) != len(paths):
        print(f"WARNING: {len(paths) - len(rows)} images failed and can be retried by rerunning")
    apply_selection(rows)
    groups, _ = make_microgroups(rows, args.group_seed, args.randomness)
    (output / "catalog.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "microgroups.json").write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(rows, output / "catalog.csv")
    render_html(rows, groups, output)
    write_summary(rows, groups, output, args.model, args.group_seed, args.randomness)
    print(f"Wrote analysis to {output}")


if __name__ == "__main__":
    main()
