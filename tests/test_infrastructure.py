import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xagon.product_store import ProductStore
from xagon.rc_backup import create_backup,restore_backup


class PortableInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_unicode_and_spaces_in_paths_search_reopen(self):
        store=ProductStore(self.root/'Библиотека с пробелами'/'learning.sqlite3')
        path=self.root/'English и русский.md';path.write_text('# MCP\n\nMCP контекст сохраняется локально.','utf-8')
        material=store.import_file(path)
        self.assertTrue(store.search('контекст',material['id']))
        self.assertEqual(ProductStore(store.db_path).get_material(material['id'])['chunks'],material['chunks'])

    def test_sqlite_snapshot_restore_unicode_path(self):
        store=ProductStore(self.root/'original/learning.sqlite3')
        material=store.import_text('MCP unicode evidence.','Тест')
        store.add_note(material['id'],'Заметка')
        backup=self.root/'Копия с пробелами';create_backup(store,backup)
        destination=self.root/'Восстановленная библиотека';restore_backup(backup,destination)
        restored=ProductStore(destination/'learning.sqlite3')
        self.assertEqual(restored.list_notes(material['id'])[0]['text'],'Заметка')

    def test_explicit_production_root_rejected_on_any_platform(self):
        root=self.root/'production';root.mkdir()
        with patch.dict(os.environ,{'XAGON_DATA_DIR':str(root)}):
            with self.assertRaises(ValueError):ProductStore(root/'learning.sqlite3')
        self.assertFalse((root/'learning.sqlite3').exists())

    def test_independent_project_stores_do_not_share_records(self):
        a=ProductStore(self.root/'project_a/learning.sqlite3');b=ProductStore(self.root/'project_b/learning.sqlite3')
        m=a.import_text('Project A MCP fixture.','A');a.add_note(m['id'],'Only A')
        self.assertEqual(b.list_materials(),[])
        self.assertEqual(len(a.list_notes(m['id'])),1)

    def test_all_python_sources_parse_and_shell_files_are_lf(self):
        import ast
        root=Path(__file__).resolve().parents[1]
        for folder in ('xagon','tests','scripts'):
            for path in (root/folder).rglob('*.py'):ast.parse(path.read_text('utf-8'),filename=str(path))
        self.assertNotIn(b'\r',(root/'scripts/test_offline.sh').read_bytes())

    @unittest.skipIf(os.name=='nt','Requires native POSIX path semantics; Linux not emulated')
    def test_windows_drive_path_rejected_on_posix(self):
        with self.assertRaises(ValueError):ProductStore(Path(r'C:\private\learning.sqlite3'))
