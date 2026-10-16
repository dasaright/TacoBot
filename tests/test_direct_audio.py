import asyncio
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from relay_agent.direct_audio import DirectAudio, resolve_routes
from relay_agent.main import RelayAgent, RelayWindow

class RoutingTests(unittest.TestCase):
    def test_distinct_outputs_and_physical_mic_required(self):
        devices = [{'id': 1, 'key': 'cable', 'output': 2, 'input': 0},
                   {'id': 2, 'key': 'speaker', 'output': 2, 'input': 0},
                   {'id': 3, 'key': 'microphone', 'output': 0, 'input': 1}]
        self.assertEqual(resolve_routes(devices, {'cable': 'cable', 'speakers': 'speaker', 'microphone': 'microphone'}), (1, 2, 3))
        with self.assertRaises(ValueError):
            resolve_routes(devices, {'cable': 'cable', 'speakers': 'cable'})
        with self.assertRaises(ValueError):
            resolve_routes(devices, {'cable': 'missing', 'speakers': 'speaker'})

    def test_pcm_reaches_both_outputs_and_stop_allows_new_clips(self):
        audio = DirectAudio(Path('ffmpeg'), queue.Queue()); audio.routes = (1, 2)
        streams = []
        class Output:
            def __init__(self, **kwargs):
                self.device = kwargs['device']; self.data = []
                streams.append(self)
            def start(self): pass
            def write(self, data): self.data.append(data)
            def abort(self): pass
            def close(self): pass
        def process(*args, **kwargs):
            import io
            return SimpleNamespace(stdout=io.BytesIO(bytes([1, 0])*1920), poll=lambda: 0,
                                   wait=lambda: 0, kill=Mock())
        with patch.dict(sys.modules, {'sounddevice': SimpleNamespace(RawOutputStream=Output)}), \
             patch('relay_agent.direct_audio.subprocess.Popen', side_effect=process):
            for _ in range(2):
                audio.play('file', Path('clip.wav'), -4, 50)
                with audio.lock:
                    threads = [item['thread'] for item in audio.clips.values()]
                for thread in threads: thread.join(timeout=2)
                audio.stop_file()
            self.assertEqual([s.device for s in streams], [1,2,1,2])
            self.assertTrue(all(s.data for s in streams))
            self.assertEqual(streams[0].data, streams[1].data)
            self.assertFalse(audio.playing)
            self.assertTrue(audio.events.empty())

    def test_microphone_is_sent_only_to_cable_not_speakers(self):
        from array import array
        delivered = threading.Event(); stopped = threading.Event()
        outputs = []
        class Input:
            def __init__(self, **kwargs): self.first = True
            def start(self): pass
            def read(self, frames):
                if self.first:
                    self.first = False
                    return array('h', [1000] * frames).tobytes(), False
                stopped.wait(2)
                raise RuntimeError('stopped')
            def abort(self): stopped.set()
            def close(self): pass
        class Output:
            def __init__(self, **kwargs): self.device = kwargs['device']; outputs.append(self)
            def start(self): pass
            def write(self, data): self.data = data; delivered.set()
            def abort(self): pass
            def close(self): pass
        devices = [{'id': 1, 'key': 'cable', 'output': 2, 'input': 0},
                   {'id': 2, 'key': 'speaker', 'output': 2, 'input': 0},
                   {'id': 3, 'key': 'mic', 'output': 0, 'input': 1}]
        sd = SimpleNamespace(RawInputStream=Input, RawOutputStream=Output,
                             check_input_settings=Mock(), check_output_settings=Mock())
        audio = DirectAudio(Path('ffmpeg'), queue.Queue())
        with patch.dict(sys.modules, {'sounddevice': sd}), patch('relay_agent.direct_audio.audio_devices', return_value=devices):
            audio.configure({'cable': 'cable', 'speakers': 'speaker', 'microphone': 'mic'})
            self.assertTrue(delivered.wait(2))
            audio.close()
        self.assertEqual([output.device for output in outputs], [1])
        self.assertEqual(list(array('h', outputs[0].data)), [1000] * 1920)
        self.assertTrue(audio.events.empty())

    def test_direct_click_never_sends_bot_request(self):
        w = RelayWindow.__new__(RelayWindow)
        w.config = {'direct_soundboard': True}; w.status = Mock(); w.send = Mock()
        w.listbox = Mock(); w.listbox.identify_region.return_value = 'cell'
        w.listbox.identify_row.return_value = 'clip'; w.clip_pressed_index = 'clip'
        w.clip_column_at = Mock(return_value='name'); w.loop = Mock()
        w.agent = SimpleNamespace(local_files={'clip': Path('clip.wav')}, play_direct_clip=Mock(return_value='pending'))
        with patch('relay_agent.main.asyncio.run_coroutine_threadsafe') as schedule:
            w.play_selected(SimpleNamespace(widget=w.listbox, x=2, y=2))
        schedule.assert_called_once_with('pending', w.loop)
        w.send.assert_not_called()

class DirectAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_play_uses_normalization_and_slider_without_websocket(self):
        agent = RelayAgent({'clip_settings': {'clip': {'volume': 45}}, 'clip_speed': -100})
        agent.local_files = {'clip': Path('clip.wav')}
        agent.normalize_clip = AsyncMock(return_value=-8)
        agent.direct_audio = Mock()
        agent.send_json = AsyncMock()
        await agent.play_direct_clip('clip')
        agent.direct_audio.play.assert_called_once_with('clip', Path('clip.wav'), -8, 45, -100)
        agent.send_json.assert_not_awaited()
        self.assertFalse(agent.direct_tasks)

    async def test_stop_cancels_pending_normalization_before_play(self):
        agent = RelayAgent({}); agent.local_files = {'clip': Path('clip.wav')}
        ready = asyncio.Event()
        async def analyze(path):
            ready.set(); await asyncio.Event().wait()
        agent.normalize_clip = analyze; agent.direct_audio = Mock()
        task = asyncio.create_task(agent.play_direct_clip('clip')); await ready.wait()
        await agent.stop_direct_clips()
        self.assertTrue(task.cancelled()); agent.direct_audio.play.assert_not_called()
        agent.direct_audio.stop_file.assert_called_once_with(None)
