import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xagon.intelligence_store import IntelligenceStore
from xagon.intelligence_chat import IntelligenceChat, BudgetGuard

class IntelligenceChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=IntelligenceStore(Path(self.temp.name)/'library.sqlite3')
        self.a=self.store.import_text('MCP не передаёт секреты.','A')
        self.b=self.store.import_text('MCP требует контекст.','B')
        self.chat=IntelligenceChat(self.store)
    def test_offline_multisource_and_reopen(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            answer=self.chat.ask('MCP',[self.a['id'],self.b['id']])
        self.assertEqual(answer['mode'],'extractive');self.assertEqual(len(answer['sources']),2)
        self.assertIn('не передаёт',answer['answer']);self.assertFalse(answer['verified_knowledge'])
        reopened=IntelligenceChat(IntelligenceStore(self.store.db_path))
        self.assertEqual(reopened.history()[0]['conversation_id'],answer['conversation_id'])
    def test_mock_and_cloud_guard(self):
        result=self.chat.ask('MCP',model='mock');self.assertEqual(result['mode'],'mock');self.assertIn('MOCK',result['answer'])
        with patch.dict(os.environ,{'XAGON_ALLOW_LIVE_API':'1'}),patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            for model in ['cloud','gemini',None,{}]:
                with self.assertRaises(PermissionError):self.chat.ask('MCP',model=model)
        self.assertEqual(len(self.chat.history()),1);self.assertFalse(self.chat.models()[-1]['enabled'])
    def test_filtered_no_results_and_no_knowledge_index(self):
        answer=self.chat.ask('секреты',[self.b['id']]);self.assertFalse(answer['sources']);self.assertIn('не генерировался',answer['answer'])
        self.assertEqual(len(self.store.list_materials()),2);self.assertEqual(self.store.list_claims(),[])
    def test_corrupt_citation_rejected_before_history(self):
        source=dict(self.a['chunks'][0]);source['text']='Invented quote'
        with patch.object(self.chat.context,'build',return_value=[source]),self.assertRaises(ValueError):self.chat.ask('MCP')
        self.assertEqual(self.chat.history(),[])
    def test_history_immutable_and_validation(self):
        self.chat.ask('MCP')
        with self.store._connect() as con:
            with self.assertRaises(Exception):con.execute('UPDATE intelligence_conversations SET payload=?',('{}',))
        with self.assertRaises(ValueError):self.chat.ask('   ')
        self.assertEqual(len(self.chat.history()),1)
    def test_future_chat_schema_before_initialization_writes(self):
        with self.store._connect() as con:con.execute('UPDATE intelligence_chat_schema SET version=2')
        before=hashlib.sha256(self.store.db_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):IntelligenceChat(self.store)
        self.assertEqual(hashlib.sha256(self.store.db_path.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main()
