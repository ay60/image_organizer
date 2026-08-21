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
    rating: int | None
    captured_at: str | None


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
                thumbnail BLOB,
                rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
                captured_at TEXT
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
        if "rating" not in columns:
            self.connection.execute("ALTER TABLE images ADD COLUMN rating INTEGER")
        if "captured_at" not in columns:
            self.connection.execute("ALTER TABLE images ADD COLUMN captured_at TEXT")
        self.connection.commit()

    def add_image(self, source: str | Path, thumbnail: bytes, captured_at: str | None = None) -> bool:
        """Record an existing file. Returns False if it is already in the collection."""
        file_path = Path(source).resolve()
        stat = file_path.stat()
        try:
            self.connection.execute(
                """INSERT INTO images(path, filename, file_size, modified_at, added_at, thumbnail, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(file_path), file_path.name, stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    thumbnail,
                    captured_at,
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

    def set_rating(self, image_id: int, rating: int | None) -> None:
        if rating is not None and not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5.")
        self.connection.execute("UPDATE images SET rating = ? WHERE id = ?", (rating, image_id))
        self.connection.commit()

    def update_captured_at(self, image_id: int, captured_at: str | None) -> None:
        self.connection.execute("UPDATE images SET captured_at = ? WHERE id = ?", (captured_at, image_id))
        self.connection.commit()

    def list_images(
        self,
        tag_names: list[str] | None = None,
        rating: int | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> list[ImageRecord]:
        """Return all images, or only images carrying every requested tag."""
        names: list[str] = []
        seen_names: set[str] = set()
        for tag_name in tag_names or []:
            normalized_name = tag_name.strip()
            if normalized_name and normalized_name.casefold() not in seen_names:
                names.append(normalized_name)
                seen_names.add(normalized_name.casefold())
        conditions: list[str] = []
        params: list[object] = []
        if rating is not None:
            conditions.append("images.rating = ?")
            params.append(rating)
        if year is not None:
            conditions.append("substr(images.captured_at, 1, 4) = ?")
            params.append(f"{year:04d}")
        if month is not None:
            conditions.append("substr(images.captured_at, 6, 2) = ?")
            params.append(f"{month:02d}")
        if names:
            placeholders = ", ".join("?" for _ in names)
            conditions.append(f"tags.name IN ({placeholders})")
            params.extend(names)
        query = "SELECT images.* FROM images"
        if names:
            query += " JOIN image_tags ON image_tags.image_id = images.id JOIN tags ON tags.id = image_tags.tag_id"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY images.id"
        if names:
            query += " HAVING COUNT(DISTINCT tags.name) = ?"
            params.append(len(names))
        query += " ORDER BY images.added_at DESC"
        rows = self.connection.execute(query, params).fetchall()
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

    def list_capture_years(self) -> list[int]:
        rows = self.connection.execute(
            """SELECT DISTINCT substr(captured_at, 1, 4) AS year
               FROM images WHERE captured_at IS NOT NULL
               ORDER BY year DESC"""
        ).fetchall()
        return [int(row["year"]) for row in rows if row["year"] and row["year"].isdigit()]

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
