"""Prepare bundled deck data for packaged/local-runtime deployments.

In Docker we keep a read-only copy of bundled data inside the image at
`/app/server/data`, while runtime state lives in a separate writable directory
mounted from a volume. On startup we copy in any missing files, import bundled
deck rows when they're absent, and rewrite stale absolute paths so deck assets
resolve inside the current runtime data directory.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from loguru import logger

_DECK_COLUMNS = [
    "id",
    "title",
    "source_filename",
    "source_path",
    "pdf_path",
    "status",
    "error",
    "slide_count",
    "intro",
    "outro",
    "persona",
    "created_at",
]

_SLIDE_COLUMNS = [
    "deck_id",
    "number",
    "title",
    "bullets",
    "notes",
    "transition",
    "image_path",
    "status",
    "error",
]


def prime_runtime_data(runtime_dir: Path, bundled_dir: Path) -> None:
    """Copy missing bundled files into the writable runtime directory."""
    runtime_dir = runtime_dir.resolve()
    bundled_dir = bundled_dir.resolve()
    if runtime_dir == bundled_dir or not bundled_dir.exists():
        return
    copied = _copy_missing_tree(bundled_dir, runtime_dir)
    if copied:
        logger.info(f"Primed runtime data from bundled image data ({copied} files copied)")


def merge_bundled_data(runtime_dir: Path, bundled_dir: Path) -> None:
    """Import missing bundled decks and rewrite file paths for this runtime."""
    runtime_dir = runtime_dir.resolve()
    bundled_dir = bundled_dir.resolve()
    if runtime_dir == bundled_dir or not bundled_dir.exists():
        return

    runtime_db = runtime_dir / "app.db"
    bundled_db = bundled_dir / "app.db"
    if not runtime_db.exists() or not bundled_db.exists():
        return

    imported = _merge_missing_decks(runtime_db, bundled_db)
    normalized = _normalize_data_paths(runtime_db, runtime_dir)
    if imported:
        logger.info(f"Imported {imported} bundled deck(s) into runtime data")
    if normalized:
        logger.info(f"Normalized {normalized} bundled/runtime file path(s)")


def _copy_missing_tree(src: Path, dst: Path) -> int:
    copied = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
            copied += 1
    return copied


def _merge_missing_decks(runtime_db: Path, bundled_db: Path) -> int:
    deck_sql = (
        f"INSERT INTO decks ({', '.join(_DECK_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _DECK_COLUMNS)})"
    )
    slide_sql = (
        f"INSERT INTO slides ({', '.join(_SLIDE_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _SLIDE_COLUMNS)})"
    )

    imported = 0
    with sqlite3.connect(runtime_db) as dest, sqlite3.connect(bundled_db) as src:
        dest.row_factory = sqlite3.Row
        src.row_factory = sqlite3.Row

        existing = {row[0] for row in dest.execute("SELECT id FROM decks")}
        bundled_ids = [
            row[0]
            for row in src.execute("SELECT id FROM decks ORDER BY created_at, id")
            if row[0] not in existing
        ]

        for deck_id in bundled_ids:
            deck_row = src.execute(
                f"SELECT {', '.join(_DECK_COLUMNS)} FROM decks WHERE id = ?",
                (deck_id,),
            ).fetchone()
            if deck_row is None:
                continue
            dest.execute(deck_sql, tuple(deck_row[col] for col in _DECK_COLUMNS))

            slide_rows = src.execute(
                f"SELECT {', '.join(_SLIDE_COLUMNS)} FROM slides "
                "WHERE deck_id = ? ORDER BY number",
                (deck_id,),
            ).fetchall()
            for slide_row in slide_rows:
                dest.execute(slide_sql, tuple(slide_row[col] for col in _SLIDE_COLUMNS))
            imported += 1

        if imported:
            dest.commit()

    return imported


def _normalize_data_paths(runtime_db: Path, runtime_dir: Path) -> int:
    updates: list[tuple[str, str, str]] = []
    slide_updates: list[tuple[str, str, int]] = []

    with sqlite3.connect(runtime_db) as conn:
        conn.row_factory = sqlite3.Row

        deck_rows = conn.execute("SELECT id, source_path, pdf_path FROM decks").fetchall()
        for row in deck_rows:
            deck_id = row["id"]
            source_path = _remap_path(row["source_path"], deck_id, runtime_dir)
            pdf_path = _remap_path(row["pdf_path"], deck_id, runtime_dir)
            if source_path and source_path != row["source_path"]:
                updates.append(("source_path", source_path, deck_id))
            if pdf_path and pdf_path != row["pdf_path"]:
                updates.append(("pdf_path", pdf_path, deck_id))

        slide_rows = conn.execute(
            "SELECT deck_id, number, image_path FROM slides"
        ).fetchall()
        for row in slide_rows:
            image_path = _remap_path(row["image_path"], row["deck_id"], runtime_dir)
            if image_path and image_path != row["image_path"]:
                slide_updates.append((image_path, row["deck_id"], row["number"]))

        for column, new_path, deck_id in updates:
            conn.execute(f"UPDATE decks SET {column} = ? WHERE id = ?", (new_path, deck_id))
        for image_path, deck_id, number in slide_updates:
            conn.execute(
                "UPDATE slides SET image_path = ? WHERE deck_id = ? AND number = ?",
                (image_path, deck_id, number),
            )

        if updates or slide_updates:
            conn.commit()

    return len(updates) + len(slide_updates)


def _remap_path(path_str: str | None, deck_id: str, runtime_dir: Path) -> str | None:
    if not path_str:
        return None

    path = Path(path_str)
    try:
        if path.exists() and path.is_relative_to(runtime_dir):
            return str(path)
    except AttributeError:
        if path.exists() and str(path).startswith(f"{runtime_dir}{Path('/')}"):
            return str(path)

    rel = _deck_relative_path(path, deck_id)
    if rel is None:
        return None

    candidate = runtime_dir / rel
    if candidate.exists():
        return str(candidate)
    return None


def _deck_relative_path(path: Path, deck_id: str) -> Path | None:
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "decks" and i + 1 < len(parts) and parts[i + 1] == deck_id:
            return Path(*parts[i:])
    return None
