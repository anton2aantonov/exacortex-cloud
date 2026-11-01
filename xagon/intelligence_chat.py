"""Provider-ready multi-source chat; every available provider is offline."""
from __future__ import annotations
import json
from typing import Protocol
from .beta_chat import ChatService
from .learning import _plain, _now, _normalize
from .fusion_store import canonical

class Provider(Protocol):
    def answer(self,question: str,sources: list[dict])->dict: ...

class BudgetGuard:
    @staticmethod
    def check(model):
        if not isinstance(model,str) or model not in ('offline','mock'):
            raise PermissionError('Реальные API запрещены: бюджет $0. Выберите offline или явно тестовый mock.')

class OfflineProvider:
    def answer(self,question,sources):
        return {'mode':'extractive','answer':ChatService._answer(sources),'sources':sources,'provider':'offline','verified_knowledge':False}

class MockProvider:
    def answer(self,question,sources):
        return {'mode':'mock','answer':'MOCK — тестовый провайдер, настоящая модель не вызывалась.\n'+ChatService._answer(sources),'sources':sources,'provider':'mock','verified_knowledge':False}

class ContextBuilder:
    def __init__(self,store):self.store=store
    def build(self,question,material_ids=None):return self.store.retrieve(question,material_ids,limit=5)

class CitationValidator:
    def __init__(self,store):self.store=store;self.legacy=ChatService(store)
    def validate(self,sources,material_ids=None):
        selected=self.store.selected_materials(material_ids)
        with self.store._connect() as con:
            canonical_sources=self.legacy._validate_sources(con,sources,None)
            for original,source in zip(sources,canonical_sources):
                if source['material_id'] not in selected:raise ValueError('Цитата из невыбранного материала')
                for ref in original.get('also_in',[]):
                    if ref.get('material_id') not in selected:raise ValueError('Дополнительный источник не выбран')
                    row=con.execute('SELECT text,start_seconds,end_seconds FROM chunks WHERE id=? AND material_id=?',(ref.get('chunk_id'),ref.get('material_id'))).fetchone()
                    if row is None or ' '.join(_normalize(row['text']).split())!=' '.join(_normalize(source['text']).split()) or any(row[k]!=ref.get(k) for k in ('start_seconds','end_seconds')):raise ValueError('Дополнительная цитата не совпадает с источником')
                source['score']=original.get('score',0);source['also_in']=original.get('also_in',[])
        return canonical_sources

class IntelligenceChat:
    def __init__(self,store):
        self.store=store
        # Inspect our version before inherited ChatService initializes tables.
        with store._connect() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_chat_schema'").fetchone():
                versions=con.execute('SELECT version FROM intelligence_chat_schema').fetchall()
                if len(versions)!=1 or versions[0][0]!=1:raise ValueError('Новая схема Intelligence Chat не поддерживается; файл не изменён')
        self.context=ContextBuilder(store);self.validator=CitationValidator(store)
        with store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS intelligence_chat_schema(version INTEGER NOT NULL)')
            rows=con.execute('SELECT version FROM intelligence_chat_schema').fetchall()
            if rows and (len(rows)!=1 or rows[0][0]!=1):raise ValueError('Новая схема Intelligence Chat не поддерживается')
            con.execute('CREATE TABLE IF NOT EXISTS intelligence_conversations(id INTEGER PRIMARY KEY, question TEXT NOT NULL, model TEXT NOT NULL, material_ids TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            if not rows:con.execute('INSERT INTO intelligence_chat_schema VALUES(1)')
            for action in ('UPDATE','DELETE'):con.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_intelligence_chat_{action} BEFORE {action} ON intelligence_conversations BEGIN SELECT RAISE(ABORT,'Chat history is append-only'); END")
    @staticmethod
    def models():
        return [{'id':'offline','enabled':True,'label':'Локальный поиск цитат','cost':0},{'id':'mock','enabled':True,'label':'Mock — тест, не настоящая модель','cost':0},{'id':'cloud','enabled':False,'label':'Cloud API запрещён','cost':None}]
    def ask(self,question,material_ids=None,model='offline'):
        BudgetGuard.check(model);question=_plain(question,'Вопрос',2000)
        selected=self.store.selected_materials(material_ids)
        sources=self.validator.validate(self.context.build(question,selected),selected)
        provider=OfflineProvider() if model=='offline' else MockProvider();result=provider.answer(question,sources)
        # A provider is an untrusted boundary too. Validate BEFORE persisting.
        if not isinstance(result,dict) or not isinstance(result.get('answer'),str) or len(result['answer'])>1024*1024:
            raise ValueError('Провайдер вернул некорректный ответ; история не изменена')
        returned=self.validator.validate(result.get('sources'),selected)
        expected=ChatService._answer(sources)
        if model=='mock':expected='MOCK — тестовый провайдер, настоящая модель не вызывалась.\n'+expected
        if (canonical(returned)!=canonical(sources) or result['answer']!=expected or
                result.get('mode')!=('extractive' if model=='offline' else 'mock') or
                result.get('provider')!=model or result.get('verified_knowledge') is not False):
            raise ValueError('Ответ offline/mock не соответствует источникам; история не изменена')
        stamp=_now();result.update(created_at=stamp,model=model,material_ids=selected)
        with self.store._connect() as con:
            cur=con.execute('INSERT INTO intelligence_conversations(question,model,material_ids,payload,created_at) VALUES(?,?,?,?,?)',(question,model,canonical(selected),canonical(result),stamp));key=cur.lastrowid
        result.update(conversation_id=key,question=question)
        return result
    def history(self):
        with self.store._connect() as con:rows=con.execute('SELECT * FROM intelligence_conversations ORDER BY id DESC LIMIT 200').fetchall()
        result=[]
        for row in rows:
            data=json.loads(row['payload']);selected=json.loads(row['material_ids']);BudgetGuard.check(row['model'])
            sources=self.validator.validate(data['sources'],selected)
            expected=(OfflineProvider() if row['model']=='offline' else MockProvider()).answer(row['question'],sources)
            if any(data.get(k)!=expected[k] for k in ('answer','mode','provider','verified_knowledge')):raise ValueError('История Intelligence Chat не совпадает с источниками')
            data.update(conversation_id=row['id'],question=row['question']);result.append(data)
        return result
