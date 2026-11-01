"""Release-candidate integration. Adds lineage, never promotes AI to source truth."""
import json
import os
from pathlib import Path
import sqlite3
from .intelligence_store import IntelligenceStore
from .fusion_store import canonical, fingerprint
from .learning import _now


class RCStore(IntelligenceStore):
    def __init__(self, db_path):
        self.db_path=Path(db_path).expanduser().resolve(); self._validate_target()
        if self.db_path.is_file():
            con=sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True)
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='rc_schema'").fetchone() and con.execute('SELECT version FROM rc_schema').fetchall()!=[(1,)]:
                    raise ValueError('Будущая схема RC не поддерживается; файл не изменён')
            finally:con.close()
        super().__init__(db_path)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS rc_schema(version INTEGER NOT NULL)')
            if not con.execute('SELECT 1 FROM rc_schema').fetchone():con.execute('INSERT INTO rc_schema VALUES(1)')
            con.execute('''CREATE TABLE IF NOT EXISTS rc_synthesis_tasks(
                id TEXT PRIMARY KEY,synthesis_id TEXT NOT NULL REFERENCES fusion_syntheses(id),item_id TEXT NOT NULL,
                text TEXT NOT NULL,citation_ids TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(synthesis_id,item_id))''')
            con.execute('''CREATE TABLE IF NOT EXISTS rc_task_events(id INTEGER PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES rc_synthesis_tasks(id),completed INTEGER NOT NULL CHECK(completed IN(0,1)),created_at TEXT NOT NULL)''')
            for table in ('rc_synthesis_tasks','rc_task_events'):
                for action in ('UPDATE','DELETE'):
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS protect_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'RC history is append-only'); END")

    def _validate_target(self):
        super()._validate_target()
        for name in ('ExaCortex_0.9_Intelligence','ExaCortex_1.0_RC','ExaCortex_1.0_RC2','ExaCortex_1.0_RC3') if os.name=='nt' else ():
            root=Path(r'C:\XAGON',name).resolve()
            if self.db_path==root or self.db_path.is_relative_to(root):raise ValueError('Исходный релиз доступен только для чтения')

    def list_library(self):
        """Three bulk reads instead of opening two connections per material."""
        with self._connect() as con:
            rows=[dict(r) for r in con.execute('''SELECT m.*,COUNT(c.id) AS chunk_count,
                COALESCE(p.status,'new') AS progress FROM materials m LEFT JOIN chunks c ON c.material_id=m.id
                LEFT JOIN beta_progress p ON p.material_id=m.id GROUP BY m.id ORDER BY m.created_at DESC,m.id''')]
            tags={}
            for row in con.execute('SELECT material_id,tag FROM beta_tags ORDER BY tag'):
                tags.setdefault(row[0],[]).append(row[1])
        for row in rows:row['tags']=tags.get(row['id'],[])
        return rows

    def create_synthesis_task(self,synthesis_id,item_id):
        result=self.get_synthesis(synthesis_id)
        item=next((x for x in result['items'] if x['id']==item_id),None)
        if item is None:raise ValueError('Вывод не найден')
        if item['status']!='user_accepted' or item['citation_status']!='references_valid':
            raise ValueError('Для задачи нужны личное принятие и валидные ссылки. Это не подтверждение истинности.')
        key='synthesis_task_'+fingerprint([synthesis_id,item_id])[:28]
        text='Практически проверьте принятый вами AI-вывод, сохраните результат и ограничения.\n'+item['text']
        with self._connect() as con:
            con.execute('INSERT OR IGNORE INTO rc_synthesis_tasks VALUES(?,?,?,?,?,?)',(key,synthesis_id,item_id,text,canonical(item['citation_ids']),_now()))
        return next(t for t in self.list_learning_tasks() if t['id']==key)

    def list_learning_tasks(self):
        tasks=super().list_learning_tasks()
        for task in tasks:
            task['origin']='source_claim_user_confirmed'
            task['source_status']=self.get_claim(task['claim_id'])['status']
            task['review_warning']=task['source_status']!='user_confirmed'
        with self._connect() as con:
            rows=[dict(r) for r in con.execute('SELECT * FROM rc_synthesis_tasks ORDER BY created_at,id')]
            events={}
            for row in con.execute('SELECT * FROM rc_task_events ORDER BY id'):events.setdefault(row['task_id'],[]).append(dict(row))
        for task in rows:
            syn=self.get_synthesis(task['synthesis_id'])
            item=next(x for x in syn['items'] if x['id']==task['item_id'])
            history=events.get(task['id'],[])
            task.update(citation_ids=json.loads(task['citation_ids']),origin='ai_synthesis_user_accepted',
                source_status=item['status'],review_warning=item['status']!='user_accepted',events=history,
                status='completed' if history and history[-1]['completed'] else 'pending',verified_mastery=False,verified_truth=False)
            tasks.append(task)
        return tasks

    def set_learning_task(self,task_id,completed):
        if not isinstance(task_id,str) or not task_id or len(task_id)>200:raise ValueError('Некорректный ID задачи')
        if type(completed) is not bool:raise ValueError('completed должен быть true/false')
        with self._connect() as con:found=con.execute('SELECT 1 FROM rc_synthesis_tasks WHERE id=?',(task_id,)).fetchone()
        if not found:return super().set_learning_task(task_id,completed)
        with self._connect() as con:con.execute('INSERT INTO rc_task_events(task_id,completed,created_at) VALUES(?,?,?)',(task_id,int(completed),_now()))
        return next(t for t in self.list_learning_tasks() if t['id']==task_id)
