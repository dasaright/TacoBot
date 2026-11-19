import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
if sys.platform != 'win32': os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from relay_agent.tts import PiperSpeech
from relay_agent.main import RelayAgent


def fake_voice():
    voice = Mock()
    def synthesize(text, audio):
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(22050); audio.writeframes(b'\0\0' * 2205)
    voice.synthesize_wav.side_effect = synthesize
    return voice


class PhraseAudioTests(unittest.TestCase):
    def test_preloaded_phrase_survives_message_cache_trim_and_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            speech = PiperSpeech(folder); speech.voice = fake_voice()
            path = speech.preload_phrase(' Good game ')
            self.assertEqual(path.parent, Path(folder) / 'phrases')
            self.assertEqual(path, speech.phrase_path('Good game'))
            self.assertGreater(path.stat().st_size, 44)
            for old in (Path(folder) / 'audio').glob('*.wav'): old.unlink()  # As a full trim would.
            self.assertEqual(speech.preload_phrase('Good game'), path)
            speech.voice.synthesize_wav.assert_called_once()
            self.assertFalse(list(Path(folder).rglob('*.tmp')))

    def test_voice_and_speaker_change_the_audio_and_unused_audio_is_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            speech = PiperSpeech(folder); speech.voice = fake_voice()
            first = speech.preload_phrase('Hi'); second = speech.preload_phrase('Bye')
            self.assertNotEqual(speech.phrase_path('Hi', 'en_US-lessac-medium', 0), speech.phrase_path('Hi', 'en_US-amy-medium', 0))
            speech.remove_phrases([second])
            self.assertFalse(first.exists()); self.assertTrue(second.exists())

    def test_invalid_phrase_is_rejected_without_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            speech = PiperSpeech(folder)
            with self.assertRaises(ValueError): speech.preload_phrase('a' * 1001)
            self.assertFalse((Path(folder) / 'phrases').exists())


class PhrasePlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_preload_reports_ready_and_play_sends_without_synthesis(self):
        with tempfile.TemporaryDirectory() as folder, patch('relay_agent.main.FILES_DIR', Path(folder)):
            agent = RelayAgent({}); path = Path(folder) / ('b' * 64 + '.wav'); path.write_bytes(b'RIFF')
            agent.speech.preload_phrase = Mock(return_value=path); agent.normalize_clip = AsyncMock(return_value=0)
            await agent.preload_phrases([{'id': 'p1', 'text': 'Hi', 'voice': 'en_US-lessac-medium', 'speaker': 0}])
            self.assertEqual(agent.events.get_nowait(), {'phrase_ready': 'p1', 'phrase_path': str(path)})
            agent.normalize_clip.assert_awaited_once_with(path)
            agent.speech.synthesize = Mock(); agent.websocket = Mock(); agent.send_json = AsyncMock()
            await agent.play_phrase(path)
            agent.speech.synthesize.assert_not_called()
            payload = agent.send_json.call_args.args[0]
            self.assertEqual(payload, {'type': 'local_play', 'guild_id': None, 'file_id': 'tts:' + path.stem, 'title': 'Text to speech'})
            self.assertEqual(agent.tts_files[payload['file_id']], path)

    async def test_failures_are_reported(self):
        with tempfile.TemporaryDirectory() as folder, patch('relay_agent.main.FILES_DIR', Path(folder)):
            agent = RelayAgent({}); agent.speech.preload_phrase = Mock(side_effect=ValueError('no model'))
            await agent.preload_phrases([{'id': 'p1', 'text': 'Hi', 'voice': 'x'}, {'id': 'p2', 'text': 'Yo', 'voice': 'x'}])
            self.assertEqual([agent.events.get_nowait()['phrase_failed'] for _ in range(2)], ['p1', 'p2'])
            agent.send_json = AsyncMock()
            await agent.play_phrase(Path(folder) / 'x.wav')
            self.assertIn('Connect your relay', agent.events.get_nowait()['error'])
            agent.websocket = Mock()
            await agent.play_phrase(Path(folder) / 'missing.wav')
            self.assertIn('missing', agent.events.get_nowait()['error']); agent.send_json.assert_not_called()


class PhrasePaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from relay_agent.desktop import application
        cls.app = application()

    def setUp(self):
        from relay_agent.qt_desktop import Desktop
        self.saved = {'tts_phrases': [{'id': 'old', 'text': 'Welcome back', 'voice': 'en_US-lessac-medium', 'speaker': 0}]}
        self.window = Desktop(self.saved, start_agent=False); self.window.show(); self.app.processEvents()
        self.controller = self.window.controller; self.controller.save = Mock()

    def tearDown(self):
        self.window.close(); self.app.processEvents()

    def test_pane_sits_right_of_the_speech_box_and_lists_saved_phrases(self):
        w = self.window; w.select_tab(2); self.app.processEvents()
        self.assertGreater(w.phrases.mapToGlobal(w.phrases.rect().topLeft()).x(), w.tts.mapToGlobal(w.tts.rect().topRight()).x())
        self.assertEqual(w.phrases.phrase_ids(), ['old'])
        self.assertIn('preparing', w.phrases.item(0).text())

    def test_typed_phrase_is_saved_with_current_voice_and_preloaded(self):
        w, c = self.window, self.controller
        with patch.object(c, 'preload_phrases') as preload:
            w.phrase_input.setText('  Nice shot  '); w.phrase_input.returnPressed.emit()
        phrase = self.saved['tts_phrases'][-1]
        self.assertEqual((phrase['text'], phrase['voice']), ('Nice shot', c.current_voice()[0]))
        preload.assert_called_once_with([phrase]); self.assertEqual(w.phrase_input.text(), '')
        with patch.object(c, 'preload_phrases') as preload:
            w.phrase_input.setText('Nice shot'); w.phrase_input.returnPressed.emit()
        preload.assert_not_called(); self.assertEqual(len(self.saved['tts_phrases']), 2)
        w.tts.setPlainText('From the box')
        with patch.object(c, 'preload_phrases'): c.add_phrase(w.tts.toPlainText())
        self.assertEqual(self.saved['tts_phrases'][-1]['text'], 'From the box')

    def test_click_plays_only_when_ready_and_failed_phrase_retries(self):
        w, c = self.window, self.controller
        with patch('relay_agent.qt_controller.asyncio.run_coroutine_threadsafe') as run:
            c.play_phrase('old'); run.assert_not_called()
            self.assertIn('still being prepared', w.status.text())
            c.handle_event({'phrase_ready': 'old', 'phrase_path': 'C:/x/a.wav'})
            self.assertNotIn('preparing', w.phrases.item(0).text())
            c.agent.play_phrase = Mock(return_value='coroutine')
            w.phrases.itemClicked.emit(w.phrases.item(0))
            c.agent.play_phrase.assert_called_once_with(Path('C:/x/a.wav')); run.assert_called_once()
        c.handle_event({'phrase_failed': 'old', 'error': 'Could not preload phrase: boom'})
        self.assertIn('failed', w.phrases.item(0).text())
        with patch.object(c, 'preload_phrases') as preload:
            c.play_phrase('old')
        preload.assert_called_once()

    def test_edit_revoice_delete_and_reorder_persist(self):
        w, c = self.window, self.controller
        with patch.object(c, 'preload_phrases'):
            c.add_phrase('Second')
            with patch('relay_agent.qt_controller.QInputDialog.getText', return_value=('Welcome home', True)):
                c.edit_phrase('old')
        self.assertEqual(self.saved['tts_phrases'][0]['text'], 'Welcome home')
        item = w.phrases.takeItem(1); w.phrases.insertItem(0, item); c.phrases_reordered()
        self.assertEqual([p['text'] for p in self.saved['tts_phrases']], ['Second', 'Welcome home'])
        w.phrases.setCurrentRow(0); c.delete_phrase()
        self.assertEqual([p['id'] for p in self.saved['tts_phrases']], ['old'])
        self.assertEqual(w.phrases.phrase_ids(), ['old'])


class PhraseCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from relay_agent.desktop import application
        cls.app = application()

    def setUp(self):
        from relay_agent.qt_desktop import Desktop
        voice = 'en_US-lessac-medium'
        self.saved = {'tts_phrase_categories': ['Supportive'],
                      'tts_phrases': [{'id': 's1', 'text': 'You got this', 'voice': voice, 'speaker': 0, 'category': 'Supportive'},
                                      {'id': 's2', 'text': 'Nice try', 'voice': voice, 'speaker': 0, 'category': 'Supportive'},
                                      {'id': 'x', 'text': 'Hello', 'voice': voice, 'speaker': 0}]}
        self.window = Desktop(self.saved, start_agent=False); self.window.show(); self.app.processEvents()
        self.c = self.window.controller; self.c.save = Mock()

    def tearDown(self):
        self.window.close(); self.app.processEvents()

    def buttons(self):
        grid = self.window.category_grid
        return [grid.itemAt(n).widget() for n in range(grid.count())]

    def test_each_category_has_a_button_that_plays_a_random_ready_member(self):
        c = self.c
        self.assertEqual([b.text() for b in self.buttons()], ['Supportive  (2)'])
        self.assertFalse(self.window.category_hint.isVisibleTo(self.window))
        with patch.object(c, 'play_phrase') as play:
            self.buttons()[0].click(); play.assert_not_called()
            self.assertIn('still being prepared', self.window.status.text())
            for key in ('s1', 's2', 'x'): c.handle_event({'phrase_ready': key, 'phrase_path': key + '.wav'})
            picks = []
            for _ in range(20):
                self.buttons()[0].click(); picks.append(play.call_args.args[0])
        self.assertEqual(set(picks), {'s1', 's2'})
        self.assertTrue(all(a != b for a, b in zip(picks, picks[1:])), 'Should not repeat the previous pick')

    def test_adding_a_category_adds_a_button_and_new_phrases_go_into_it(self):
        c, w = self.c, self.window
        with patch('relay_agent.qt_controller.QInputDialog.getText', return_value=('  Hype  ', True)):
            c.add_category()
        self.assertEqual(self.saved['tts_phrase_categories'], ['Supportive', 'Hype'])
        self.assertEqual([b.text() for b in self.buttons()], ['Supportive  (2)', 'Hype  (0)'])
        self.assertEqual(w.phrase_category.currentData(), 'Hype')
        with patch.object(c, 'preload_phrases'): c.add_phrase('Lets go')
        self.assertEqual(self.saved['tts_phrases'][-1]['category'], 'Hype')
        with patch.object(c, 'play_phrase') as play:
            self.buttons()[1].click(); play.assert_not_called()
        with patch('relay_agent.qt_controller.QInputDialog.getText', return_value=('hype', True)):
            self.assertIsNone(c.add_category())
        self.assertEqual(len(self.saved['tts_phrase_categories']), 2)

    def test_assign_rename_and_delete_category(self):
        c = self.c
        c.set_phrase_category('x', 'Supportive')
        self.assertEqual(self.buttons()[0].text(), 'Supportive  (3)')
        self.assertIn('Supportive', self.window.phrases.item(2).text())
        with patch('relay_agent.qt_controller.QInputDialog.getText', return_value=('Kind', True)):
            c.rename_category('Supportive')
        self.assertEqual({p.get('category') for p in self.saved['tts_phrases']}, {'Kind'})
        with patch('relay_agent.qt_controller.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            c.delete_category('Kind')
        self.assertEqual(self.saved['tts_phrase_categories'], [])
        self.assertEqual(len(self.saved['tts_phrases']), 3)
        self.assertFalse(any('category' in p for p in self.saved['tts_phrases']))
        self.assertEqual(self.buttons(), [])


if __name__ == '__main__':
    unittest.main()
