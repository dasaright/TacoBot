import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
if sys.platform!='win32': os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from relay_agent.desktop import application
from relay_agent.qt_desktop import Desktop


class QtDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=application()

    def setUp(self):
        self.saved={'clip_labels':{'Memes':'#d9b3ff'},'clip_settings':{'a':{'label':'Memes','volume':20}},
                    'clip_sort_keys':[['shared',False],['name',True]],'clip_column_order':['name','shared','volume','label'],
                    'music_favorites':[{'title':'Playlist','url':'https://youtube.com/playlist?list=PL1','playlist':True,
                     'tracks':[{'title':'One','webpage_url':'https://youtu.be/one'},{'title':'Two','webpage_url':'https://youtu.be/two'}]},
                     {'title':'Other','url':'https://youtu.be/other','playlist':False}]}
        self.window=Desktop(self.saved,start_agent=False); self.window.show(); self.app.processEvents()
        self.controller=self.window.controller; self.controller.send=Mock()
        self.controller.agent.local_files={'a':Path('A.ogg'),'b':Path('B.mp3')}; self.controller.clip_origins={'a':'Local','b':'Shared'}; self.controller.render_clips(); self.app.processEvents()

    def tearDown(self):
        self.window.close(); self.app.processEvents()

    def test_all_pages_resident_and_switch_does_not_rebuild_models(self):
        w=self.window; model=w.clip_model; rows=model.rows
        with patch.object(self.controller,'render_clips') as rebuild, patch.object(self.controller,'refresh_files') as scan:
            for n in [1,2,0]*10:
                w.tabs[n].pressed.emit(); self.app.processEvents()
                self.assertEqual(w.pages.currentIndex(),n)
                self.assertTrue(all(page.isVisible() for page in w.page_widgets))
            rebuild.assert_not_called(); scan.assert_not_called()
        self.assertIs(w.clip_model,model); self.assertIs(w.clip_model.rows,rows)
        self.assertFalse(any(page.testAttribute(Qt.WidgetAttribute.WA_NativeWindow) for page in w.page_widgets))

    def test_sort_settings_column_order_and_search_survive_native_gui(self):
        c=self.controller; w=self.window
        self.assertEqual(c.file_ids,['b','a'])
        self.assertEqual([w.clips.horizontalHeader().logicalIndex(n) for n in range(4)],[0,2,1,3])
        w.search.setText('memes'); self.app.processEvents(); self.assertEqual(c.file_ids,['a'])
        w.search.clear(); c.sort_clips('shared'); self.assertEqual(c.file_ids,['a','b'])
        self.assertEqual(self.saved['clip_sort_keys'][0],['shared',True])
        self.assertEqual(self.saved['clip_settings']['a'],{'label':'Memes','volume':20})

    def test_clip_click_only_name_and_slider_does_not_play(self):
        w=self.window; w.select_tab(1); self.app.processEvents()
        index=w.clip_model.index(0,0); rect=w.clips.visualRect(index)
        QTest.mouseClick(w.clips.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
        self.assertEqual(self.controller.send.call_args.args[0]['type'],'local_play')
        self.controller.send.reset_mock()
        index=w.clip_model.index(0,1); rect=w.clips.visualRect(index)
        key=index.data(Qt.ItemDataRole.UserRole)
        QTest.mouseClick(w.clips.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.right()-60,rect.center().y()))
        self.assertGreater(self.saved['clip_settings'][key]['volume'],80)
        self.controller.send.assert_not_called()

    def test_native_favorite_drag_payload_preserves_playlist_children(self):
        c=self.controller; tree=c.favorites_view
        playlist=tree.get_children()[0]; children=tree.get_children(playlist)
        tree.items[playlist].setExpanded(True); self.app.processEvents()
        tree.selection_set(children)
        selected=c.ordered_selection('favorites'); self.assertEqual(selected,list(children))
        c.drop_music({'kind':'favorites','ids':selected})
        self.assertEqual(c.send.call_args.args[0]['items'],[{'url':'https://youtu.be/one','playlist':False},{'url':'https://youtu.be/two','playlist':False}])
        tree.selection_set([playlist,*children]); c.add_favorite_selection()
        self.assertEqual(c.send.call_args.args[0]['items'],[{'url':'https://youtube.com/playlist?list=PL1','playlist':True}])

    def test_queue_pause_icon_selection_remove_and_move(self):
        c=self.controller; w=self.window
        c.render_music({'current':'One','paused':False,'has_previous':True,'playlist':[{'id':'one','title':'One','url':'https://youtu.be/one','current':True},{'id':'two','title':'Two','url':'https://youtu.be/two','current':False}]})
        self.assertEqual(w.play.text(),'Pause'); self.assertTrue(w.previous.isEnabled())
        c.queue_view.selection_set(['one','two']); c.remove_queue_song()
        self.assertEqual(c.send.call_args_list[0].args[0]['track_ids'],['one','two'])
        c.drop_music({'kind':'queue','ids':['two']},'one')
        self.assertIn({'type':'music_control','guild_id':None,'action':'move','track_ids':['two'],'before_id':'one'},[call.args[0] for call in c.send.call_args_list])
        c.render_music({'current':'One','paused':True,'playlist':[]})
        self.assertEqual(w.play.text(),'Play')

    def test_tts_shortcuts_and_each_volume_is_independent(self):
        w=self.window; c=self.controller; w.select_tab(2); self.app.processEvents()
        w.tts.setPlainText('Hello there'); QTest.keyClick(w.tts,Qt.Key.Key_Backspace,Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(w.tts.toPlainText(),'')
        with patch.object(c,'play_text') as play:
            QTest.keyClick(w.tts,Qt.Key.Key_Return); play.assert_called_once()
        w.music_volume.setValue(45); w.clip_volume.setValue(60); w.tts_volume.setValue(75)
        self.assertEqual([self.saved[k] for k in ('music_volume','bot_clip_volume','tts_volume')],[45,60,75])
        w.playlist_toggle.setChecked(True); self.assertTrue(self.saved['paste_playlist'])

    def test_voice_manager_still_lists_download_enable_and_speaker_options(self):
        c=self.controller; c.manage_voices(); self.app.processEvents()
        self.assertGreater(len(c.voice_models.get_children()),60)
        self.assertEqual(len(c.voice_manager_buttons),5)
        self.assertGreater(self.window.voice.count(),0); self.assertGreater(self.window.speaker.count(),0)
        c.voice_manager.close(); self.app.processEvents()
