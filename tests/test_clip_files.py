import tempfile
import unittest
from pathlib import Path
from relay_agent.main import rename_soundboard_clip

class ClipFileTests(unittest.TestCase):
    def test_rename_preserves_extension_and_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.ogg'
            path.write_bytes(b'audio')
            target=rename_soundboard_clip(path,'New Name')
            self.assertEqual(target.name,'New Name.ogg')
            self.assertEqual(target.read_bytes(),b'audio')
            self.assertFalse(path.exists())
    def test_rename_does_not_overwrite_another_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.mp3'
            other=Path(directory)/'new.mp3'
            path.write_bytes(b'old'); other.write_bytes(b'new')
            with self.assertRaises(ValueError): rename_soundboard_clip(path,'new')
            self.assertEqual(other.read_bytes(),b'new')
            for name in ['../escape', 'CON', '', 'bad:name', 'name.']:
                with self.subTest(name=name),self.assertRaises(ValueError): rename_soundboard_clip(path,name)

class ActiveClipEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_rename_waits_for_ffmpeg_exit_and_leaves_other_clips_running(self):
        import asyncio
        import os
        import shutil
        import wave
        from unittest.mock import AsyncMock, patch
        from relay_agent.main import RelayAgent
        ffmpeg=os.getenv('TEST_FFMPEG_PATH') or shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg is required; packaging CI runs this with bundled FFmpeg')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'playing.wav'
            with wave.open(str(path),'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(48000)
                output.writeframes(b'\0'*48000*4*10)
            agent=RelayAgent({})
            agent.local_files={'clip':path}
            agent.send_json=AsyncMock()
            started=asyncio.Event()
            async def send(frame): started.set()
            agent.send_binary=send
            other=asyncio.create_task(asyncio.sleep(30))
            agent.clip_tasks['other']=other
            agent.clip_file_ids['other']='other-file'
            with patch('relay_agent.main.FFMPEG_PATH',Path(ffmpeg)):
                task=asyncio.create_task(agent.stream('request','local:clip'))
                agent.clip_tasks['request']=task
                agent.clip_file_ids['request']='clip'
                try:
                    await asyncio.wait_for(started.wait(),10)
                    await asyncio.wait_for(agent.edit_clip_file('clip',path,'renamed'),10)
                    self.assertTrue(task.done())
                    self.assertTrue((Path(directory)/'renamed.wav').is_file())
                    self.assertFalse(path.exists())
                    self.assertFalse(other.done())
                finally:
                    task.cancel(); other.cancel()
                    await asyncio.gather(task,other,return_exceptions=True)


class ShareClipTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_keeps_local_and_share_then_delete_preserves_shared_bytes(self):
        from relay_agent.main import RelayAgent
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)/'local'; local.mkdir()
            shared = Path(directory)/'shared'
            source = local/'clip.ogg'; source.write_bytes(b'audio')
            agent = RelayAgent({})
            target = await agent.share_clip_file('clip', source, shared)
            self.assertTrue(source.exists())
            self.assertEqual(target.read_bytes(), b'audio')
            target = await agent.share_clip_file('clip', source, shared, remove_local=True)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b'audio')

    async def test_conflict_preserves_both_files_even_when_delete_selected(self):
        from relay_agent.main import RelayAgent
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)/'local'; local.mkdir()
            shared = Path(directory)/'shared'; shared.mkdir()
            source = local/'clip.mp3'; source.write_bytes(b'local')
            (shared/'clip.mp3').write_bytes(b'other')
            with self.assertRaises(ValueError):
                await RelayAgent({}).share_clip_file('clip', source, shared, remove_local=True)
            self.assertEqual(source.read_bytes(), b'local')
            self.assertEqual((shared/'clip.mp3').read_bytes(), b'other')


class NormalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_gain_lowers_loud_clips_boosts_quiet_and_reserves_slider_headroom(self):
        from relay_agent.main import clip_normalization_gain
        self.assertEqual(clip_normalization_gain('mean_volume: -10.0 dB max_volume: -4.0 dB'), -6)
        self.assertEqual(clip_normalization_gain('mean_volume: -30.0 dB max_volume: -15.0 dB'), 14)
        # Peaky clip: limited to 6 dB over the -1 dB ceiling instead of staying quiet.
        self.assertEqual(clip_normalization_gain('mean_volume: -25.0 dB max_volume: -2.0 dB'), 7)
        self.assertEqual(clip_normalization_gain('mean_volume: -91.0 dB max_volume: -91.0 dB'), 0)
        self.assertEqual(clip_normalization_gain('mean_volume: -80.0 dB max_volume: -60.0 dB'), 24)

    async def test_real_audio_normalizes_then_slider_and_reuses_measurement(self):
        import asyncio, math, os, shutil, wave
        from array import array
        from unittest.mock import AsyncMock, patch
        from relay_agent.main import RelayAgent, PCM_FRAME_BYTES
        ffmpeg = os.getenv('TEST_FFMPEG_PATH') or shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('Windows packaging runs this with bundled FFmpeg')
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, amplitude in [('quiet',1000), ('loud',20000)]:
                path = Path(directory)/(name+'.wav')
                samples = array('h', [int(amplitude*math.sin(2*math.pi*440*i/48000)) for i in range(12000)])
                with wave.open(str(path),'wb') as output:
                    output.setnchannels(1); output.setsampwidth(2); output.setframerate(48000); output.writeframes(samples.tobytes())
                paths.append(path)
            original = [p.read_bytes() for p in paths]
            agent = RelayAgent({}); agent.local_files = dict(zip(['quiet','loud'],paths))
            agent.send_json = AsyncMock()
            frames = []
            async def collect(frame): frames.append(frame[7:])
            agent.send_binary = collect
            async def play(clip):
                frames.clear()
                await agent.stream('request','local:'+clip)
                self.assertTrue(frames)
                self.assertTrue(all(len(f)==PCM_FRAME_BYTES for f in frames))
                samples=array('h',b''.join(frames))
                return math.sqrt(sum(x*x for x in samples)/len(samples))
            real_spawn = asyncio.create_subprocess_exec
            with patch('relay_agent.main.FFMPEG_PATH',Path(ffmpeg)), patch('relay_agent.main.asyncio.create_subprocess_exec',side_effect=real_spawn) as spawn:
                await agent.preload_normalization(dict(agent.local_files))
                await agent.preload_task
                self.assertEqual(spawn.call_count, 2)
                await agent.preload_normalization(dict(agent.local_files))
                await agent.preload_task
                self.assertEqual(spawn.call_count, 2)
                quiet = await play('quiet'); loud = await play('loud')
                self.assertLess(abs(20*math.log10(quiet/loud)), 0.3)
                self.assertEqual(spawn.call_count,4)
                agent.config['clip_settings']={'quiet':{'volume':100}}
                boosted = await play('quiet')
                self.assertAlmostEqual(boosted/quiet,2,delta=0.02)
                self.assertEqual(spawn.call_count,5)
            self.assertEqual([p.read_bytes() for p in paths], original)
