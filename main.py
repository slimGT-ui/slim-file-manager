#!/usr/bin/env python3

import sys
import os
import shutil
import zipfile
import subprocess
import stat
from datetime import datetime

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QLineEdit,
    QTextEdit, QToolBar, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QMenu, QInputDialog, QAbstractItemView,
    QDialog, QProgressBar, QTabWidget, QSplitter, QFrame
)

from PySide6.QtCore import Qt, QSize, QThread, Signal, QEvent, QUrl, QMimeData, QSettings
from PySide6.QtGui import (
    QFont, QAction, QPixmap, QWheelEvent, QIcon, QKeySequence,
    QShortcut, QDrag, QImage
)


# ---------------------------------------------------------------------------
# Эмодзи-иконки вместо текстовых префиксов — интуитивно понятно, что за файл
# ---------------------------------------------------------------------------
EMOJI_FOLDER = "📁"
EMOJI_DEFAULT_FILE = "📄"

EXTENSION_EMOJI_MAP = {
    ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".webp": "🖼️",
    ".gif": "🖼️", ".bmp": "🖼️", ".svg": "🖼️", ".ico": "🖼️",
    ".zip": "📦", ".rar": "📦", ".7z": "📦", ".tar": "📦",
    ".gz": "📦", ".bz2": "📦", ".xz": "📦",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".ogg": "🎵",
    ".m4a": "🎵", ".aac": "🎵",
    ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬",
    ".webm": "🎬", ".flv": "🎬",
    ".pdf": "📕", ".doc": "📘", ".docx": "📘",
    ".xls": "📊", ".xlsx": "📊", ".csv": "📊",
    ".ppt": "📙", ".pptx": "📙",
    ".txt": "📝", ".md": "📝",
    ".py": "🐍", ".js": "💻", ".ts": "💻", ".html": "💻",
    ".css": "💻", ".cpp": "💻", ".c": "💻", ".java": "💻",
    ".sh": "💻", ".json": "🧩", ".ini": "⚙️", ".conf": "⚙️",
    ".yml": "⚙️", ".yaml": "⚙️", ".xml": "🧩",
    ".ttf": "🔤", ".otf": "🔤",
    ".exe": "⚡", ".app": "⚡", ".appimage": "⚡",
}

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
TEXT_PREVIEW_EXTS = (
    ".txt", ".json", ".md", ".csv", ".html", ".css", ".js", ".py",
    ".cpp", ".c", ".java", ".sh", ".conf", ".ini", ".yml", ".yaml", ".xml", ".log"
)


def get_emoji_for(filename, is_dir):
    if is_dir:
        return EMOJI_FOLDER
    ext = os.path.splitext(filename)[1].lower()
    return EXTENSION_EMOJI_MAP.get(ext, EMOJI_DEFAULT_FILE)


def format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


# ---------------------------------------------------------------------------
# Фоновые потоки
# ---------------------------------------------------------------------------
class FileOpThread(QThread):
    progress = Signal(int, str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, src_paths, target_dir, is_move=False):
        super().__init__()
        self.src_paths = src_paths
        self.target_dir = target_dir
        self.is_move = is_move

    def run(self):
        try:
            total_items = max(len(self.src_paths), 1)
            for i, src in enumerate(self.src_paths):
                if not os.path.exists(src):
                    continue
                filename = os.path.basename(src)
                dest = os.path.join(self.target_dir, filename)

                if src == dest:
                    continue

                percent = int((i / total_items) * 100)
                verb = "Перемещение" if self.is_move else "Копирование"
                self.progress.emit(percent, f"{verb}: {filename}")

                if self.is_move:
                    shutil.move(src, dest)
                else:
                    if os.path.isdir(src):
                        shutil.copytree(src, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dest)

            self.progress.emit(100, "Готово!")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Операция с файлами")
        self.setFixedSize(420, 110)

        layout = QVBoxLayout(self)
        self.label = QLabel("Подготовка к передаче...")
        layout.addWidget(self.label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.setStyleSheet("""
            QDialog { background: #15151c; color: #eeeeee; }
            QLabel { color: #eeeeee; font-size: 13px; }
            QProgressBar { background: #20202a; border: none; border-radius: 6px; text-align: center; color: white; }
            QProgressBar::chunk { background: #3a3a4c; border-radius: 6px; }
        """)

    def update_progress(self, val, text):
        self.progress_bar.setValue(val)
        self.label.setText(text)


class ThumbnailLoader(QThread):
    thumbnail_loaded = Signal(str, QIcon)

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths

    def run(self):
        for path in self.file_paths:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    70, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.thumbnail_loaded.emit(path, QIcon(scaled_pixmap))


# ---------------------------------------------------------------------------
# Дочерние окна
# ---------------------------------------------------------------------------
class ImageViewer(QMainWindow):
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.setWindowTitle(f"Image Viewer — {os.path.basename(file_path)}")
        self.resize(900, 600)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background: #111116; border: none;")
        self.setCentralWidget(self.view)

        self.pixmap = QPixmap(file_path)
        if not self.pixmap.isNull():
            self.pixmap_item = QGraphicsPixmapItem(self.pixmap)
            self.scene.addItem(self.pixmap_item)
            self.view.fitInView(self.pixmap_item, Qt.KeepAspectRatio)
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось загрузить изображение.")

    def wheelEvent(self, event: QWheelEvent):
        zoom_factor = 1.15
        if event.angleDelta().y() > 0:
            self.view.scale(zoom_factor, zoom_factor)
        else:
            self.view.scale(1 / zoom_factor, 1 / zoom_factor)


class TextEditor(QMainWindow):
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.setWindowTitle(f"Text Editor — {os.path.basename(file_path)}")
        self.resize(1000, 700)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.installEventFilter(self)
        self.setCentralWidget(self.editor)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        save_action = QAction("💾 Save", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save_file)
        toolbar.addAction(save_action)

        self.setStyleSheet("""
            QMainWindow { background: #15151c; }
            QToolBar { background: #1d1d26; border: none; padding: 8px; }
            QToolButton { color: white; background: #292936; border: none; border-radius: 8px; padding: 8px 14px; }
            QToolButton:hover { background: #383846; }
            QTextEdit { background: #15151c; color: #eeeeee; border: none; padding: 20px; font-family: monospace; font-size: 15px; selection-background-color: #3a3a4c; }
        """)

        self.load_file()

    def eventFilter(self, obj, event):
        if obj == self.editor and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Tab:
                self.editor.insertPlainText("    ")
                return True
        return super().eventFilter(obj, event)

    def load_file(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as file:
                self.editor.setPlainText(file.read())
        except Exception as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n\n{error}")

    def save_file(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as file:
                file.write(self.editor.toPlainText())
            QMessageBox.information(self, "Сохранено", "Файл успешно сохранён!")
        except Exception as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n\n{error}")


class ZipViewer(QMainWindow):
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.setWindowTitle(f"Archive — {os.path.basename(file_path)}")
        self.resize(700, 500)

        self.list_widget = QListWidget()
        self.setCentralWidget(self.list_widget)

        self.setStyleSheet("""
            QMainWindow { background: #15151c; }
            QListWidget { background: #15151c; color: #eeeeee; border: none; padding: 10px; font-family: monospace; }
            QListWidget::item { padding: 6px; border-bottom: 1px solid #20202a; }
        """)

        self.load_archive()

    def load_archive(self):
        try:
            with zipfile.ZipFile(self.file_path, 'r') as zf:
                for info in zf.infolist():
                    size_kb = round(info.file_size / 1024, 1)
                    item_emoji = "📁" if info.is_dir() else get_emoji_for(info.filename, False)
                    item_text = f"{item_emoji}  {info.filename}  ({size_kb} KB)"
                    self.list_widget.addItem(item_text)
        except Exception as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать архив:\n\n{error}")


# ---------------------------------------------------------------------------
# Список файлов с drag&drop, клавиатурой (Enter/Space)
# ---------------------------------------------------------------------------
class FileListWidget(QListWidget):
    def __init__(self, main_window, tab, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.tab = tab
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.drag_start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self.drag_start_pos:
            super().mouseMoveEvent(event)
            return

        if (event.position().toPoint() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        selected_items = self.selectedItems()
        if not selected_items:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        urls = [QUrl.fromLocalFile(item.data(Qt.UserRole)) for item in selected_items]
        mime_data.setUrls(urls)
        drag.setMimeData(mime_data)

        pixmap = selected_items[0].icon().pixmap(QSize(48, 48))
        if not pixmap.isNull():
            drag.setPixmap(pixmap)

        drag.exec(Qt.CopyAction | Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return

        urls = event.mimeData().urls()
        src_paths = [u.toLocalFile() for u in urls if u.isLocalFile()]

        if not src_paths:
            return

        target_item = self.itemAt(event.position().toPoint())
        target_dir = self.tab.current_path

        if target_item:
            item_path = target_item.data(Qt.UserRole)
            if os.path.isdir(item_path):
                target_dir = item_path

        if target_dir in src_paths:
            return

        menu = QMenu(self)
        move_action = menu.addAction("➡️ Переместить сюда")
        copy_action = menu.addAction("📋 Копировать сюда")
        menu.addSeparator()
        menu.addAction("✖ Отмена")

        chosen_action = menu.exec(self.mapToGlobal(event.position().toPoint()))

        if chosen_action == move_action:
            self.main_window.process_drop_files(src_paths, target_dir, self.tab, is_move=True)
            event.acceptProposedAction()
        elif chosen_action == copy_action:
            self.main_window.process_drop_files(src_paths, target_dir, self.tab, is_move=False)
            event.acceptProposedAction()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            item = self.currentItem()
            if item:
                self.main_window.open_item(item)
            return
        if event.key() == Qt.Key_Space:
            self.main_window.toggle_preview_panel(force_show=True)
            return
        if event.key() == Qt.Key_Backspace:
            self.tab.go_back()
            return
        super().keyPressEvent(event)


class SidebarWidget(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for u in urls:
                path = u.toLocalFile()
                if os.path.isdir(path):
                    folder_name = os.path.basename(path) or path
                    self.main_window.add_sidebar_button(f"📌 {folder_name}", path, is_custom=True)
            event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Одна вкладка = одна открытая директория со своей историей и настройками
# ---------------------------------------------------------------------------
class BrowserTab(QWidget):
    SORT_LABELS = {"name": "Имя", "size": "Размер", "modified": "Дата изменения"}

    def __init__(self, main_window, start_path, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.current_path = start_path
        self.show_hidden = False
        self.is_list_view = False
        self.sort_key = "name"
        self.sort_reverse = False
        self.history_back = []
        self.history_forward = []
        self.loader_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        top_bar = QHBoxLayout()
        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        self.back_button.setFixedWidth(40)
        self.forward_button.setFixedWidth(40)
        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)

        self.breadcrumb_container = QWidget()
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb_container)
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb_layout.setSpacing(4)
        self.breadcrumb_layout.setAlignment(Qt.AlignLeft)

        self.sort_button = QPushButton("↕ Имя")
        self.sort_button.setToolTip("Сортировка")
        self.sort_button.clicked.connect(self.show_sort_menu)

        self.view_toggle_button = QPushButton("⬜ Вид")
        self.view_toggle_button.setToolTip("Переключить сетка/список")
        self.view_toggle_button.clicked.connect(self.toggle_view_mode)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Поиск...")
        self.search_input.setFixedWidth(150)
        self.search_input.textChanged.connect(self.filter_files)

        self.hidden_button = QPushButton("👁 Скрытые")
        self.hidden_button.setToolTip("Показать/скрыть скрытые файлы (Ctrl+H)")
        self.hidden_button.setFixedWidth(100)
        self.hidden_button.clicked.connect(self.toggle_hidden)

        top_bar.addWidget(self.back_button)
        top_bar.addWidget(self.forward_button)
        top_bar.addWidget(self.breadcrumb_container, 1)
        top_bar.addWidget(self.sort_button)
        top_bar.addWidget(self.view_toggle_button)
        top_bar.addWidget(self.search_input)
        top_bar.addWidget(self.hidden_button)
        layout.addLayout(top_bar)

        self.file_list = FileListWidget(main_window, self)
        self.file_list.setViewMode(QListWidget.IconMode)
        self.file_list.setResizeMode(QListWidget.Adjust)
        self.file_list.setMovement(QListWidget.Static)
        self.file_list.setSpacing(15)
        self.file_list.setIconSize(QSize(70, 70))
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)

        emoji_font = QFont()
        emoji_font.setPointSize(11)
        self.file_list.setFont(emoji_font)

        self.file_list.itemDoubleClicked.connect(main_window.open_item)
        self.file_list.itemSelectionChanged.connect(lambda: main_window.on_selection_changed(self))
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(
            lambda pos: main_window.show_context_menu(self, pos)
        )

        layout.addWidget(self.file_list)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #999; padding: 4px 2px;")
        layout.addWidget(self.status_label)

        self.update_breadcrumbs()

    # --- навигация -------------------------------------------------------
    def go_to(self, path, add_to_history=True):
        if os.path.exists(path) and os.path.isdir(path):
            if add_to_history and self.current_path != path:
                self.history_back.append(self.current_path)
                self.history_forward.clear()
            self.current_path = path
            self.load_files()
            self.main_window.update_tab_title(self)

    def go_back(self):
        if self.history_back:
            self.history_forward.append(self.current_path)
            target = self.history_back.pop()
            self.go_to(target, add_to_history=False)

    def go_forward(self):
        if self.history_forward:
            self.history_back.append(self.current_path)
            target = self.history_forward.pop()
            self.go_to(target, add_to_history=False)

    def update_breadcrumbs(self):
        for i in reversed(range(self.breadcrumb_layout.count())):
            item = self.breadcrumb_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)

        path_parts = [p for p in self.current_path.split(os.sep) if p]

        root_btn = QPushButton("🏠 /")
        root_btn.clicked.connect(lambda: self.go_to("/"))
        self.breadcrumb_layout.addWidget(root_btn)

        accumulated = ""
        for part in path_parts:
            accumulated += "/" + part
            btn = QPushButton(f"{part} /")
            btn.clicked.connect(lambda _, p=accumulated: self.go_to(p))
            self.breadcrumb_layout.addWidget(btn)

    # --- вид / фильтры -----------------------------------------------------
    def toggle_view_mode(self):
        self.is_list_view = not self.is_list_view
        if self.is_list_view:
            self.file_list.setViewMode(QListWidget.ListMode)
            self.file_list.setIconSize(QSize(28, 28))
            self.file_list.setSpacing(2)
        else:
            self.file_list.setViewMode(QListWidget.IconMode)
            self.file_list.setIconSize(QSize(70, 70))
            self.file_list.setSpacing(15)
        self.load_files()

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.load_files()

    def filter_files(self, text):
        search_query = text.lower()
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            filename = os.path.basename(item.data(Qt.UserRole))
            item.setHidden(search_query not in filename.lower())

    def show_sort_menu(self):
        menu = QMenu(self)
        for key, label in self.SORT_LABELS.items():
            mark = "✓ " if key == self.sort_key else "   "
            action = menu.addAction(f"{mark}{label}")
            action.setData(key)
        menu.addSeparator()
        rev_mark = "✓ " if self.sort_reverse else "   "
        rev_action = menu.addAction(f"{rev_mark}Обратный порядок")

        chosen = menu.exec(self.sort_button.mapToGlobal(self.sort_button.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen == rev_action:
            self.sort_reverse = not self.sort_reverse
        else:
            key = chosen.data()
            if key:
                self.sort_key = key
        self.sort_button.setText(f"↕ {self.SORT_LABELS[self.sort_key]}")
        self.load_files()

    # --- загрузка списка ---------------------------------------------------
    def load_files(self):
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.terminate()
            self.loader_thread.wait()

        self.file_list.clear()
        self.search_input.clear()
        self.update_breadcrumbs()

        try:
            names = os.listdir(self.current_path)

            if not self.show_hidden:
                names = [f for f in names if not f.startswith(".")]

            entries = []
            for name in names:
                full_path = os.path.join(self.current_path, name)
                try:
                    st = os.stat(full_path)
                except OSError:
                    continue
                is_dir = os.path.isdir(full_path)
                entries.append({
                    "name": name,
                    "path": full_path,
                    "is_dir": is_dir,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })

            if self.sort_key == "size":
                entries.sort(key=lambda e: (not e["is_dir"], e["size"]))
            elif self.sort_key == "modified":
                entries.sort(key=lambda e: (not e["is_dir"], e["mtime"]))
            else:
                entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

            if self.sort_reverse:
                dirs = [e for e in entries if e["is_dir"]]
                files = [e for e in entries if not e["is_dir"]]
                dirs.reverse()
                files.reverse()
                entries = dirs + files

            images_to_load = []

            for e in entries:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, e["path"])

                emoji = get_emoji_for(e["name"], e["is_dir"])
                separator = " " if self.is_list_view else "\n"
                item.setText(f"{emoji}{separator}{e['name']}")

                ext = os.path.splitext(e["name"])[1].lower()
                if not e["is_dir"] and ext in IMG_EXTS:
                    images_to_load.append(e["path"])

                self.file_list.addItem(item)

            if images_to_load and not self.is_list_view:
                self.loader_thread = ThumbnailLoader(images_to_load)
                self.loader_thread.thumbnail_loaded.connect(self.on_thumbnail_loaded)
                self.loader_thread.start()

            self.update_status_bar()

        except Exception as error:
            QMessageBox.critical(self, "Ошибка", str(error))

    def on_thumbnail_loaded(self, path, icon):
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.data(Qt.UserRole) == path:
                item.setIcon(icon)
                item.setText(os.path.basename(path))
                break

    def update_status_bar(self):
        total = self.file_list.count()
        selected_items = self.file_list.selectedItems()
        if selected_items:
            total_size = 0
            for it in selected_items:
                p = it.data(Qt.UserRole)
                if os.path.isfile(p):
                    try:
                        total_size += os.path.getsize(p)
                    except OSError:
                        pass
            self.status_label.setText(
                f"Выбрано: {len(selected_items)} из {total}  •  {format_size(total_size)}"
            )
        else:
            self.status_label.setText(f"Объектов: {total}")


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------
class FileManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Slim File Manager")
        self.resize(1300, 780)

        self.settings = QSettings("SlimDev", "SlimFileManager")

        # --- ФИКС БАГА: QSettings может вернуть None вместо [] для пустого
        # списка (известная особенность). Раньше это оборачивалось в [None]
        # и падало на os.path.exists(None). Теперь фильтруем пустые значения.
        raw_pins = self.settings.value("custom_pins", [])
        if raw_pins is None:
            raw_pins = []
        if not isinstance(raw_pins, list):
            raw_pins = [raw_pins]
        self.custom_pins = [p for p in raw_pins if p]

        start_path = os.path.expanduser("~")
        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            arg_path = os.path.abspath(sys.argv[1])
            start_path = arg_path if os.path.isdir(arg_path) else os.path.dirname(arg_path)
        else:
            last_path = self.settings.value("last_path", "")
            if last_path and os.path.isdir(last_path):
                start_path = last_path

        self.clipboard_paths = []
        self.is_cut_operation = False
        self.undo_stack = []
        self.pending_undo = None

        self.child_window = None
        self.op_thread = None
        self.prog_dialog = None
        self.preview_visible = False

        self.setup_ui(start_path)
        self.setup_shortcuts()

    # --- UI ------------------------------------------------------------
    def setup_ui(self, start_path):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = SidebarWidget(self)
        self.sidebar.setFixedWidth(220)
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(15, 20, 15, 20)

        title = QLabel("SLIM FILES")
        title.setFont(QFont("Sans", 18, QFont.Bold))
        self.sidebar_layout.addWidget(title)
        self.sidebar_layout.addSpacing(20)

        self.sidebar_buttons_layout = QVBoxLayout()
        self.sidebar_layout.addLayout(self.sidebar_buttons_layout)

        default_buttons = [
            ("🏠 Home", "~"),
            ("🖥️ Desktop", "~/Desktop"),
            ("⬇️ Downloads", "~/Downloads"),
            ("📄 Documents", "~/Documents"),
            ("🖼️ Pictures", "~/Pictures"),
        ]
        for text, path in default_buttons:
            self.add_sidebar_button(text, os.path.expanduser(path))

        for path in self.custom_pins:
            if os.path.exists(path):
                folder_name = os.path.basename(path) or path
                self.add_sidebar_button(f"📌 {folder_name}", path, is_custom=True, save=False)

        self.sidebar_layout.addStretch()

        self.new_tab_btn = QPushButton("➕ Новая вкладка")
        self.new_tab_btn.clicked.connect(lambda: self.add_new_tab())
        self.sidebar_layout.addWidget(self.new_tab_btn)

        self.preview_toggle_btn = QPushButton("👀 Предпросмотр")
        self.preview_toggle_btn.setToolTip("Ctrl+P")
        self.preview_toggle_btn.clicked.connect(lambda: self.toggle_preview_panel())
        self.sidebar_layout.addWidget(self.preview_toggle_btn)

        version = QLabel("Slim File Manager v2.0")
        version.setStyleSheet("color: #777;")
        self.sidebar_layout.addWidget(version)

        main_layout.addWidget(self.sidebar)

        # вкладки
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

        # панель предпросмотра
        self.preview_panel = QFrame()
        self.preview_panel.setFixedWidth(280)
        self.preview_panel.setStyleSheet("QFrame { background: #1a1a22; border-left: 1px solid #26262f; }")
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setAlignment(Qt.AlignTop)

        self.preview_icon_label = QLabel()
        self.preview_icon_label.setAlignment(Qt.AlignCenter)
        self.preview_icon_label.setFixedHeight(180)
        self.preview_icon_label.setStyleSheet("font-size: 64px;")
        preview_layout.addWidget(self.preview_icon_label)

        self.preview_name_label = QLabel("")
        self.preview_name_label.setWordWrap(True)
        self.preview_name_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 8px;")
        preview_layout.addWidget(self.preview_name_label)

        self.preview_info_label = QLabel("")
        self.preview_info_label.setWordWrap(True)
        self.preview_info_label.setStyleSheet("color: #999; font-size: 12px; margin-top: 4px;")
        preview_layout.addWidget(self.preview_info_label)

        self.preview_text_label = QLabel("")
        self.preview_text_label.setWordWrap(True)
        self.preview_text_label.setAlignment(Qt.AlignTop)
        self.preview_text_label.setStyleSheet(
            "color: #ccc; font-family: monospace; font-size: 11px; margin-top: 10px;"
        )
        preview_layout.addWidget(self.preview_text_label)
        preview_layout.addStretch()
        self.preview_panel.setVisible(False)

        splitter = QSplitter()
        splitter.addWidget(self.tab_widget)
        splitter.addWidget(self.preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        content_wrap = QWidget()
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.addWidget(splitter)

        main_layout.addWidget(content_wrap)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #15151c; color: #eeeeee; font-size: 14px; }
            QPushButton { background: #20202a; border: none; border-radius: 8px; padding: 8px 12px; text-align: center; }
            QPushButton:hover { background: #2c2c38; }
            QLineEdit { background: #20202a; border: none; border-radius: 8px; padding: 8px 12px; color: white; }
            QListWidget { background: #15151c; border: none; }
            QListWidget::item { background: #20202a; border-radius: 10px; padding: 8px; margin: 3px; }
            QListWidget::item:hover { background: #292936; }
            QListWidget::item:selected { background: #3a3a4c; border: 1px solid #5a5a7c; }
            QMenu { background: #20202a; color: white; border: 1px solid #2c2c38; padding: 5px; border-radius: 8px; }
            QMenu::item { padding: 8px 20px; border-radius: 5px; }
            QMenu::item:selected { background: #3a3a4c; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #1d1d26; color: #ccc; padding: 8px 16px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
            QTabBar::tab:selected { background: #20202a; color: white; }
            QSplitter::handle { background: #15151c; }
        """)

        self.add_new_tab(start_path)

    # --- вкладки ---------------------------------------------------------
    def add_new_tab(self, path=None):
        if path is None:
            path = self.current_tab().current_path if self.tab_widget.count() else os.path.expanduser("~")
        tab = BrowserTab(self, path)
        index = self.tab_widget.addTab(tab, os.path.basename(path) or "/")
        self.tab_widget.setCurrentIndex(index)
        tab.load_files()
        return tab

    def close_tab(self, index):
        if self.tab_widget.count() <= 1:
            return
        widget = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        widget.deleteLater()

    def current_tab(self):
        return self.tab_widget.currentWidget()

    def update_tab_title(self, tab):
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self.tab_widget.setTabText(index, os.path.basename(tab.current_path) or "/")

    def on_tab_changed(self, _index):
        self.update_preview(self.current_tab())

    def go_to(self, path):
        self.current_tab().go_to(path)

    # --- сайдбар ---------------------------------------------------------
    def add_sidebar_button(self, text, path, is_custom=False, save=True):
        btn = QPushButton(text)
        btn.setStyleSheet("text-align: left;")
        btn.clicked.connect(lambda _, p=path: self.go_to(p))

        if is_custom:
            if save and path not in self.custom_pins:
                self.custom_pins.append(path)
                self.settings.setValue("custom_pins", self.custom_pins)
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, b=btn, p=path: self.remove_sidebar_button(b, p, pos))

        self.sidebar_buttons_layout.addWidget(btn)

    def remove_sidebar_button(self, button, path, pos):
        menu = QMenu(self)
        remove_action = menu.addAction("🗑️ Удалить из быстрого доступа")
        if menu.exec(button.mapToGlobal(pos)) == remove_action:
            if path in self.custom_pins:
                self.custom_pins.remove(path)
                self.settings.setValue("custom_pins", self.custom_pins)
            button.deleteLater()

    # --- панель предпросмотра ---------------------------------------------
    def toggle_preview_panel(self, force_show=False):
        if force_show:
            self.preview_visible = True
        else:
            self.preview_visible = not self.preview_visible
        self.preview_panel.setVisible(self.preview_visible)
        if self.preview_visible:
            self.update_preview(self.current_tab())

    def on_selection_changed(self, tab):
        tab.update_status_bar()
        if tab is self.current_tab():
            self.update_preview(tab)

    def update_preview(self, tab):
        if not self.preview_visible or tab is None:
            return
        selected = tab.file_list.selectedItems()
        if len(selected) != 1:
            self.preview_icon_label.setText("")
            self.preview_name_label.setText("Выберите один объект" if not selected else f"Выбрано {len(selected)} объектов")
            self.preview_info_label.setText("")
            self.preview_text_label.setText("")
            return

        path = selected[0].data(Qt.UserRole)
        is_dir = os.path.isdir(path)
        name = os.path.basename(path)

        self.preview_name_label.setText(name)
        self.preview_text_label.setText("")

        ext = os.path.splitext(name)[1].lower()
        if not is_dir and ext in IMG_EXTS:
            pix = QPixmap(path)
            if not pix.isNull():
                self.preview_icon_label.setPixmap(
                    pix.scaled(240, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self.preview_icon_label.setText(get_emoji_for(name, is_dir))
        else:
            self.preview_icon_label.setPixmap(QPixmap())
            self.preview_icon_label.setText(get_emoji_for(name, is_dir))

        try:
            st = os.stat(path)
            size_txt = format_size(st.st_size) if not is_dir else "—"
            modified = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')
            self.preview_info_label.setText(f"{'Папка' if is_dir else 'Файл'} · {size_txt}\nИзменён: {modified}")
        except OSError:
            self.preview_info_label.setText("")

        if not is_dir and ext in TEXT_PREVIEW_EXTS:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    snippet = f.read(500)
                self.preview_text_label.setText(snippet)
            except Exception:
                pass

    # --- горячие клавиши ---------------------------------------------------
    def setup_shortcuts(self):
        QShortcut(QKeySequence.Delete, self, self.delete_shortcut)
        QShortcut(QKeySequence.Copy, self, self.copy_shortcut)
        QShortcut(QKeySequence.Cut, self, self.cut_shortcut)
        QShortcut(QKeySequence.Paste, self, self.paste_shortcut)
        QShortcut(QKeySequence("F2"), self, self.rename_shortcut)
        QShortcut(QKeySequence("Ctrl+N"), self, self.create_folder)
        QShortcut(QKeySequence("Ctrl+Shift+N"), self, self.create_file)
        QShortcut(QKeySequence("Ctrl+H"), self, lambda: self.current_tab().toggle_hidden())
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self.current_tab().go_back())
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self.current_tab().go_forward())
        QShortcut(QKeySequence("Ctrl+A"), self, lambda: self.current_tab().file_list.selectAll())
        QShortcut(QKeySequence("Ctrl+L"), self, self.focus_search)
        QShortcut(QKeySequence("F5"), self, lambda: self.current_tab().load_files())
        QShortcut(QKeySequence("Ctrl+T"), self, lambda: self.add_new_tab())
        QShortcut(QKeySequence("Ctrl+W"), self, lambda: self.close_tab(self.tab_widget.currentIndex()))
        QShortcut(QKeySequence("Ctrl+Tab"), self, self.next_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, self.prev_tab)
        QShortcut(QKeySequence("Ctrl+P"), self, lambda: self.toggle_preview_panel())
        QShortcut(QKeySequence("Ctrl+Z"), self, self.perform_undo)

    def focus_search(self):
        self.current_tab().search_input.setFocus()
        self.current_tab().search_input.selectAll()

    def next_tab(self):
        count = self.tab_widget.count()
        if count > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() + 1) % count)

    def prev_tab(self):
        count = self.tab_widget.count()
        if count > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() - 1) % count)

    # --- undo ---------------------------------------------------------
    def push_undo(self, kind, **data):
        self.undo_stack.append({"kind": kind, **data})
        if len(self.undo_stack) > 25:
            self.undo_stack.pop(0)

    def perform_undo(self):
        if not self.undo_stack:
            return
        action = self.undo_stack.pop()
        kind = action["kind"]
        try:
            if kind == "rename":
                if os.path.exists(action["new_path"]):
                    os.rename(action["new_path"], action["old_path"])
            elif kind in ("move", "paste_move"):
                for src, dest in action["pairs"]:
                    if os.path.exists(dest):
                        shutil.move(dest, src)
            elif kind == "paste_copy":
                for _src, dest in action["pairs"]:
                    if os.path.exists(dest):
                        if os.path.isdir(dest):
                            shutil.rmtree(dest)
                        else:
                            os.remove(dest)
            elif kind in ("create_folder", "create_file"):
                p = action["path"]
                if os.path.exists(p):
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
        except Exception as e:
            QMessageBox.warning(self, "Отмена", f"Не удалось отменить действие:\n{e}")

        for i in range(self.tab_widget.count()):
            self.tab_widget.widget(i).load_files()

    # --- файловые операции (действуют на активной вкладке) -----------------
    def process_drop_files(self, src_paths, target_dir, tab, is_move=False):
        kind = "move" if is_move else "paste_copy"
        self.run_async_file_operation(src_paths, target_dir, tab, is_move, undo_kind=kind)

    def run_async_file_operation(self, src_paths, target_dir, tab, is_move=False, undo_kind=None):
        pairs = [(src, os.path.join(target_dir, os.path.basename(src)))
                 for src in src_paths if os.path.exists(src) and src != os.path.join(target_dir, os.path.basename(src))]
        self.pending_undo = {"kind": undo_kind, "pairs": pairs} if undo_kind and pairs else None
        self.pending_tab = tab

        self.prog_dialog = ProgressDialog(self)
        self.prog_dialog.show()

        self.op_thread = FileOpThread(src_paths, target_dir, is_move)
        self.op_thread.progress.connect(self.prog_dialog.update_progress)
        self.op_thread.finished.connect(self.on_op_finished)
        self.op_thread.error.connect(self.on_op_error)
        self.op_thread.start()

    def on_op_finished(self):
        if self.prog_dialog:
            self.prog_dialog.close()
        if self.pending_undo:
            self.push_undo(self.pending_undo["kind"], pairs=self.pending_undo["pairs"])
            self.pending_undo = None
        for i in range(self.tab_widget.count()):
            self.tab_widget.widget(i).load_files()

    def on_op_error(self, err_msg):
        if self.prog_dialog:
            self.prog_dialog.close()
        self.pending_undo = None
        QMessageBox.critical(self, "Ошибка операции", f"Произошла ошибка:\n\n{err_msg}")

    def show_context_menu(self, tab, position):
        menu = QMenu(self)
        selected_items = tab.file_list.selectedItems()

        if selected_items:
            compress_action = menu.addAction("🗜️ Сжать в ZIP-архив")

            rename_action = None
            run_action = None
            edit_action = None
            extract_action = None
            copy_img_action = None
            prop_action = None
            open_new_tab_action = None
            path = None

            if len(selected_items) == 1:
                path = selected_items[0].data(Qt.UserRole)
                ext = os.path.splitext(path)[1].lower()

                if os.path.isdir(path):
                    open_new_tab_action = menu.addAction("🗂️ Открыть в новой вкладке")

                rename_action = menu.addAction("✏️ Переименовать (F2)")

                if ext in IMG_EXTS:
                    copy_img_action = menu.addAction("📋 Копировать картинку")

                if ext == ".py":
                    run_action = menu.addAction("▶️ Запустить скрипт")
                    edit_action = menu.addAction("📝 Редактировать код")

                if ext == ".zip":
                    extract_action = menu.addAction("📂 Распаковать здесь")

            copy_action = menu.addAction("📋 Копировать (Ctrl+C)")
            cut_action = menu.addAction("✂️ Вырезать (Ctrl+X)")
            delete_action = menu.addAction(f"🗑️ Удалить ({len(selected_items)}) (Del)")

            menu.addSeparator()
            if len(selected_items) == 1:
                prop_action = menu.addAction("ℹ️ Свойства")

            action = menu.exec(tab.file_list.mapToGlobal(position))

            if compress_action and action == compress_action:
                self.compress_to_zip(tab, [item.data(Qt.UserRole) for item in selected_items])
            elif open_new_tab_action and action == open_new_tab_action:
                self.add_new_tab(path)
            elif rename_action and action == rename_action:
                self.rename_shortcut()
            elif copy_img_action and action == copy_img_action:
                self.copy_image_to_clipboard(path)
            elif run_action and action == run_action:
                self.run_python_file(path)
            elif edit_action and action == edit_action:
                self.child_window = TextEditor(path)
                self.child_window.show()
            elif extract_action and action == extract_action:
                self.extract_zip(tab, path)
            elif action == copy_action:
                self.copy_shortcut()
            elif action == cut_action:
                self.cut_shortcut()
            elif action == delete_action:
                self.delete_shortcut()
            elif prop_action and action == prop_action:
                self.show_properties_dialog(selected_items[0].data(Qt.UserRole))

        else:
            create_folder_action = menu.addAction("📁 Создать папку (Ctrl+N)")
            create_file_action = menu.addAction("📄 Создать файл (Ctrl+Shift+N)")
            paste_action = menu.addAction("📋 Вставить (Ctrl+V)")

            clipboard = QApplication.clipboard()
            paste_action.setEnabled(len(self.clipboard_paths) > 0 or clipboard.mimeData().hasImage())

            action = menu.exec(tab.file_list.mapToGlobal(position))

            if action == create_folder_action:
                self.create_folder()
            elif action == create_file_action:
                self.create_file()
            elif action == paste_action:
                self.paste_shortcut()

    def show_properties_dialog(self, path):
        try:
            st = os.stat(path)
            size = format_size(st.st_size) if os.path.isfile(path) else "—"
            created = datetime.fromtimestamp(st.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
            modified = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            perms = stat.filemode(st.st_mode)
            emoji = get_emoji_for(path, os.path.isdir(path))

            msg = (
                f"<b>{emoji} Имя:</b> {os.path.basename(path)}<br>"
                f"<b>Путь:</b> {path}<br>"
                f"<b>Тип:</b> {'Директория' if os.path.isdir(path) else 'Файл'}<br>"
                f"<b>Размер:</b> {size}<br><br>"
                f"<b>Изменен:</b> {modified}<br>"
                f"<b>Создан:</b> {created}<br>"
                f"<b>Права доступа:</b> {perms}"
            )
            QMessageBox.information(self, f"Свойства — {os.path.basename(path)}", msg)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать свойства:\n{e}")

    def copy_image_to_clipboard(self, path):
        img = QImage(path)
        if not img.isNull():
            QApplication.clipboard().setImage(img)
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось скопировать изображение")

    def run_python_file(self, path):
        try:
            folder = os.path.dirname(path)
            subprocess.Popen([sys.executable, path], cwd=folder)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка запуска", f"Не удалось запустить файл:\n\n{e}")

    def extract_zip(self, tab, path):
        target_dir = os.path.splitext(path)[0]
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                zf.extractall(target_dir)
            tab.load_files()
            QMessageBox.information(self, "Успех", f"Распаковано в:\n{os.path.basename(target_dir)}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось распаковать:\n\n{e}")

    def compress_to_zip(self, tab, paths):
        if not paths:
            return

        default_name = os.path.basename(paths[0]) + ".zip" if len(paths) == 1 else "archive.zip"
        archive_name, ok = QInputDialog.getText(
            self, "Сжатие в ZIP", "Введите имя архива:", QLineEdit.Normal, default_name
        )

        if ok and archive_name:
            if not archive_name.endswith(".zip"):
                archive_name += ".zip"

            zip_path = os.path.join(tab.current_path, archive_name)

            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for src in paths:
                        if os.path.isfile(src):
                            zf.write(src, os.path.basename(src))
                        elif os.path.isdir(src):
                            for root, dirs, files in os.walk(src):
                                for file in files:
                                    full_file_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(full_file_path, os.path.dirname(src))
                                    zf.write(full_file_path, rel_path)

                tab.load_files()
                QMessageBox.information(self, "Успех", f"Архив создан:\n{archive_name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать архив:\n\n{e}")

    def create_folder(self):
        tab = self.current_tab()
        name, ok = QInputDialog.getText(self, "Новая папка", "Введите название папки:")
        if ok and name:
            new_path = os.path.join(tab.current_path, name)
            try:
                os.makedirs(new_path, exist_ok=True)
                self.push_undo("create_folder", path=new_path)
                tab.load_files()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать папку:\n{e}")

    def create_file(self):
        tab = self.current_tab()
        name, ok = QInputDialog.getText(self, "Новый файл", "Введите название файла:")
        if ok and name:
            new_path = os.path.join(tab.current_path, name)
            try:
                with open(new_path, "w", encoding="utf-8"):
                    pass
                self.push_undo("create_file", path=new_path)
                tab.load_files()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать файл:\n{e}")

    def rename_shortcut(self):
        tab = self.current_tab()
        selected = tab.file_list.selectedItems()
        if len(selected) == 1:
            old_path = selected[0].data(Qt.UserRole)
            old_name = os.path.basename(old_path)
            new_name, ok = QInputDialog.getText(self, "Переименование", "Новое имя:", QLineEdit.Normal, old_name)
            if ok and new_name and new_name != old_name:
                new_path = os.path.join(tab.current_path, new_name)
                try:
                    os.rename(old_path, new_path)
                    self.push_undo("rename", old_path=old_path, new_path=new_path)
                    tab.load_files()
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка", f"Не удалось переименовать:\n{e}")

    def delete_shortcut(self):
        tab = self.current_tab()
        selected_items = tab.file_list.selectedItems()
        if not selected_items:
            return

        count = len(selected_items)
        msg_type = "в системную корзину" if HAS_SEND2TRASH else "навсегда (без возможности отмены)"
        message = f"Отправить в {msg_type} ({count} объектов)?"

        reply = QMessageBox.question(
            self, "Удаление", message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            for item in selected_items:
                path = item.data(Qt.UserRole)
                try:
                    if HAS_SEND2TRASH:
                        send2trash(path)
                    else:
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка", f"Не удалось удалить {os.path.basename(path)}:\n{e}")
            tab.load_files()

    def copy_shortcut(self):
        selected_items = self.current_tab().file_list.selectedItems()
        if selected_items:
            self.clipboard_paths = [item.data(Qt.UserRole) for item in selected_items]
            self.is_cut_operation = False

    def cut_shortcut(self):
        selected_items = self.current_tab().file_list.selectedItems()
        if selected_items:
            self.clipboard_paths = [item.data(Qt.UserRole) for item in selected_items]
            self.is_cut_operation = True

    def paste_shortcut(self):
        tab = self.current_tab()
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()

        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"pasted_image_{time_str}.png"
                dest_path = os.path.join(tab.current_path, filename)
                try:
                    image.save(dest_path, "PNG")
                    self.push_undo("create_file", path=dest_path)
                    tab.load_files()
                    return
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить из буфера:\n{e}")

        if not self.clipboard_paths:
            return

        kind = "paste_move" if self.is_cut_operation else "paste_copy"
        self.run_async_file_operation(
            self.clipboard_paths, tab.current_path, tab, self.is_cut_operation, undo_kind=kind
        )

        if self.is_cut_operation:
            self.clipboard_paths = []

    def open_item(self, item):
        path = item.data(Qt.UserRole)

        if os.path.isdir(path):
            self.current_tab().go_to(path)
            return

        ext = os.path.splitext(path)[1].lower()

        if ext == ".py":
            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.ControlModifier:
                self.child_window = TextEditor(path)
                self.child_window.show()
            else:
                self.run_python_file(path)
            return

        if ext == ".zip":
            self.child_window = ZipViewer(path)
            self.child_window.show()
            return

        text_exts = (".txt", ".json", ".md", ".csv", ".html", ".css", ".js", ".cpp", ".c", ".java", ".sh", ".conf", ".ini")

        if ext in IMG_EXTS:
            self.child_window = ImageViewer(path)
            self.child_window.show()
        elif ext in text_exts:
            self.child_window = TextEditor(path)
            self.child_window.show()
        else:
            QMessageBox.information(self, "Файл", f"Не удалось распознать формат:\n\n{os.path.basename(path)}")

    def closeEvent(self, event):
        try:
            self.settings.setValue("last_path", self.current_tab().current_path)
        except Exception:
            pass
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FileManager()
    window.show()
    sys.exit(app.exec())
