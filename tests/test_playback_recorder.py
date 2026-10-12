from array import array
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from relay_agent.playback_recorder import PlaybackBuffer
from relay_agent.unshare_clip import copy_local
from relay_agent.clip_recorder import reserve_clip


class PlaybackTests(unittest.TestCase):
    def buffer(self):
        value=PlaybackBuffer(2); value.rate=10; value.channels=1; value.started=0
        return value

    def test_snapshot_contains_only_latest_seconds(self):
        value=self.buffer()
        for end,sample in [(1,1),(2,2),(3,3),(4,4)]: value.append(array('h',[sample]*10).tobytes(),end)
        data,rate,channels=value.snapshot(4)
        self.assertEqual(list(array('h',data)),[3]*10+[4]*10)
        self.assertLessEqual(len(value.chunks),3)

    def test_silence_and_short_initial_buffer_keep_correct_timing(self):
        value=self.buffer(); value.append(array('h',[7]*5).tobytes(),.5)
        data,_,_=value.snapshot(1)
        self.assertEqual(list(array('h',data)),[7]*5+[0]*5)
        data,_,_=value.snapshot(4)
        self.assertEqual(data,b'\0'*40)
        value.clear()
        with self.assertRaisesRegex(ValueError,'Enable'): value.snapshot(5)

    def test_unshare_copy_preserves_source_and_existing_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shared=root/'shared'; local=root/'local'; shared.mkdir(); local.mkdir()
            source=shared/'clip.mp3'; source.write_bytes(b'audio'); (local/'clip.mp3').write_bytes(b'keep')
            result=copy_local(source,local)
            self.assertEqual(result.name,'clip (2).mp3'); self.assertEqual(result.read_bytes(),b'audio')
            self.assertEqual(source.read_bytes(),b'audio'); self.assertEqual((local/'clip.mp3').read_bytes(),b'keep')
            with self.assertRaises(ValueError): copy_local(source,shared)

    def test_playback_names_are_numbered_without_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(reserve_clip(directory,'Playback',False).name,'Playback 1.mp3')
            self.assertEqual(reserve_clip(directory,'Playback',False).name,'Playback 2.mp3')
