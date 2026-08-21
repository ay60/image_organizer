from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6 import QtCore, QtGui, QtWidgets

from .database import Database
from .metadata import extract_captured_at

IMAGE_FILTER = "Image files (*.jpg *.jpeg *.png *.gif *.bmp *.webp *.tif *.tiff)"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
THUMBNAIL_MAX_SIDE = 256


class OrganizerWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.database = Database()
        self.active_tags: list[str] = []
        self.filter_rating: int | None = None
        self.filter_year: int | None = None
        self.filter_month: int | None = None
        self.view_mode = "grid"
        self.setWindowTitle("Image Organizer")
        self.resize(1000, 700)
        self._build_ui()
        self.refresh_collection()

    def _build_ui(self) -> None:
        toolbar = self.addToolBar("Organizer")
        toolbar.setMovable(False)
        action = toolbar.addAction("Add images...")
        action.triggered.connect(self.add_images)
        action = toolbar.addAction("Remove from collection")
        action.triggered.connect(self.remove_selected)
        tags = QtWidgets.QMenu("Tags", self)
        tags.addAction("Assign tags to selected images...", self.assign_tags)
        tags.addAction("Clean up unused tags", self.cleanup_unused_tags)
        self._add_toolbar_menu(toolbar, "Tags", tags)
        filters = QtWidgets.QMenu("Filter", self)
        filters.addAction("Set filters...", self.choose_filter_tags)
        filters.addAction("Clear filters", self.clear_tag_filter)
        self._add_toolbar_menu(toolbar, "Filter", filters)
        view = QtWidgets.QMenu("View", self)
        view.addAction("Grid (thumbnails)", lambda: self.set_view("grid"))
        view.addAction("List (details)", lambda: self.set_view("list"))
        self._add_toolbar_menu(toolbar, "View", view)
        toolbar.addSeparator()
        action = toolbar.addAction("↑")
        action.setToolTip("Previous image")
        action.triggered.connect(lambda: self.move_current(-1))
        action = toolbar.addAction("↓")
        action.setToolTip("Next image")
        action.triggered.connect(lambda: self.move_current(1))
        selection = QtWidgets.QMenu("Selection", self)
        selection.addAction("Select all", self.select_all)
        selection.addAction("Clear selection", self.clear_selection)
        selection.addAction("Set rating...", self.set_rating)
        selection.addAction("Open selected images", self.open_selected_full_resolution)
        self._add_toolbar_menu(toolbar, "Selection", selection)
        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status)

        self.collection = QtWidgets.QListWidget()
        self.collection.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.collection.setViewMode(QtWidgets.QListView.IconMode)
        self.collection.setResizeMode(QtWidgets.QListView.Adjust)
        self.collection.setMovement(QtWidgets.QListView.Static)
        self.collection.setSpacing(12)
        self.collection.setIconSize(QtCore.QSize(256, 256))
        self.collection.setGridSize(QtCore.QSize(280, 290))
        self.collection.itemDoubleClicked.connect(self.open_full_resolution)

        self.detail_collection = QtWidgets.QTreeWidget()
        self.detail_collection.setHeaderLabels(["Preview", "File name", "Captured", "Rating"])
        self.detail_collection.setRootIsDecorated(False)
        self.detail_collection.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.detail_collection.setIconSize(QtCore.QSize(128, 128))
        self.detail_collection.setUniformRowHeights(True)
        header = self.detail_collection.header()
        header.setStretchLastSection(False)
        for column in range(self.detail_collection.columnCount()):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
        self.detail_collection.setColumnWidth(0, 150)
        self.detail_collection.setColumnWidth(2, 180)
        self.detail_collection.setColumnWidth(3, 70)
        self.detail_collection.itemDoubleClicked.connect(lambda item, _column: self.open_full_resolution(item))

        self.collection_stack = QtWidgets.QStackedWidget()
        self.collection_stack.addWidget(self.collection)
        self.collection_stack.addWidget(self.detail_collection)
        self.setCentralWidget(self.collection_stack)

    @staticmethod
    def _add_toolbar_menu(toolbar: QtWidgets.QToolBar, title: str, menu: QtWidgets.QMenu) -> None:
        button = QtWidgets.QToolButton(toolbar)
        button.setText(title)
        button.setMenu(menu)
        button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        toolbar.addWidget(button)

    def add_images(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add images to collection", "", IMAGE_FILTER)
        if not paths:
            return
        added = duplicates = 0
        failures: list[str] = []
        for selected in paths:
            source = Path(selected)
            if source.suffix.lower() not in IMAGE_EXTENSIONS:
                failures.append(f"{source.name}: unsupported file type")
                continue
            try:
                if self.database.add_image(source, self._create_thumbnail(source), extract_captured_at(source)):
                    added += 1
                else:
                    duplicates += 1
            except (OSError, ValueError, UnidentifiedImageError) as error:
                failures.append(f"{source.name}: {error}")
        self.refresh_collection()
        summary = f"Added {added} image{'s' if added != 1 else ''}."
        if duplicates:
            summary += f" {duplicates} already in collection."
        if failures:
            summary += f" {len(failures)} could not be added."
            QtWidgets.QMessageBox.warning(self, "Some images were not added", "\n".join(failures))
        self.status.setText(summary)

    def refresh_collection(self) -> None:
        self.collection.clear()
        self.detail_collection.clear()
        records = self.database.list_images(self.active_tags, self.filter_rating, self.filter_year, self.filter_month)
        for image in records:
            thumbnail = image.thumbnail
            if thumbnail and max(self._thumbnail_dimensions(thumbnail)) < THUMBNAIL_MAX_SIDE:
                try:
                    thumbnail = self._create_thumbnail(Path(image.path))
                    self.database.update_thumbnail(image.id, thumbnail)
                except (OSError, UnidentifiedImageError):
                    pass
            icon = self._thumbnail_icon(thumbnail)
            item = QtWidgets.QListWidgetItem(icon, "")
            item.setData(QtCore.Qt.UserRole, image.id)
            item.setToolTip(image.path)
            self.collection.addItem(item)

            detail_item = QtWidgets.QTreeWidgetItem()
            detail_item.setIcon(0, icon)
            detail_item.setText(1, image.filename)
            detail_item.setText(2, image.captured_at or "unknown date")
            detail_item.setText(3, f"★{image.rating}" if image.rating else "")
            detail_item.setData(0, QtCore.Qt.UserRole, image.id)
            detail_item.setToolTip(0, image.path)
            self.detail_collection.addTopLevelItem(detail_item)
        self.set_view(self.view_mode, refresh=False)
        filters = list(self.active_tags)
        if self.filter_rating is not None:
            filters.append(f"rating {self.filter_rating}")
        if self.filter_year is not None:
            filters.append(str(self.filter_year))
        if self.filter_month is not None:
            filters.append(f"month {self.filter_month}")
        filter_text = f" matching: {', '.join(filters)}" if filters else ""
        self.status.setText(f"{len(records)} image{'s' if len(records) != 1 else ''} in collection{filter_text}.")

    def set_view(self, mode: str, refresh: bool = True) -> None:
        self.view_mode = mode
        if mode == "grid":
            self.collection_stack.setCurrentWidget(self.collection)
            self.collection.setSpacing(12)
            self.collection.setIconSize(QtCore.QSize(256, 256))
            self.collection.setGridSize(QtCore.QSize(280, 290))
            for index in range(self.collection.count()):
                self.collection.item(index).setText("")
        else:
            self.collection_stack.setCurrentWidget(self.detail_collection)
            # The grid dimensions from thumbnail mode otherwise leave every
            # detail row at the grid's 290-pixel height.
            self.collection.setSpacing(0)
            self.collection.setGridSize(QtCore.QSize())
            self.collection.setIconSize(QtCore.QSize(128, 128))
            for index in range(self.collection.count()):
                item = self.collection.item(index)
                rating = item.data(QtCore.Qt.UserRole + 1)
                captured_at = item.data(QtCore.Qt.UserRole + 2) or "unknown date"
                rating_text = f"  ★{rating}" if rating else ""
                item.setText(f"{Path(item.toolTip()).name}  |  {captured_at}{rating_text}")
        if refresh:
            self.refresh_collection()

    def selected_ids(self) -> list[int]:
        if self.view_mode == "grid":
            return [int(item.data(QtCore.Qt.UserRole)) for item in self.collection.selectedItems()]
        return [int(item.data(0, QtCore.Qt.UserRole)) for item in self.detail_collection.selectedItems()]

    @staticmethod
    def _display_text(image) -> str:
        rating_text = f"  ★{image.rating}" if image.rating else ""
        return f"{image.filename}  |  {image.captured_at or 'unknown date'}{rating_text}"

    def remove_selected(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QtWidgets.QMessageBox.information(self, "No images selected", "Select one or more images first.")
            return
        prompt = f"Remove {len(selected)} image record{'s' if len(selected) != 1 else ''} from the organizer?\n\nThe source files will not be changed."
        if QtWidgets.QMessageBox.question(self, "Remove from collection", prompt) != QtWidgets.QMessageBox.Yes:
            return
        for image_id in selected:
            self.database.remove_image(image_id)
        self.refresh_collection()

    def assign_tags(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QtWidgets.QMessageBox.information(self, "No images selected", "Select one or more images first.")
            return
        tags = TagDialog(self, "Assign tags", self.database.list_tag_names()).get_tags()
        if tags is None:
            return
        for image_id in selected:
            for tag in tags:
                self.database.add_tag_to_image(image_id, tag)

    def cleanup_unused_tags(self) -> None:
        count = self.database.delete_unused_tags()
        QtWidgets.QMessageBox.information(
            self,
            "Tag cleanup",
            f"Removed {count} unused tag{'s' if count != 1 else ''}.",
        )

    def choose_filter_tags(self) -> None:
        result = FilterDialog(self, self.database.list_tag_names(), self.database.list_capture_years(), self.active_tags, self.filter_rating, self.filter_year, self.filter_month).get_filters()
        if result is not None:
            self.active_tags, self.filter_rating, self.filter_year, self.filter_month = result
            self.refresh_collection()

    def clear_tag_filter(self) -> None:
        self.active_tags = []
        self.filter_rating = self.filter_year = self.filter_month = None
        self.refresh_collection()

    def select_all(self) -> None:
        if self.view_mode == "grid":
            self.collection.selectAll()
        else:
            self.detail_collection.selectAll()

    def clear_selection(self) -> None:
        if self.view_mode == "grid":
            self.collection.clearSelection()
        else:
            self.detail_collection.clearSelection()

    def set_rating(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QtWidgets.QMessageBox.information(self, "No images selected", "Select one or more images first.")
            return
        value, accepted = QtWidgets.QInputDialog.getInt(self, "Set rating", "Rating (1-5, or 0 to clear):", 5, 0, 5)
        if accepted:
            for image_id in selected:
                self.database.set_rating(image_id, value or None)
            self.refresh_collection()

    def move_current(self, direction: int) -> None:
        if self.view_mode == "grid":
            if not self.collection.count():
                return
            current = self.collection.currentRow()
            target = max(0, min(self.collection.count() - 1, current + direction))
            self.collection.setCurrentRow(target)
            self.collection.scrollToItem(self.collection.item(target))
            return
        if not self.detail_collection.topLevelItemCount():
            return
        current_item = self.detail_collection.currentItem()
        current = self.detail_collection.indexOfTopLevelItem(current_item) if current_item else 0
        target = max(0, min(self.detail_collection.topLevelItemCount() - 1, current + direction))
        item = self.detail_collection.topLevelItem(target)
        self.detail_collection.setCurrentItem(item)
        self.detail_collection.scrollToItem(item)

    def open_full_resolution(self, item: QtWidgets.QListWidgetItem | QtWidgets.QTreeWidgetItem) -> None:
        image_id = int(item.data(0, QtCore.Qt.UserRole) if isinstance(item, QtWidgets.QTreeWidgetItem) else item.data(QtCore.Qt.UserRole))
        record = next((record for record in self.database.list_images() if record.id == image_id), None)
        if record is None:
            QtWidgets.QMessageBox.critical(self, "Image unavailable", "The source image could not be found.")
            return
        self._open_source_path(Path(record.path))

    def open_selected_full_resolution(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QtWidgets.QMessageBox.information(self, "No images selected", "Select one or more images first.")
            return
        records = {record.id: record for record in self.database.list_images()}
        missing = 0
        for image_id in selected:
            record = records.get(image_id)
            if record is None or not self._open_source_path(Path(record.path), show_error=False):
                missing += 1
        if missing:
            QtWidgets.QMessageBox.warning(self, "Some images unavailable", f"{missing} selected image(s) could not be opened.")

    def _open_source_path(self, source: Path, show_error: bool = True) -> bool:
        if not source.is_file():
            if show_error:
                QtWidgets.QMessageBox.critical(self, "Image unavailable", f"The source image could not be found:\n\n{source}")
            return False
        try:
            if sys.platform == "win32":
                os.startfile(str(source))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(source)])
            else:
                subprocess.Popen(["xdg-open", str(source)])
            return True
        except OSError as error:
            if show_error:
                QtWidgets.QMessageBox.critical(self, "Could not open image", str(error))
            return False

    @staticmethod
    def _create_thumbnail(source: Path) -> bytes:
        with Image.open(source) as image:
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE))
            if preview.mode not in ("RGB", "L"):
                preview = preview.convert("RGB")
            output = BytesIO()
            preview.save(output, format="JPEG", quality=82, optimize=True)
            return output.getvalue()

    @staticmethod
    def _thumbnail_dimensions(thumbnail: bytes) -> tuple[int, int]:
        with Image.open(BytesIO(thumbnail)) as image:
            return image.size

    @staticmethod
    def _thumbnail_icon(thumbnail: bytes | None) -> QtGui.QIcon:
        if not thumbnail:
            return QtGui.QIcon()
        image = Image.open(BytesIO(thumbnail)).convert("RGBA")
        data = image.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(data, image.width, image.height, QtGui.QImage.Format_RGBA8888).copy()
        return QtGui.QIcon(QtGui.QPixmap.fromImage(qimage))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.database.close()
        event.accept()


class TagDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, title: str, tags: list[str], selected: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.tags = QtWidgets.QListWidget()
        self.tags.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        selected_names = {tag.casefold() for tag in (selected or [])}
        for tag in tags:
            item = QtWidgets.QListWidgetItem(tag)
            item.setSelected(tag.casefold() in selected_names)
            self.tags.addItem(item)
        self.new_tags = QtWidgets.QLineEdit()
        self.new_tags.setPlaceholderText("New tags, comma-separated")
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QFormLayout(self)
        layout.addRow("Existing tags", self.tags)
        layout.addRow("New tags", self.new_tags)
        layout.addRow(buttons)

    def get_tags(self) -> list[str] | None:
        if self.exec() != QtWidgets.QDialog.Accepted:
            return None
        values = [item.text() for item in self.tags.selectedItems()]
        values.extend(tag.strip() for tag in self.new_tags.text().split(",") if tag.strip())
        return list({tag.casefold(): tag for tag in values}.values())


class FilterDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, tags: list[str], years: list[int], selected_tags: list[str], rating: int | None, year: int | None, month: int | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter images")
        self.tags = QtWidgets.QListWidget()
        self.tags.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        selected_names = {tag.casefold() for tag in selected_tags}
        for tag in tags:
            item = QtWidgets.QListWidgetItem(tag)
            item.setSelected(tag.casefold() in selected_names)
            self.tags.addItem(item)
        self.rating = QtWidgets.QComboBox()
        self.rating.addItem("Any rating", None)
        for value in range(1, 6):
            self.rating.addItem(str(value), value)
        if rating is not None:
            self.rating.setCurrentIndex(rating)
        self.year = QtWidgets.QComboBox()
        self.year.addItem("Any year", None)
        for value in years:
            self.year.addItem(str(value), value)
        if year is not None:
            index = self.year.findData(year)
            if index >= 0:
                self.year.setCurrentIndex(index)
        self.month = QtWidgets.QComboBox()
        self.month.addItem("Any month", None)
        for value in range(1, 13):
            self.month.addItem(f"{value:02d}", value)
        if month is not None:
            self.month.setCurrentIndex(month)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QFormLayout(self)
        layout.addRow("Tags (all selected)", self.tags)
        layout.addRow("Rating", self.rating)
        layout.addRow("Year taken", self.year)
        layout.addRow("Month taken", self.month)
        layout.addRow(buttons)

    def get_filters(self) -> tuple[list[str], int | None, int | None, int | None] | None:
        if self.exec() != QtWidgets.QDialog.Accepted:
            return None
        tags = [item.text() for item in self.tags.selectedItems()]
        return tags, self.rating.currentData(), self.year.currentData(), self.month.currentData()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = OrganizerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
