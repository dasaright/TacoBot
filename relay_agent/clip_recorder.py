"""Windows speaker-loopback recording, streamed to disk then encoded as MP3."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import wave


def capture_speakers(consume,stop_event,prepare,ready):
    """One native capture implementation for Record and continuous lookback."""
    import pyaudiowpatch as audio
    errors=[]
    with audio.PyAudio() as manager:
        device=manager.get_default_wasapi_loopback()
        channels,rate=int(device['maxInputChannels']),int(device['defaultSampleRate'])
        if channels<1 or rate<1: raise RuntimeError('Default playback device does not support recording.')
        prepare(rate,channels)
        def callback(data,count,timing,status):
            try: consume(data); return (None,audio.paContinue)
            except Exception as exc: errors.append(exc); stop_event.set(); return (None,audio.paAbort)
        stream=manager.open(format=audio.paInt16,channels=channels,rate=rate,input=True,
            input_device_index=device['index'],frames_per_buffer=1024,stream_callback=callback)
        try:
            ready(device)
            while not stop_event.wait(.1):
                if not stream.is_active(): raise RuntimeError('Speaker capture stopped. Check your playback device.')
        finally:
            try: stream.stop_stream()
            finally: stream.close()
        if errors: raise errors[0]


def reserve_clip(folder, prefix='clipped soundboard', parentheses=True):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError('Choose an existing clips folder before recording.')
    number = 1
    while True:
        target = folder / (f'{prefix} ({number}).mp3' if parentheses else f'{prefix} {number}.mp3')
        try:
            with target.open('xb'):
                pass
            return target
        except FileExistsError:
            number += 1


def encode_clip(wav, folder, ffmpeg, prefix='clipped soundboard', parentheses=True):
    target = reserve_clip(folder, prefix, parentheses)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.tacobot-record-', suffix='.tmp', dir=folder)
        os.close(fd)
        result = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(wav), '-vn', '-ac', '2', '-codec:a', 'libmp3lame', '-q:a', '2', '-f', 'mp3', temporary],
            capture_output=True, timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or Path(temporary).stat().st_size == 0:
            raise RuntimeError('Could not encode recording: '+result.stderr.decode(errors='replace')[-500:])
        os.replace(temporary, target)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        if temporary: Path(temporary).unlink(missing_ok=True)


class ClipRecorder:
    def __init__(self, ffmpeg, events):
        self.ffmpeg, self.events = ffmpeg, events
        self.busy = False
        self.stop_event = threading.Event()

    def start(self, folder):
        if self.busy:
            return
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError('Choose an existing clips folder before recording.')
        if sys.platform != 'win32':
            raise RuntimeError('Speaker recording is available in the Windows relay.')
        # Fail before capturing if the selected destination cannot accept files.
        fd, probe = tempfile.mkstemp(prefix='.tacobot-record-', suffix='.tmp', dir=folder)
        os.close(fd); Path(probe).unlink()
        self.busy = True
        self.stop_event.clear()
        self.events.put({'recorder_state': 'starting'})
        threading.Thread(target=self._record, args=(folder,), daemon=True).start()

    def stop(self):
        if self.busy:
            self.stop_event.set()

    def _record(self, folder):
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='TacoBot-record-', suffix='.wav')
            os.close(fd)
            with wave.open(temporary,'wb') as output:
                def prepare(rate,channels): output.setnchannels(channels); output.setsampwidth(2); output.setframerate(rate)
                capture_speakers(output.writeframesraw,self.stop_event,prepare,
                    lambda device:self.events.put({'recorder_state':'recording','recording_device':device['name']}))
                frames=output.getnframes()
            if frames == 0:
                raise RuntimeError('No speaker audio was captured. Play audio before recording.')
            self.events.put({'recorder_state': 'saving'})
            target = encode_clip(temporary, folder, self.ffmpeg)
            self.events.put({'recorded_clip': str(target), 'recording_folder': str(folder)})
        except Exception as exc:
            self.events.put({'recording_error': str(exc)})
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            self.busy = False
            self.events.put({'recorder_state': 'idle'})
