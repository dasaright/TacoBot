"""Retained Graphite clip editor with a native-painted waveform and trim handles."""
from pathlib import Path
import queue
import threading

from PySide6.QtCore import Qt,QTimer,Signal,QRectF
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QCheckBox,QDoubleSpinBox,QFileDialog,QSlider)

from . import main as engine
from .clip_editing import waveform,crop_clip,ClipPreview


class Waveform(QWidget):
    selectionChanged=Signal(float,float)
    def __init__(self):
        super().__init__(); self.setMinimumHeight(160); self.duration=0; self.peaks=[]
        self.start=0; self.end=0; self.position=0; self.drag=None; self.zoom=1
        self.setMouseTracking(True)

    def loaded(self,duration,peaks):
        self.duration=duration; maximum=max(peaks,default=1) or 1; self.peaks=[peak/maximum for peak in peaks]; self.start=0; self.end=duration; self.position=0; self.zoom=1; self.update()

    def view_range(self):
        span=self.duration/max(1,self.zoom)
        left=max(0,min(self.duration-span,self.position-span/2))
        return left,max(.001,span)

    def x(self,seconds):
        left,span=self.view_range(); return 18+(seconds-left)/span*max(1,self.width()-36)

    def seconds(self,x):
        left,span=self.view_range(); return max(0,min(self.duration,left+(x-18)/max(1,self.width()-36)*span))

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.fillRect(self.rect(),QColor('#1b2027'))
        if not self.duration:
            p.setPen(QColor('#8d9aa8')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Select a clip to load its waveform'); return
        top,bottom=16,self.height()-30; center=(top+bottom)/2
        a,b=self.x(self.start),self.x(self.end)
        p.fillRect(QRectF(a,top,b-a,bottom-top),QColor('#243c37'))
        p.setPen(QPen(QColor('#34414e'),1)); p.drawLine(18,int(center),self.width()-18,int(center))
        left,span=self.view_range(); step=max(1,len(self.peaks)//max(1,self.width()-36))
        for i in range(0,len(self.peaks),step):
            at=(i+.5)/len(self.peaks)*self.duration
            if not left<=at<=left+span: continue
            height=max(1,max(self.peaks[i:i+step])*(bottom-top)*.45)
            p.setPen(QPen(QColor('#83dec9' if self.start<=at<=self.end else '#61707f'),1.5))
            x=self.x(at); p.drawLine(int(x),int(center-height),int(x),int(center+height))
        for x in (a,b):
            p.setPen(QPen(QColor('#52d4ba'),2)); p.drawLine(int(x),top,int(x),bottom)
            p.fillRect(QRectF(x-5,top,10,18),QColor('#52d4ba'))
        p.setPen(QPen(QColor('#f5cf80'),1.5)); p.drawLine(int(self.x(self.position)),top,int(self.x(self.position)),bottom)
        p.setPen(QColor('#a5b3c1'))
        for n in range(6):
            seconds=left+span*n/5; x=int(18+(self.width()-36)*n/5)
            p.drawText(QRectF(x-34,bottom+6,68,20),Qt.AlignmentFlag.AlignCenter,f'{seconds:.2f}s')

    def mousePressEvent(self,event):
        if not self.duration: return
        x=event.position().x()
        if abs(x-self.x(self.start))<=12: self.drag='start'
        elif abs(x-self.x(self.end))<=12: self.drag='end'
        else: self.position=self.seconds(x); self.update()

    def mouseMoveEvent(self,event):
        if not self.duration: return
        value=self.seconds(event.position().x())
        if self.drag=='start': self.start=min(value,max(0,self.end-.01))
        elif self.drag=='end': self.end=max(value,min(self.duration,self.start+.01))
        else:
            near=min(abs(event.position().x()-self.x(self.start)),abs(event.position().x()-self.x(self.end)))<=12
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.PointingHandCursor); return
        self.selectionChanged.emit(self.start,self.end); self.update()

    def mouseReleaseEvent(self,event): self.drag=None


class ClipEditor(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.events=queue.Queue(); self.preview=ClipPreview(engine.FFMPEG_PATH,self.events)
        from .qt_desktop import button
        self.path=None; self.key=None; self.origin='Local'; self.loaded_fingerprint=None; self.generation=0; self.busy=False; self.files_signature=None
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(14)
        top=QWidget(); line=QHBoxLayout(top); line.setContentsMargins(0,0,0,0)
        self.files=QComboBox(); self.files.setMinimumWidth(300); line.addWidget(self.files,1)
        self.load=button('Load clip',self.load_selected,'folder'); line.addWidget(self.load)
        browse=button('Open audio file',self.browse,'folder'); line.addWidget(browse); layout.addWidget(top)
        self.info=QLabel('Select a saved clip, then load it to edit.'); layout.addWidget(self.info)
        self.wave=Waveform(); self.wave.selectionChanged.connect(self.selection); layout.addWidget(self.wave,1)
        controls=QWidget(); buttons=QHBoxLayout(controls); buttons.setContentsMargins(0,0,0,0)
        self.play=button('Play selection',self.play_selection,'play'); buttons.addWidget(self.play)
        end=button('Play end',lambda:self.play_selection(tail=True),'play'); buttons.addWidget(end)
        stop=button('Stop',self.stop_preview,'stop'); buttons.addWidget(stop)
        buttons.addWidget(QLabel('Start')); self.start=QDoubleSpinBox(); buttons.addWidget(self.start)
        buttons.addWidget(QLabel('End')); self.end=QDoubleSpinBox(); buttons.addWidget(self.end)
        for box in (self.start,self.end): box.setDecimals(3); box.setSuffix(' s'); box.setSingleStep(.05); box.setFixedWidth(115)
        self.start.valueChanged.connect(self.fields_changed); self.end.valueChanged.connect(self.fields_changed)
        reset=button('Reset selection',self.reset,'refresh'); buttons.addWidget(reset)
        buttons.addWidget(QLabel('Zoom')); self.zoom=QSlider(Qt.Orientation.Horizontal); self.zoom.setRange(1,20); self.zoom.setFixedWidth(110); self.zoom.valueChanged.connect(self.zoom_changed); buttons.addWidget(self.zoom); buttons.addStretch(); layout.addWidget(controls)
        bottom=QWidget(); actions=QHBoxLayout(bottom); actions.setContentsMargins(0,0,0,0)
        self.save_new=QCheckBox('Save as new file'); actions.addWidget(self.save_new)
        self.save=button('Save trim',self.save_selection,'save',True); actions.addWidget(self.save)
        self.selected=QLabel(''); actions.addWidget(self.selected); actions.addStretch(); layout.addWidget(bottom)
        self.message=QLabel('Preview plays locally through your default speakers.'); self.message.setWordWrap(True); layout.addWidget(self.message)
        self.play.setEnabled(False); self.save.setEnabled(False)
        self.timer=QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(30)

    def set_files(self,files,origins):
        signature=tuple((key,str(path),origins.get(key)) for key,path in files.items())
        if signature==self.files_signature: return
        self.files_signature=signature; selected=self.files.currentData(); self.files.blockSignals(True); self.files.clear()
        for key,path,origin in sorted(signature,key=lambda x:Path(x[1]).stem.casefold()):
            self.files.addItem(f'{Path(path).stem} · {origin or "Local"}',(key,path,origin))
        for n in range(self.files.count()):
            if self.files.itemData(n)==selected: self.files.setCurrentIndex(n); break
        self.files.blockSignals(False)

    def load_selected(self):
        entry=self.files.currentData()
        if entry: self.load_path(Path(entry[1]),entry[0],entry[2])

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'Open audio clip',self.window.controller.folder.get(),'Audio (*.mp3 *.ogg *.opus *.wav *.flac *.m4a *.aac *.wma *.aif *.aiff)')
        if path: self.load_path(Path(path))

    def load_path(self,path,key=None,origin='Local'):
        if self.busy: return
        import hashlib
        path=Path(path).resolve(); key=key or hashlib.sha256(str(path).encode()).hexdigest()
        if path.parent==engine.SHARED_CLIPS_DIR.resolve(): origin='Shared'
        self.stop_preview(); self.generation+=1; generation=self.generation; self.path=Path(path); self.key=key; self.origin=origin
        self.loaded_fingerprint=None; self.play.setEnabled(False); self.save.setEnabled(False); self.message.setText('Loading waveform…')
        def worker():
            try: self.events.put({'loaded':waveform(path,engine.FFMPEG_PATH),'generation':generation})
            except Exception as exc: self.events.put({'error':str(exc),'generation':generation})
        threading.Thread(target=worker,daemon=True).start()

    def selection(self,start,end):
        for box,value in ((self.start,start),(self.end,end)):
            box.blockSignals(True); box.setValue(value); box.blockSignals(False)
        self.wave.start=start; self.wave.end=end; self.wave.update(); self.selected.setText(f'Selection: {max(0,end-start):.3f} seconds')
        self.save.setEnabled(self.loaded_fingerprint is not None and end>start and not self.busy)

    def fields_changed(self): self.selection(self.start.value(),self.end.value())

    def reset(self):
        self.stop_preview(); self.wave.position=0; self.zoom.setValue(1); self.selection(0,self.wave.duration)

    def zoom_changed(self,value): self.wave.zoom=value; self.wave.update()

    def stop_preview(self):
        from .qt_desktop import icon
        self.preview.stop(); self.play.setText('Play selection'); self.play.setIcon(icon('play'))

    def play_selection(self,tail=False):
        if self.path is None or self.loaded_fingerprint is None: return
        if self.end.value()<=self.start.value(): self.message.setText('End must be after start.'); return
        if self.preview.playing and not tail:
            from .qt_desktop import icon
            paused=self.preview.pause(); self.play.setText('Resume' if paused else 'Pause'); self.play.setIcon(icon('play' if paused else 'pause')); return
        start=max(self.start.value(),self.end.value()-2) if tail else self.start.value()
        from .qt_desktop import icon
        self.preview.start(self.path,start,self.end.value()); self.play.setText('Pause'); self.play.setIcon(icon('pause'))

    def save_selection(self):
        if self.busy or self.loaded_fingerprint is None: return
        self.stop_preview(); source=self.path; key=self.key; start=self.start.value(); end=self.end.value()
        if (self.save_new.isChecked() or self.origin=='Shared') and not self.window.controller.folder.get(): self.window.controller.choose_folder()
        new=self.save_new.isChecked() or self.origin=='Shared'; folder=self.window.controller.folder.get() or str(source.parent)
        if new and Path(folder).resolve()==engine.SHARED_CLIPS_DIR.resolve(): self.message.setText('Choose a local clips folder for the edited copy.'); return
        self.busy=True; self.save.setEnabled(False); self.play.setEnabled(False); self.message.setText('Encoding selected audio…')
        generation=self.generation; expected=self.loaded_fingerprint
        if key: self.window.controller.editing_clips.add(key)
        def worker():
            try:
                self.preview.wait()
                target=crop_clip(source,start,end,engine.FFMPEG_PATH,new,folder,expected,
                    release=lambda:self.window.controller.release_editor_source(key,source))
                self.events.put({'saved':str(target),'source':str(source),'source_key':key,'generation':generation})
            except Exception as exc: self.events.put({'error':str(exc),'save_failed':True,'source_key':key,'generation':generation})
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        for _ in range(30):
            try: event=self.events.get_nowait()
            except queue.Empty: break
            if 'preview_position' in event or event.get('preview_finished') or event.get('preview_error'):
                if event['generation']!=self.preview.generation: continue
                if 'preview_position' in event: self.wave.position=event['preview_position']; self.wave.update()
                if event.get('preview_finished'):
                    from .qt_desktop import icon
                    self.play.setText('Play selection'); self.play.setIcon(icon('play'))
                if event.get('preview_error'): self.message.setText(event['preview_error'])
                continue
            if event.get('saved') or event.get('save_failed'):
                self.busy=False; self.window.controller.editing_clips.discard(event.get('source_key')); self.play.setEnabled(True)
            if event['generation']!=self.generation: continue
            if event.get('loaded'):
                duration,peaks,self.loaded_fingerprint=event['loaded']; self.wave.loaded(duration,peaks); self.zoom.setValue(1)
                self.start.setRange(0,duration); self.end.setRange(0,duration); self.selection(0,duration)
                self.info.setText(f'{self.path.name} · {duration:.3f} seconds'); self.play.setEnabled(True)
                shared=self.origin=='Shared'; self.save_new.setChecked(shared); self.save_new.setEnabled(not shared)
                self.message.setText('Drag the mint handles, or enter start/end times. Shared clips save as new local copies.' if shared else 'Drag the mint handles, or enter start/end times. Preview before saving.')
            if event.get('saved'):
                self.window.controller.editor_saved(event); self.message.setText('Saved '+Path(event['saved']).name)
                if self.window.controller.close_after_recording: QTimer.singleShot(0,self.window.close)
                else: self.load_path(Path(event['saved']),origin='Local')
            if event.get('error'): self.message.setText(event['error']); self.save.setEnabled(self.loaded_fingerprint is not None)

    def shutdown(self): self.timer.stop(); self.preview.stop()
