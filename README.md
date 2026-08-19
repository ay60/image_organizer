# Image Organizer

A non-destructive desktop image organizer. The app keeps its own SQLite database and never changes, copies, moves, or scans image folders.

## First milestone

- Add one or more existing images through a native file chooser
- Store a record for it in a local SQLite database
- Generate and store a small organizer-owned thumbnail for it
- Show the collection and its basic file metadata
- Remove a record from the collection without touching the source file

## Run

Requires Python 3.10+, Pillow, and PySide6.

```powershell
python -m organizer
```

On Windows, you can also double-click `launch_image_organizer.bat`; it uses the project's `wenv` virtual environment.

The database is created at `%LOCALAPPDATA%\\ImageOrganizer\\organizer.sqlite3` on Windows (or the equivalent app-data location on other systems).

## Data model direction

Images are external, immutable source files. The database stores their paths and organizer-owned metadata. Future albums will be saved tag queries: selecting several tags will produce the images that contain every selected tag.

`images` and `tags` use a many-to-many `image_tags` join table. Deleting an image record cascades to delete its `image_tags` rows only; it never deletes the source image or shared tags.

Thumbnails are generated from a selected image and stored in the organizer database. They are derived previews, not edits to the source image.
