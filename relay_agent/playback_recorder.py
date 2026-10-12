"""Lossless PCM lookback: native samples stay contiguous despite callback jitter."""
from collections import deque
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave

from .clip_recorder import encode_clip,capture_speakers


class PlaybackBuffer:
    def __init__(self, seconds=30):
        self.seconds=seconds; self.rate=48000; self.channels=2
        self.started=None; self.chunks=deque(); self.byte_count=0; self.lock=threading.Lock()

    def configure(self, rate, channels):
        with self.lock:
            self.rate=rate; self.channels=channels; self.chunks.clear(); self.byte_count=0; self.started=time.monotonic()

    def set_seconds(self, seconds):
        with self.lock:
            self.seconds=max(1,min(300,int(seconds))); self.trim(time.monotonic())

    def trim(self, now):
        limit=int(self.seconds*self.rate)*self.channels*2
        while self.chunks and self.byte_count>limit:
            excess=self.byte_count-limit
            chunk=self.chunks.popleft()
            if len(chunk)>excess:
                self.chunks.appendleft(chunk[excess:]); self.byte_count-=excess
            else: self.byte_count-=len(chunk)

    def append(self, data, now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            width=self.channels*2; data=bytes(data)
            if len(data)%width: raise ValueError('Speaker capture returned an incomplete PCM frame.')
            if data: self.chunks.append(data); self.byte_count+=len(data); self.trim(now)

    def snapshot(self, now=None):
        chunks,rate,channels=self.freeze_snapshot()
        return b''.join(chunks),rate,channels

    def freeze_snapshot(self):
        with self.lock:
            if self.started is None: raise ValueError('Enable playback lookback first.')
            if not self.byte_count: raise ValueError('Wait for speaker audio to be captured before saving.')
            # Never insert silence, resample, or overlap buffers using Python
            # callback timestamps. Record writes exactly this same PCM sequence.
            return tuple(self.chunks),self.rate,self.channels

    def clear(self):
        with self.lock: self.chunks.clear(); self.byte_count=0; self.started=None

    def available_seconds(self):
        with self.lock:
            return self.byte_count/(self.rate*self.channels*2) if self.started is not None else 0


class PlaybackRecorder:
    def __init__(self, ffmpeg, events, seconds=30):
        self.ffmpeg=ffmpeg; self.events=events; self.buffer=PlaybackBuffer(seconds)
        self.active=False; self.saving=False; self.stop_event=threading.Event()

    def start(self):
        if self.active: return
        if sys.platform!='win32': raise RuntimeError('Playback lookback requires the Windows relay.')
        self.active=True; self.stop_event.clear()
        threading.Thread(target=self.capture,daemon=True).start()

    def stop(self):
        self.stop_event.set()

    def capture(self):
        try:
            capture_speakers(self.buffer.append,self.stop_event,self.buffer.configure,
                lambda device:self.events.put({'lookback_state':'ready','lookback_device':device['name']}))
        except Exception as exc: self.events.put({'lookback_error':str(exc)})
        finally:
            if not self.saving: self.buffer.clear()
            self.active=False; self.events.put({'lookback_state':'stopped'})

    def save(self, folder):
        if self.saving: return
        if not self.active: raise ValueError('Enable playback lookback first.')
        if not Path(folder).is_dir(): raise ValueError('Choose a clips folder first.')
        chunks,rate,channels=self.buffer.freeze_snapshot()
        # Encoding and snapshot copying never run on the GUI thread.
        self.saving=True
        self.events.put({'playback_save_started':True})
        def worker():
            try:
                data=b''.join(chunks)
                with tempfile.TemporaryDirectory(prefix='TacoBot-playback-') as directory:
                    source=Path(directory)/'playback.wav'
                    with wave.open(str(source),'wb') as output:
                        output.setnchannels(channels); output.setsampwidth(2); output.setframerate(rate); output.writeframes(data)
                    target=encode_clip(source,folder,self.ffmpeg,'Playback',False)
                self.events.put({'recorded_clip':str(target),'recording_folder':str(folder)})
            except Exception as exc: self.events.put({'recording_error':str(exc)})
            finally:
                self.saving=False
                if not self.active: self.buffer.clear()
                self.events.put({'playback_saved':True})
        threading.Thread(target=worker,daemon=True).start()
