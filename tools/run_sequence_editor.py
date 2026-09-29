#!/usr/bin/env python3
"""Run the localhost-only sequence editor and save reviewable handoff traces."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import re
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "editor"
WORKING_CATALOG = ROOT / "analysis" / "catalog.json"
WORKING_SEQUENCE = ROOT / "analysis" / "sequence.json"
DEMO_CATALOG = ROOT / "demo" / "catalog.json"
DEMO_SEQUENCE = ROOT / "demo" / "sequence.json"
CATALOG_PATH = WORKING_CATALOG
CATALOG_ROOT = CATALOG_PATH.parent
SEQUENCE_PATH = WORKING_SEQUENCE
TRACE_DIR = ROOT / "analysis" / "sequence_edits"
LATEST_PATH = TRACE_DIR / "latest.json"
MAX_BODY = 2_000_000


def photo_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:14]


def sequence_hash() -> str:
    return hashlib.sha256(SEQUENCE_PATH.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict]:
    rows = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    photos: dict[str, dict] = {}
    for row in rows:
        identifier = photo_id(row["relative_path"])
        selection = row.get("selection") or {}
        thumbnail = None
        if row.get("thumbnail"):
            candidate = (CATALOG_ROOT / row["thumbnail"]).resolve()
            if CATALOG_ROOT.resolve() in candidate.parents and candidate.is_file():
                thumbnail = candidate
        photos[identifier] = {
            "id": identifier,
            "sourcePath": row["relative_path"],
            "filename": row["filename"],
            "caption": row.get("label", {}).get("content_summary") or row["filename"],
            "chapter": row.get("chapter", "unassigned"),
            "selection": selection.get("status", "unreviewed"),
            "width": row.get("features", {}).get("width"),
            "height": row.get("features", {}).get("height"),
            "ratio": row.get("features", {}).get("aspect_ratio"),
            "colors": row.get("features", {}).get("dominant_colors", [])[:5],
            "thumbnailPath": thumbnail,
        }
    return photos


def load_project() -> tuple[dict, dict[str, dict]]:
    sequence = json.loads(SEQUENCE_PATH.read_text(encoding="utf-8"))
    return sequence, load_catalog()


def public_state() -> dict:
    sequence, photos = load_project()
    paths = [path for movement in sequence["movements"] for path in movement["members"]]
    order = [photo_id(path) for path in paths]
    missing = [identifier for identifier in order if identifier not in photos]
    if missing:
        raise RuntimeError(f"Sequence contains {len(missing)} photographs missing from the catalog")
    public_photos = []
    for item in photos.values():
        public_photos.append({key: value for key, value in item.items() if key != "thumbnailPath"} | {"thumb": f"/thumb/{item['id']}"})
    public_photos.sort(key=lambda item: (item["chapter"], item["filename"].lower(), item["id"]))
    latest_draft, latest_version = load_latest_version()
    return {
        "version": 3,
        "title": sequence.get("title", "Portfolio mock-up"),
        "subtitle": sequence.get("subtitle", ""),
        "baseSequenceSha256": sequence_hash(),
        "roles": sequence.get("role_schema", []),
        "movements": [
            {"id": item.get("id", f"relation-{index + 1:02d}"), "title": item.get("title", ""), "note": item.get("note", "")}
            for index, item in enumerate(sequence["movements"])
        ],
        "order": order,
        "photos": public_photos,
        "latestDraft": latest_draft,
        "latestVersion": latest_version,
    }


def safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return value[:48] or "sequence-edit"


def placeholder_svg(photo: dict) -> bytes:
    filename = html.escape(photo.get("filename") or "Mock photograph")
    chapter = html.escape(photo.get("chapter") or "Unassigned")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="720" viewBox="0 0 960 720">
<rect width="960" height="720" fill="#ddd8ce"/>
<rect x="48" y="48" width="864" height="624" rx="24" fill="none" stroke="#706c63" stroke-width="3" stroke-dasharray="12 12"/>
<text x="480" y="326" text-anchor="middle" fill="#393731" font-family="system-ui, sans-serif" font-size="42">Mock photograph</text>
<text x="480" y="382" text-anchor="middle" fill="#706c63" font-family="system-ui, sans-serif" font-size="25">{filename}</text>
<text x="480" y="424" text-anchor="middle" fill="#706c63" font-family="system-ui, sans-serif" font-size="21">{chapter}</text>
</svg>'''
    return svg.encode("utf-8")


def clean_cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def canonical_drafts(sequence: dict, order: list[str], exhibition_order: list[str | None] | None = None) -> dict:
    exhibition_order = exhibition_order or order
    movements = sequence["movements"]
    book = []
    for start in range(0, len(order), 2):
        left = movements[start // 5]
        right = movements[min(start + 1, len(order) - 1) // 5]
        comment = left.get("title", "") if left.get("title") == right.get("title") else f"{left.get('title', '')} → {right.get('title', '')}"
        book.append({
            "id": f"book-spread-{len(book) + 1:03d}",
            "title": f"Spread {len(book) + 1:02d}",
            "comment": comment,
            "slots": order[start:start + 2],
        })
    exhibition = []
    for index, movement in enumerate(movements):
        exhibition.append({
            "id": movement.get("id") or f"exhibition-block-{index + 1:03d}",
            "title": movement.get("title") or f"Block {index + 1:02d}",
            "comment": movement.get("note", ""),
            "slots": exhibition_order[index * 5:index * 5 + 5],
        })
    return {"book": book, "exhibition": exhibition}


def normalize_blocks(raw: object, label: str, slot_count: int, photos: dict[str, dict]) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError(f"The {label} draft must be a list of blocks.")
    blocks: list[dict] = []
    block_ids: set[str] = set()
    photo_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{label.title()} block {index + 1} is invalid.")
        block_id = str(item.get("id") or "").strip()[:160]
        if not block_id or block_id in block_ids:
            raise ValueError(f"{label.title()} block IDs must be present and unique.")
        block_ids.add(block_id)
        slots = item.get("slots")
        if not isinstance(slots, list) or len(slots) != slot_count:
            raise ValueError(f"Each {label} block must keep exactly {slot_count} slots.")
        clean_slots: list[str | None] = []
        for identifier in slots:
            if identifier is None:
                clean_slots.append(None)
                continue
            if not isinstance(identifier, str) or identifier not in photos:
                raise ValueError(f"The {label} draft contains an unknown photo ID.")
            if identifier in photo_ids:
                raise ValueError(f"The {label} draft contains a duplicate photograph.")
            photo_ids.add(identifier)
            clean_slots.append(identifier)
        blocks.append({
            "id": block_id,
            "title": str(item.get("title") or "").strip()[:120],
            "comment": str(item.get("comment") or "").strip()[:1200],
            "slots": clean_slots,
        })
    return blocks


def request_drafts(request: dict, sequence: dict, photos: dict[str, dict]) -> dict:
    raw = request.get("drafts")
    if isinstance(raw, dict):
        book_raw = raw.get("bookBlocks") if "bookBlocks" in raw else raw.get("book")
        exhibition_raw = raw.get("exhibitionBlocks") if "exhibitionBlocks" in raw else raw.get("exhibition")
    else:
        base_order = [photo_id(path) for movement in sequence["movements"] for path in movement["members"]]
        order = request.get("order") if isinstance(request.get("order"), list) else base_order
        exhibition_order = request.get("exhibitionSlots") if isinstance(request.get("exhibitionSlots"), list) else order
        legacy = canonical_drafts(sequence, order, exhibition_order)
        book_raw, exhibition_raw = legacy["book"], legacy["exhibition"]
    return {
        "book": normalize_blocks(book_raw, "book", 2, photos),
        "exhibition": normalize_blocks(exhibition_raw, "exhibition", 5, photos),
    }


def detailed_block(block: dict, photos: dict[str, dict], roles: list[dict], view: str, rank: int) -> dict:
    slots = []
    for index, identifier in enumerate(block["slots"]):
        role = None
        if view == "exhibition":
            role = roles[index].get("label") if index < len(roles) else f"Slot {index + 1}"
        if identifier is None:
            slots.append({"slot": index + 1, "role": role, "photoId": None, "empty": True})
            continue
        photo = photos[identifier]
        slots.append({
            "slot": index + 1,
            "role": role,
            "photoId": identifier,
            "empty": False,
            "sourcePath": photo["sourcePath"],
            "filename": photo["filename"],
            "caption": photo["caption"],
            "chapter": photo["chapter"],
        })
    return {"rank": rank, "id": block["id"], "title": block["title"], "comment": block["comment"], "slots": slots}


def legacy_trace_drafts(data: dict, sequence: dict) -> dict | None:
    order = data.get("bookOrder") or data.get("editedOrder")
    exhibition_order = data.get("exhibitionOrder")
    if not isinstance(order, list):
        return None
    if not isinstance(exhibition_order, list):
        exhibition_order = order
    return canonical_drafts(sequence, order, exhibition_order)


def load_latest_version() -> tuple[dict | None, dict | None]:
    if not LATEST_PATH.is_file():
        return None, None
    try:
        data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        if data.get("baseSequenceSha256") != sequence_hash() or not isinstance(data.get("drafts"), dict):
            return None, None
        metadata = {key: data.get(key) for key in ("savedAt", "sessionName", "sourceTrace")}
        return data["drafts"], metadata
    except (OSError, json.JSONDecodeError):
        return None, None


def history_versions(limit: int = 60) -> list[dict]:
    if not TRACE_DIR.is_dir():
        return []
    versions = []
    for path in sorted(TRACE_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name == LATEST_PATH.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = data.get("summary") or {}
        if data.get("schemaVersion") == 3:
            summary_text = f"{summary.get('bookSpreads', 0)} spreads · {summary.get('exhibitionBlocks', 0)} blocks · {summary.get('actionsRecorded', 0)} actions"
        else:
            summary_text = f"Legacy trace · {summary.get('actionsRecorded', 0)} actions"
        versions.append({
            "file": path.name,
            "savedAt": data.get("savedAt", ""),
            "sessionName": data.get("sessionName", "Saved version"),
            "summary": summary_text,
        })
        if len(versions) >= limit:
            break
    return versions


def load_version(file_name: str) -> dict:
    if Path(file_name).name != file_name or not file_name.endswith(".json") or file_name == LATEST_PATH.name:
        raise ValueError("Invalid version filename.")
    path = TRACE_DIR / file_name
    if not path.is_file():
        raise ValueError("That saved version does not exist.")
    data = json.loads(path.read_text(encoding="utf-8"))
    sequence, photos = load_project()
    if data.get("baseSequenceSha256") != sequence_hash():
        raise ValueError("That version belongs to an older canonical sequence.")
    raw_drafts = data.get("drafts") if data.get("schemaVersion") == 3 else legacy_trace_drafts(data, sequence)
    if not isinstance(raw_drafts, dict):
        raise ValueError("That trace cannot be restored as an editable version.")
    drafts = {
        "book": normalize_blocks(raw_drafts.get("book"), "book", 2, photos),
        "exhibition": normalize_blocks(raw_drafts.get("exhibition"), "exhibition", 5, photos),
    }
    return {"drafts": drafts, "sessionName": data.get("sessionName", "Saved version"), "savedAt": data.get("savedAt", "")}


def block_markdown(markdown: list[str], heading: str, blocks: list[dict], view: str) -> None:
    markdown += ["", f"## {heading}", ""]
    for block in blocks:
        markdown += [f"### {block['rank']:02d}. {clean_cell(block['title']) or '(Untitled)'}", "", clean_cell(block["comment"]) or "No comment.", ""]
        if view == "exhibition":
            markdown += ["| Slot | Role | Photograph | Source | ID |", "|---:|---|---|---|---|"]
        else:
            markdown += ["| Slot | Page side | Photograph | Source | ID |", "|---:|---|---|---|---|"]
        for slot in block["slots"]:
            label = slot.get("role") or ("Left" if slot["slot"] == 1 else "Right")
            if slot["empty"]:
                markdown.append(f"| {slot['slot']} | {clean_cell(label)} | **Empty slot** | — | — |")
            else:
                markdown.append(f"| {slot['slot']} | {clean_cell(label)} | {clean_cell(slot['filename'])} | `{clean_cell(slot['sourcePath'])}` | `{slot['photoId']}` |")
        markdown.append("")


def write_trace(request: dict) -> tuple[Path, Path, dict, str, str]:
    sequence, photos = load_project()
    current_hash = sequence_hash()
    if request.get("baseSequenceSha256") != current_hash:
        raise ValueError("The canonical sequence changed after this editor session began. Reload before saving.")
    drafts = request_drafts(request, sequence, photos)
    roles = sequence.get("role_schema", [])
    book_blocks = [detailed_block(block, photos, roles, "book", index + 1) for index, block in enumerate(drafts["book"])]
    exhibition_blocks = [detailed_block(block, photos, roles, "exhibition", index + 1) for index, block in enumerate(drafts["exhibition"])]
    base_ids = {photo_id(path) for movement in sequence["movements"] for path in movement["members"]}
    book_ids = {identifier for block in drafts["book"] for identifier in block["slots"] if identifier}
    exhibition_ids = {identifier for block in drafts["exhibition"] for identifier in block["slots"] if identifier}
    book_unused_ids = sorted(base_ids - book_ids)
    exhibition_unused_ids = sorted(base_ids - exhibition_ids)

    def unused_photos(identifiers: list[str]) -> list[dict]:
        return [
            {
                "id": identifier,
                "filename": photos[identifier]["filename"],
                "sourcePath": photos[identifier]["sourcePath"],
            }
            for identifier in identifiers
            if identifier in photos
        ]

    book_unused = unused_photos(book_unused_ids)
    exhibition_unused = unused_photos(exhibition_unused_ids)
    raw_actions = request.get("actions") if isinstance(request.get("actions"), list) else []
    actions = [item for item in raw_actions[:10_000] if isinstance(item, dict)]
    saved_at = datetime.now(timezone.utc).replace(microsecond=0)
    saved_at_text = saved_at.isoformat().replace("+00:00", "Z")
    session_name = str(request.get("sessionName") or "Sequence edit").strip()[:80]
    note = str(request.get("note") or "").strip()[:1200]
    summary = {
        "bookSpreads": len(book_blocks),
        "bookPhotos": len(book_ids),
        "bookEmptySlots": sum(slot["empty"] for block in book_blocks for slot in block["slots"]),
        "exhibitionBlocks": len(exhibition_blocks),
        "exhibitionPhotos": len(exhibition_ids),
        "exhibitionEmptySlots": sum(slot["empty"] for block in exhibition_blocks for slot in block["slots"]),
        "bookPhotosReturnedToUnused": len(book_unused_ids),
        "exhibitionPhotosReturnedToUnused": len(exhibition_unused_ids),
        "actionsRecorded": len(actions),
    }
    trace = {
        "schemaVersion": 3,
        "kind": "photo-portfolio-editable-drafts",
        "savedAt": saved_at_text,
        "sessionName": session_name,
        "note": note,
        "sourceSequence": "analysis/photobook/sequence.json",
        "baseSequenceSha256": current_hash,
        "summary": summary,
        "drafts": drafts,
        "bookBlocks": book_blocks,
        "exhibitionBlocks": exhibition_blocks,
        "bookPhotosReturnedToUnused": book_unused,
        "exhibitionPhotosReturnedToUnused": exhibition_unused,
        "actions": actions,
    }
    markdown = [
        f"# {session_name}", "", f"Saved: {saved_at_text}", f"Base sequence SHA-256: `{current_hash}`", "",
        "## Editor note", "", note or "No note supplied.", "", "## Summary", "",
        f"- Book: {summary['bookSpreads']} spreads, {summary['bookPhotos']} photographs, {summary['bookEmptySlots']} empty slots.",
        f"- Exhibition: {summary['exhibitionBlocks']} blocks, {summary['exhibitionPhotos']} photographs, {summary['exhibitionEmptySlots']} empty slots.",
        f"- {summary['bookPhotosReturnedToUnused']} canonical photographs are Unused in Book; {summary['exhibitionPhotosReturnedToUnused']} are Unused in Exhibition.",
        f"- {len(actions)} editing actions were recorded.", "", "## Action timeline", "",
    ]
    if actions:
        markdown.extend(f"- {clean_cell(item.get('at'))} — {clean_cell(item.get('description') or item.get('type'))}" for item in actions)
    else:
        markdown.append("- No actions recorded.")
    markdown += ["", "## Photographs returned to Unused", "", "### Book", ""]
    if book_unused:
        markdown.extend(f"- {clean_cell(item['filename'])} — `{clean_cell(item['sourcePath'])}`" for item in book_unused)
    else:
        markdown.append("- None.")
    markdown += ["", "### Exhibition", ""]
    if exhibition_unused:
        markdown.extend(f"- {clean_cell(item['filename'])} — `{clean_cell(item['sourcePath'])}`" for item in exhibition_unused)
    else:
        markdown.append("- None.")
    block_markdown(markdown, "Book spreads", book_blocks, "book")
    block_markdown(markdown, "Exhibition blocks", exhibition_blocks, "exhibition")
    markdown += ["## Applying this version", "", "This file is a complete editable checkpoint. Review it before updating the public website, then rebuild and verify both Book and Exhibition views.", ""]

    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = saved_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    basename = f"{stamp}--{safe_slug(session_name)}"
    json_path = TRACE_DIR / f"{basename}.json"
    md_path = TRACE_DIR / f"{basename}.md"
    suffix = 2
    while json_path.exists() or md_path.exists():
        json_path = TRACE_DIR / f"{basename}-{suffix}.json"
        md_path = TRACE_DIR / f"{basename}-{suffix}.md"
        suffix += 1
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=TRACE_DIR, delete=False) as handle:
        json.dump(trace, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        json_tmp = Path(handle.name)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=TRACE_DIR, delete=False) as handle:
        handle.write("\n".join(markdown))
        md_tmp = Path(handle.name)
    json_tmp.replace(json_path)
    md_tmp.replace(md_path)
    latest = {
        "schemaVersion": 1,
        "kind": "photo-portfolio-latest-editable-draft",
        "savedAt": saved_at_text,
        "sessionName": session_name,
        "sourceTrace": json_path.name,
        "baseSequenceSha256": current_hash,
        "drafts": drafts,
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=TRACE_DIR, delete=False) as handle:
        json.dump(latest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        latest_tmp = Path(handle.name)
    latest_tmp.replace(LATEST_PATH)
    return json_path, md_path, summary, saved_at_text, session_name


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "PhotoSequenceEditor/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        if args and str(args[0]).startswith("GET /thumb/"):
            return
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                self.send_json(public_state())
                return
            if path == "/api/history":
                self.send_json({"versions": history_versions()})
                return
            if path.startswith("/api/version/"):
                file_name = unquote(path.removeprefix("/api/version/"))
                self.send_json(load_version(file_name))
                return
            if path.startswith("/thumb/"):
                identifier = path.removeprefix("/thumb/")
                _, photos = load_project()
                photo = photos.get(identifier)
                if not photo:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                thumbnail = photo["thumbnailPath"]
                if thumbnail and thumbnail.is_file():
                    content_type = mimetypes.guess_type(thumbnail.name)[0] or "image/jpeg"
                    self.send_bytes(thumbnail.read_bytes(), content_type)
                else:
                    self.send_bytes(placeholder_svg(photo), "image/svg+xml; charset=utf-8")
                return
            static = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}.get(path)
            if static:
                target = EDITOR / static
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type in {"application/javascript", "text/javascript"}:
                    content_type += "; charset=utf-8"
                self.send_bytes(target.read_bytes(), content_type)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # keep the local UI useful and return a readable error
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/save":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("Invalid request size.")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            json_path, md_path, summary, saved_at, session_name = write_trace(request)
            self.send_json({
                "ok": True,
                "jsonPath": str(json_path.relative_to(ROOT)),
                "markdownPath": str(md_path.relative_to(ROOT)),
                "summary": summary,
                "savedAt": saved_at,
                "sessionName": session_name,
            })
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    global CATALOG_PATH, CATALOG_ROOT, SEQUENCE_PATH, TRACE_DIR, LATEST_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--catalog", type=Path, help="Catalog JSON (default: analysis/catalog.json, then demo data)")
    parser.add_argument("--sequence", type=Path, help="Sequence JSON (default: analysis/sequence.json, then demo data)")
    parser.add_argument("--trace-dir", type=Path, default=TRACE_DIR, help="Directory for saved editor versions")
    args = parser.parse_args()
    if args.catalog or args.sequence:
        if not args.catalog or not args.sequence:
            raise SystemExit("Use --catalog and --sequence together.")
        CATALOG_PATH = args.catalog.resolve()
        SEQUENCE_PATH = args.sequence.resolve()
    elif WORKING_CATALOG.is_file() and WORKING_SEQUENCE.is_file():
        CATALOG_PATH = WORKING_CATALOG
        SEQUENCE_PATH = WORKING_SEQUENCE
    else:
        CATALOG_PATH = DEMO_CATALOG
        SEQUENCE_PATH = DEMO_SEQUENCE
    CATALOG_ROOT = CATALOG_PATH.parent
    TRACE_DIR = args.trace_dir.resolve()
    LATEST_PATH = TRACE_DIR / "latest.json"
    if not CATALOG_PATH.exists() or not SEQUENCE_PATH.exists():
        raise SystemExit("The catalog or canonical sequence is missing.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), EditorHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Sequence editor: {url}")
    print(f"Catalog: {CATALOG_PATH.relative_to(ROOT) if ROOT in CATALOG_PATH.parents else CATALOG_PATH}")
    print(f"Traces save under {TRACE_DIR}. Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping sequence editor.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
