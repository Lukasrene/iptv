from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QPalette, QPixmap
from PySide6.QtMultimediaWidgets import QVideoWidget
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
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import subprocess
import time
from uuid import uuid4

from m3u_player.caster import CastManager
from m3u_player.playlist import Channel, parse_m3u
from m3u_player.proxy import LIVE_START_OFFSET, HlsProxy
from m3u_player.recorder import Recorder
from m3u_player.source import load_playlist_text
from m3u_player.store import Config, default_config_path
from m3u_player.thumbnails import Thumbnailer
from m3u_player.transport import (
    LIVE_EPSILON,
    ReplaySkip,
    SeekAccumulator,
    SmoothedClock,
    StallDetector,
    clamp_seek,
    hover_label,
    signed_delta_label,
)

FAVORITES_LABEL = "★ Favorites"

# How close to the end of the armed VOD snapshot playback may get before we
# re-arm against a fresher one. A re-arm costs a seek (~0.2s), so this trades
# a small periodic hitch against playback hitting the end of the snapshot and
# stopping. Raise it if re-arms become audible.
REARM_MARGIN = 12.0

# Segments that must exist before pointing the player at the relay. The
# provider bursts its ~20s backlog on connect, so this many land in ~1.4s and
# first video follows at ~2.5-3.0s (measured; the raw provider stream managed
# ~3.4s at best). The live manifest's start offset clamps to what is buffered,
# and the tail of the burst builds the full cushion within a few seconds of
# playback starting.
START_MIN_SEGMENTS = 3


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
# Video frame (Qt-owned video surface + double-click to toggle fullscreen)
# --------------------------------------------------------------------------- #
class VideoFrame(QVideoWidget):
    doubleClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # focusable so playback shortcuts reach the window (clicking the video
        # takes focus away from the channel list, where arrows navigate instead)
        self.setFocusPolicy(Qt.StrongFocus)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("black"))
        self.setPalette(pal)

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()


class Timeline(QSlider):
    """DVR scrubber. Range is 0..1000 (a fraction of the buffered window). Emits
    ``seekRequested(fraction)`` on click/drag, and ``hovered(fraction, globalPos)``
    while the pointer moves over it so a scene preview can follow the cursor."""

    seekRequested = Signal(float)
    hovered = Signal(float, object)
    hoverExited = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 1000)
        self.setMouseTracking(True)
        self.setEnabled(False)
        self.sliderReleased.connect(lambda: self.seekRequested.emit(self.value() / 1000.0))

    def _fraction_at(self, x: float) -> float:
        w = max(1, self.width())
        return min(1.0, max(0.0, x / w))

    def mouseMoveEvent(self, event) -> None:
        frac = self._fraction_at(event.position().x())
        self.hovered.emit(frac, event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.hoverExited.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        frac = self._fraction_at(event.position().x())
        self.setValue(int(frac * 1000))
        self.seekRequested.emit(frac)
        super().mousePressEvent(event)


class HoverPreview(QWidget):
    """Floating scene preview (thumbnail + jump time) shown above the timeline
    while hovering."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._img = QLabel()
        self._img.setFixedSize(160, 90)
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet("background:#000; border-radius:4px; color:#888;")
        self._txt = QLabel()
        self._txt.setAlignment(Qt.AlignCenter)
        self._txt.setStyleSheet("color:white; font-weight:bold;")
        lay.addWidget(self._img)
        lay.addWidget(self._txt)
        self.setStyleSheet("background: rgba(24,24,24,235); border-radius:8px;")

    def show_at(self, global_pos, pixmap, text: str) -> None:
        if pixmap is not None and not pixmap.isNull():
            self._img.setPixmap(
                pixmap.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self._img.setPixmap(QPixmap())
            self._img.setText("…")
        self._txt.setText(text)
        self.adjustSize()
        self.move(global_pos.x() - self.width() // 2, global_pos.y() - self.height() - 16)
        self.show()


class ControlOverlay(QWidget):
    """Floating, draggable transport controls shown over the video in fullscreen.

    It's a top-level frameless window so it reliably paints above VLC's native
    video surface — a plain child widget can be obscured by the native view on
    macOS. Drag it anywhere by its grip or background.
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # must never take focus, or it would swallow the playback shortcuts
        self.setFocusPolicy(Qt.NoFocus)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 6, 12, 12)
        self._lay.setSpacing(8)
        self._grip = QLabel("⠿  drag to move")
        self._grip.setAlignment(Qt.AlignCenter)
        self._grip.setStyleSheet("color:#bbb; font-size:11px;")
        self._grip.setFixedHeight(16)  # own row — never overlaps the timeline
        self._grip.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # let clicks on the grip fall through so they start a drag
        self._grip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lay.addWidget(self._grip, 0)
        self._drag_off = None
        self.setMinimumWidth(620)
        self.setStyleSheet("background: rgba(24,24,24,228); border-radius:10px;")

    def adopt(self, *widgets) -> None:
        for w in widgets:
            self._lay.addWidget(w, 0)
            w.show()
        self._lay.activate()
        self.adjustSize()

    def mousePressEvent(self, event) -> None:
        self._drag_off = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_off is not None:
            self.move(event.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_off = None


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
        self._recorder: Recorder | None = None
        self._thumbnailer: Thumbnailer | None = None
        self._active_channel: Channel | None = None
        self._is_casting = False
        self._seek_acc = SeekAccumulator()
        # "starting" = recorder spinning up, player not armed yet
        # "live"     = local relay, open-ended manifest, following live
        # "dvr"      = local relay, VOD snapshot, rewound and seekable
        self._play_mode = "starting"
        self._pending_dvr_seek: float | None = None
        self._pause_after_switch = False
        self._live_clock = SmoothedClock()
        self._cast_clock = SmoothedClock(resync_threshold=4.0)
        self._stall = StallDetector()
        self._replay_skip = ReplaySkip()
        self._overlay_pos = None  # remembered fullscreen control position

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

        # DVR timeline row
        self.timeline_widget = QWidget()
        tlayout = QHBoxLayout(self.timeline_widget)
        tlayout.setContentsMargins(0, 0, 0, 0)
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self._on_timeline_seek)
        self.timeline.hovered.connect(self._on_timeline_hover)
        self.timeline.hoverExited.connect(self._on_timeline_hover_exit)
        self._hover_preview = HoverPreview(self)
        self.behind_live_label = QLabel("")
        self.behind_live_label.setMinimumWidth(120)
        self.behind_live_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tlayout.addWidget(self.timeline, 1)
        tlayout.addWidget(self.behind_live_label)
        rlayout.addWidget(self.timeline_widget)

        self.controls_widget = QWidget()
        controls = QHBoxLayout(self.controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        live_btn = QPushButton("Live ⏭")
        live_btn.setToolTip("Jump forward to the live edge")
        live_btn.clicked.connect(self._seek_live)
        back5_btn = QPushButton("−5s")
        back5_btn.setToolTip("Back 5 seconds (←)")
        back5_btn.clicked.connect(lambda: self._seek_step(-5))
        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setToolTip("Play / pause (Space)")
        self.pause_btn.clicked.connect(self._pause_toggle)
        fwd5_btn = QPushButton("+5s")
        fwd5_btn.setToolTip("Forward 5 seconds (→)")
        fwd5_btn.clicked.connect(lambda: self._seek_step(5))
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self._on_play_clicked)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._on_stop_clicked)
        fs_btn = QPushButton("⛶")
        fs_btn.setToolTip("Toggle fullscreen (F, Esc to leave)")
        fs_btn.clicked.connect(self._toggle_fullscreen)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(120)
        self.volume.valueChanged.connect(self._on_volume)
        for w in (live_btn, back5_btn, self.pause_btn, fwd5_btn, self.play_btn, stop_btn):
            controls.addWidget(w)
        controls.addStretch(1)
        self.volume.setToolTip("Volume (↑ / ↓)")
        controls.addWidget(QLabel("🔊"))
        controls.addWidget(self.volume)
        controls.addWidget(fs_btn)
        # Controls must not take keyboard focus, or Space would press a button
        # and arrows would drag a slider instead of driving playback.
        for w in (live_btn, back5_btn, self.pause_btn, fwd5_btn, self.play_btn,
                  stop_btn, fs_btn, self.volume, self.timeline):
            w.setFocusPolicy(Qt.NoFocus)
        rlayout.addWidget(self.controls_widget)
        splitter.addWidget(right)

        splitter.setSizes([220, 320, 560])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

        # In fullscreen these are hidden; the transport moves to a floating
        # overlay instead of disappearing.
        self._chrome = [self.group_list, middle, self.now_playing]
        self._right_layout = rlayout
        self._overlay = ControlOverlay(self)
        self._overlay.hide()

        # search debounce
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)

        # panel navigation keys (see eventFilter)
        self.group_list.installEventFilter(self)
        self.channel_view.installEventFilter(self)

        # DVR position polling (updates the timeline from the active target)
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(250)  # smooth motion rather than 1s ticks
        self._pos_timer.timeout.connect(self._update_timeline)
        self._pos_timer.start()
        self._window_seconds = 0.0  # current buffered window length
        self._dvr_origin = 0.0  # absolute start of the armed VOD snapshot

        # debounce that fires one coalesced seek after rapid ±5s presses
        self._seek_commit_timer = QTimer(self)
        self._seek_commit_timer.setSingleShot(True)
        self._seek_commit_timer.setInterval(350)
        self._seek_commit_timer.timeout.connect(self._commit_seek)

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
        self.player.bind_surface(self.video_frame)
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

    # ---- DVR stream lifecycle (recorder + relay feed both local and cast) --- #
    def _ensure_stream(self, channel: Channel) -> str:
        """Start (or reuse) the DVR recorder+relay for this channel and return the
        local seekable manifest URL that both VLC and the Chromecast play."""
        if (
            self._recorder is not None
            and self._active_channel is not None
            and self._active_channel.url == channel.url
        ):
            return self.proxy.manifest_url()
        self._teardown_stream()
        dvr_dir = default_config_path().parent / "dvr" / uuid4().hex
        # Feed the raw continuous stream to ffmpeg and re-segment it locally into
        # short segments — that's what lets us sit a few seconds behind live
        # instead of ~30s (see Recorder docstring).
        self._recorder = Recorder(channel.url, dvr_dir)
        self._recorder.start()
        self.proxy.attach_recorder(self._recorder)
        self.proxy.start()
        self._thumbnailer = Thumbnailer(dvr_dir / "thumbs", self._recorder)
        self._thumbnailer.start()
        self._live_clock.reset()
        self._cast_clock.reset()
        self._active_channel = channel
        return self.proxy.manifest_url()

    def _teardown_stream(self) -> None:
        for stopper in (
            self._thumbnailer.stop if self._thumbnailer else None,
            self._recorder.stop if self._recorder else None,
        ):
            if stopper is not None:
                try:
                    stopper()
                except Exception:
                    pass
        self._thumbnailer = None
        self._recorder = None
        self._active_channel = None

    def _play(self, channel: Channel | None) -> None:
        if channel is None or self.player is None:
            return
        try:
            # One provider connection, ever: the account allows a single stream
            # (max_connections=1), so the old raw-stream bootstrap fought the
            # recorder for it. The recorder's backlog burst puts the first
            # segments on disk in ~1.4s, so starting from the relay is as fast
            # as the raw stream was (~2.5-3.0s to first video, measured) —
            # _update_timeline arms the player once enough segments exist.
            #
            # Stop before swapping recorders: the player would otherwise keep
            # demuxing the old channel's manifest while the proxy starts
            # serving the new channel underneath it — measured degrading the
            # next channel to a stutter until well after the re-arm.
            self.player.stop()
            self._ensure_stream(channel)
            self._play_mode = "starting"
            self._pending_dvr_seek = None
            self._pause_after_switch = False
            self._stall.reset()
            self._replay_skip.cancel()
        except Exception as exc:
            self.statusBar().showMessage("Couldn't play this channel")
            QMessageBox.warning(self, "Playback error", str(exc))
            return
        self._is_casting = False
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
        self._teardown_stream()
        self._is_casting = False
        self.now_playing.setText("Nothing playing")

    def _on_volume(self, value: int) -> None:
        if self.player:
            self.player.set_volume(value)

    # ---- DVR transport (acts on local VLC, or the Chromecast when casting) --- #
    # All positions below are *absolute*: seconds since this recording started,
    # not offsets into the current buffer. ffmpeg drops segments off the front of
    # the rolling window, and measuring from the buffer start would slide the
    # whole timeline underneath the viewer each time that happened.
    def _buffer_start(self) -> float:
        """Absolute position of the oldest segment still on disk."""
        return self._recorder.evicted_seconds() if self._recorder is not None else 0.0

    def _safe_live(self) -> float:
        """Furthest actually-playable absolute position (no smoothing)."""
        snap = self._recorder.snapshot() if self._recorder is not None else []
        if not snap:
            return 0.0
        end = self._buffer_start() + sum(s.duration for s in snap)
        margin = max(4.0, 1.2 * snap[-1].duration)
        return max(self._buffer_start(), end - margin)

    def _stream_extents(self) -> tuple[float, float, float]:
        """Return (buffer_start, safe_live, display_live) — all absolute seconds.

        ``safe_live`` is the furthest position that is actually *playable*: the
        start of the newest complete segment, so there is a full segment of
        runway before the next one arrives. That is the minimum safe margin —
        going closer to the edge starves the player, because content only
        arrives one segment at a time.

        ``display_live`` is ``safe_live`` projected with wall-clock time, used
        only for the steady "behind live" readout. It must never be used as a
        seek target: it runs ahead of what has actually been downloaded.
        """
        now = time.monotonic()
        safe_live = self._safe_live()
        display_live = self._live_clock.update(safe_live, now) or 0.0
        return self._buffer_start(), safe_live, display_live

    # ---- timeline maps the buffered span [start, safe_live] onto 0..1 ---- #
    def _fraction_to_abs(self, fraction: float) -> float:
        start, safe_live, _ = self._stream_extents()
        return start + fraction * max(0.0, safe_live - start)

    def _abs_to_fraction(self, pos: float) -> float:
        start, safe_live, _ = self._stream_extents()
        span = safe_live - start
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (pos - start) / span))

    def _current_position(self) -> float:
        now = time.monotonic()
        if self._is_casting and self.cast_manager.is_active():
            raw = self.cast_manager.current_time()
            advancing = not self.cast_manager.is_paused()
            # cast status arrives in bursts; project between pushes so the bar glides
            return self._cast_clock.update(raw, now, advancing=advancing) or 0.0
        if self._play_mode in ("starting", "live"):
            return self._safe_live()  # following live; not a seekable timeline
        if self.player is not None:
            # player time is relative to the armed snapshot's first segment
            return self._dvr_origin + self.player.time_s()
        return 0.0

    def _arm_dvr(self) -> None:
        """Point the player at a fresh VOD snapshot and remember its origin."""
        segs = self._recorder.snapshot()
        self._dvr_origin = self._recorder.origin_for(segs)
        self.player.play(self.proxy.dvr_url())

    def _switch_to_dvr(self, target: float) -> bool:
        """Move local playback onto the DVR relay so it can seek.

        Returns False if the buffer has not built up enough yet.
        """
        if self.player is None or self._recorder is None:
            return False
        start, safe_live, _ = self._stream_extents()
        if safe_live <= start:
            self.statusBar().showMessage("Buffering — rewind available in a moment")
            return False
        if self._play_mode != "dvr":
            self._arm_dvr()
            self._play_mode = "dvr"
        self._pending_dvr_seek = clamp_seek(target, start, safe_live)
        self._replay_skip.cancel()  # rewound: the replayed stretch is now just history
        return True

    def _maybe_start_from_buffer(self) -> None:
        """Arm the player once the recorder has produced enough to start from.

        Playback *only* ever runs off the local relay. It used to bootstrap on
        the raw provider stream for a fast first picture, but the account allows
        a single connection (max_connections=1), so that second connection
        raced the recorder's — the provider kills the older one after a grace
        period. The raw stream also broke up on its own: its program changes
        (ad insertion and the like) made Qt rebuild its decoders — measured 367
        decode errors over 4 minutes, against zero through ffmpeg's remux.

        Waiting for the buffer is no longer slow, because the provider bursts
        its ~20s backlog on connect: the first segments land in ~1.4s and first
        video follows at ~2.5-3.0s (measured — the raw bootstrap managed ~3.4s).

        The open-ended manifest, not a VOD snapshot — at the live edge a
        snapshot ends within seconds of the playhead and would need re-arming
        constantly, and every re-arm is a visible reload.
        """
        if (
            self._play_mode != "starting"
            or self.player is None
            or self._recorder is None
            or self._is_casting
        ):
            return
        if len(self._recorder.snapshot()) < START_MIN_SEGMENTS:
            return
        self.player.play(self.proxy.live_url())
        self._play_mode = "live"

    def _rearm_dvr(self) -> None:
        """Re-point at a fresher snapshot, preserving position.

        A VOD manifest is frozen at the newest segment it listed, so playback
        behind live would otherwise simply stop when it reached that point.
        Re-arming costs one seek (~0.2s), which is why this is tolerable at all.
        If we have already caught up to live, go back to the open-ended live
        manifest instead — it follows the edge without re-arming.
        """
        if self.player is None or self._recorder is None:
            return
        pos = self._current_position()
        _, safe_live, _ = self._stream_extents()
        if pos >= safe_live - LIVE_EPSILON:
            self._seek_live()
            return
        was_paused = self.player.is_paused()
        self._arm_dvr()
        self._pending_dvr_seek = pos
        self._pause_after_switch = was_paused

    def _target_position_length(self) -> tuple[float, float]:
        """(position, display live edge) — for the readout only."""
        _, _, display_live = self._stream_extents()
        return self._current_position(), display_live

    def _seek_to(self, t: float) -> None:
        # Clamp to what is actually downloaded — never the projected edge, or the
        # player waits for content that does not exist yet.
        start, safe_live, _ = self._stream_extents()
        t = clamp_seek(t, start, safe_live)
        if self._is_casting and self.cast_manager.is_active():
            # the cast receiver plays the live manifest, whose timeline still
            # starts at the buffer front
            self.cast_manager.seek(t - start)
            return
        if self._play_mode in ("starting", "live"):
            # rewinding for the first time: hop onto a seekable snapshot
            self._switch_to_dvr(t)
            return
        if self.player is not None:
            if t < self._dvr_origin:
                # older than the armed snapshot — re-arm to reach it
                self._arm_dvr()
                self._pending_dvr_seek = t
                return
            self.player.seek_s(t - self._dvr_origin)

    def _seek_relative(self, delta: float) -> None:
        self._seek_to(self._current_position() + delta)

    def _seek_live(self) -> None:
        self._seek_acc.take()  # cancel any pending stacked step
        if self._is_casting and self.cast_manager.is_active():
            self.cast_manager.seek(self._safe_live() - self._buffer_start())
            return
        # Back onto the open-ended relay manifest — the same source live
        # playback normally uses. Never the raw provider stream (a second
        # provider connection fights the recorder's, and it breaks up — see
        # _maybe_start_from_buffer), and not the armed snapshot (frozen when
        # built, so seeking to its end lands short and then re-arms constantly).
        if self._recorder is not None and self.player is not None:
            self.player.play(self.proxy.live_url())
            self._play_mode = "live"
            self._pending_dvr_seek = None
            self._pause_after_switch = False
            self._dvr_origin = 0.0
            self._replay_skip.cancel()  # re-armed at the fresh edge already

    def _pause_toggle(self) -> None:
        if self._is_casting and self.cast_manager.is_active():
            if self.cast_manager.is_paused():
                self.cast_manager.resume()
            else:
                self.cast_manager.pause()
            return
        if self._play_mode in ("starting", "live"):
            # Pausing a live stream only works on the DVR copy — hop across at
            # the live point and pause there so resuming continues from here.
            if self._switch_to_dvr(self._safe_live()):
                self._pause_after_switch = True
            return
        if self.player is not None:
            self.player.pause()

    def _seek_step(self, delta: float) -> None:
        # Coalesce rapid presses: stack onto the pending target and fire once.
        start, safe_live, display_live = self._stream_extents()
        target = self._seek_acc.add(delta, self._current_position(), start, safe_live)
        self._window_seconds = safe_live
        if safe_live > start:
            self.timeline.setValue(int(self._abs_to_fraction(target) * 1000))
            self.behind_live_label.setText(self._behind_text(target, safe_live, display_live))
        self._seek_commit_timer.start()

    def _behind_text(self, pos: float, safe_live: float, display_live: float) -> str:
        """Steady 'behind live' readout, showing LIVE at the live edge."""
        if pos >= safe_live - LIVE_EPSILON:
            return "LIVE"
        return hover_label(pos, display_live)

    def _commit_seek(self) -> None:
        target = self._seek_acc.take()
        if target is not None:
            self._seek_to(target)

    def _on_timeline_seek(self, fraction: float) -> None:
        self._seek_to(self._fraction_to_abs(fraction))

    def _on_timeline_hover(self, fraction: float, global_pos) -> None:
        start, safe_live, display_live = self._stream_extents()
        if safe_live <= start:
            return
        hovered = self._fraction_to_abs(fraction)
        pos = self._current_position()
        text = (
            f"{signed_delta_label(hovered, pos)}   "
            f"({self._behind_text(hovered, safe_live, display_live)})"
        )
        pixmap = None
        if self._thumbnailer is not None:
            path = self._thumbnailer.thumbnail_for_offset(hovered)
            if path:
                pixmap = QPixmap(path)
        self._hover_preview.show_at(global_pos, pixmap, text)

    def _on_timeline_hover_exit(self) -> None:
        self._hover_preview.hide()

    def _update_timeline(self) -> None:
        active = self._recorder is not None
        self.timeline.setEnabled(active)
        if not active:
            self.behind_live_label.setText("")
            return
        # apply a deferred seek once the relay has actually opened and reports a
        # real duration (a VOD manifest is seekable, an open live playlist is not)
        if (
            self._pending_dvr_seek is not None
            and self._play_mode == "dvr"
            and self.player is not None
            and self.player.is_seekable()
        ):
            self.player.seek_s(max(0.0, self._pending_dvr_seek - self._dvr_origin))
            self._pending_dvr_seek = None
            if self._pause_after_switch:
                self.player.pause()
                self._pause_after_switch = False
        # A VOD snapshot ends at the newest segment it listed. Re-arm before
        # playback runs into that wall.
        elif (
            self._pending_dvr_seek is None
            and self._play_mode == "dvr"
            and self.player is not None
            and not self.player.is_paused()
            and self.player.at_end_of_buffer(REARM_MARGIN)
        ):
            self._rearm_dvr()

        # the upstream connection can drop (VPN reconnect, provider reset);
        # relaunch it so the buffer resumes growing instead of freezing
        if self._recorder is not None and self._recorder.restart_if_dead():
            self.statusBar().showMessage("Stream interrupted — reconnecting…", 4000)
            if self._play_mode == "live" and not self._is_casting:
                self._replay_skip.note_restart(self._recorder.extent()[1])
        # …and once the buffer has regrown a cushion past the point of the
        # drop, snap forward over the ~10-20s the provider replays on
        # reconnect. Without this the viewer seamlessly re-watches it and
        # falls that much further behind true live on every drop.
        if (
            self._play_mode == "live"
            and self.player is not None
            and self._recorder is not None
            and self._replay_skip.should_skip(
                self._recorder.extent()[1], LIVE_START_OFFSET
            )
        ):
            self.player.play(self.proxy.live_url())

        # channel just started? arm the player once the buffer can carry it
        self._maybe_start_from_buffer()

        start, safe_live, display_live = self._stream_extents()
        pos = self._current_position()
        self._window_seconds = safe_live
        # A starved player is a freeze frame — indistinguishable from broken
        # playback from the viewer's POV. The engine's reported position stops
        # advancing in that state (unlike _current_position, which projects the
        # live edge), so watch it directly and say so.
        stalled = self._stall.update(
            self.player.time_s() if self.player is not None else 0.0,
            playing=(
                not self._is_casting
                and self.player is not None
                and self.player.is_playing()
            ),
            now=time.monotonic(),
        )
        # don't fight an active drag or a pending coalesced seek
        if safe_live > start and not self.timeline.isSliderDown() and not self._seek_acc.pending:
            self.timeline.setValue(int(self._abs_to_fraction(pos) * 1000))
            if stalled:
                self.behind_live_label.setText("buffering…")
            else:
                self.behind_live_label.setText(self._behind_text(pos, safe_live, display_live))
        elif safe_live <= start:
            self.behind_live_label.setText("buffering…")
        paused = (
            self.cast_manager.is_paused()
            if (self._is_casting and self.cast_manager.is_active())
            else (self.player.is_paused() if self.player else False)
        )
        self.pause_btn.setText("▶" if paused else "⏸")

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
            # transport follows you into fullscreen as a draggable overlay
            self._overlay.adopt(self.timeline_widget, self.controls_widget)
            self._overlay.show()
            self._overlay.adjustSize()  # after show, so children report real sizes
            self._place_overlay()
            self._overlay.raise_()
            # keep the keyboard with the main window (the overlay is a separate
            # top-level window); no lists on screen, so arrows should seek
            self.activateWindow()
            self.video_frame.setFocus()
            self._is_fullscreen = True
        else:
            self._overlay_pos = self._overlay.pos()
            self._overlay.hide()
            # put the transport back into the right pane, below the video
            self._right_layout.addWidget(self.timeline_widget)
            self._right_layout.addWidget(self.controls_widget)
            for w in self._chrome:
                w.show()
            self._toolbar.show()
            self.statusBar().show()
            self.showNormal()
            self._is_fullscreen = False

    def _place_overlay(self) -> None:
        """Restore the remembered overlay position, else centre it near the bottom."""
        if self._overlay_pos is not None:
            self._overlay.move(self._overlay_pos)
            return
        screen = self.screen().geometry() if self.screen() else self.geometry()
        x = screen.x() + (screen.width() - self._overlay.width()) // 2
        y = screen.y() + screen.height() - self._overlay.height() - 60
        self._overlay.move(max(screen.x(), x), max(screen.y(), y))

    # ---- keyboard shortcuts -------------------------------------------- #
    VOLUME_STEP = 5
    SEEK_STEP = 5

    def _nudge_volume(self, delta: int) -> None:
        self.volume.setValue(max(0, min(100, self.volume.value() + delta)))

    def _focus_channels(self) -> None:
        """Move into the channel list, landing on the first row if nothing is
        selected yet.

        After a group change the view keeps a *current* index without any
        selection, so checking ``isValid()`` alone isn't enough — we'd skip
        selecting and land with no visible highlight. Select explicitly.
        """
        if not self.channel_model.rowCount():
            return
        self.channel_view.setFocus()
        selection = self.channel_view.selectionModel()
        index = self.channel_view.currentIndex()
        if not index.isValid() or selection is None or not selection.hasSelection():
            index = self.channel_model.index(0)
        self.channel_view.setCurrentIndex(index)
        if selection is not None:
            selection.select(index, QItemSelectionModel.ClearAndSelect)
        self.channel_view.scrollTo(index)

    def eventFilter(self, obj, event):
        """Keys while browsing the panels.

        Up/Down keep their normal list navigation; Left/Right move between the
        groups and channels panels (rather than seeking, which is what they do
        while watching); Enter starts the selected channel. Space and F are
        handled here too so they don't get eaten by the list's type-ahead.
        """
        if event.type() == QEvent.KeyPress:
            key = event.key()
            enter = (Qt.Key_Return, Qt.Key_Enter)
            if obj is self.group_list:
                if key == Qt.Key_Right or key in enter:
                    self._focus_channels()
                    return True
            elif obj is self.channel_view:
                if key == Qt.Key_Left:
                    self.group_list.setFocus()
                    return True
                if key in enter:
                    self._play(self._selected_channel())
                    return True
            if obj in (self.group_list, self.channel_view):
                if key == Qt.Key_Space:
                    self._pause_toggle()
                    return True
                if key == Qt.Key_F:
                    self._toggle_fullscreen()
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        # Never hijack typing — the search box keeps every keystroke.
        if isinstance(QApplication.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key_Escape:
            if self._is_fullscreen:
                self._toggle_fullscreen()
                return
        elif key == Qt.Key_F:
            self._toggle_fullscreen()
            return
        elif key == Qt.Key_Space:
            self._pause_toggle()
            return
        elif key == Qt.Key_Left:
            self._seek_step(-self.SEEK_STEP)
            return
        elif key == Qt.Key_Right:
            self._seek_step(self.SEEK_STEP)
            return
        elif key == Qt.Key_Up:
            self._nudge_volume(self.VOLUME_STEP)
            return
        elif key == Qt.Key_Down:
            self._nudge_volume(-self.VOLUME_STEP)
            return
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
        # Route through the local DVR relay (the provider's redirect +
        # session-hashed segments can't be played by the Chromecast directly),
        # and keep the Mac awake so the relay stays alive while casting.
        try:
            self._ensure_stream(channel)
            # Build the URL against an address *this device* can reach us on.
            # lan_ip() follows the default route, which a VPN owns — handing the
            # TV a tunnel address means it silently loads nothing.
            local_url = self.proxy.manifest_url(self.cast_manager.device_host(uuid_str))
        except Exception as exc:
            self._on_cast_failed(f"Couldn't start local relay: {exc}")
            return
        self._is_casting = True
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
        self._is_casting = False
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
        self._is_casting = False
        self._teardown_stream()
        self.now_playing.setText("Nothing playing")
        self.statusBar().showMessage("Cast stopped")

    def closeEvent(self, event) -> None:
        self._keep_awake.off()
        for w in (self._overlay, self._hover_preview):
            try:
                w.hide()
            except Exception:
                pass
        for closer in (self._teardown_stream, self.proxy.stop, self.cast_manager.shutdown):
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
