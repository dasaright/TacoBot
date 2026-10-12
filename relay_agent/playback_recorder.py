"""A bounded, timestamped speaker lookback. Nothing is saved until requested."""
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
        self.started=None; self.chunks=deque(); self.lock=threading.Lock()

    def configure(self, rate, channels):
        with self.lock:
            self.rate=rate; self.channels=channels; self.chunks.clear(); self.started=time.monotonic()

    def set_seconds(self, seconds):
        with self.lock:
            self.seconds=max(1,min(300,int(seconds))); self.trim(time.monotonic())

    def trim(self, now):
        while self.chunks and self.chunks[0][0] < now-self.seconds:
            self.chunks.popleft()

    def append(self, data, now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            self.chunks.append((now,bytes(data))); self.trim(now)

    def snapshot(self, now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            if self.started is None: raise ValueError('Enable playback lookback first.')
            duration=min(self.seconds,max(0,now-self.started))
            frames=int(duration*self.rate)
            if frames<1: raise ValueError('Wait for some playback audio to be buffered.')
            start=now-frames/self.rate; width=self.channels*2; output=bytearray(frames*width)
            self.trim(now)
            for end,data in self.chunks:
                count=len(data)//width; offset=round((end-count/self.rate-start)*self.rate)
                left=max(0,offset); right=min(frames,offset+count)
                if right>left: output[left*width:right*width]=data[(left-offset)*width:(right-offset)*width]
            return bytes(output),self.rate,self.channels

    def clear(self):
        with self.lock: self.chunks.clear(); self.started=None

    def available_seconds(self):
        with self.lock:
            return min(self.seconds,max(0,time.monotonic()-self.started)) if self.started is not None else 0


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
        # Encoding and snapshot copying never run on the GUI thread.
        self.saving=True
        self.events.put({'playback_save_started':True})
        requested_at=time.monotonic()
        def worker():
            try:
                data,rate,channels=self.buffer.snapshot(requested_at)
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
