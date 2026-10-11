import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from relay_agent.main import RelayWindow

class FavoriteTests(unittest.TestCase):
    def window(self):
        w = RelayWindow.__new__(RelayWindow)
        w.config = {}; w.save = Mock(); w.status = Mock(); w.send = Mock()
        w.favorites_view = Mock(); w.favorites_view.get_children.return_value = []
        w.favorites_view.identify_region.return_value = 'cell'
        w.queue_context_id = 'track'
        w.queue_view = Mock(); w.queue_view.selection.return_value = []
        w.last_music = {'playlist': [{'id': 'track', 'title': 'Song', 'url': 'https://youtu.be/abc'}]}
        return w

    def test_favorite_persists_and_deduplicates_and_double_click_enqueues(self):
        w = self.window()
        w.favorite_song(); w.favorite_song()
        self.assertEqual(w.config['music_favorites'], [{'title': 'Song', 'url': 'https://youtu.be/abc', 'playlist': False}])
        w.save.assert_called_once()
        key = next(iter(w.favorite_items))
        w.favorites_view.identify_row.return_value = key
        w.play_favorite(SimpleNamespace(x=5, y=5))
        w.send.assert_called_once_with({'type': 'music_enqueue', 'guild_id': None,
                                        'url': 'https://youtu.be/abc', 'playlist': False})
        w.favorite_context_id = key; w.remove_favorite()
        self.assertEqual(w.config['music_favorites'], [])

    def test_saved_playlist_queues_as_playlist_and_header_does_not_play(self):
        w = self.window()
        w.save_music_favorite({'title': 'Mix', 'url': 'https://youtube.com/playlist?list=PL1', 'playlist': True})
        w.favorites_view.identify_row.return_value = next(iter(w.favorite_items))
        w.play_favorite(SimpleNamespace(x=5, y=5))
        self.assertTrue(w.send.call_args.args[0]['playlist'])
        w.send.reset_mock(); w.favorites_view.identify_region.return_value = 'heading'
        w.play_favorite(SimpleNamespace(x=5, y=5)); w.send.assert_not_called()

    def test_remove_queue_sends_exact_track_without_touching_favorites(self):
        w = self.window(); w.refresh_music = Mock()
        w.remove_queue_song()
        w.send.assert_called_once_with({'type': 'music_control', 'guild_id': None,
                                        'action': 'remove', 'track_id': 'track'})
        self.assertEqual(w.config, {})

    def test_multi_remove_sends_one_atomic_request(self):
        w = self.window(); w.refresh_music = Mock()
        w.queue_view.selection.return_value = ["one", "two"]
        w.remove_queue_song()
        w.send.assert_called_once_with({"type": "music_control", "guild_id": None,
                                       "action": "remove", "track_ids": ["one", "two"]})

    def test_expanded_playlist_tracks_are_individually_playable(self):
        w = self.window()
        w.save_music_favorite({"title": "Mix", "url": "https://youtube.com/playlist?list=PL1",
                               "playlist": True, "tracks": [{"title": "Song", "webpage_url": "https://youtu.be/abc"}]})
        child = next(key for key in w.favorite_items if ":" in key)
        w.favorites_view.identify_row.return_value = child
        w.play_favorite(SimpleNamespace(x=5, y=5))
        self.assertFalse(w.send.call_args.args[0]["playlist"])
        self.assertEqual(w.send.call_args.args[0]["url"], "https://youtu.be/abc")


class PlaylistBrowseTests(unittest.IsolatedAsyncioTestCase):
    async def test_browse_loads_metadata_without_playing_or_enqueuing(self):
        import json
        from unittest.mock import AsyncMock, patch
        from relay_agent.main import RelayAgent
        agent = RelayAgent({})
        info = {"entries": [{"id": "abc", "title": "Song"}, {"id": "bad", "availability": "private"}]}
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(json.dumps(info).encode(), b"")))
        agent.send_json = AsyncMock()
        with patch("relay_agent.main.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            await agent.browse_playlist("saved", "https://youtube.com/playlist?list=PL1")
        event = agent.events.get_nowait()
        self.assertEqual(event["favorite_playlist"], "saved")
        self.assertEqual(event["tracks"], [{"title": "Song", "webpage_url": "https://www.youtube.com/watch?v=abc"}])
        self.assertIn("--playlist-end", spawn.call_args.args)
        agent.send_json.assert_not_awaited()


class SelectionQueueTests(unittest.TestCase):
    def test_selected_playlist_and_child_are_not_added_twice(self):
        w = RelayWindow.__new__(RelayWindow)
        w.favorites_view = Mock(); w.favorites_view.selection.return_value = ["list", "child", "song"]
        w.favorites_view.parent.side_effect = lambda key: "list" if key == "child" else ""
        w.visible_music_rows = Mock(return_value=["list", "child", "song"])
        w.favorite_items = {"list": {"url": "https://youtube.com/playlist?list=PL1", "playlist": True},
                            "child": {"url": "https://youtu.be/one"}, "song": {"url": "https://youtu.be/two"}}
        w.send = Mock(); w.status = Mock()
        w.add_favorite_selection()
        self.assertEqual(w.send.call_args.args[0]["items"], [
            {"url": "https://youtube.com/playlist?list=PL1", "playlist": True},
            {"url": "https://youtu.be/two", "playlist": False}])

    def test_shift_selection_and_press_preserve_multi_selection(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock()
        tree.identify_region.return_value = "cell"; tree.identify_row.return_value = "three"
        tree.selection.return_value = ["one"]
        w.queue_view = Mock(); w.favorites_view = tree
        w.visible_music_rows = Mock(return_value=["one", "two", "three"])
        w.music_selection_anchors = {tree: "one"}
        w.begin_music_drag(tree, SimpleNamespace(x=1, y=1, x_root=10, y_root=10, state=1))
        tree.selection_set.assert_called_with(["one", "two", "three"])
        tree.selection.return_value = ["one", "two", "three"]
        tree.identify_row.return_value = "two"
        w.begin_music_drag(tree, SimpleNamespace(x=1, y=1, x_root=10, y_root=10, state=0))
        tree.selection_set.assert_called_with(["one", "two", "three"])

    def test_range_drag_selects_adjacent_visible_rows(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock()
        tree.winfo_rootx.return_value = tree.winfo_rooty.return_value = 0
        tree.winfo_width.return_value = tree.winfo_height.return_value = 200
        tree.identify_row.return_value = "three"
        w.queue_view = Mock(); w.favorites_view = tree
        w.visible_music_rows = Mock(return_value=["one", "two", "three"])
        w.music_drag = {"tree": tree, "row": "one", "x": 10, "y": 10, "mode": "range", "moved": False}
        w.music_drag_motion(SimpleNamespace(x_root=10, y_root=80))
        tree.selection_set.assert_called_once_with(["one", "two", "three"])

    def test_diagonal_drag_preserves_existing_multi_selection(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock()
        tree.winfo_rootx.return_value = tree.winfo_rooty.return_value = 0
        tree.winfo_width.return_value = tree.winfo_height.return_value = 200
        w.queue_view = Mock(); w.favorites_view = tree; w.status = Mock()
        w.music_drag = {"tree": tree, "row": "one", "x": 10, "y": 10, "mode": "move",
                        "moved": False, "selected_count": 3}
        w.music_drag_motion(SimpleNamespace(x_root=20, y_root=80))
        tree.selection_set.assert_not_called()

    def test_name_tree_area_drag_adds_full_selection_to_queue(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock(); queue = Mock()
        tree.identify_region.return_value = "tree"
        tree.identify.return_value = "Treeitem.text"
        tree.identify_row.return_value = "one"
        tree.selection.return_value = ["one", "two"]
        tree.parent.return_value = ""
        tree.winfo_rootx.return_value = tree.winfo_rooty.return_value = 0
        tree.winfo_width.return_value = tree.winfo_height.return_value = 200
        queue.winfo_rootx.return_value = 250; queue.winfo_rooty.return_value = 0
        queue.winfo_width.return_value = queue.winfo_height.return_value = 200
        w.queue_view = queue; w.favorites_view = tree
        w.visible_music_rows = Mock(return_value=["one", "two"])
        w.favorite_items = {"one": {"url": "https://youtu.be/one"}, "two": {"url": "https://youtu.be/two"}}
        w.status = Mock(); w.send = Mock()
        self.assertEqual(w.begin_music_drag(tree, SimpleNamespace(x=30, y=30, x_root=30, y_root=30, state=0)), "break")
        event = SimpleNamespace(widget=tree, x_root=280, y_root=80)
        w.music_drag_motion(event)
        w.music_drag_release(event)
        self.assertEqual(w.send.call_args.args[0]["items"], [
            {"url": "https://youtu.be/one", "playlist": False},
            {"url": "https://youtu.be/two", "playlist": False}])

    def test_favorite_context_keeps_selected_children_and_songs(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock()
        tree.identify_region.return_value = "tree"
        tree.identify_row.return_value = "child"
        tree.parent.return_value = "playlist"
        tree.selection.return_value = ["child", "song"]
        w.favorites_view = tree; w.favorite_menu = Mock()
        w.favorite_context_menu(SimpleNamespace(x=20, y=40, x_root=20, y_root=40))
        tree.selection_set.assert_not_called()
        self.assertEqual(w.favorite_context_id, "playlist")
        w.favorite_menu.tk_popup.assert_called_once()

    def test_expander_keeps_native_playlist_open_behavior(self):
        w = RelayWindow.__new__(RelayWindow); tree = Mock()
        tree.identify_region.return_value = "tree"; tree.identify.return_value = "Treeitem.indicator"
        w.favorites_view = tree; w.queue_view = Mock()
        self.assertIsNone(w.begin_music_drag(tree, SimpleNamespace(x=2, y=30)))
        tree.selection_set.assert_not_called()
