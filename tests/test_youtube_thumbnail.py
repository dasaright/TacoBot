import unittest
from relay_agent.youtube_thumbnail import youtube_thumbnail


class ThumbnailTests(unittest.TestCase):
    def test_youtube_url_variations_have_same_thumbnail(self):
        for url in ['https://youtu.be/abcdefghijk?si=123','https://www.youtube.com/watch?v=abcdefghijk&list=PL1',
                    'https://music.youtube.com/watch?v=abcdefghijk','https://youtube.com/shorts/abcdefghijk']:
            self.assertEqual(youtube_thumbnail(url),'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg')

    def test_arbitrary_and_nonvideo_urls_do_not_load_images(self):
        for url in [None,'local:clip','https://youtube.com/playlist?list=PL1','https://example.com/watch?v=abcdefghijk',
                    'https://youtube.com.evil.com/watch?v=abcdefghijk','https://user@youtube.com/watch?v=abcdefghijk',
                    'https://youtube.com/watch?v=abc','http://youtube.com/watch?v=abcdefghijk']:
            self.assertIsNone(youtube_thumbnail(url))
