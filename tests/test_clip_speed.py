import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from relay_agent.clip_speed import speed_percent, speed_factor, tempo_filter


class ClipSpeedTests(unittest.TestCase):
    def test_speed_range_and_safe_filter_stages(self):
        for value,expected in [(-200,1/3),(-100,.5),(0,1),(100,2),(200,3)]:
            self.assertAlmostEqual(speed_factor(value),expected)
            product=1
            for stage in tempo_filter(value).split(','):
                factor=float(stage.split('=')[1])
                self.assertGreaterEqual(factor,.5); self.assertLessEqual(factor,2)
                product*=factor
            self.assertAlmostEqual(product,expected,places=6)
        self.assertEqual(speed_percent('invalid'),0)
        self.assertEqual(speed_percent(-999),-200)
        self.assertEqual(speed_percent(999),200)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_real_audio_duration_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.wav'
            with wave.open(str(source),'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(48000)
                import math,struct
                output.writeframes(b''.join(struct.pack('<h',int(10000*math.sin(2*math.pi*440*n/48000))) for n in range(48000*3)))
            for setting in (-200,0,200):
                dest=Path(directory)/f'{setting}.wav'
                subprocess.run([shutil.which('ffmpeg'),'-v','error','-i',str(source),'-af',tempo_filter(setting),str(dest)],check=True,capture_output=True)
                with wave.open(str(dest),'rb') as audio: duration=audio.getnframes()/audio.getframerate()
                self.assertAlmostEqual(duration,3/speed_factor(setting),delta=.15)
