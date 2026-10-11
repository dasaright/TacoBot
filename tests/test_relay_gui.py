import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from relay_agent.main import RelayWindow


class ClipClickTests(unittest.TestCase):
    def window(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.get_children.return_value = []
        window.listbox.identify_row.return_value = "clip"
        window.listbox.identify_region.return_value = "cell"
        window.listbox.identify_column.return_value = "#1"
        window.listbox.bbox.return_value = (0, 0, 150, 20)
        window.listbox.winfo_width.return_value = 200
        window.file_ids = ['clip']
        window.clip_pressed_index = "clip"
        window.target_id = Mock(return_value='1')
        window.destination = Mock()
        window.destination.current.return_value = 0
        window.targets = [{'id':'1'}]
        window.config = {}
        window.agent = SimpleNamespace(local_files={'clip': Path('clip.mp3')})
        window.status = Mock()
        window.send = Mock()
        return window

    def test_release_from_drag_drop_does_not_play_clip(self):
        window = self.window()
        window.clip_pressed_index = None
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=10))
        window.send.assert_not_called()

    def test_click_below_rows_does_not_replay_previous_selection(self):
        window = self.window()
        window.listbox.identify_row.return_value = ""
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=100))
        window.send.assert_not_called()

    def test_click_in_blank_space_right_of_filename_does_not_play(self):
        window = self.window()
        window.listbox.identify_column.return_value = "#2"
        window.play_selected(SimpleNamespace(widget=window.listbox, x=180, y=10))
        window.send.assert_not_called()

    def test_click_on_another_widget_never_plays_clip(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=Mock(), x=30, y=10))
        window.send.assert_not_called()

    def test_file_row_click_plays_exact_file(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=10))
        window.send.assert_called_once_with({'type': 'local_play', 'guild_id': None, 'file_id': 'clip', 'title': 'clip.mp3'})


class SortingTests(unittest.TestCase):
    def test_shared_sort_toggles_group_and_keeps_names_alphabetical(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.get_children.return_value = []
        window.agent = SimpleNamespace(local_files={'1':Path('Z.mp3'), '2':Path('A.mp3'), '3':Path('B.ogg')})
        window.clip_origins = {'1':'Local','2':'Local','3':'Shared'}
        window.clip_widgets = {}
        window.preload_clip_widgets = Mock()
        window.root = Mock()
        window.config = {}
        window.sort_keys = [('name', False)]
        window.sort_column = 'name'
        window.sort_reverse = False
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['3','2','1'])
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['2','1','3'])

class SongClickTests(unittest.TestCase):
    def test_song_click_sends_stable_song_id(self):
        window=RelayWindow.__new__(RelayWindow)
        window.queue_view=Mock()
        window.queue_view.selection.return_value=[]
        window.queue_view.identify_row.return_value='song-id'
        window.queue_view.identify_region.return_value='cell'
        window.queue_pressed='song-id'
        window.last_music={'playlist':[]}
        window.target_id=Mock(return_value='1')
        window.send=Mock()
        window.song_selected(SimpleNamespace(widget=window.queue_view,x=20,y=20))
        window.send.assert_called_once_with({'type':'music_control','guild_id':'1','action':'select','track_id':'song-id'})

class HeaderDragTests(unittest.TestCase):
    def test_saved_order_validation_and_move_past_shared(self):
        from relay_agent.main import clip_column_order, reordered_columns
        order = clip_column_order(None)
        self.assertEqual(reordered_columns(order, 'volume', 'shared'), ['name','shared','volume','label'])
        self.assertEqual(clip_column_order(['name','name','volume','label']), order)

    def test_drag_changes_order_without_sorting_or_playing(self):
        window = RelayWindow.__new__(RelayWindow)
        window.root = Mock(); window.save = Mock(); window.send = Mock(); window.sort_clips = Mock()
        window.listbox = Mock(); window.config = {}; window.column_order = ['name','volume','shared','label']
        window.header_drag = {'column':'volume','x':100,'moved':True}
        window.listbox.identify_column.return_value = '#3'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=250,y=10))
        self.assertEqual(window.config['clip_column_order'], ['name','shared','volume','label'])
        window.sort_clips.assert_not_called(); window.send.assert_not_called()

    def test_reordered_name_is_only_playable_column(self):
        window = ClipClickTests().window()
        window.column_order = ['shared','volume','label','name']
        window.listbox.identify_column.return_value = '#1'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=30,y=10))
        window.send.assert_not_called()
        window.clip_pressed_index = 'clip'
        window.listbox.identify_column.return_value = '#4'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=30,y=10))
        window.send.assert_called_once()

    def test_real_tabs_and_reordered_control_positions(self):
        import tkinter as tk
        from unittest.mock import patch
        from relay_agent.main import create_root
        try:
            root = create_root()
        except tk.TclError:
            self.skipTest('Requires desktop display; Windows packaging CI runs this')
        try:
            with patch('threading.Thread.start'):
                window = RelayWindow(root, {})
            window.save = Mock(); window.send = Mock()
            root.update()
            window.notebook.select(1); root.update()
            # The lists must shrink before the footer loses its allocated space.
            for size in ('1180x780', '1160x620', '1400x900'):
                root.geometry(size); root.update()
                for button in window.clip_action_buttons:
                    self.assertTrue(button.winfo_viewable())
                    self.assertGreater(button.winfo_height(), 1)
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                         root.winfo_rooty() + root.winfo_height())
                self.assertLessEqual(window.notebook.winfo_y() + window.notebook.winfo_height(),
                                     window.status_label.winfo_rooty() - root.winfo_rooty())
            root.geometry('1180x780'); window.notebook.select(0); root.update()
            self.assertEqual(window.direct_checkbox.master, window.tab_buttons[0].master)
            self.assertFalse(window.stop_clips_button.winfo_viewable())
            self.assertTrue(window.toggle_button.winfo_viewable())
            window.set_music_playing(True)
            self.assertEqual(window.toggle_button.cget("text"), "Pause")
            window.set_music_playing(False)
            self.assertEqual(window.toggle_button.cget("text"), "Play")
            window.notebook.select(2); root.update()
            self.assertTrue(window.tts_voice_picker.winfo_viewable())
            self.assertFalse(window.toggle_button.winfo_viewable())
            self.assertFalse(window.stop_clips_button.winfo_viewable())
            window.notebook.select(1); root.update()
            self.assertFalse(window.tts_voice_picker.winfo_viewable())
            self.assertTrue(window.stop_clips_button.winfo_viewable())
            self.assertEqual(int(window.clip_search_entry.cget("width")), 15)
            from tkinter import font, ttk
            window.bot_clip_volume.set(100)
            window.set_bot_clip_volume("100")
            root.update()
            self.assertEqual(window.bot_volume_text.get(), "100%")
            label_font = font.Font(root=root, font=ttk.Style(root).lookup("Muted.TLabel", "font"))
            self.assertGreaterEqual(window.bot_volume_label.winfo_width(), label_font.measure("100%") + 6)
            self.assertGreaterEqual(window.refresh_button.graphite_icon.width(), 28)
            self.assertEqual(root.title(), "TacoBot")
            self.assertEqual(window.stop_clips_button.master, window.refresh_button.master)
            self.assertEqual(window.clip_search_entry.master.master.master, window.refresh_button.master)
            self.assertEqual(window.stop_clips_button.winfo_rooty(), window.refresh_button.winfo_rooty())
            window.notebook.select(1); root.update()
            self.assertLess(window.stop_clips_button.winfo_rootx(), window.refresh_button.winfo_rootx())
            self.assertFalse(window.clear_search_button.winfo_ismapped())
            window.clip_search.set("test"); root.update()
            self.assertTrue(window.clear_search_button.winfo_ismapped())
            window.clear_clip_search(); root.update()
            self.assertEqual(window.clip_search.get(), "")
            self.assertFalse(window.clear_search_button.winfo_ismapped())
            self.assertEqual(window.folder_label.master.master, window.listbox.master)
            window.notebook.select(0); root.update()
            self.assertNotEqual(window.refresh_button.master, window.direct_checkbox.master)
            self.assertLess(window.refresh_button.winfo_x(), window.sync_button.winfo_x())
            self.assertLess(window.sync_button.winfo_x(), window.direct_checkbox.winfo_x())
            self.assertEqual(window.direct_checkbox.pack_info()['anchor'], 'center')
            self.assertEqual(window.auto_update_checkbox.pack_info()['anchor'], 'center')

            self.assertEqual(len(window.notebook.tabs()), 3)
            window.manage_voices()
            root.update()
            self.assertGreater(len(window.voice_models.get_children()), 60)
            window.voice_manager.destroy()
            self.assertFalse(window.direct_mode.get())
            self.assertEqual(window.direct_checkbox.cget('text'), 'Direct')
            self.assertEqual(window.direct_checkbox.master, window.auto_update_checkbox.master)
            self.assertFalse(hasattr(window, 'destination'))
            self.assertGreater(window.tab_buttons[0].winfo_height(), window.tab_buttons[1].winfo_height())
            from tkinter import ttk
            ttk.Style(root).configure('Treeview.Heading', padding=(8, 18))
            root.update()  # Flush the theme change after exercising both tabs.
            window.agent.local_files = {'clip':Path('Test.mp3')}
            window.clip_origins = {'clip':'Shared'}
            window.render_clips(); root.update()
            window.notebook.select(1); root.update()
            window.position_clip_widgets(); root.update()
            self.assertEqual(window.clip_canvases['label'].winfo_y(), window.listbox.bbox('clip', 'label')[1])
            self.assertEqual(window.clip_canvases['volume'].winfo_y(), window.listbox.bbox('clip', 'volume')[1])
            window.clip_search.set('not found'); root.update()
            self.assertFalse(window.listbox.get_children())
            self.assertFalse(window.clip_canvases['label'].winfo_ismapped())
            self.assertIn('clip', window.agent.local_files)
            window.clip_search.set('tEsT'); root.update()
            self.assertEqual(window.listbox.get_children(), ('clip',))
            window.config.setdefault('clip_settings', {})['clip'] = {'label': 'Special'}
            window.clip_search.set('special'); root.update()
            self.assertEqual(window.listbox.get_children(), ('clip',))
            window.clip_search.set(''); root.update()
            self.assertGreater(window.tab_buttons[1].winfo_height(), window.tab_buttons[0].winfo_height())
            self.assertEqual(len(window.clip_canvases), 3)
            window.listbox.column('label', width=100)
            root.update()
            total_width = sum(window.listbox.column(c, 'width') for c in window.column_order)
            self.assertLess(total_width, window.listbox.winfo_width() - 10)
            self.assertEqual(window.listbox.identify_column(total_width + 8), '')
            label_menu = window.clip_widgets['clip']['menu']
            root.tk.call(label_menu['postcommand'])
            self.assertEqual(window.listbox.selection(), ('clip',))
            self.assertFalse(any(call.args[0].get('type') == 'local_play' for call in window.send.call_args_list))
            self.assertEqual(window.toolbar_buttons[0].master, window.tab_toolbars[1])
            self.assertEqual(window.update_button.master, window.direct_checkbox.master)
            self.assertEqual(window.clip_action_buttons[0].master, window.tab_toolbars[1])
            from tkinter import ttk
            self.assertEqual(int(ttk.Style(root).lookup('Soundboard.Treeview', 'rowheight')), 33)
            window.header_drag = {'column':'volume','x':100,'moved':True}
            # Drop on the Shared header using its actual on-screen coordinate.
            box = window.listbox.bbox('clip','shared')
            window.play_selected(SimpleNamespace(widget=window.listbox,x=box[0]+box[2]//2,y=5))
            root.update()
            self.assertEqual(window.column_order, ['name','shared','volume','label'])
            frame, label = window.clip_canvases['volume'], window.clip_canvases['label']
            self.assertEqual(frame.winfo_x(), window.listbox.bbox('clip','volume')[0])
            self.assertEqual(label.winfo_x(), window.listbox.bbox('clip','label')[0])
            window.notebook.select(0); root.update()
            self.assertLess(window.favorites_view.winfo_rootx(), window.queue_view.winfo_rootx())
            window.last_music = {'playlist': [{'id': 'test', 'title': 'Favorite song', 'url': 'https://youtu.be/abc'}]}
            window.queue_context_id = 'test'
            window.favorite_song()
            self.assertEqual(len(window.favorites_view.get_children()), 1)
            self.assertEqual(window.queue_menu.entrycget(0, 'label'), 'Favorite')
            self.assertEqual(window.queue_menu.entrycget(1, 'label'), 'Remove from queue')
            window.save_music_favorite({"url": "https://youtube.com/playlist?list=PL1", "title": "Mix", "playlist": True,
                "tracks": [{"title": "One", "webpage_url": "https://youtu.be/one"}]})
            parent = next(key for key, item in window.favorite_items.items() if item.get("playlist"))
            window.favorites_view.item(parent, open=True); root.update()
            children = window.favorites_view.get_children(parent)
            self.assertEqual(len(children), 1)
            self.assertEqual(window.favorites_view.item(children[0], "values"), ("One",))
            self.assertEqual(str(window.queue_view.cget("selectmode")), "extended")
            self.assertTrue(window.paste_playlist_switch.winfo_viewable())
            window.queue_view.insert("", "end", iid="one", values=("One",))
            window.queue_view.insert("", "end", iid="two", values=("Two",))
            window.queue_view.selection_set(("one", "two"))
            window.send.reset_mock()
            window.remove_queue_song()
            self.assertEqual(window.send.call_args_list[0].args[0]["track_ids"], ["one", "two"])
            window.notebook.select(1); root.update()
            self.assertFalse(window.paste_playlist_switch.winfo_viewable())
            self.assertTrue(window.listbox.winfo_ismapped())
            canvases = dict(window.clip_canvases)
            for tab in (0, 2, 1, 0, 1):
                window.notebook.select(tab); root.update()
            self.assertEqual(window.clip_canvases, canvases)
            window.notebook.select(0); root.update()
            self.assertEqual(str(window.favorites_view.cget("selectmode")), "extended")
            self.assertEqual(window.save_playlist_button.master, window.add_selection_button.master)
            self.assertGreater(window.add_selection_button.winfo_x(), window.save_playlist_button.winfo_x())
            self.assertTrue(window.add_selection_button.winfo_viewable())
            self.assertNotEqual(str(window.add_selection_button.cget("image")), str(window.save_playlist_button.cget("image")))
            queue_ids = list(window.queue_view.get_children())
            box = window.queue_view.bbox(queue_ids[0])
            event = SimpleNamespace(widget=window.queue_view, x=box[0]+30, y=box[1]+5,
                x_root=window.queue_view.winfo_rootx()+box[0]+30, y_root=window.queue_view.winfo_rooty()+box[1]+5, state=0)
            window.song_mouse_down(event)
            window.music_drag["moved"] = True
            window.send.reset_mock()
            window.music_drag_release(SimpleNamespace(widget=window.queue_view,
                x_root=window.queue_view.winfo_rootx()+30,
                y_root=window.queue_view.winfo_rooty()+window.queue_view.winfo_height()-10))
            self.assertEqual(window.send.call_args_list[0].args[0]["action"], "move")
        finally:
            root.destroy()
