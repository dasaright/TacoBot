import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from relay_agent.loudness import stream_normalization_filter
from relay_agent.main import FFMPEG_PATH, RelayAgent


class StreamLoudnessTests(unittest.IsolatedAsyncioTestCase):
    def test_filter_targets_youtube_loudness_with_clip_peak_ceiling(self):
        self.assertEqual(stream_normalization_filter(), 'loudnorm=I=-14:TP=-1:LRA=11')

    async def test_youtube_stream_is_normalised_by_ffmpeg(self):
        agent = RelayAgent({}); agent.send_json = AsyncMock()
        calls = []

        async def spawn(*args, **kwargs):
            calls.append(args)
            if args[0] == str(FFMPEG_PATH):
                raise OSError('stop after capturing ffmpeg arguments')
            process = MagicMock(); process.returncode = None; process.communicate = AsyncMock(return_value=(b'', b''))
            return process

        with patch('relay_agent.main.asyncio.create_subprocess_exec', new=spawn):
            await agent.stream('request', 'https://www.youtube.com/watch?v=abc')
        ffmpeg = next(args for args in calls if args[0] == str(FFMPEG_PATH))
        self.assertEqual(ffmpeg[ffmpeg.index('-af') + 1], stream_normalization_filter())
        self.assertLess(ffmpeg.index('pipe:0'), ffmpeg.index('-af'))


if __name__ == '__main__':
    unittest.main()
