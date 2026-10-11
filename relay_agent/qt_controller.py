"""Reuse the established audio/network/update actions with native Qt controls."""
import asyncio
import hashlib
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QBrush
from PySide6.QtWidgets import (QApplication, QMenu, QFileDialog, QInputDialog, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QLineEdit,
    QTreeWidget, QAbstractItemView)

from . import main as engine
from .qt_adapters import Value, Control, Text, Root, Tree


class Controller(engine.RelayWindow):
    def __init__(self, view, config, start_agent=True):
        self.view, self.config = view, config
        self.root = Root(view); self.agent = engine.RelayAgent(config)
        self.loop = asyncio.new_event_loop(); self.started = start_agent
        self.status = Value(getter=view.status.text,setter=view.status.setText)
        self.identity = Value(getter=view.identity.text,setter=view.identity.setText)
        self.folder = Value(config.get('mp3_folder',''),setter=lambda text:view.folder.setText(text or 'No local folder selected'))
        self.auto_update = Value(getter=view.auto.isChecked,setter=view.auto.setChecked)
        self.direct_mode = Value(getter=view.direct.isChecked,setter=self.set_direct_check)
        self.direct_checkbox = Control(view.direct); self.update_button = Control(view.check); self.install_button = Control(view.install)
        self.paste_playlist = Value(getter=view.playlist_toggle.isChecked)
        self.favorites_view = Tree(view.favorites); self.queue_view = Tree(view.queue)
        self.favorite_context_id = None; self.queue_context_id = None; self.last_music = None
        self.sync_running = False; self.editing_clips = set(); self.clip_origins = {}; self.file_ids = []
        self.sort_keys = engine.clip_sort_keys(config.get('clip_sort_keys'))
        self.voice_manager = None; self.tts_text = Text(view.tts); self.translated_text = Text(view.translation)
        self.tts_play_button = Control(view.play_text_button); self.translate_button = Control(view.translate); self.translated_play_button = Control(view.translated_play)
        self.translation_source = Value(getter=view.source.currentText); self.translation_target = Value(getter=view.target.currentText)
        self.tts_voice_picker = Control(view.voice); self.tts_speaker_picker = Control(view.speaker)
        self.tts_voice = Value(getter=view.voice.currentText,setter=lambda text:self.set_combo(view.voice,text))
        self.tts_speaker = Value(getter=view.speaker.currentText,setter=lambda text:self.set_combo(view.speaker,text))
        current = config.get('tts_voice',engine.VOICE)
        self.tts_voice.set(next((label for label,voice in engine.VOICE_OPTIONS.items() if voice == current),''))
        # populate before resolving saved choice, without emitting UI callbacks
        enabled = config.get('tts_enabled_voices',[engine.VOICE])
        view.voice.addItems([label for label,voice in engine.VOICE_OPTIONS.items() if voice in enabled])
        self.tts_voice.set(next((label for label,voice in engine.VOICE_OPTIONS.items() if voice == current),view.voice.currentText()))
        self.refresh_enabled_voices()
        self.update_check_running = False; self.pending_update = None; self.update_idle_since = None
        failed = engine.FILES_DIR/'update-status.txt'
        self.next_update_check = time.monotonic()+21600 if failed.is_file() and 'failed' in failed.read_text(encoding='utf-8-sig',errors='replace').lower() else 0
        self.next_music_refresh = time.monotonic()+2
        self.save_timer = QTimer(view); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.save)
        self.poll_timer = QTimer(view); self.poll_timer.timeout.connect(self.poll); self.poll_timer.start(16)
        self.render_favorites()
        if start_agent:
            threading.Thread(target=self.run_agent,daemon=True).start(); self.refresh_files()
            if config.get('direct_soundboard'): QTimer.singleShot(300,lambda:self.direct_changed(True))

    def set_combo(self, combo, text):
        combo.blockSignals(True)
        if text and combo.findText(text)<0: combo.addItem(text)
        combo.setCurrentText(text); combo.blockSignals(False)

    def set_direct_check(self,value):
        self.view.direct.blockSignals(True); self.view.direct.setChecked(value); self.view.direct.blockSignals(False)

    def save_later(self):
        self.save_timer.start(250)

    def save(self):
        if self.started:
            engine.save_config(self.config)

    def auto_changed(self,value):
        self.config['auto_update']=value; self.save(); self.next_update_check=0

    def paste_changed(self,value):
        self.config['paste_playlist']=value; self.save()

    def direct_changed(self,value):
        self.set_direct_check(value); self.toggle_direct_mode()

    def direct_audio_settings(self,enable=False):
        try:
            devices=engine.audio_devices()
        except Exception as exc:
            QMessageBox.warning(self.view,'Audio',str(exc)); return
        outputs=[d for d in devices if d['output']>=2]
        inputs=[d for d in devices if d['input']>=1 and not any(term in d['name'].lower() for term in ('cable output','voicemeeter output'))]
        if not outputs:
            QMessageBox.warning(self.view,'Audio','No stereo audio outputs found.'); return
        dialog=QDialog(self.view); dialog.setWindowTitle('Direct soundboard audio'); layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Select CABLE Input as the virtual output, then CABLE Output as Discord’s microphone.\nSpeakers hear clips; optional microphone passthrough mixes your voice into the cable.'))
        previous=self.config.get('direct_audio_devices',{}); boxes={}
        cable=next((d['key'] for d in outputs if 'cable input' in d['name'].lower()),outputs[0]['key'])
        speaker=next((d['key'] for d in outputs if not any(term in d['name'].lower() for term in ('cable','voicemeeter'))),outputs[0]['key'])
        for key,title,choices,default in [('cable','Virtual output',[d['key'] for d in outputs],cable),('speakers','Speakers / headphones',[d['key'] for d in outputs],speaker),('microphone','Microphone',['No microphone passthrough']+[d['key'] for d in inputs],previous.get('microphone') or 'No microphone passthrough')]:
            layout.addWidget(QLabel(title)); combo=QComboBox(); combo.addItems(choices); combo.setCurrentText(previous.get(key) or default); layout.addWidget(combo); boxes[key]=combo
        apply=QPushButton('Save and enable' if enable else 'Save'); layout.addWidget(apply)
        def commit():
            self.config['direct_audio_devices']={key:box.currentText() for key,box in boxes.items()}
            if self.config['direct_audio_devices']['microphone']=='No microphone passthrough': self.config['direct_audio_devices']['microphone']=None
            self.save(); dialog.accept()
            if enable or self.direct_mode.get(): self.direct_changed(True)
        apply.clicked.connect(commit); dialog.exec()

    def render_clips(self):
        ids=engine.sorted_clip_ids(self.agent.local_files,self.clip_origins,self.config.get('clip_settings',{}),self.sort_keys)
        query=self.view.search.text().strip().casefold(); settings=self.config.get('clip_settings',{}); colors=self.config.get('clip_labels',{})
        self.file_ids=[key for key in ids if not query or query in self.agent.local_files[key].stem.casefold() or query in settings.get(key,{}).get('label','').casefold()]
        rows=[]
        for key in self.file_ids:
            item=settings.get(key,{}); label=item.get('label','')
            rows.append((key,self.agent.local_files[key].stem,item.get('volume',0),self.clip_origins.get(key)=='Shared',label,colors.get(label)))
        self.view.clip_model.load(rows)

    def sort_clips(self,column):
        reverse=not self.sort_keys[0][1] if self.sort_keys and self.sort_keys[0][0]==column else False
        self.sort_keys=[(column,reverse)]+[(c,r) for c,r in self.sort_keys if c!=column]
        self.config['clip_sort_keys']=[list(key) for key in self.sort_keys]; self.save(); self.render_clips()

    def save_columns(self):
        header=self.view.clips.horizontalHeader()
        self.config['clip_column_order']=[engine.CLIP_COLUMNS[header.logicalIndex(n)] for n in range(4)]
        self.config['clip_column_widths']={name:header.sectionSize(n) for n,name in enumerate(engine.CLIP_COLUMNS)}
        self.save_later()

    def set_volume(self,key,value):
        self.clip_setting(key)['volume']=value; self.save_later()

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self.view,'Local clips folder',self.folder.get())
        if folder:
            self.config['mp3_folder']=folder; self.folder.set(folder); self.save(); self.refresh_files()

    def import_files(self,paths):
        if not self.folder.get():
            self.choose_folder()
        if not self.folder.get(): return
        folder=self.folder.get()
        def worker():
            try:
                copied,skipped=engine.copy_soundboard_files(paths,folder)
                self.agent.events.put({'import_done':True,'folder':folder,'status':f'Imported {len(copied)} clips; skipped {len(skipped)}.'})
            except Exception as exc: self.agent.events.put({'error':str(exc)})
        threading.Thread(target=worker,daemon=True).start()

    def play_clip(self,key):
        if key not in self.agent.local_files or key in self.editing_clips: return
        if not self.view.direct.isEnabled(): self.status.set('Wait for direct audio setup to finish.'); return
        if self.config.get('direct_soundboard'):
            asyncio.run_coroutine_threadsafe(self.agent.play_direct_clip(key),self.loop)
        else:
            self.send({'type':'local_play','guild_id':None,'file_id':key,'title':self.agent.local_files[key].name})

    def label_menu(self,key,index):
        self.view.clips.selectRow(index.row()); menu=QMenu(self.view)
        menu.addAction('No label',lambda:self.set_label(key,''))
        for name,color in self.config.get('clip_labels',{}).items():
            action=menu.addAction(name); action.triggered.connect(lambda checked=False,name=name:self.set_label(key,name))
        menu.exec(self.view.clips.viewport().mapToGlobal(self.view.clips.visualRect(index).bottomLeft()))

    def set_label(self,key,name):
        self.clip_setting(key)['label']=name; self.save(); self.render_clips()
        if key in self.file_ids: self.view.clips.selectRow(self.file_ids.index(key))

    def clip_menu(self,point):
        index=self.view.clips.indexAt(point)
        if not index.isValid(): return
        key=index.data(Qt.ItemDataRole.UserRole); self.view.clips.selectRow(index.row()); self.context_clip_id=key
        menu=QMenu(self.view); path=self.agent.local_files[key]; local=self.clip_origins.get(key)=='Local'
        for text,delete in [('Share',False),('Share then del local',True)]:
            action=menu.addAction(text); action.setEnabled(local and not self.sync_running); action.triggered.connect(lambda checked=False,delete=delete:self.share_local(key,delete))
        menu.addSeparator(); menu.addAction('Rename',lambda:self.rename(key)); menu.addAction('Delete',lambda:self.delete(key)); menu.addSeparator()
        menu.addAction('Add Label',lambda:self.add_label_for(key)); colors=menu.addMenu('Change Label Color'); label=self.clip_setting(key).get('label'); colors.setEnabled(bool(label))
        for name,color in engine.LABEL_COLORS.items():
            action=colors.addAction(name); action.setIcon(self.color_icon(color)); action.triggered.connect(lambda checked=False,color=color:self.label_color(label,color))
        menu.exec(self.view.clips.viewport().mapToGlobal(point))

    def color_icon(self,color):
        from PySide6.QtGui import QPixmap,QIcon
        pixmap=QPixmap(14,14); pixmap.fill(QColor(color)); return QIcon(pixmap)

    def add_label_for(self,key):
        name,ok=QInputDialog.getText(self.view,'Add Label','Label name:')
        if ok and name.strip():
            name=name.strip(); self.config.setdefault('clip_labels',{}).setdefault(name,engine.LABEL_COLORS['Light Blue']); self.set_label(key,name)

    def label_color(self,name,color):
        if name: self.config.setdefault('clip_labels',{})[name]=color; self.save(); self.render_clips()

    def rename(self,key):
        path=self.agent.local_files[key]; name,ok=QInputDialog.getText(self.view,'Rename clip','New filename (extension is preserved):',text=path.stem)
        if ok:
            try: engine.soundboard_rename_target(path,name)
            except ValueError as exc: QMessageBox.warning(self.view,'Rename',str(exc)); return
            self.context_clip_id=key; self.start_clip_edit(path,name)

    def delete(self,key):
        path=self.agent.local_files[key]; warning=f'Delete {path.name}?'
        if self.clip_origins.get(key)=='Shared' and self.config.get('shared_owner'): warning+='\nThis deletes the GitHub shared file for everyone.'
        if QMessageBox.question(self.view,'Delete clip',warning)==QMessageBox.StandardButton.Yes:
            self.context_clip_id=key; self.start_clip_edit(path)

    def start_clip_edit(self,path,new_name=None):
        key=self.context_clip_id
        if key in self.editing_clips or (self.clip_origins.get(key)=='Shared' and self.sync_running):
            self.status.set('Wait for the current clip operation to finish.'); return
        try:
            target=engine.soundboard_rename_target(path,new_name) if new_name is not None else None
        except ValueError as exc:
            QMessageBox.warning(self.view,'Could not edit clip',str(exc)); return
        self.editing_clips.add(key)
        async def edit():
            if self.clip_origins.get(key)=='Shared' and self.config.get('shared_owner'):
                await asyncio.to_thread(engine.edit_shared_clip,self.config,path,target.name if target else None)
            return await self.agent.edit_clip_file(key,path,new_name)
        future=asyncio.run_coroutine_threadsafe(edit(),self.loop)
        def done(result):
            try: self.agent.events.put({'clip_edit_done':key,'status':result.result(),'renamed_path':str(target) if target else None})
            except Exception as exc: self.agent.events.put({'clip_edit_done':key,'clip_edit_error':str(exc)})
        future.add_done_callback(done)

    def share_local(self,key,delete=False):
        if self.sync_running or self.editing_clips: return
        path=self.agent.local_files[key]; self.editing_clips.add(key); settings=dict(self.clip_setting(key)); config=self.config
        async def work():
            try:
                await asyncio.to_thread(engine.check_share_target,config,path)
                target=await self.agent.share_clip_file(key,path,engine.SHARED_CLIPS_DIR,delete)
                self.agent.events.put({'share_done':key,'shared_path':str(target),'share_settings':settings})
            except Exception as exc: self.agent.events.put({'share_done':key,'share_error':str(exc)})
        asyncio.run_coroutine_threadsafe(work(),self.loop)

    def ordered_selection(self,kind):
        tree=self.favorites_view if kind=='favorites' else self.queue_view
        selected=set(tree.selection()); return [key for key in self.visible_music_rows(tree) if key in selected]

    def drop_music(self,payload,before=None):
        if payload.get('kind')=='favorites':
            self.favorites_view.selection_set([key for key in payload.get('ids',[]) if key in self.favorite_items]); self.add_favorite_selection()
        elif payload.get('kind')=='queue':
            ids=[key for key in payload.get('ids',[]) if self.queue_view.exists(key)]
            if ids: self.send({'type':'music_control','guild_id':None,'action':'move','track_ids':ids,'before_id':before}); self.refresh_music()

    def enqueue_item(self,item):
        key=item.data(0,Qt.ItemDataRole.UserRole); saved=self.favorite_items.get(key)
        if saved: self.send({'type':'music_enqueue','guild_id':None,'url':saved['url'],'playlist':bool(saved.get('playlist'))})

    def expand_playlist(self,item):
        self.view.favorites.setCurrentItem(item); self.expand_favorite_playlist()

    def select_song(self,item):
        if len(self.queue_view.selection())!=1 or QApplication.keyboardModifiers() & (Qt.KeyboardModifier.ShiftModifier|Qt.KeyboardModifier.ControlModifier): return
        self.send({'type':'music_control','guild_id':None,'action':'select','track_id':item.data(0,Qt.ItemDataRole.UserRole)})

    def favorite_selection(self):
        for key in self.ordered_selection('queue'):
            self.queue_context_id=key; self.favorite_song()

    def remove_saved(self,item):
        if item:
            self.favorite_context_id=item.parent().data(0,Qt.ItemDataRole.UserRole) if item.parent() else item.data(0,Qt.ItemDataRole.UserRole)
            self.remove_favorite()

    def save_playlist_link(self):
        text,ok=QInputDialog.getText(self.view,'Save playlist','YouTube playlist URL:')
        url=engine.clipboard_youtube_link(text)
        if not ok: return
        if not url or not engine.re.search(r'[?&]list=[^&]+',url): QMessageBox.warning(self.view,'Playlist','Enter a YouTube playlist link.'); return
        title,ok=QInputDialog.getText(self.view,'Playlist name','Name:')
        if ok and title.strip(): self.save_music_favorite({'url':url,'title':title.strip(),'playlist':True})

    def manage_voices(self):
        dialog=QDialog(self.view); dialog.setWindowTitle('TacoBot · Voice models'); dialog.resize(900,560); layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Download and enable the voice models you want to keep locally.'))
        search=QLineEdit(); search.setPlaceholderText('Filter voice models'); layout.addWidget(search)
        tree=QTreeWidget(); tree.setColumnCount(4); tree.setHeaderLabels(['Voice model','Downloaded','Enabled','Speakers']); tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); tree.setRootIsDecorated(False); tree.header().resizeSection(0,490); layout.addWidget(tree,1)
        self.voice_models=Tree(tree); self.voice_filter=Value(getter=search.text); self.voice_model_status=Value(setter=lambda text:status.setText(text)); self.voice_manager_buttons=[]
        buttons=QHBoxLayout(); layout.addLayout(buttons)
        for text,action in [('Download & enable','download'),('Enable','enable'),('Disable','disable'),('Remove download','remove'),('Use selected','use')]:
            widget=QPushButton(text); widget.clicked.connect(lambda checked=False,action=action:self.change_voice_models(action)); buttons.addWidget(widget); self.voice_manager_buttons.append(Control(widget))
        status=QLabel('Select one or more models.'); layout.addWidget(status); self.voice_manager=dialog
        search.textChanged.connect(self.refresh_voice_models); self.refresh_voice_models(); dialog.finished.connect(lambda:self.clear_voice_manager()); dialog.show()

    def clear_voice_manager(self):
        self.voice_manager=None

    def refresh_voice_models(self):
        if self.voice_manager is None: return
        selected=self.voice_models.selection(); self.voice_models.delete(*self.voice_models.get_children()); query=self.voice_filter.get().casefold(); enabled=self.config.get('tts_enabled_voices',[engine.VOICE])
        for label,voice in engine.VOICE_OPTIONS.items():
            if query in label.casefold(): self.voice_models.insert('','end',iid=voice,values=(label,'Yes' if self.agent.speech.downloaded(voice) else 'No','✓' if voice in enabled else '',engine.VOICE_CATALOG[voice]['speakers']))
        self.voice_models.selection_set([key for key in selected if self.voice_models.exists(key)])

    def poll(self):
        started=time.monotonic()
        while not self.agent.events.empty() and time.monotonic()-started<.004:
            self.handle_event(self.agent.events.get_nowait())
        if self.started and time.monotonic()>=self.next_music_refresh:
            self.next_music_refresh=time.monotonic()+2
            if self.agent.websocket: self.refresh_music()
        if self.started: self.poll_updates()

    def handle_event(self,event):
        if 'relay_name' in event: self.view.setWindowTitle('TacoBot — '+event['relay_name']); self.identity.set(event['relay_name']); self.save()
        if event.get('token_required'):
            token,ok=QInputDialog.getText(self.view,'New token needed','Use !token in Discord, then paste the new token:',QLineEdit.EchoMode.Password)
            self.config['relay_token']=token.strip() if ok else ''; self.save()
            if ok and token.strip(): self.loop.call_soon_threadsafe(self.agent.token_ready.set)
        if 'direct_ready' in event or 'direct_failed' in event:
            enabled=event.get('direct_ready',False); self.set_direct_check(enabled); self.config['direct_soundboard']=enabled; self.view.direct.setEnabled(True); self.save()
        if 'normalization_cache' in event: self.config['clip_normalization']=event['normalization_cache']; self.save_later()
        if event.get('update_checked'):
            self.update_check_running=False; self.view.check.setEnabled(True)
            if event.get('update_stage') is not None: self.pending_update=(event['update_stage'],event.get('update_manual',False)); self.update_idle_since=None; self.view.install.setEnabled(True)
        if 'library' in event and event['generation']==self.scan_generation:
            self.agent.local_files=event['library']; self.clip_origins=event['origins']; self.render_clips()
            if self.started: asyncio.run_coroutine_threadsafe(self.agent.preload_normalization(dict(self.agent.local_files)),self.loop)
        if event.get('import_done') and event.get('folder')==self.folder.get(): self.refresh_files()
        if event.get('sync_done'): self.sync_running=False; self.refresh_files()
        if event.get('share_done'):
            self.editing_clips.discard(event['share_done'])
            if not event.get('share_error'):
                key=hashlib.sha256(str(Path(event['shared_path']).resolve()).encode()).hexdigest(); self.config.setdefault('clip_settings',{})[key]=event['share_settings']; self.save(); self.sync_shared()
            self.refresh_files()
        if event.get('clip_edit_done'):
            key=event['clip_edit_done']; self.editing_clips.discard(key)
            if event.get('renamed_path'):
                settings=self.config.setdefault('clip_settings',{}); old=settings.pop(key,None)
                if old is not None: settings[hashlib.sha256(str(Path(event['renamed_path']).resolve()).encode()).hexdigest()]=old; self.save()
            self.refresh_files()
        if 'favorite_playlist' in event:
            key=event['favorite_playlist']; getattr(self,'loading_playlists',set()).discard(key)
            if 'tracks' in event and key in self.favorite_items: self.favorite_items[key]['tracks']=event['tracks']; self.save(); self.render_favorites()
        if event.get('voice_models_done'):
            enabled=set(self.config.get('tts_enabled_voices',[engine.VOICE])); finished=event['voice_models_finished']
            if event['voice_models_action']=='download': enabled.update(finished)
            else: enabled.difference_update(finished)
            self.config['tts_enabled_voices']=sorted(enabled); self.refresh_enabled_voices(); self.refresh_voice_models()
            if self.voice_manager:
                for control in self.voice_manager_buttons: control.configure(state='normal')
                self.voice_model_status.set(event.get('error') or 'Voice models updated.')
        if event.get('translation_done'):
            self.view.translate.setEnabled(True)
            if 'translated_text' in event: self.view.translation.setPlainText(event['translated_text']); self.translated_language=event['translated_language']; self.view.translated_play.setEnabled(True)
        if event.get('tts_done'): self.view.play_text_button.setEnabled(True)
        if 'music' in event: self.render_music(event['music'])
        error=event.get('error') or event.get('clip_edit_error') or event.get('share_error') or event.get('direct_failed')
        if error or event.get('status'): self.status.set(error or event['status'])

    def render_music(self,music):
        if music==self.last_music: return
        self.last_music=music; self.view.now_playing.setText(music.get('current') or 'Your next song starts here')
        ids=self.queue_view.selection(); scroll=self.view.queue.verticalScrollBar().value()
        self.view.queue.setUpdatesEnabled(False)
        try:
            self.queue_view.delete(*self.queue_view.get_children())
            for song in music.get('playlist',[]):
                label=('Paused: ' if music.get('paused') else 'Playing: ') if song.get('current') else ''
                self.queue_view.insert('','end',iid=song['id'],values=(label+song['title'],),tags=('current',) if song.get('current') else ())
            self.queue_view.selection_set([key for key in ids if self.queue_view.exists(key)]); self.view.queue.verticalScrollBar().setValue(scroll)
        finally: self.view.queue.setUpdatesEnabled(True)
        playing=bool(music.get('current')) and not music.get('paused'); self.view.play.setText('Pause' if playing else 'Play')
        from .qt_desktop import icon
        self.view.play.setIcon(icon('pause' if playing else 'play'))
        self.view.previous.setEnabled(bool(music.get('has_previous')))

    def close(self):
        self.view.close()

    def shutdown(self):
        self.poll_timer.stop(); self.save_timer.stop(); self.agent.direct_audio.close(); self.config['clip_normalization']=self.agent.normalization_cache; self.save()
        if self.started:
            def cancel():
                for task in asyncio.all_tasks(self.loop): task.cancel()
            self.loop.call_soon_threadsafe(cancel)
        else:
            self.loop.close()
