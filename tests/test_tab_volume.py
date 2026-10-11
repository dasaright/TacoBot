import unittest
from array import array
from relay_agent.main import RelayAgent

class TabVolumeTests(unittest.TestCase):
    def test_independent_gains_and_live_adjustment(self):
        agent = RelayAgent({"music_volume": 20, "bot_clip_volume": 50, "tts_volume": 80,
                            "clip_settings": {"clip": {"volume": 100}}})
        data = array("h", [1000, -1000]).tobytes()
        def values(url):
            return list(array("h", agent.apply_stream_volume(data, url)))
        self.assertEqual(values("https://youtube.com/watch?v=video"), [200, -200])
        self.assertEqual(values("local:clip"), [1000, -1000])
        self.assertEqual(values("local:tts:generated"), [800, -800])
        agent.config["music_volume"] = 0
        self.assertEqual(values("https://youtube.com/watch?v=video"), [0, 0])
        self.assertEqual(values("local:tts:generated"), [800, -800])

    def test_defaults_preserve_previous_audio_level(self):
        agent = RelayAgent({})
        data = array("h", [1000, -1000]).tobytes()
        for url in ("https://youtube.com/watch?v=video", "local:clip", "local:tts:generated"):
            self.assertEqual(agent.apply_stream_volume(data, url), data)
