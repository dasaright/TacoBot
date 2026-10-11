import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

from relay_agent.updater import (UPDATE_FILES, BUNDLE_NAME, MANIFEST_NAME, find_update,
    extract_verified_bundle, write_install_script, trusted_asset_url)


def asset(name, build=5):
    return {'name':name, 'browser_download_url':f'https://github.com/dasaright/TacoBot/releases/download/relay-{build}/{name}'}


class UpdateTests(unittest.TestCase):
    def test_find_new_release_skips_drafts_and_incomplete_releases(self):
        releases = [
            {'tag_name':'relay-99','draft':True,'assets':[asset(BUNDLE_NAME), asset(MANIFEST_NAME)]},
            {'tag_name':'relay-100','assets':[]},
            {'tag_name':'relay-5','assets':[asset(BUNDLE_NAME),asset(MANIFEST_NAME)]}]
        with patch('relay_agent.updater.read_json', side_effect=[releases, {'schema':1,'build':5,'sha256':'a'*64}]):
            result=find_update(4)
        self.assertEqual(result['build'],5)

    def test_no_update_when_build_matches(self):
        with patch('relay_agent.updater.read_json', return_value=[{'tag_name':'relay-5','assets':[asset(BUNDLE_NAME),asset(MANIFEST_NAME)]}]):
            self.assertIsNone(find_update(5))

    def test_update_cannot_point_to_other_repository(self):
        with self.assertRaises(ValueError):
            trusted_asset_url('https://github.com/other/repo/releases/download/relay-5/file.zip')

    def test_bad_manifest_build_is_rejected(self):
        with patch('relay_agent.updater.read_json', side_effect=[
            [{'tag_name':'relay-5','assets':[asset(BUNDLE_NAME),asset(MANIFEST_NAME)]}],
            {'schema':1,'build':6,'sha256':'a'*64}]):
            with self.assertRaises(ValueError):find_update(4)

    def test_valid_bundle_has_only_program_files_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); archive=root/'update.zip'; target=root/'app';target.mkdir()
            config=target/'relay-config.json';config.write_text('private settings')
            with zipfile.ZipFile(archive,'w') as z:
                for file in UPDATE_FILES:z.writestr(file,b'updated content')
            extract_verified_bundle(archive,target,hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(config.read_text(),'private settings')
            self.assertTrue((target/'Files/ffmpeg.exe').exists())

    def test_checksum_and_unexpected_zip_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); archive=root/'update.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for file in UPDATE_FILES:z.writestr(file,b'content')
                z.writestr('relay-config.json',b'overwrite')
            with self.assertRaisesRegex(ValueError,'checksum'):
                extract_verified_bundle(archive,root/'target','0'*64)
            with self.assertRaisesRegex(ValueError,'Unexpected'):
                extract_verified_bundle(archive,root/'target',hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_extracted_limit_is_separate_from_download_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                for name in UPDATE_FILES:
                    bundle.writestr(name, b'x' * 100)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch('relay_agent.updater.MAX_DOWNLOAD_BYTES', 50), patch('relay_agent.updater.MAX_EXTRACTED_BYTES', 500):
                extract_verified_bundle(archive, root / 'accepted', digest)
            with patch('relay_agent.updater.MAX_EXTRACTED_BYTES', 300):
                with self.assertRaisesRegex(ValueError, 'size'):
                    extract_verified_bundle(archive, root / 'rejected', digest)
            self.assertFalse((root / 'rejected').exists())

    def test_windows_installer_preserves_settings_and_soundboard(self):
        powershell=shutil.which('powershell.exe')
        if not powershell:self.skipTest('Windows installer test')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stage=root/'stage';target=root/'app';stage.mkdir();target.mkdir()
            for file in UPDATE_FILES:
                old=target/file;old.parent.mkdir(parents=True,exist_ok=True);old.write_bytes(b'old')
                new=stage/'payload'/file;new.parent.mkdir(parents=True,exist_ok=True);new.write_bytes(b'new')
            config=target/'relay-config.json';config.write_text(json.dumps({'auto_update':False,'relay_token':'test'}))
            clips=target/'my clips';clips.mkdir();(clips/'sound.ogg').write_bytes(b'clip')
            script=write_install_script(stage)
            result=subprocess.run([powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),
                '-Stage',str(stage),'-Target',str(target),'-NoRestart'],capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr)
            for file in UPDATE_FILES:self.assertEqual((target/file).read_bytes(),b'new')
            self.assertFalse(json.loads(config.read_text())['auto_update'])
            self.assertEqual((clips/'sound.ogg').read_bytes(),b'clip')

    def test_windows_failed_install_restores_previous_program(self):
        powershell=shutil.which('powershell.exe')
        if not powershell:self.skipTest('Windows rollback test')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stage=root/'stage';target=root/'app';stage.mkdir();target.mkdir()
            for file in UPDATE_FILES:
                old=target/file;old.parent.mkdir(parents=True,exist_ok=True);old.write_bytes(b'old')
                if file != 'README.md':
                    new=stage/'payload'/file;new.parent.mkdir(parents=True,exist_ok=True);new.write_bytes(b'new')
            script=write_install_script(stage)
            result=subprocess.run([powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),
                '-Stage',str(stage),'-Target',str(target),'-NoRestart','-MaxAttempts','1'],capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,1)
            for file in UPDATE_FILES:self.assertEqual((target/file).read_bytes(),b'old')


class UpdateWindowTests(unittest.TestCase):
    def window(self):
        from relay_agent.main import RelayWindow
        from types import SimpleNamespace
        from unittest.mock import Mock
        window = RelayWindow.__new__(RelayWindow)
        window.auto_update = Mock()
        window.auto_update.get.return_value = True
        window.agent = SimpleNamespace(stream_task=None, clip_tasks={}, direct_tasks={}, direct_audio=SimpleNamespace(playing=False))
        window.last_music = None
        window.pending_update = (Path('test-stage'), False)
        window.update_idle_since = 0
        window.save = Mock()
        window.close = Mock()
        window.status = Mock()
        return window

    def test_stale_remote_queue_does_not_block_install(self):
        window = self.window()
        window.last_music = {'current': None, 'queue': ['song']}
        with patch('relay_agent.main.launch_installer') as install:
            window.poll_updates()
        install.assert_called_once()
        window.close.assert_called_once()

    def test_install_waits_while_clip_is_active(self):
        window = self.window()
        from unittest.mock import Mock
        window.agent.clip_tasks = {'clip': Mock(done=lambda:False)}
        with patch('relay_agent.main.launch_installer') as install:
            window.poll_updates()
        install.assert_not_called()

    def test_install_launches_and_closes_after_idle_period(self):
        window = self.window()
        with patch('relay_agent.main.time.monotonic', return_value=31), patch('relay_agent.main.launch_installer') as install:
            self.assertTrue(window.poll_updates())
        install.assert_called_once()
        window.close.assert_called_once()
        window.save.assert_called_once()

class ForceUpdateTests(UpdateWindowTests):
    def test_manual_update_bypasses_active_clip(self):
        from unittest.mock import Mock
        window = self.window()
        window.agent.clip_tasks = {'clip': Mock(done=lambda:False)}
        window.pending_update = (Path('test-stage'), True)
        with patch('relay_agent.main.launch_installer') as install:
            window.poll_updates()
        install.assert_called_once()
        window.close.assert_called_once()

class InstallButtonTests(UpdateWindowTests):
    def test_no_pending_update_disables_install(self):
        from unittest.mock import Mock
        window=self.window()
        window.install_button=Mock()
        window.pending_update=None
        window.next_update_check=float('inf')
        window.poll_updates()
        window.install_button.configure.assert_called_with(state='disabled')

    def test_downloaded_update_enables_install(self):
        from unittest.mock import Mock
        window=self.window()
        window.install_button=Mock()
        window.update_idle_since=None
        window.poll_updates()
        window.install_button.configure.assert_called_with(state='normal')

class InstallerEnvironmentTests(unittest.TestCase):
    def test_restart_does_not_reuse_old_pyinstaller_runtime(self):
        from relay_agent.updater import installer_environment
        with patch.dict('os.environ', {'_PYI_APPLICATION_HOME_DIR':'old-temp','_PYI_PARENT_PROCESS_LEVEL':'1','_MEIPASS2':'old-temp','OTHER':'keep'}):
            env=installer_environment()
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR',env)
        self.assertNotIn('_PYI_PARENT_PROCESS_LEVEL',env)
        self.assertNotIn('_MEIPASS2',env)
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        self.assertEqual(env['OTHER'],'keep')
