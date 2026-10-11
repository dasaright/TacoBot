"""Measure displayed frames, not just which tab is selected."""
import json
from pathlib import Path
import statistics
import sys
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageGrab


@unittest.skipUnless(sys.platform == "win32", "Windows compositor integration test")
class PresentationTests(unittest.TestCase):
    def test_complete_frames_and_tab_latency_with_large_library(self):
        import ctypes
        from relay_agent.main import RelayWindow, create_root, LABEL_COLORS
        from relay_agent.presentation import windows_api, COMPOSITED
        output = Path("presentation-diagnostics")
        output.mkdir(exist_ok=True)
        root = create_root()
        samples = []
        stop = threading.Event()
        sampler = None
        try:
            with patch("threading.Thread.start"), patch.object(RelayWindow, "refresh_files"), patch.object(RelayWindow, "poll"):
                window = RelayWindow(root, {})
            root.geometry("1160x620+10+10")
            root.attributes("-topmost", True)
            root.lift(); root.focus_force(); root.update()
            api, get_style, _, _ = windows_api()
            hwnd = api.GetAncestor(root.winfo_id(), 2)
            self.assertTrue(get_style(hwnd, -20) & COMPOSITED)
            ctypes.windll.user32.SetCursorPos(0, 0)
            colors = list(LABEL_COLORS.values())
            window.config["clip_labels"] = {f"Label {n}": color for n, color in enumerate(colors)}
            window.agent.local_files = {str(n): Path(f"Clip {n:04d}.ogg") for n in range(1000)}
            window.clip_origins = {str(n): "Shared" if n % 2 else "Local" for n in range(1000)}
            window.config["clip_settings"] = {str(n): {"label": f"Label {n % len(colors)}", "volume": n % 101} for n in range(1000)}
            window.render_clips()
            for n in range(500):
                window.queue_view.insert("", "end", iid=f"song{n}", values=(f"Song {n:04d}",))
            window.tts_text.insert("1.0", "Prepared text-to-speech panel")
            root.update()
            panel = window.notebook
            # Sample only the pane: footer status and OS title bar are unrelated.
            box = (panel.winfo_rootx(), panel.winfo_rooty(),
                   panel.winfo_rootx() + min(900, panel.winfo_width()),
                   panel.winfo_rooty() + min(340, panel.winfo_height()))
            def capture():
                return ImageGrab.grab(bbox=box, all_screens=True)
            def signature(image):
                return image.convert("RGB").resize((96, 48), Image.Resampling.NEAREST).tobytes()
            references = []
            for tab in range(3):
                window.press_tab(tab); root.update()
                ctypes.windll.dwmapi.DwmFlush()
                image = capture()
                image.save(output / f"tab-{tab}.png")
                references.append(signature(image))
            self.assertEqual(len(set(references)), 3, "Screen capture must distinguish every pane")
            errors = []
            def observe():
                while not stop.is_set():
                    try:
                        samples.append(capture())
                    except Exception as exc:
                        errors.append(str(exc)); break
            sampler = threading.Thread(target=observe, daemon=True)
            sampler.start()
            timings = []
            # Includes input handler, layout, native painting and DWM presentation.
            for tab in [0, 1, 2] * 20:
                started = time.perf_counter()
                window.press_tab(tab)
                root.update_idletasks()
                ctypes.windll.dwmapi.DwmFlush()
                timings.append((time.perf_counter() - started) * 1000)
            stop.set(); sampler.join(timeout=5)
            self.assertFalse(sampler.is_alive())
            self.assertFalse(errors)
            partial = [image for image in samples if signature(image) not in references]
            for n, image in enumerate(partial[:8]):
                image.save(output / f"partial-{n}.png")
            p95 = sorted(timings)[int(len(timings) * .95) - 1]
            report = {"clips": 1000, "queue_songs": 500, "switches": len(timings),
                      "sampled_frames": len(samples), "partial_frames": len(partial),
                      "median_ms": round(statistics.median(timings), 2),
                      "p95_ms": round(p95, 2), "max_ms": round(max(timings), 2)}
            (output / "timing.json").write_text(json.dumps(report, indent=2))
            print("Presentation benchmark:", json.dumps(report), flush=True)
            self.assertGreater(len(samples), 10)
            self.assertEqual(len(partial), 0, "Visible intermediate/blank frames; inspect diagnostics")
            self.assertLess(p95, 50, "95% of full presentations must finish within 50 ms")
            self.assertLess(max(timings), 120, "No long tab-switch stalls")
        finally:
            stop.set()
            if sampler:
                sampler.join(timeout=5)
            root.destroy()
