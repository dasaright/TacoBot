import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from relay_agent.translation import LocalTranslator, route
from relay_agent.tts import VOICE_OPTIONS, VOICE_CATALOG

class TranslationTests(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(route('de','bg'),[('de','en'),('en','bg')])
        self.assertEqual(route('en','fr'),[('en','fr')])
        self.assertEqual(route('nl','nl'),[])
        with self.assertRaises(ValueError):route('unknown','en')

    def test_validation_same_language_and_pivot(self):
        with tempfile.TemporaryDirectory() as directory:
            translator=LocalTranslator(directory)
            self.assertEqual(translator.translate(' hello ','en','en'),'hello')
            translator.translate_pair=Mock(side_effect=['English text','Dutch text'])
            self.assertEqual(translator.translate('Hallo','de','nl'),'Dutch text')
            self.assertEqual(translator.translate_pair.call_args_list[1].args,('English text',('en','nl')))
            for text in ['', 'a'*1001]:
                with self.assertRaises(ValueError):translator.translate(text,'en','fr')

    def test_voice_catalog_covers_all_languages_and_speakers(self):
        from relay_agent.tts import LANGUAGES as VOICE_LANGUAGES
        self.assertEqual(set(v['language'] for v in VOICE_CATALOG.values() if v.get('engine','piper')=='piper'),{'en','nl','de','fr','bg'})
        self.assertEqual(set(v['language'] for v in VOICE_CATALOG.values()),set(VOICE_LANGUAGES.values()))
        self.assertEqual(len(VOICE_OPTIONS),len(VOICE_CATALOG))
        self.assertEqual(VOICE_CATALOG['bg_BG-dimitar-medium']['speakers'],1)
        self.assertEqual(VOICE_CATALOG['de_DE-thorsten_emotional-medium']['speakers'],8)
