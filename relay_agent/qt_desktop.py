"""Retained, single-backing-store TacoBot desktop. Audio runs independently."""
import asyncio
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QMimeData, QPoint, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QImage, QPixmap, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedLayout, QPushButton, QLabel, QCheckBox, QLineEdit, QSlider, QTableView,
    QStyledItemDelegate, QTreeWidget, QAbstractItemView, QSplitter, QTextEdit,
    QComboBox, QSizePolicy, QMenu, QFileDialog, QInputDialog, QMessageBox, QDialog, QHeaderView, QSystemTrayIcon)

from . import main as engine
from .app_icon import application_icon

STYLE = '''
QWidget { background:#14171c; color:#dce5ef; font-family:"Segoe UI"; font-size:13px; }
QMainWindow { background:#14171c; }
QPushButton { background:#202731; border:0; border-radius:6px; padding:7px 10px; }
QPushButton:hover { background:#2a3741; }
QPushButton:pressed { background:#34564f; }
QPushButton:checked { background:#243c37; color:#52d4ba; font-weight:600; }
QPushButton:disabled { color:#62707c; background:#1b2027; }
QPushButton[primary="true"] { background:#52d4ba; color:#10211e; }
QLineEdit,QTextEdit,QComboBox { background:#1b2027; border:0; border-radius:5px; padding:6px; }
QTreeView,QTableView { background:#1b2027; alternate-background-color:#191e25; border:0; outline:0; }
QTreeView::item,QTableView::item { padding:2px 5px; }
QTreeView::item:selected,QTableView::item:selected { background:#294943; color:#f3faf8; }
QHeaderView::section { background:#162337; color:#c8d5e4; border:0; padding:7px 8px; font-weight:600; }
QScrollBar:vertical { background:#14171c; width:8px; margin:0; border:0; }
QScrollBar::handle:vertical { background:#526170; border-radius:4px; min-height:36px; }
QScrollBar::handle:vertical:hover { background:#52d4ba; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:none; }
QScrollBar:horizontal { height:8px; background:#14171c; }
QScrollBar::handle:horizontal { background:#526170; border-radius:4px; }
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal { width:0; }
QSlider::groove:horizontal { height:4px; background:#39434f; border-radius:2px; }
QSlider::sub-page:horizontal { background:#52d4ba; border-radius:2px; }
QSlider::handle:horizontal { width:12px; margin:-4px 0; border-radius:6px; background:#52d4ba; }
QCheckBox { spacing:6px; }
QCheckBox::indicator { width:16px; height:16px; border-radius:4px; background:#27323b; border:1px solid #526170; }
QCheckBox::indicator:checked { background:#52d4ba; border:1px solid #52d4ba; }
QMenu { background:#202731; border:1px solid #34414e; padding:5px; }
QMenu::item { padding:7px 22px; border-radius:4px; }
QMenu::item:selected { background:#34564f; }
QSplitter::handle { background:#14171c; width:8px; }
'''

ICONS = {}

def icon(name, color="#52d4ba"):
    key=(name,color)
    if key not in ICONS:
        image = engine.render_button_icon(name, 128, color).convert('RGBA')
        qimage = QImage(image.tobytes(), image.width, image.height, QImage.Format.Format_RGBA8888).copy()
        ICONS[key] = QIcon(QPixmap.fromImage(qimage))
    return ICONS[key]


def button(text, callback, symbol=None, primary=False):
    widget = QPushButton(text); widget.clicked.connect(callback)
    widget.setProperty('primary', primary)
    if symbol:
        widget.setIcon(icon(symbol,"#10211e" if primary else "#52d4ba")); widget.setIconSize(QSize(27, 27))
    return widget


def row(parent):
    layout = QHBoxLayout(parent); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
    return layout


class MusicTree(QTreeWidget):
    MIME = 'application/x-tacobot-selection'
    def __init__(self, view, kind):
        super().__init__(); self.view, self.kind = view, kind
        self.setColumnCount(1); self.setHeaderLabels(['Saved songs & playlists' if kind == 'favorites' else 'Your music queue'])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(True); self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setDragEnabled(True); self.setAcceptDrops(kind == 'queue'); self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction if kind == 'favorites' else Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)

    def startDrag(self, actions):
        ids = self.view.controller.ordered_selection(self.kind)
        if not ids:
            return
        mime = QMimeData(); mime.setData(self.MIME, json.dumps({'kind': self.kind, 'ids': ids}).encode())
        drag = QDrag(self); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction if self.kind == 'favorites' else Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if self.kind == 'queue' and event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(self.MIME):
            event.ignore(); return
        payload = json.loads(bytes(event.mimeData().data(self.MIME)))
        target = self.itemAt(event.position().toPoint())
        before = target.data(0, Qt.ItemDataRole.UserRole) if target else None
        if target and event.position().y() > self.visualItemRect(target).center().y():
            n = self.indexOfTopLevelItem(target) + 1
            item = self.topLevelItem(n) if n < self.topLevelItemCount() else None
            before = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.view.controller.drop_music(payload, before)
        event.acceptProposedAction()

    def context_menu(self, point):
        item = self.itemAt(point)
        if item and not item.isSelected():
            self.clearSelection(); item.setSelected(True)
        if not self.selectedItems():
            return
        controller = self.view.controller; menu = QMenu(self)
        if self.kind == 'favorites':
            menu.addAction(icon('arrow'), 'Add selection to queue', controller.add_favorite_selection)
            menu.addAction('Remove saved item', lambda: controller.remove_saved(item))
        else:
            menu.addAction('Favorite selection', controller.favorite_selection)
            menu.addAction('Remove from queue', controller.remove_queue_song)
        menu.exec(self.viewport().mapToGlobal(point))


class ClipModel(QAbstractTableModel):
    HEADERS = ['Name', 'Volume', 'Shared', 'Label']
    def __init__(self, view):
        super().__init__(view); self.view, self.rows = view, []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        key, name, volume, shared, label, color = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return ['▶  ' + name, volume, shared, label or 'Select'][index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return key
        if role == Qt.ItemDataRole.BackgroundRole and index.column() == 3 and color:
            return QColor(color)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]

    def load(self, rows):
        self.beginResetModel(); self.rows = rows; self.endResetModel()


class ClipDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view); self.view, self.drag_key = view, None

    def paint(self, painter, option, index):
        if index.column() == 0:
            super().paint(painter, option, index); return
        painter.save(); painter.setClipRect(option.rect)
        controller = self.view.controller
        key = index.data(Qt.ItemDataRole.UserRole)
        value = index.data()
        painter.fillRect(option.rect, QColor('#294943' if option.state & self.view.selected_state else ('#191e25' if index.row() % 2 else '#1b2027')))
        rect = option.rect; center = rect.center()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if index.column() == 1:
            volume = controller.config.get('clip_settings', {}).get(key, {}).get('volume', 0)
            left, right = rect.left()+12, max(rect.left()+13, rect.right()-58)
            x = left + (right-left)*(volume+100)/200
            painter.setPen(QPen(QColor('#39434f'), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(left, center.y(), right, center.y())
            painter.setPen(QPen(QColor('#52d4ba'), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(left, center.y(), round(x), center.y())
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#52d4ba')); painter.drawEllipse(QPoint(round(x), center.y()), 5, 5)
            painter.setPen(QColor('#c3cdd8')); painter.drawText(rect.adjusted(0, 0, -5, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{volume:+d}%' if volume else '0%')
        elif index.column() == 2:
            painter.setPen(QPen(QColor('#52d4ba' if value else '#526170'), 1)); painter.setBrush(QColor('#52d4ba' if value else '#252d36'))
            painter.drawRoundedRect(center.x()-7, center.y()-7, 14, 14, 3, 3)
            if value:
                painter.setPen(QPen(QColor('#10211e'), 2)); painter.drawLine(center.x()-4, center.y(), center.x()-1, center.y()+3); painter.drawLine(center.x()-1, center.y()+3, center.x()+5, center.y()-4)
        else:
            color = index.data(Qt.ItemDataRole.BackgroundRole)
            if color: painter.fillRect(rect, color)
            painter.setPen(QColor('#10211e' if color else '#a3adb9'))
            painter.drawText(rect.adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(value))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if index.column() != 1: return False
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease): return False
        if event.type() == QEvent.Type.MouseMove and not event.buttons() & Qt.MouseButton.LeftButton: return False
        left, right = option.rect.left()+12, max(option.rect.left()+13, option.rect.right()-58)
        value = round(max(0, min(1, (event.position().x()-left)/(right-left))) * 200 - 100)
        self.view.controller.set_volume(index.data(Qt.ItemDataRole.UserRole), value)
        self.view.clips.viewport().update(option.rect)
        return True


class ClipTable(QTableView):
    def wheelEvent(self, event):
        pixels = event.pixelDelta().y() or event.angleDelta().y()/120*225
        bar = self.verticalScrollBar(); bar.setValue(bar.value()-round(pixels)); event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        self.window().controller.import_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]); event.acceptProposedAction()


class SpeechInput(QTextEdit):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Backspace and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.clear(); return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.window().controller.play_text(); return
        super().keyPressEvent(event)


class Desktop(QMainWindow):
    def __init__(self, config, start_agent=True):
        super().__init__(); self.config = config; self.closing = False
        self.setWindowTitle('TacoBot'); self.resize(1280, 780); self.setMinimumSize(1180, 620); self.setAcceptDrops(True)
        self.setWindowIcon(application_icon())
        self.setStyleSheet(STYLE)
        from PySide6.QtWidgets import QStyle
        self.selected_state = QStyle.StateFlag.State_Selected
        shell = QWidget(); self.setCentralWidget(shell); layout = QVBoxLayout(shell); layout.setContentsMargins(22, 16, 22, 12); layout.setSpacing(12)
        header = QWidget(); top = row(header); logo=QLabel(); logo.setPixmap(self.windowIcon().pixmap(32,32)); top.addWidget(logo)
        brand = QLabel('TacoBot'); brand.setStyleSheet('font-size:21px; font-weight:600;'); top.addWidget(brand)
        self.tabs = []
        for n, (title, symbol) in enumerate([('YouTube Music','play'),('Soundboard','soundboard'),('TTS','speech')]):
            tab = QPushButton(title); tab.setCheckable(True); tab.setFixedSize(155 if n == 0 else 140 if n == 1 else 88, 44); tab.setIcon(icon(symbol)); tab.setIconSize(QSize(28,28)); tab.pressed.connect(lambda n=n:self.select_tab(n)); top.addWidget(tab); self.tabs.append(tab)
        top.addStretch()
        top.addWidget(button('Audio', lambda:self.controller.direct_audio_settings(), 'audio'))
        self.direct = QCheckBox('Direct'); self.direct.toggled.connect(lambda value:self.controller.direct_changed(value)); top.addWidget(self.direct)
        self.auto = QCheckBox('Auto-update'); self.auto.setChecked(config.get('auto_update', True)); self.auto.toggled.connect(lambda value:self.controller.auto_changed(value)); top.addWidget(self.auto)
        self.check = button('Check updates', lambda:self.controller.check_updates(True), 'check'); top.addWidget(self.check)
        self.install = button('Install', lambda:self.controller.install_update_now(), 'download'); self.install.setEnabled(False); top.addWidget(self.install)
        layout.addWidget(header)
        self.page_host = QWidget(); self.pages = QStackedLayout(self.page_host); self.pages.setContentsMargins(0,0,0,0); self.pages.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.page_widgets = [QWidget() for _ in range(3)]
        for page in self.page_widgets:
            page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.pages.addWidget(page)
        self.build_music(); self.build_clips(); self.build_tts(); layout.addWidget(self.page_host, 1)
        footer = QWidget(); foot = row(footer); self.status = QLabel('Connecting your relay'); self.status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); foot.addWidget(self.status,1); self.identity = QLabel(config.get('relay_name','Personal relay')); foot.addWidget(self.identity); foot.addWidget(QLabel(f'TacoBot v1.{engine.RELAY_BUILD}')); layout.addWidget(footer)
        from .qt_controller import Controller
        self.controller = Controller(self, config, start_agent=start_agent)
        self.tray = QSystemTrayIcon(self.windowIcon(), self); self.tray.setToolTip('TacoBot')
        tray_menu=QMenu(self); tray_menu.addAction('Open TacoBot',self.restore_window); tray_menu.addAction('Quit',self.close)
        self.tray.setContextMenu(tray_menu); self.tray.activated.connect(self.tray_activated)
        if start_agent and QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()
        self.select_tab(0)
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self); self.paste_shortcut.setContext(Qt.ShortcutContext.WindowShortcut); self.paste_shortcut.activated.connect(self.clipboard_play)
        # Pre-render every page once at startup. No object creation, file scan,
        # model reset, or animation is performed by select_tab.
        from .presentation import prepare_pages
        self.prepared_pages = prepare_pages(self)

    def select_tab(self, index):
        self.pages.setCurrentIndex(index)
        self.page_widgets[index].setFocus(Qt.FocusReason.OtherFocusReason)
        for n, tab in enumerate(self.tabs): tab.setChecked(n == index)

    def restore_window(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def tray_activated(self,reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick): self.restore_window()

    def volume(self, layout, key):
        layout.addWidget(QLabel('Volume')); slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(0,100); slider.setValue(self.config.get(key,100)); slider.setFixedWidth(105)
        text = QLabel(f'{slider.value()}%'); text.setFixedWidth(42)
        def changed(value):
            text.setText(f'{value}%'); self.controller.config[key] = value; self.controller.save_later()
        slider.valueChanged.connect(changed); layout.addWidget(slider); layout.addWidget(text); return slider

    def build_music(self):
        area = QVBoxLayout(self.page_widgets[0]); area.setContentsMargins(0,0,0,0); area.setSpacing(14)
        tools = QWidget(); bar = row(tools)
        self.previous=button('Previous', lambda:self.controller.music_control('previous'), 'previous'); self.previous.setEnabled(False); bar.addWidget(self.previous)
        self.play = button('Play', lambda:self.controller.music_control('toggle'), 'play', True); bar.addWidget(self.play)
        bar.addWidget(button('Next', lambda:self.controller.music_control('next'), 'next'))
        self.playlist_toggle = QCheckBox('Paste full playlist'); self.playlist_toggle.setChecked(self.config.get('paste_playlist',False)); self.playlist_toggle.toggled.connect(lambda value:self.controller.paste_changed(value)); bar.addWidget(self.playlist_toggle)
        self.music_volume = self.volume(bar,'music_volume'); bar.addStretch(); area.addWidget(tools)
        self.now_playing = QLabel('Your next song starts here'); self.now_playing.setWordWrap(True); self.now_playing.setStyleSheet('font-size:20px; font-weight:600; padding:12px 0;'); area.addWidget(self.now_playing)
        split = QSplitter(); saved = QWidget(); left = QVBoxLayout(saved); left.setContentsMargins(0,0,0,0)
        self.favorites = MusicTree(self,'favorites'); left.addWidget(self.favorites,1)
        buttons = QWidget(); controls = row(buttons); controls.addWidget(button('Save Playlist Link', lambda:self.controller.save_playlist_link(),'save')); controls.addWidget(button('Add selection to queue',lambda:self.controller.add_favorite_selection(),'arrow')); controls.addStretch(); left.addWidget(buttons)
        self.queue = MusicTree(self,'queue'); self.queue.setRootIsDecorated(False)
        self.favorites.itemDoubleClicked.connect(lambda item,column:self.controller.enqueue_item(item)); self.favorites.itemExpanded.connect(lambda item:self.controller.expand_playlist(item))
        self.queue.itemDoubleClicked.connect(lambda item,column:self.controller.select_song(item)); split.addWidget(saved); split.addWidget(self.queue); split.setSizes([440,700]); area.addWidget(split,1)
        QShortcut(QKeySequence(Qt.Key.Key_Delete),self.queue,activated=lambda:self.controller.remove_queue_song())

    def build_clips(self):
        area = QVBoxLayout(self.page_widgets[1]); area.setContentsMargins(0,0,0,0); area.setSpacing(8)
        tools = QWidget(); bar = row(tools); bar.addWidget(button('Stop clips',lambda:self.controller.stop(),'stop'))
        bar.addWidget(QLabel('Search clips')); self.search = QLineEdit(); self.search.setClearButtonEnabled(True); self.search.setFixedWidth(190); self.search.setPlaceholderText('Name or label'); self.search.textChanged.connect(lambda:self.controller.render_clips()); bar.addWidget(self.search)
        self.clip_volume = self.volume(bar,'bot_clip_volume'); bar.addWidget(button('Refresh',lambda:self.controller.refresh_files(),'refresh')); bar.addWidget(button('Sync',lambda:self.controller.sync_shared(),'sync')); bar.addStretch(); bar.addWidget(button('Open folder',lambda:self.controller.choose_folder(),'folder')); area.addWidget(tools)
        details = QWidget(); line = row(details); line.addStretch(); line.addWidget(QLabel('Click a clip to play · Right-click to manage')); self.folder = QLabel(self.config.get('mp3_folder','No local folder selected')); self.folder.setMaximumWidth(400); self.folder.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); line.addWidget(self.folder); area.addWidget(details)
        self.clips = ClipTable(); self.clips.setAcceptDrops(True); self.clips.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.clip_model = ClipModel(self); self.clips.setModel(self.clip_model); self.clips.setItemDelegate(ClipDelegate(self)); self.clips.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.clips.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.clips.setShowGrid(False); self.clips.setAlternatingRowColors(True); self.clips.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel); self.clips.verticalHeader().hide(); self.clips.verticalHeader().setDefaultSectionSize(25); self.clips.verticalHeader().setMinimumSectionSize(25)
        header = self.clips.horizontalHeader(); header.setSectionsMovable(True); header.setStretchLastSection(False)
        for n,width in enumerate((440,230,90,190)): self.clips.setColumnWidth(n,width)
        order = engine.clip_column_order(self.config.get('clip_column_order'))
        for target,name in enumerate(order): header.moveSection(header.visualIndex(engine.CLIP_COLUMNS.index(name)),target)
        widths = self.config.get('clip_column_widths',{})
        for n,name in enumerate(engine.CLIP_COLUMNS):
            if isinstance(widths.get(name),int): self.clips.setColumnWidth(n,max(55,widths[name]))
        header.sectionMoved.connect(lambda:self.controller.save_columns()); header.sectionResized.connect(lambda:self.controller.save_columns()); header.sectionClicked.connect(lambda n:self.controller.sort_clips(engine.CLIP_COLUMNS[n]) if n != 1 else None)
        self.clips.clicked.connect(self.clip_clicked); self.clips.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.clips.customContextMenuRequested.connect(lambda point:self.controller.clip_menu(point)); area.addWidget(self.clips,1)

    def clip_clicked(self, index):
        key = index.data(Qt.ItemDataRole.UserRole)
        if index.column() == 0: self.controller.play_clip(key)
        elif index.column() == 3: self.controller.label_menu(key,index)

    def build_tts(self):
        area = QVBoxLayout(self.page_widgets[2]); area.setContentsMargins(0,0,0,0); area.setSpacing(12)
        tools = QWidget(); bar = row(tools); bar.addWidget(QLabel('Voice')); self.voice = QComboBox(); self.voice.setMinimumWidth(260); bar.addWidget(self.voice); bar.addWidget(QLabel('Speaker')); self.speaker = QComboBox(); self.speaker.setMinimumWidth(150); bar.addWidget(self.speaker)
        bar.addWidget(button('Manage voices',lambda:self.controller.manage_voices(),'speech')); self.tts_volume = self.volume(bar,'tts_volume'); bar.addStretch(); area.addWidget(tools)
        area.addWidget(QLabel('TEXT TO SPEECH')); self.tts = SpeechInput(); self.tts.setPlaceholderText('Enter text, then press Enter to speak. Shift+Enter adds a line.'); self.tts.setFixedHeight(160); area.addWidget(self.tts)
        controls = QWidget(); buttons = row(controls); self.play_text_button = button('Play text',lambda:self.controller.play_text(),'play',True); buttons.addWidget(self.play_text_button); buttons.addWidget(button('Stop speech / clips',lambda:self.controller.send({'type':'local_stop','guild_id':None}),'stop')); buttons.addStretch(); area.addWidget(controls)
        area.addWidget(QLabel('Piper · Generated locally, no API fees · Ctrl+Backspace deletes a word · Shift+Backspace clears text'))
        translate = QWidget(); line = row(translate); line.addWidget(QLabel('Translate from')); self.source = QComboBox(); self.source.addItems(engine.LANGUAGES); self.source.setCurrentText(self.config.get('translation_source','English')); line.addWidget(self.source); line.addWidget(QLabel('to')); self.target = QComboBox(); self.target.addItems(engine.LANGUAGES); self.target.setCurrentText(self.config.get('translation_target','German')); line.addWidget(self.target)
        self.translate = button('Translate',lambda:self.controller.translate_text()); self.translated_play = button('Play translated',lambda:self.controller.play_translated(),'play'); self.translated_play.setEnabled(False); line.addWidget(self.translate); line.addWidget(self.translated_play); line.addStretch(); area.addWidget(translate)
        self.translation = QTextEdit(); self.translation.setReadOnly(True); self.translation.setFixedHeight(130); area.addWidget(self.translation); area.addWidget(QLabel('Translation runs locally. First use downloads the required language models.')); area.addStretch()
        self.voice.currentTextChanged.connect(lambda:self.controller.save_tts_voice() if hasattr(self,'controller') else None); self.speaker.currentTextChanged.connect(lambda:self.controller.save_tts_voice() if hasattr(self,'controller') else None)

    def clipboard_play(self):
        url = engine.clipboard_youtube_link(QApplication.clipboard().text())
        if url: self.controller.send({'type':'music_enqueue','guild_id':None,'url':url,'playlist':self.playlist_toggle.isChecked()})
        else:
            focus = QApplication.focusWidget()
            if isinstance(focus,(QLineEdit,QTextEdit)): focus.paste()

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self,event):
        self.controller.import_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]); event.acceptProposedAction()

    def closeEvent(self,event):
        if not self.closing:
            self.closing = True; self.controller.shutdown()
            self.tray.hide()
        event.accept()
