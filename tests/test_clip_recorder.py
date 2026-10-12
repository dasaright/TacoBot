import queue
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from relay_agent.clip_recorder import ClipRecorder, encode_clip, reserve_clip


class RecorderTests(unittest.TestCase):
    def test_numbered_names_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            first=reserve_clip(directory); first.write_bytes(b'keep')
            self.assertEqual(reserve_clip(directory).name,'clipped soundboard (2).mp3')
            self.assertEqual(first.read_bytes(),b'keep')

    def test_encoding_failure_cleans_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('relay_agent.clip_recorder.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr=b'failed')):
                with self.assertRaisesRegex(RuntimeError,'encode'): encode_clip('capture.wav',directory,'ffmpeg')
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_capture_streams_pcm_to_disk_and_closes_before_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            events=queue.Queue(); recorder=ClipRecorder('ffmpeg',events); recorder.busy=True; recorder.stop_event.set()
            manager=Mock(); manager.__enter__=Mock(return_value=manager); manager.__exit__=Mock()
            manager.get_default_wasapi_loopback.return_value={'maxInputChannels':2,'defaultSampleRate':48000,'index':10,'name':'Speakers loopback'}
            stream=Mock()
            def opened(**kwargs):
                self.assertEqual(kwargs['input_device_index'],10); self.assertEqual(kwargs['channels'],2)
                kwargs['stream_callback'](b'\x00\x00'*960,480,{},0)
                return stream
            manager.open.side_effect=opened
            backend=SimpleNamespace(PyAudio=lambda:manager,paInt16=8,paContinue=0,paAbort=2)
            def encoded(wav,folder,ffmpeg):
                stream.close.assert_called_once()
                with wave.open(wav) as captured: self.assertEqual(captured.getnframes(),480)
                target=Path(folder)/'clipped soundboard (1).mp3'; target.write_bytes(b'mp3'); return target
            with patch.dict('sys.modules',{'pyaudiowpatch':backend}),patch('relay_agent.clip_recorder.encode_clip',side_effect=encoded):
                recorder._record(Path(directory))
            payloads=[]
            while not events.empty(): payloads.append(events.get_nowait())
            self.assertEqual([p['recorder_state'] for p in payloads if 'recorder_state' in p],['recording','saving','idle'])
            self.assertTrue(any('recorded_clip' in p for p in payloads)); self.assertFalse(recorder.busy)
            stream.stop_stream.assert_called_once()

    def test_real_mp3_encode(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg: self.skipTest('FFmpeg not installed in test environment')
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.wav'
            with wave.open(str(source),'wb') as audio:
                audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000); audio.writeframes(b'\0'*48000*4)
            result=encode_clip(source,directory,ffmpeg)
            self.assertGreater(result.stat().st_size,100); self.assertEqual(result.suffix,'.mp3')
