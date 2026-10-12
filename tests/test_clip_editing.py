from array import array
from pathlib import Path
import queue
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from relay_agent.clip_editing import waveform,crop_clip,fingerprint
from relay_agent.playback_recorder import PlaybackRecorder


class ClipEditingTests(unittest.TestCase):
    def setUp(self):
        self.ffmpeg=shutil.which('ffmpeg')
        if not self.ffmpeg: self.skipTest('FFmpeg not installed')
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.source=self.root/'source.wav'
        with wave.open(str(self.source),'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(array('h',[0]*8000+[10000,-10000]*4000+[0]*8000).tobytes())

    def test_waveform_and_new_trim_preserve_original(self):
        duration,peaks,stamp=waveform(self.source,self.ffmpeg)
        self.assertAlmostEqual(duration,3); self.assertGreater(max(peaks),.2)
        original=self.source.read_bytes(); target=crop_clip(self.source,1,2,self.ffmpeg,True,self.root,stamp)
        with wave.open(str(target)) as audio: self.assertAlmostEqual(audio.getnframes()/audio.getframerate(),1)
        self.assertEqual(self.source.read_bytes(),original); self.assertIn('edited 1',target.name)

    def test_overwrite_is_atomic_and_changed_files_are_rejected(self):
        stamp=fingerprint(self.source)
        release=[]; crop_clip(self.source,.5,2.5,self.ffmpeg,expected=stamp,release=lambda:release.append(True))
        self.assertEqual(release,[True])
        with wave.open(str(self.source)) as audio: self.assertAlmostEqual(audio.getnframes()/audio.getframerate(),2)
        with self.assertRaisesRegex(ValueError,'changed'): crop_clip(self.source,0,1,self.ffmpeg,expected=stamp)
        before=self.source.read_bytes()
        with patch('relay_agent.clip_editing.run_ffmpeg',side_effect=ValueError('failed')):
            with self.assertRaises(ValueError): crop_clip(self.source,0,1,self.ffmpeg)
        self.assertEqual(self.source.read_bytes(),before)

    def test_save_playback_real_pipeline_outputs_latest_seconds_without_stopping_capture(self):
        events=queue.Queue(); recorder=PlaybackRecorder(self.ffmpeg,events,1); recorder.active=True
        recorder.buffer.configure(48000,2); recorder.buffer.started=time.monotonic()-2
        recorder.buffer.append(array('h',[100]*96000+[10000]*96000).tobytes())
        recorder.save(self.root)
        deadline=time.monotonic()+15; result=None
        while time.monotonic()<deadline:
            try: event=events.get(timeout=.1)
            except queue.Empty: continue
            self.assertNotIn('recording_error',event)
            if event.get('recorded_clip'): result=Path(event['recorded_clip'])
            if event.get('playback_saved'): break
        self.assertIsNotNone(result); self.assertEqual(result.name,'Playback 1.mp3')
        duration,peaks,_=waveform(result,self.ffmpeg)
        self.assertAlmostEqual(duration,1,delta=.03); self.assertGreater(max(peaks),.15)
        self.assertTrue(recorder.active); self.assertFalse(recorder.saving)
