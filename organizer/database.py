"""Persistence for organizer-owned metadata only; source files are never changed."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ImageRecord:
    id: int
    path: str
    filename: str
    file_size: int
    modified_at: str
    added_at: str
    thumbnail: bytes | None


class Database:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / ".local" / "share"))
            path = app_data / "ImageOrganizer" / "organizer.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                added_at TEXT NOT NULL,
                thumbnail BLOB
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
            CREATE TABLE IF NOT EXISTS image_tags (
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (image_id, tag_id)
            );
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(images)")}
        if "thumbnail" not in columns:
            self.connection.execute("ALTER TABLE images ADD COLUMN thumbnail BLOB")
        self.connection.commit()

    def add_image(self, source: str | Path, thumbnail: bytes) -> bool:
        """Record an existing file. Returns False if it is already in the collection."""
        file_path = Path(source).resolve()
        stat = file_path.stat()
        try:
            self.connection.execute(
                """INSERT INTO images(path, filename, file_size, modified_at, added_at, thumbnail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(file_path), file_path.name, stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    thumbnail,
                ),
            )
        except sqlite3.IntegrityError:
            return False
        self.connection.commit()
        return True

    def update_thumbnail(self, image_id: int, thumbnail: bytes) -> None:
        self.connection.execute(
            "UPDATE images SET thumbnail = ? WHERE id = ?", (thumbnail, image_id)
        )
        self.connection.commit()

    def list_images(self, tag_names: list[str] | None = None) -> list[ImageRecord]:
        """Return all images, or only images carrying every requested tag."""
        names: list[str] = []
        seen_names: set[str] = set()
        for tag_name in tag_names or []:
            normalized_name = tag_name.strip()
            if normalized_name and normalized_name.casefold() not in seen_names:
                names.append(normalized_name)
                seen_names.add(normalized_name.casefold())
        if not names:
            rows = self.connection.execute(
                "SELECT * FROM images ORDER BY added_at DESC"
            ).fetchall()
        else:
            placeholders = ", ".join("?" for _ in names)
            rows = self.connection.execute(
                f"""SELECT images.* FROM images
                    JOIN image_tags ON image_tags.image_id = images.id
                    JOIN tags ON tags.id = image_tags.tag_id
                    WHERE tags.name IN ({placeholders})
                    GROUP BY images.id
                    HAVING COUNT(DISTINCT tags.name) = ?
                    ORDER BY images.added_at DESC""",
                (*names, len(names)),
            ).fetchall()
        return [ImageRecord(**dict(row)) for row in rows]

    def remove_image(self, image_id: int) -> None:
        """Remove a record and its tag associations, never the source image."""
        self.connection.execute("DELETE FROM images WHERE id = ?", (image_id,))
        self.connection.commit()

    def add_tag_to_image(self, image_id: int, tag_name: str) -> None:
        """Associate an image with a tag, creating the tag when needed."""
        normalized_name = tag_name.strip()
        if not normalized_name:
            raise ValueError("A tag name cannot be empty.")
        self.connection.execute(
            "INSERT INTO tags(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (normalized_name,),
        )
        self.connection.execute(
            """INSERT INTO image_tags(image_id, tag_id)
               SELECT ?, id FROM tags WHERE name = ?
               ON CONFLICT(image_id, tag_id) DO NOTHING""",
            (image_id, normalized_name),
        )
        self.connection.commit()

    def tag_names_for_image(self, image_id: int) -> list[str]:
        rows = self.connection.execute(
            """SELECT tags.name FROM tags
               JOIN image_tags ON image_tags.tag_id = tags.id
               WHERE image_tags.image_id = ?
               ORDER BY tags.name COLLATE NOCASE""",
            (image_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def list_tag_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [row["name"] for row in rows]

    def image_ids_for_all_tags(self, tag_names: list[str]) -> list[int]:
        """Return the intersection: images carrying every supplied tag."""
        names = [name.strip() for name in tag_names if name.strip()]
        if not names:
            return [record.id for record in self.list_images()]
        placeholders = ", ".join("?" for _ in names)
        rows = self.connection.execute(
            f"""SELECT images.id FROM images
                JOIN image_tags ON image_tags.image_id = images.id
                JOIN tags ON tags.id = image_tags.tag_id
                WHERE tags.name IN ({placeholders})
                GROUP BY images.id
                HAVING COUNT(DISTINCT tags.name) = ?""",
            (*names, len(set(name.casefold() for name in names))),
        ).fetchall()
        return [row["id"] for row in rows]

    def close(self) -> None:
        self.connection.close()
