import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from relay_agent import main


def deno_zip(directory, payload=b'fake deno'):
    archive = Path(directory) / 'deno.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('deno.exe', payload)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


class DenoRuntimeTests(unittest.TestCase):
    def test_downloads_verifies_and_installs_once(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, digest = deno_zip(directory)
            target = Path(directory) / 'Files' / 'deno.exe'
            self.assertTrue(main.ensure_deno(archive.as_uri(), digest, target))
            self.assertEqual(target.read_bytes(), b'fake deno')
            self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ['deno.exe'])
            with patch.object(main.urllib.request, 'urlopen') as fetch:
                self.assertTrue(main.ensure_deno(archive.as_uri(), digest, target)); fetch.assert_not_called()

    def test_checksum_mismatch_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = deno_zip(directory)
            target = Path(directory) / 'Files' / 'deno.exe'
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertFalse(main.ensure_deno(archive.as_uri(), '0' * 64, target))
            self.assertEqual(list(target.parent.iterdir()), [])

    def test_youtube_args_use_bundled_deno_only_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            deno = Path(directory) / 'deno.exe'
            with patch.object(main, 'DENO_PATH', deno):
                self.assertNotIn('--js-runtimes', main.youtube_common_args({}))
                deno.write_bytes(b'x')
                args = main.youtube_common_args({})
                self.assertEqual(args[args.index('--js-runtimes') + 1], f'deno:{deno}')


if __name__ == '__main__':
    unittest.main()
