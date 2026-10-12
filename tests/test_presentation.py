"""Gate Windows releases on displayed pixels and actual presentation latency."""
import json
from pathlib import Path
import statistics
import sys
import threading
import time
import unittest

from PIL import Image, ImageGrab


@unittest.skipUnless(sys.platform=='win32','Windows screen-presentation integration test')
class PresentationTests(unittest.TestCase):
    def test_complete_frames_and_tab_latency_with_large_library(self):
        import ctypes
        from PySide6.QtCore import QPoint
        from relay_agent.desktop import application
        from relay_agent.qt_desktop import Desktop
        from relay_agent.main import LABEL_COLORS
        app=application(); output=Path('presentation-diagnostics'); output.mkdir(exist_ok=True)
        samples=[]; stop=threading.Event(); sampler=None; window=Desktop({},start_agent=False)
        try:
            window.move(10,10); window.resize(1180,620); window.show(); window.raise_(); window.activateWindow(); app.processEvents()
            ctypes.windll.user32.SetCursorPos(0,0)
            controller=window.controller; colors=list(LABEL_COLORS.values())
            controller.config['clip_labels']={f'Label {n}':color for n,color in enumerate(colors)}
            controller.agent.local_files={str(n):Path(f'Clip {n:04d}.ogg') for n in range(1000)}
            controller.clip_origins={str(n):'Shared' if n%2 else 'Local' for n in range(1000)}
            controller.config['clip_settings']={str(n):{'label':f'Label {n%len(colors)}','volume':n%101} for n in range(1000)}
            controller.render_clips()
            controller.render_music({'current':'Song 0000','paused':False,'has_previous':False,
                'playlist':[{'id':str(n),'title':f'Song {n:04d}','current':n==0} for n in range(500)]})
            window.tts.setPlainText('Prepared text-to-speech panel'); app.processEvents()
            pane=window.page_host; point=pane.mapToGlobal(QPoint(0,0))
            box=(point.x(),point.y(),point.x()+min(900,pane.width()),point.y()+min(340,pane.height()))
            def capture(): return ImageGrab.grab(bbox=box,all_screens=True)
            def signature(image): return image.convert('RGB').resize((96,48),Image.Resampling.NEAREST).tobytes()
            references=[]
            for tab in range(4):
                window.select_tab(tab); app.processEvents(); ctypes.windll.dwmapi.DwmFlush()
                image=capture(); image.save(output/f'tab-{tab}.png'); references.append(signature(image))
            self.assertEqual(len(set(references)),4,'Captured panes must be distinct')
            # Measure presentation without a GIL-holding screen recorder. Pixel
            # acquisition is checked afterwards and its overhead is reported
            # separately; it must not be mistaken for application latency.
            timings=[]; capture_times=[]; missing=[]
            for tab in [0,1,2,3]*20:
                started=time.perf_counter(); window.tabs[tab].pressed.emit(); app.processEvents(); ctypes.windll.dwmapi.DwmFlush()
                timings.append((time.perf_counter()-started)*1000)
                capture_started=time.perf_counter(); shown=signature(capture())
                capture_times.append((time.perf_counter()-capture_started)*1000)
                if shown!=references[tab]: missing.append(tab)
            errors=[]
            def observe():
                while not stop.is_set():
                    try: samples.append(capture())
                    except Exception as exc: errors.append(str(exc)); break
                    stop.wait(.002)
            sampler=threading.Thread(target=observe,daemon=True); sampler.start()
            # Independent visual-transition test. Recording cannot skew the
            # latency benchmark above, but still catches any intermediate frame.
            from PySide6.QtTest import QTest
            for tab in [0,1,2,3]*12:
                window.tabs[tab].pressed.emit(); app.processEvents(); ctypes.windll.dwmapi.DwmFlush(); QTest.qWait(16)
            stop.set(); sampler.join(timeout=5)
            partial=[image for image in samples if signature(image) not in references]
            for n,image in enumerate(partial[:8]): image.save(output/f'partial-{n}.png')
            p95=sorted(timings)[int(len(timings)*.95)-1]
            report={'toolkit':'Qt 6','clips':1000,'queue_songs':500,'switches':len(timings),
                    'sampled_frames':len(samples),'partial_frames':len(partial),'missing_target_frames':len(missing),
                    'median_ms':round(statistics.median(timings),2),'p95_ms':round(p95,2),'max_ms':round(max(timings),2),
                    'screen_capture_median_ms':round(statistics.median(capture_times),2)}
            (output/'timing.json').write_text(json.dumps(report,indent=2)); print('Presentation benchmark:',json.dumps(report),flush=True)
            self.assertFalse(sampler.is_alive()); self.assertFalse(errors); self.assertGreater(len(samples),10)
            self.assertFalse(missing,'Requested pixels did not appear promptly')
            self.assertEqual(len(partial),0,'Mixed/blank visible frames; inspect screen diagnostics')
            self.assertLess(p95,50,'95% of requested views must actually reach the screen within 50 ms')
            self.assertLess(max(timings),120,'No long tab-switch stalls')
        finally:
            stop.set()
            if sampler: sampler.join(timeout=5)
            window.close(); app.processEvents()
