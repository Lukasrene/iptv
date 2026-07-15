from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import subprocess

from m3u_player.caster import CastManager
from m3u_player.hls import to_hls_url
from m3u_player.playlist import Channel, parse_m3u
from m3u_player.proxy import HlsProxy
from m3u_player.source import load_playlist_text
from m3u_player.store import Config, default_config_path

FAVORITES_LABEL = "★ Favorites"


class KeepAwake:
    """Prevents the Mac from sleeping while active (via `caffeinate`), so the
    local cast relay keeps running. No-op off macOS."""

    def __init__(self):
        self._proc = None

    def on(self) -> None:
        if self._proc is None and sys.platform == "darwin":
            try:
                self._proc = subprocess.Popen(["caffeinate", "-i", "-m", "-s"])
            except Exception:
                self._proc = None

    def off(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


def cache_path() -> Path:
    return default_config_path().parent / "playlist_cache.m3u"


# --------------------------------------------------------------------------- #
# Channel list model
# --------------------------------------------------------------------------- #
class ChannelListModel(QAbstractListModel):
    """Holds the currently displayed channels. Cheap for tens of thousands of
    rows because it only ever stores a plain Python list and renders on demand."""

    def __init__(self, config: Config):
        super().__init__()
        self._channels: list[Channel] = []
        self._config = config

    def set_channels(self, channels: list[Channel]) -> None:
        self.beginResetModel()
        self._channels = channels
        self.endResetModel()

    def channel_at(self, index: QModelIndex) -> Channel | None:
        if index.isValid() and 0 <= index.row() < len(self._channels):
            return self._channels[index.row()]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._channels)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        c = self._channels[index.row()]
        if role == Qt.DisplayRole:
            star = "★ " if self._config.is_favorite(c) else ""
            return f"{star}{c.name}"
        return None

    def refresh(self) -> None:
        if self._channels:
            top = self.index(0)
            bottom = self.index(len(self._channels) - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole])


# --------------------------------------------------------------------------- #
# Background playlist loader
# --------------------------------------------------------------------------- #
class LoaderThread(QThread):
    loaded = Signal(object)   # list[Channel]
    failed = Signal(str)

    def __init__(self, source: dict):
        super().__init__()
        self._source = source

    def run(self) -> None:
        try:
            text = load_playlist_text(self._source, cache_path())
            channels = parse_m3u(text)
            self.loaded.emit(channels)
        except Exception as exc:  # surfaced to the UI thread
            self.failed.emit(str(exc))


class CastWorker(QThread):
    """Runs a blocking cast/stop call off the UI thread."""

    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.ok.emit(result or "")
        except Exception as exc:
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------- #
# Open dialog (URL or file)
# --------------------------------------------------------------------------- #
class OpenDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open playlist")
        self.setMinimumWidth(460)
        self._file_path: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste a playlist URL (e.g. your provider's m3u link):"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://provider/get.php?username=…&type=m3u_plus")
        layout.addWidget(self.url_edit)

        layout.addWidget(QLabel("…or choose a local .m3u file:"))
        file_row = QHBoxLayout()
        self.file_label = QLabel("No file chosen")
        self.file_label.setStyleSheet("color: gray;")
        choose = QPushButton("Choose file…")
        choose.clicked.connect(self._choose_file)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(choose)
        layout.addLayout(file_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose playlist", "", "Playlists (*.m3u *.m3u8);;All files (*)"
        )
        if path:
            self._file_path = path
            self.file_label.setText(Path(path).name)
            self.file_label.setStyleSheet("")

    def source(self) -> dict | None:
        url = self.url_edit.text().strip()
        if url:
            return {"type": "url", "value": url}
        if self._file_path:
            return {"type": "file", "value": self._file_path}
        return None


# --------------------------------------------------------------------------- #
# Video frame (native window for VLC + double-click to toggle fullscreen)
# --------------------------------------------------------------------------- #
class VideoFrame(QWidget):
    doubleClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("black"))
        self.setPalette(pal)

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.player = None  # bound after show()
        self.all_channels: list[Channel] = []
        self.group_map: dict[str, list[Channel]] = {}
        self.current_group: str | None = None
        self._loader: LoaderThread | None = None
        self._is_fullscreen = False
        self.cast_manager = CastManager()
        self.proxy = HlsProxy()
        self._keep_awake = KeepAwake()
        self._cast_workers: list[CastWorker] = []

        self.setWindowTitle("M3U Player")
        self.resize(1100, 700)

        self._build_ui()
        self._build_toolbar()

    # ---- UI construction ---------------------------------------------- #
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)

        # Left: groups
        self.group_list = QListWidget()
        self.group_list.currentTextChanged.connect(self._on_group_changed)
        splitter.addWidget(self.group_list)

        # Middle: search + channels
        middle = QWidget()
        mlayout = QVBoxLayout(middle)
        mlayout.setContentsMargins(0, 0, 0, 0)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search channels…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_text)
        mlayout.addWidget(self.search_edit)

        self.channel_model = ChannelListModel(self.config)
        self.channel_view = QListView()
        self.channel_view.setModel(self.channel_model)
        self.channel_view.setUniformItemSizes(True)
        self.channel_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.channel_view.customContextMenuRequested.connect(self._on_channel_menu)
        self.channel_view.doubleClicked.connect(self._on_channel_activated)
        mlayout.addWidget(self.channel_view)
        splitter.addWidget(middle)

        # Right: video + controls
        right = QWidget()
        rlayout = QVBoxLayout(right)
        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setStyleSheet("font-weight: bold; padding: 2px;")
        rlayout.addWidget(self.now_playing)

        self.video_frame = VideoFrame()
        self.video_frame.doubleClicked.connect(self._toggle_fullscreen)
        rlayout.addWidget(self.video_frame, 1)

        self.controls_widget = QWidget()
        controls = QHBoxLayout(self.controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self._on_play_clicked)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._on_stop_clicked)
        fs_btn = QPushButton("⛶")
        fs_btn.clicked.connect(self._toggle_fullscreen)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.valueChanged.connect(self._on_volume)
        controls.addWidget(self.play_btn)
        controls.addWidget(stop_btn)
        controls.addWidget(QLabel("🔊"))
        controls.addWidget(self.volume, 1)
        controls.addWidget(fs_btn)
        rlayout.addWidget(self.controls_widget)
        splitter.addWidget(right)

        splitter.setSizes([220, 320, 560])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

        # widgets hidden when the video goes fullscreen
        self._chrome = [self.group_list, middle, self.now_playing, self.controls_widget]

        # search debounce
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        self._toolbar = tb
        open_action = QAction("Open…", self)
        open_action.triggered.connect(self._open_dialog)
        tb.addAction(open_action)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._refresh)
        tb.addAction(refresh_action)

    # ---- player binding (after the native window exists) --------------- #
    def bind_player(self, player) -> None:
        self.player = player
        self.player.bind_window(self.video_frame)
        self.player.set_volume(self.volume.value())

    # ---- loading ------------------------------------------------------- #
    def start_load(self, source: dict) -> None:
        self.config.source = source
        self.config.save()
        self.statusBar().showMessage("Loading playlist…")
        self.group_list.setEnabled(False)
        self._loader = LoaderThread(source)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_loaded(self, channels: list[Channel]) -> None:
        self.all_channels = channels
        self.group_map = {}
        for c in channels:
            self.group_map.setdefault(c.group, []).append(c)

        self.group_list.blockSignals(True)
        self.group_list.clear()
        self.group_list.addItem(FAVORITES_LABEL)
        for group in sorted(self.group_map):
            self.group_list.addItem(group)
        self.group_list.blockSignals(False)
        self.group_list.setEnabled(True)
        self.statusBar().showMessage(
            f"{len(channels)} channels in {len(self.group_map)} groups"
        )
        self._restore_last_selection()

    def _on_load_failed(self, message: str) -> None:
        self.group_list.setEnabled(True)
        self.statusBar().showMessage("Couldn't load playlist")
        QMessageBox.warning(self, "Load failed", f"Couldn't load the playlist:\n{message}")

    def _restore_last_selection(self) -> None:
        target = self.config.last_group
        if target and target in self.group_map:
            items = self.group_list.findItems(target, Qt.MatchExactly)
            if items:
                self.group_list.setCurrentItem(items[0])
        elif self.group_list.count():
            self.group_list.setCurrentRow(0)

        watched = self.config.last_watched
        if watched:
            for row in range(self.channel_model.rowCount()):
                c = self.channel_model.channel_at(self.channel_model.index(row))
                if c and c.name == watched.get("name") and c.group == watched.get("group"):
                    self.channel_view.setCurrentIndex(self.channel_model.index(row))
                    break

    # ---- group / search ------------------------------------------------ #
    def _on_group_changed(self, group: str) -> None:
        self.current_group = group
        self.search_edit.clear()  # clearing triggers _apply_search -> shows group
        self._show_current_group()

    def _show_current_group(self) -> None:
        if self.current_group == FAVORITES_LABEL:
            channels = [c for c in self.all_channels if self.config.is_favorite(c)]
        else:
            channels = self.group_map.get(self.current_group, [])
        self.channel_model.set_channels(channels)

    def _on_search_text(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_search(self) -> None:
        text = self.search_edit.text().strip().lower()
        if not text:
            self._show_current_group()
            return
        matches = [c for c in self.all_channels if text in c.name.lower()]
        self.channel_model.set_channels(matches)

    # ---- playback ------------------------------------------------------ #
    def _selected_channel(self) -> Channel | None:
        return self.channel_model.channel_at(self.channel_view.currentIndex())

    def _on_channel_activated(self, index: QModelIndex) -> None:
        self._play(self.channel_model.channel_at(index))

    def _on_play_clicked(self) -> None:
        self._play(self._selected_channel())

    def _play(self, channel: Channel | None) -> None:
        if channel is None or self.player is None:
            return
        try:
            self.player.play(channel.url)
        except Exception as exc:
            self.statusBar().showMessage("Couldn't play this channel")
            QMessageBox.warning(self, "Playback error", str(exc))
            return
        self.now_playing.setText(channel.name)
        self.config.last_watched = {
            "group": channel.group,
            "name": channel.name,
            "stream_id": channel.stream_id,
        }
        self.config.save()

    def _on_stop_clicked(self) -> None:
        if self.player:
            self.player.stop()
        self.now_playing.setText("Nothing playing")

    def _on_volume(self, value: int) -> None:
        if self.player:
            self.player.set_volume(value)

    def _toggle_fullscreen(self) -> None:
        # VLC's own fullscreen is a no-op for an embedded surface on macOS, so we
        # do it at the window level: hide all chrome and let the video fill the
        # screen. The video surface is never reparented, so VLC's binding holds.
        if not self._is_fullscreen:
            for w in self._chrome:
                w.hide()
            self._toolbar.hide()
            self.statusBar().hide()
            self.showFullScreen()
            self._is_fullscreen = True
        else:
            for w in self._chrome:
                w.show()
            self._toolbar.show()
            self.statusBar().show()
            self.showNormal()
            self._is_fullscreen = False

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._is_fullscreen:
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    # ---- context menu: favorites + cast -------------------------------- #
    def _on_channel_menu(self, pos) -> None:
        index = self.channel_view.indexAt(pos)
        channel = self.channel_model.channel_at(index)
        if channel is None:
            return
        menu = QMenu(self)
        fav_action = menu.addAction(
            "Remove from favorites"
            if self.config.is_favorite(channel)
            else "Add to favorites"
        )

        cast_menu = menu.addMenu("Cast")
        device_actions = {}
        devices = self.cast_manager.list_devices()
        if devices:
            for uuid_str, name in devices:
                device_actions[cast_menu.addAction(name)] = uuid_str
        else:
            searching = cast_menu.addAction("Searching…")
            searching.setEnabled(False)
        cast_menu.addSeparator()
        rescan_action = cast_menu.addAction("Rescan")
        stop_cast_action = (
            menu.addAction("Stop casting") if self.cast_manager.is_active() else None
        )

        chosen = menu.exec(self.channel_view.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == fav_action:
            self.config.toggle_favorite(channel)
            self.config.save()
            if self.current_group == FAVORITES_LABEL:
                self._show_current_group()
            else:
                self.channel_model.refresh()
        elif chosen in device_actions:
            self._start_cast(channel, device_actions[chosen])
        elif chosen == rescan_action:
            self.cast_manager.rescan()
            self.statusBar().showMessage("Rescanning for cast devices…")
        elif stop_cast_action is not None and chosen == stop_cast_action:
            self._stop_cast()

    # ---- casting ------------------------------------------------------- #
    def _run_cast_worker(self, fn, on_ok, on_failed) -> None:
        worker = CastWorker(fn)
        worker.ok.connect(on_ok)
        worker.failed.connect(on_failed)
        worker.finished.connect(lambda w=worker: self._cast_workers.remove(w))
        self._cast_workers.append(worker)
        worker.start()

    def _start_cast(self, channel: Channel, uuid_str: str) -> None:
        if self.player:
            self.player.stop()
        # Route the stream through the local relay (the provider's redirect +
        # session-hashed segments can't be played by the Chromecast directly),
        # and keep the Mac awake so the relay stays alive while casting.
        self.proxy.set_source(to_hls_url(channel.url))
        try:
            self.proxy.start()
        except Exception as exc:
            self._on_cast_failed(f"Couldn't start local relay: {exc}")
            return
        local_url = self.proxy.manifest_url()
        self._keep_awake.on()
        self.now_playing.setText(f"📺 Casting '{channel.name}'…")
        self.statusBar().showMessage("Connecting to cast device…")
        self.config.last_watched = {
            "group": channel.group,
            "name": channel.name,
            "stream_id": channel.stream_id,
        }
        self.config.save()
        self._run_cast_worker(
            lambda: self.cast_manager.cast(uuid_str, local_url, channel.name),
            lambda device: self._on_cast_ok(channel, device),
            self._on_cast_failed,
        )

    def _on_cast_ok(self, channel: Channel, device: str) -> None:
        self.now_playing.setText(f"📺 Casting '{channel.name}' to {device}")
        self.statusBar().showMessage(f"Casting to {device}")

    def _on_cast_failed(self, message: str) -> None:
        self._keep_awake.off()
        self.now_playing.setText("Nothing playing")
        self.statusBar().showMessage("Couldn't cast to device")
        QMessageBox.warning(self, "Cast failed", message)

    def _stop_cast(self) -> None:
        self.statusBar().showMessage("Stopping cast…")
        self._run_cast_worker(
            lambda: (self.cast_manager.stop(), "")[1],
            lambda _: self._on_cast_stopped(),
            lambda _: self._on_cast_stopped(),
        )

    def _on_cast_stopped(self) -> None:
        self._keep_awake.off()
        self.now_playing.setText("Nothing playing")
        self.statusBar().showMessage("Cast stopped")

    def closeEvent(self, event) -> None:
        self._keep_awake.off()
        for closer in (self.proxy.stop, self.cast_manager.shutdown):
            try:
                closer()
            except Exception:
                pass
        super().closeEvent(event)

    # ---- open / refresh ------------------------------------------------ #
    def _open_dialog(self) -> None:
        dialog = OpenDialog(self)
        if dialog.exec() == QDialog.Accepted:
            source = dialog.source()
            if source:
                self.start_load(source)
            else:
                QMessageBox.information(
                    self, "Nothing selected", "Enter a URL or choose a file."
                )

    def _refresh(self) -> None:
        if self.config.source:
            self.start_load(self.config.source)
        else:
            self._open_dialog()


def main() -> None:
    app = QApplication(sys.argv)
    config = Config.load()

    from m3u_player.player import Player

    window = MainWindow(config)
    window.show()
    window.bind_player(Player())
    window.cast_manager.start_discovery()

    if config.source:
        window.start_load(config.source)
    else:
        window._open_dialog()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
