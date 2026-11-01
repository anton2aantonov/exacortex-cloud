import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from xagon.product_store import ProductStore
from xagon.saved_xagon import snapshot
from xagon.manual_exchange import parse_manual_response
from xagon.fusion_store import fingerprint
from xagon.provider_service import FixtureProvider,run_fixture,ModelConfiguration
from tests.rc3_fixtures import synthetic_cache


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);merged=synthetic_cache(self.root/'saved')
        self.bundle=snapshot(self.root/'saved',merged)
        self.store=ProductStore(self.root/'new/learning.sqlite3')

    def test_18_transcripts_16_vision_registration_and_dedup(self):
        first=self.store.import_saved_xagon(self.bundle);second=self.store.import_saved_xagon(self.bundle)
        self.assertEqual(first,second);self.assertEqual(len(self.store.list_materials()),2)
        self.assertEqual((first['transcriptions'],first['vision_done']),(18,16))
        self.assertEqual(first['remaining_vision'],['seg_016','seg_017'])
        self.assertIn('uncertainties',first['segments'][0]['vision_metadata'])
        vision=self.store.get_material(first['vision_material_id'])
        self.assertEqual(len(vision['chunks']),16);self.assertEqual(vision['chunks'][3]['start_seconds'],91)
        self.assertIn('не подтверждено',vision['chunks'][0]['text'])
        claims=self.store.extract_claims(vision['id'])
        self.assertTrue(all(c['method']=='extractive_rule' and c['status']=='unreviewed' and c['evidence'][0]['validity']=='valid' for c in claims))

    def test_tampered_bundle_rejected_without_materials(self):
        for target in ('merged','vision'):
            bad=copy.deepcopy(self.bundle)
            if target=='merged':bad['merged']['words'][0]['text']='tampered'
            else:bad['segments'][0]['vision']['observations'][0]['screen_summary']='tampered'
            with self.assertRaises(ValueError):self.store.import_saved_xagon(bad)
        self.assertEqual(self.store.list_materials(),[])

    def test_atomic_import_rolls_back_if_registry_write_fails(self):
        with self.store._connect() as con:
            con.execute("CREATE TRIGGER fixture_abort BEFORE INSERT ON rc3_saved_xagon BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.import_saved_xagon(self.bundle)
        self.assertEqual(self.store.list_materials(),[])
        with self.store._connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0],0)

    def package(self):
        material=self.store.import_text('MCP source evidence stays local.','Demo')
        return self.store.create_package('MCP',[material['id']],'Compare sources')

    def test_manual_text_retains_original_and_refs(self):
        p=self.package();ref=p['citations'][0]['citation_id'];raw=f'Provided fixture statement [{ref}].\n\nUnproven fixture paragraph.'
        syn=self.store.import_manual_response(p['id'],raw,mock=True)
        self.assertEqual(syn['original_response'],raw);self.assertEqual(syn['origin'],'mock')
        self.assertEqual(syn['items'][1]['citation_status'],'unsupported')
        self.assertEqual(syn['response_sha256'],hashlib.sha256(raw.encode()).hexdigest())
        reopened=ProductStore(self.store.db_path).get_synthesis(syn['id'])
        self.assertEqual(reopened['original_response'],raw)

    def test_manual_fenced_json_and_unknown_ref(self):
        p=self.package();ref=p['citations'][0]['citation_id']
        raw='```json\n'+json.dumps({'items':[{'text':'Fixture','citation_ids':[ref]}]})+'\n```'
        self.assertEqual(self.store.import_manual_response(p['id'],raw,mock=True)['items'][0]['citation_status'],'references_valid')
        count=len(self.store.list_syntheses())
        with self.assertRaises(ValueError):self.store.import_manual_response(p['id'],'Unknown [cite_missing]',mock=True)
        self.assertEqual(len(self.store.list_syntheses()),count)

    def test_manual_preserves_leading_and_trailing_whitespace(self):
        p=self.package();ref=p['citations'][0]['citation_id']
        raw=f'  Fixture [{ref}]\n\n'
        self.assertEqual(self.store.import_manual_response(p['id'],raw,mock=True)['original_response'],raw)

    def test_duplicate_json_keys_and_bad_fence_rejected(self):
        for raw in ['{"items":[],"items":[]}','```json\n{}','{"items":NaN}','[1]']:
            with self.assertRaises(ValueError):parse_manual_response(raw)

    def test_snapshot_read_only_hashes(self):
        paths=[p for p in (self.root/'saved').rglob('*') if p.is_file()]
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        snapshot(self.root/'saved',self.root/'saved/merged.json')
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_future_schema_no_write(self):
        with self.store._connect() as con:con.execute('UPDATE rc3_schema SET version=2')
        before=self.store.db_path.read_bytes()
        with self.assertRaises(ValueError):ProductStore(self.store.db_path)
        self.assertEqual(self.store.db_path.read_bytes(),before)

    def test_provider_fixture_success_and_explicit_fallback(self):
        p=self.package();ref=p['citations'][0]['citation_id'];raw=json.dumps({'items':[{'text':'Fixture','citation_ids':[ref]}]})
        with patch('socket.socket.connect',side_effect=AssertionError('network')):
            result=run_fixture(self.store,'MCP',FixtureProvider(raw))
            self.assertEqual(result['mode'],'mock_structured');self.assertFalse(result['fallback'])
            for provider in [FixtureProvider('malformed'),FixtureProvider(error=TimeoutError()),FixtureProvider(error=RuntimeError())]:
                result=run_fixture(self.store,'MCP',provider)
                self.assertTrue(result['fallback']);self.assertEqual(result['mode'],'extractive');self.assertFalse(result['real_ai'])
        with self.assertRaises(PermissionError):run_fixture(self.store,'MCP',FixtureProvider(raw),configuration=ModelConfiguration(provider='cloud'))


if __name__=='__main__':unittest.main()
