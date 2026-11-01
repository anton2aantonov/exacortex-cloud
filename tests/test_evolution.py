"""Synthetic Evolution regression tests. No models or transport."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from xagon.product_store import ProductStore

class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ProductStore(Path(self.temp.name)/'library.sqlite3')
        self.materials=[self.store.import_text(text,title,title+'.md') for title,text in [
            ('course','MCP requires evidence.'),('document','MCP requires evidence. Additional synthetic context.'),
            ('project','MCP does not require evidence.')]]
        self.claims=[self.store.create_claim(m['id'], 'MCP requires evidence.' if i<2 else 'MCP does not require evidence.', m['chunks'][0]['id']) for i,m in enumerate(self.materials)]
        self.gap=self.store.create_claim(self.materials[0]['id'],'MCP requires more evidence')
        self.package=self.store.create_package('MCP',[m['id'] for m in self.materials],'Compare')
    def payload(self,text='Synthetic conclusion'):
        c=self.claims[0]
        return {'origin':'mock','package_id':self.package['id'],'items':[{'text':text,
            'citation_ids':[self.package['citations'][0]['citation_id']],
            'claim_ids':[c['id']],'evidence_ids':[c['evidence'][0]['id']]}]}
    def test_claim_triage_and_gaps(self):
        result=self.package['evolution']
        self.assertFalse(result['semantic_analysis']);self.assertFalse(result['independently_verified'])
        self.assertIn('exact_text',[p['relation'] for p in result['pairs']])
        self.assertIn('potential_conflict',[p['relation'] for p in result['pairs']])
        self.assertIn({'claim_id':self.gap['id'],'reason':'no_evidence'},result['evidence_gaps'])
        self.assertEqual(len(self.package['boundaries']),4)
        self.assertEqual(len(self.store.list_claims()),4)
    def test_versions_and_order_independent_comparison(self):
        a=self.store.import_synthesis(self.package['id'],self.payload())
        payload=self.payload('Second synthetic conclusion');payload['parent_synthesis_id']=a['id']
        b=self.store.import_synthesis(self.package['id'],payload)
        self.assertEqual(b['parent_synthesis_id'],a['id'])
        comparison=self.store.compare_syntheses(a['id'],b['id'])
        self.assertEqual(len(comparison['added']),1);self.assertEqual(len(comparison['removed']),1)
        two=self.payload();two['items'].append(dict(two['items'][0],text='Another'))
        c=self.store.import_synthesis(self.package['id'],two)
        two['items'].reverse();d=self.store.import_synthesis(self.package['id'],two)
        self.assertEqual(len(self.store.compare_syntheses(c['id'],d['id'])['unchanged']),2)
        self.assertEqual(ProductStore(self.store.db_path).get_synthesis(a['id'])['items'][0]['text'],'Synthetic conclusion')
    def test_invalid_references_atomic(self):
        for field,value in [('claim_ids',['missing']),('evidence_ids',['missing']),('evidence_ids',[self.claims[1]['evidence'][0]['id']]),('citation_ids',['missing'])]:
            payload=self.payload();payload['items'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.store.import_synthesis(self.package['id'],payload)
        self.assertEqual(self.store.list_syntheses(),[])
        payload=self.payload();payload['package_id']='other'
        with self.assertRaises(ValueError):self.store.import_synthesis(self.package['id'],payload)
    def test_parent_scope_and_append_only(self):
        a=self.store.import_synthesis(self.package['id'],self.payload())
        other=self.store.create_package('evidence',[self.materials[0]['id']],'Other')
        payload={'origin':'mock','parent_synthesis_id':a['id'],'items':[{'text':'Other'}]}
        with self.assertRaises(ValueError):self.store.import_synthesis(other['id'],payload)
        with self.store._connect() as con:
            with self.assertRaises(sqlite3.IntegrityError):con.execute('DELETE FROM fusion_syntheses')
    def test_user_decision_task_lineage(self):
        a=self.store.import_synthesis(self.package['id'],self.payload())
        with self.assertRaises(ValueError):self.store.create_synthesis_task(a['id'],'item_1')
        self.store.review_synthesis(a['id'],'item_1','accept')
        task=self.store.create_synthesis_task(a['id'],'item_1')
        self.assertEqual(task['synthesis_id'],a['id']);self.assertFalse(task['verified_truth'])
        self.store.review_synthesis(a['id'],'item_1','reject')
        self.assertTrue(self.store.list_learning_tasks()[0]['review_warning'])
        self.assertEqual(self.store.get_claim(self.claims[0]['id'])['status'],'unreviewed')

    def test_evidence_only_conclusion_can_be_practiced(self):
        payload=self.payload();payload['items'][0]['citation_ids']=[]
        syn=self.store.import_synthesis(self.package['id'],payload)
        self.assertEqual(syn['items'][0]['citation_status'],'references_valid')
        self.store.review_synthesis(syn['id'],'item_1','accept')
        task=self.store.create_synthesis_task(syn['id'],'item_1')
        self.assertEqual(task['synthesis_id'],syn['id'])
        self.assertEqual(self.store.get_synthesis(syn['id'])['items'][0]['evidence_ids'],payload['items'][0]['evidence_ids'])

    def test_external_reports_and_user_decisions_remain_separate(self):
        self.store.add_external_check(self.claims[0]['id'],'https://example.invalid/synthetic','2026-11-01','Synthetic report','Not fetched')
        p=self.store.create_package('MCP',[m['id'] for m in self.materials],'Compare')
        self.assertFalse(p['external_verifications'][0]['independently_verified_by_app'])
        self.assertEqual(self.package['external_verifications'],[])
        syn=self.store.import_synthesis(self.package['id'],self.payload())
        self.store.review_synthesis(syn['id'],'item_1','accept')
        self.assertEqual(self.store.get_claim(self.claims[0]['id'])['status'],'unreviewed')
        self.assertEqual(self.store.get_package(self.package['id']),self.package)
