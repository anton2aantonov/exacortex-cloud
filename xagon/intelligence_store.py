"""Local retrieval, refresh comparisons and personal learning-task lineage."""
from __future__ import annotations
import json
from collections import defaultdict, deque
from pathlib import Path
import sqlite3
from .fusion_store import FusionStore, fingerprint, canonical
from .learning import _plain, _now, _terms, _WORDS, _normalize

class IntelligenceStore(FusionStore):
    def __init__(self, db_path):
        self.db_path=Path(db_path).expanduser().resolve();self._validate_target()
        if self.db_path.is_file():
            con=sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True)
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_schema'").fetchone() and con.execute('SELECT version FROM intelligence_schema').fetchall()!=[(1,)]:raise ValueError('Новая схема Intelligence не поддерживается; файл не изменён')
            finally:con.close()
        super().__init__(db_path)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS intelligence_schema(version INTEGER NOT NULL)')
            if not con.execute('SELECT 1 FROM intelligence_schema').fetchone():con.execute('INSERT INTO intelligence_schema VALUES(1)')
            for sql in [
                'CREATE TABLE IF NOT EXISTS intelligence_runs(id INTEGER PRIMARY KEY, package_id TEXT NOT NULL REFERENCES fusion_packages(id), result_package_id TEXT NOT NULL REFERENCES fusion_packages(id), created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS intelligence_tasks(id TEXT PRIMARY KEY, claim_id TEXT NOT NULL UNIQUE REFERENCES knowledge_claims(id), text TEXT NOT NULL, created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS intelligence_task_events(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES intelligence_tasks(id), completed INTEGER NOT NULL CHECK(completed IN (0,1)), created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS intelligence_refresh(id TEXT PRIMARY KEY, old_material_id TEXT NOT NULL REFERENCES materials(id), new_material_id TEXT NOT NULL REFERENCES materials(id), payload TEXT NOT NULL, checked_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS intelligence_checks(id INTEGER PRIMARY KEY, claim_ids TEXT NOT NULL, note TEXT NOT NULL, checked_at TEXT NOT NULL)',
            ]:con.execute(sql)
            for table in ('intelligence_runs','intelligence_tasks','intelligence_task_events','intelligence_refresh','intelligence_checks'):
                for action in ('UPDATE','DELETE'):con.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Intelligence history is append-only'); END")

    def _validate_target(self):
        super()._validate_target()
        root=Path(r'C:\XAGON\ExaCortex_Knowledge_Fusion').resolve()
        if self.db_path==root or self.db_path.is_relative_to(root):raise ValueError('Исходный Fusion доступен только для чтения')

    def selected_materials(self, material_ids):
        if material_ids is None:
            material_ids=[m['id'] for m in self.list_materials()]
            if len(material_ids)>10:raise ValueError('Выберите до10 источников для поиска')
        if not isinstance(material_ids,list) or len(material_ids)>10 or any(not isinstance(x,str) for x in material_ids):raise ValueError('Ожидается список до10 источников')
        ids=list(dict.fromkeys(material_ids))
        for mid in ids:self.get_material(mid)
        return ids

    def retrieve(self, query, material_ids=None, limit=20):
        if not isinstance(query,str) or len(query)>2000:raise ValueError('Запрос до2000символов')
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('Лимит1..50')
        ids=self.selected_materials(material_ids);terms=_terms(query);ranked=[]
        for mid in ids:
            for pos,c in enumerate(self.search(query,mid,limit=50)):
                words=_WORDS.findall(_normalize(c['text']))
                score=sum(any(w.startswith(t) for w in words) for t in terms)
                ranked.append((score,pos,c))
        ranked.sort(key=lambda x:(-x[0],x[1],x[2]['id']))
        result=[];seen={}
        for score,pos,c in ranked:
            key=' '.join(_normalize(c['text']).split())
            provenance={k:c[k] for k in ('material_id','start_seconds','end_seconds')};provenance['chunk_id']=c['id']
            if key in seen:seen[key]['also_in'].append(provenance);continue
            item=dict(c,score=score,also_in=[provenance]);seen[key]=item;result.append(item)
        return result[:limit]

    def context_package(self, topic, material_ids, task):
        return self.create_package(topic,self.selected_materials(material_ids),task)

    def reprocess_package(self, package_id, task=None):
        old=self.get_package(package_id)
        result=self.create_package(old['topic'],[m['id'] for m in old['sources']],old['task'] if task is None else task)
        with self._connect() as con:con.execute('INSERT INTO intelligence_runs(package_id,result_package_id,created_at) VALUES(?,?,?)',(package_id,result['id'],_now()))
        return result

    def package_runs(self, package_id):
        self.get_package(package_id)
        with self._connect() as con:return [dict(r) for r in con.execute('SELECT * FROM intelligence_runs WHERE package_id=? ORDER BY id',(package_id,))]

    def compare_syntheses(self, left_id, right_id):
        a=self.get_synthesis(left_id);b=self.get_synthesis(right_id)
        def key(item):
            return fingerprint({k:item.get(k,[]) for k in ('text','kind','citation_ids','claim_ids','evidence_ids')})
        right=defaultdict(deque)
        for item in b['items']:right[key(item)].append(item)
        unchanged=[];removed=[];matched_ids=set();matches=[]
        for item in a['items']:
            bucket=right[key(item)]
            if bucket:
                match=bucket.popleft();matched_ids.add(match['id'])
                unchanged.append(item)
                matches.append({'left_item_id':item['id'],'right_item_id':match['id']})
            else:removed.append(item)
        added=[item for item in b['items'] if item['id'] not in matched_ids]
        return {'left_id':left_id,'right_id':right_id,'same_package':a['package_id']==b['package_id'],
                'method':'exact_content_and_provenance','unchanged':unchanged,'matches':matches,
                'removed':removed,
                'added':added,
                'user_decisions':{'left':a['reviews'],'right':b['reviews']},
                'warning':'Точное сравнение текста и ссылок независимо от позиции, не смысловой анализ и не проверка истинности.'}

    def create_learning_task(self, claim_id):
        claim=self.get_claim(claim_id)
        if claim['status']!='user_confirmed':raise ValueError('Сначала подтвердите утверждение пользователем; это не независимая проверка')
        key='task_'+fingerprint(claim_id)[:28]
        text='Примените утверждение в локальном проекте, сохраните результат и ограничения. Источник: '+claim_id+'\n'+claim['text']
        with self._connect() as con:con.execute('INSERT OR IGNORE INTO intelligence_tasks VALUES(?,?,?,?)',(key,claim_id,text,_now()))
        return next(t for t in self.list_learning_tasks() if t['id']==key)

    def list_learning_tasks(self):
        with self._connect() as con:
            result=[]
            for row in con.execute('SELECT * FROM intelligence_tasks ORDER BY created_at,id'):
                t=dict(row);events=[dict(e) for e in con.execute('SELECT * FROM intelligence_task_events WHERE task_id=? ORDER BY id',(t['id'],))]
                t.update(status='completed' if events and events[-1]['completed'] else 'pending',events=events,verified_mastery=False);result.append(t)
            return result

    def set_learning_task(self, task_id, completed):
        if type(completed) is not bool:raise ValueError('completed должен быть true/false')
        tasks=self.list_learning_tasks()
        if not any(t['id']==task_id for t in tasks):raise KeyError('Задание не найдено')
        with self._connect() as con:con.execute('INSERT INTO intelligence_task_events(task_id,completed,created_at) VALUES(?,?,?)',(task_id,int(completed),_now()))
        return next(t for t in self.list_learning_tasks() if t['id']==task_id)

    def import_revision(self, old_material_id, text, title, source_name='revision.md'):
        self.get_material(old_material_id)
        new=self.import_text(text,title,source_name)
        comparison=self.compare_materials(old_material_id,new['id'])
        return {'old_material_id':old_material_id,'new_material_id':new['id'],'comparison_id':comparison['id']}

    def compare_materials(self, old_id, new_id):
        old=self.get_material(old_id);new=self.get_material(new_id)
        a=[c['text'] for c in old['chunks']];b=[c['text'] for c in new['chunks']]
        aset,bset=set(a),set(b)
        removed=[s for s in a if s not in bset];added=[s for s in b if s not in aset]
        claims=[{'id':c['id'],'text':c['text'],'status':c['status'],'reason':'Исходный фрагмент отсутствует в новой версии; возможна устарелость, не опровержение'} for c in self.list_claims(material_id=old_id) if c.get('source_text') in removed or c['original_text'] in removed]
        key='refresh_'+fingerprint([old_id,new_id])[:28]
        result={'id':key,'old_material_id':old_id,'new_material_id':new_id,'added':added,'removed':removed,'unchanged_count':sum(s in bset for s in a),'checked_at':_now(),'warning':'Точное сравнение фрагментов: потенциальные изменения, не внешняя проверка.','potentially_outdated_claims':claims}
        with self._connect() as con:
            con.execute('INSERT OR IGNORE INTO intelligence_refresh VALUES(?,?,?,?,?)',(key,old_id,new_id,canonical(result),result['checked_at']))
            return json.loads(con.execute('SELECT payload FROM intelligence_refresh WHERE id=?',(key,)).fetchone()[0])

    def list_refresh(self):
        with self._connect() as con:return [json.loads(r[0]) for r in con.execute('SELECT payload FROM intelligence_refresh ORDER BY checked_at DESC,id')]

    def refresh_report(self):
        comparisons=self.list_refresh();unique={c['id']:c for x in comparisons for c in x['potentially_outdated_claims']}
        with self._connect() as con:checks=[dict(r) for r in con.execute('SELECT * FROM intelligence_checks ORDER BY id DESC')]
        for row in checks:row['claim_ids']=json.loads(row['claim_ids'])
        return {'comparisons':comparisons,'potentially_outdated_claims':list(unique.values()),'checks':checks,'external_check_performed':False}

    def record_refresh(self, claim_ids, note):
        if not isinstance(claim_ids,list) or not 1<=len(claim_ids)<=50 or any(not isinstance(c,str) for c in claim_ids):raise ValueError('Выберите1..50 утверждений')
        for cid in claim_ids:self.get_claim(cid)
        note=_plain(note,'Результат проверки',20000);stamp=_now();ids=list(dict.fromkeys(claim_ids))
        with self._connect() as con:
            cur=con.execute('INSERT INTO intelligence_checks(claim_ids,note,checked_at) VALUES(?,?,?)',(canonical(ids),note,stamp))
            return {'id':cur.lastrowid,'claim_ids':ids,'note':note,'checked_at':stamp,'external_check_performed':False}

    def import_project(self,text,title):
        result=self.import_text(text,title,'project.md');self.set_tags(result['id'],list(dict.fromkeys(self.get_tags(result['id'])+['project'])));return result
