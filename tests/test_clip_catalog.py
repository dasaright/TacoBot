import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from relay_agent import clip_catalog as catalog


def files(folder):
    return {hashlib.sha256(str(path.resolve()).encode()).hexdigest(): path
            for path in Path(folder).glob('*.ogg')}


class ClipCatalogTests(unittest.TestCase):
    def test_create_empty_folder_and_migrate_only_portable_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); catalog.load_folder(folder,{}, {})
            self.assertTrue((folder/catalog.CATALOG_NAME).exists())
            (folder/'Taco clip.ogg').write_bytes(b'audio'); found=files(folder); key=next(iter(found))
            config={'relay_token':'secret','clip_settings':{key:{'label':'Meme','volume':25,'private':'secret'}},'clip_labels':{'Meme':'#d9b3ff'}}
            settings, colors=catalog.load_folder(folder,found,config)
            self.assertEqual(settings[key],{'label':'Meme','volume':25})
            stored=(folder/catalog.CATALOG_NAME).read_text()
            self.assertNotIn('secret',stored); self.assertNotIn(str(folder),stored)
            self.assertEqual(colors,{'Meme':'#d9b3ff'})

    def test_folder_move_and_fresh_config_restore_labels_colors_and_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); old=root/'old'; old.mkdir(); (old/'Clip.ogg').write_bytes(b'audio')
            found=files(old); key=next(iter(found)); cfg={'clip_settings':{key:{'label':'Laugh','volume':-30}},'clip_labels':{'Laugh':'#ffb3c1'}}
            catalog.load_folder(old,found,cfg)
            new=root/'fresh install clips'; shutil.copytree(old,new)
            loaded, colors=catalog.load_folder(new,files(new),{})
            newkey=next(iter(loaded)); self.assertNotEqual(key,newkey)
            self.assertEqual(loaded[newkey],{'label':'Laugh','volume':-30})
            self.assertEqual(colors,{'Laugh':'#ffb3c1'})

    def test_rename_save_and_unavailable_clips_keep_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); old=folder/'Old.ogg'; old.write_bytes(b'audio')
            found=files(folder); key=next(iter(found)); cfg={'clip_settings':{key:{'label':'Fun','volume':10}}}
            catalog.load_folder(folder,found,cfg)
            new=folder/'New.ogg'; old.rename(new); catalog.rename_entry(old,new)
            found=files(folder); key=next(iter(found)); cfg={'clip_settings':{key:{'label':'New Label','volume':50}},'clip_labels':{'New Label':'#abcdff'}}
            catalog.save_folders(found,cfg); catalog.save_folders({}, {})
            loaded,_=catalog.load_folder(folder,found,{})
            self.assertEqual(loaded[key],cfg['clip_settings'][key])
            self.assertNotIn('Old.ogg',catalog.read_catalog(folder)['clips'])

    def test_corrupt_primary_recovers_backup_without_erasing_original_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); path=folder/catalog.CATALOG_NAME
            good={'schema':1,'clips':{'Missing.ogg':{'label':'Keep'}},'labels':{}}
            path.with_suffix('.json.bak').write_text(json.dumps(good)); path.write_text('broken')
            self.assertEqual(catalog.read_catalog(folder),good)
            path.write_text('broken'); path.with_suffix('.json.bak').write_text('also broken')
            with self.assertRaisesRegex(ValueError,'preserved'): catalog.load_folder(folder,{}, {})
            self.assertEqual(path.read_text(),'broken')

    def test_same_filename_in_separate_folders_has_separate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,label in [('local','Local Label'),('shared','Shared Label')]:
                folder=root/name; folder.mkdir(); (folder/'Same.ogg').write_bytes(b'audio')
                found=files(folder); key=next(iter(found))
                catalog.load_folder(folder,found,{'clip_settings':{key:{'label':label}}})
            for name,label in [('local','Local Label'),('shared','Shared Label')]:
                loaded,_=catalog.load_folder(root/name,files(root/name),{})
                self.assertEqual(next(iter(loaded.values()))['label'],label)
