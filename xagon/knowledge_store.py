"""Append-only knowledge claims, evidence and user review ledger. No model/network calls."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from .beta_store import BetaStore
from .learning import _plain, _now, _markdown_text, _clock

STATUSES = ('unreviewed', 'user_confirmed', 'corrected', 'rejected', 'needs_check')
RELATIONS = ('related', 'complements', 'potential_conflict', 'alternative')
ACTIONS = {'confirm':'user_confirmed','correct':'corrected','reject':'rejected','needs_check':'needs_check'}

def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()

class KnowledgeStore(BetaStore):
    def __init__(self, db_path):
        p=Path(db_path).expanduser().resolve()
        self.db_path=p
        self._validate_target()
        if p.is_file():
            con=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_schema'").fetchone():
                    rows=con.execute('SELECT version FROM knowledge_schema').fetchall()
                    if rows != [(1,)]:raise ValueError('Несовместимая версия Knowledge; файл не изменён')
            finally:con.close()
        super().__init__(db_path)
        self._knowledge_migrate()

    def _validate_target(self):
        super()._validate_target()
        forbidden = [Path(r'C:\XAGON\ExaCortex_0.7_Learning_Beta'), Path(r'C:\XAGON\night-quality-2026-11-01'), Path(r'C:\XAGON_SAFETY_BACKUP_2026-10-31'), Path(r'C:\XAGON\ExaCortex_0.7_preserved_2026-11-01')]
        for root in forbidden:
            root=root.resolve()
            if self.db_path==root or self.db_path.is_relative_to(root):raise ValueError('Оригинальная версия/резервная копия доступны только для чтения')
        if any(re.fullmatch(r'XAGON_v\d.*',p.name,re.IGNORECASE) for p in self.db_path.parents):
            raise ValueError('Архивная версия доступна только для чтения')

    def _knowledge_migrate(self):
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS knowledge_schema(version INTEGER NOT NULL)')
            rows=con.execute('SELECT version FROM knowledge_schema').fetchall()
            if rows and (len(rows)!=1 or rows[0][0]!=1):raise ValueError('Несовместимая схема Knowledge')
            statements=[
                '''CREATE TABLE IF NOT EXISTS knowledge_claims(id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id), source_version TEXT NOT NULL, chunk_id INTEGER REFERENCES chunks(id), original_text TEXT NOT NULL, source_text TEXT, start_seconds REAL, end_seconds REAL, method TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(material_id,source_version,chunk_id,original_text,method))''',
                '''CREATE TABLE IF NOT EXISTS knowledge_revisions(id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES knowledge_claims(id), parent_id INTEGER REFERENCES knowledge_revisions(id), text TEXT NOT NULL, created_at TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS knowledge_reviews(id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES knowledge_claims(id), action TEXT NOT NULL, status TEXT NOT NULL, comment TEXT NOT NULL, revision_id INTEGER REFERENCES knowledge_revisions(id), created_at TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS knowledge_evidence(id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES knowledge_claims(id), material_id TEXT NOT NULL REFERENCES materials(id), source_version TEXT NOT NULL, chunk_id INTEGER NOT NULL REFERENCES chunks(id), text TEXT NOT NULL, text_sha256 TEXT NOT NULL, start_seconds REAL, end_seconds REAL, source_name TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(claim_id,material_id,chunk_id))''',
                '''CREATE TABLE IF NOT EXISTS knowledge_links(id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES knowledge_claims(id), other_id TEXT NOT NULL REFERENCES knowledge_claims(id), relation TEXT NOT NULL, method TEXT NOT NULL, hypothesis INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(claim_id,other_id,relation,method))'''
            ]
            statements.append('''CREATE TABLE IF NOT EXISTS knowledge_frames(id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES knowledge_claims(id), material_id TEXT NOT NULL REFERENCES materials(id), source_version TEXT NOT NULL, filename TEXT NOT NULL, asset_id TEXT NOT NULL, sha256 TEXT NOT NULL, time_seconds REAL, created_at TEXT NOT NULL, UNIQUE(claim_id,asset_id))''')
            for sql in statements:con.execute(sql)
            for table in ['materials','chunks','knowledge_claims','knowledge_revisions','knowledge_reviews','knowledge_evidence','knowledge_links','knowledge_frames']:
                for action in ['UPDATE','DELETE']:
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS protect_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Originals and knowledge history are append-only'); END")
            if not rows:con.execute('INSERT INTO knowledge_schema VALUES(1)')

    @staticmethod
    def _claim(con, claim_id):
        if not isinstance(claim_id,str):raise ValueError('Некорректный идентификатор утверждения')
        row=con.execute('SELECT * FROM knowledge_claims WHERE id=?',(claim_id,)).fetchone()
        if row is None:raise KeyError('Утверждение не найдено')
        return dict(row)

    def _evidence(self, con, claim_id, material_id, chunk_id):
        self._material(con,material_id);self._chunk_id(con,material_id,chunk_id)
        chunk=con.execute('SELECT c.*,m.content_sha256,m.source_name FROM chunks c JOIN materials m ON m.id=c.material_id WHERE c.id=?',(chunk_id,)).fetchone()
        eid='ev_'+digest(f'{claim_id}|{material_id}|{chunk_id}')[:28]
        con.execute('INSERT OR IGNORE INTO knowledge_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,claim_id,material_id,chunk['content_sha256'],chunk_id,chunk['text'],digest(chunk['text']),chunk['start_seconds'],chunk['end_seconds'],chunk['source_name'],_now()))
        return eid

    def create_claim(self, material_id, text, chunk_id=None, method='user'):
        text=_plain(text,'Утверждение',20000)
        if not isinstance(method,str) or method not in ('user','extractive_rule'):raise ValueError('Метод должен быть user или extractive_rule')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE');self._material(con,material_id)
            m=con.execute('SELECT * FROM materials WHERE id=?',(material_id,)).fetchone()
            chunk=None
            if chunk_id is not None:
                self._chunk_id(con,material_id,chunk_id)
                chunk=con.execute('SELECT * FROM chunks WHERE id=?',(chunk_id,)).fetchone()
            if method=='extractive_rule' and (chunk is None or text != chunk['text']):raise ValueError('Извлечённая цитата должна точно совпадать с фрагментом')
            cid='claim_'+digest(json.dumps([material_id,m['content_sha256'],chunk_id,text,method],ensure_ascii=False))[:28]
            con.execute('INSERT OR IGNORE INTO knowledge_claims VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,material_id,m['content_sha256'],chunk_id,text,chunk['text'] if chunk else None,chunk['start_seconds'] if chunk else None,chunk['end_seconds'] if chunk else None,method,_now()))
            if chunk: self._evidence(con,cid,material_id,chunk_id)
        return self.get_claim(cid)

    def extract_claims(self, material_id):
        m=self.get_material(material_id)
        return [self.create_claim(material_id,c['text'],c['id'],'extractive_rule') for c in m['chunks']]

    def add_evidence(self, claim_id, material_id, chunk_id):
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE');self._claim(con,claim_id)
            eid=self._evidence(con,claim_id,material_id,chunk_id)
        return next(e for e in self.get_claim(claim_id)['evidence'] if e['id']==eid)

    def get_claim(self, claim_id):
        with self._connect() as con:
            result=self._claim(con,claim_id)
            result['revisions']=[dict(r) for r in con.execute('SELECT * FROM knowledge_revisions WHERE claim_id=? ORDER BY id',(claim_id,))]
            result['reviews']=[dict(r) for r in con.execute('SELECT * FROM knowledge_reviews WHERE claim_id=? ORDER BY id',(claim_id,))]
            result['text']=result['revisions'][-1]['text'] if result['revisions'] else result['original_text']
            result['status']=result['reviews'][-1]['status'] if result['reviews'] else 'unreviewed'
            result['evidence']=[]
            for row in con.execute('SELECT * FROM knowledge_evidence WHERE claim_id=? ORDER BY created_at,id',(claim_id,)):
                e=dict(row)
                actual=con.execute('SELECT c.*,m.content_sha256,m.source_name FROM chunks c JOIN materials m ON m.id=c.material_id WHERE c.id=? AND c.material_id=?',(e['chunk_id'],e['material_id'])).fetchone()
                e['validity']='valid' if actual and actual['text']==e['text'] and digest(actual['text'])==e['text_sha256'] and actual['content_sha256']==e['source_version'] and actual['start_seconds']==e['start_seconds'] and actual['end_seconds']==e['end_seconds'] and actual['source_name']==e['source_name'] else ('missing' if actual is None else 'mismatch')
                e['kind']='transcript';e['verified_truth']=False
                result['evidence'].append(e)
            for row in con.execute('SELECT * FROM knowledge_frames WHERE claim_id=? ORDER BY created_at,id',(claim_id,)):
                e=dict(row)
                if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg)',e['asset_id']):raise ValueError('Повреждённый идентификатор кадра')
                p=self._asset_path(e['asset_id'])
                e['kind']='frame';e['verified_truth']=False
                material=con.execute('SELECT content_sha256 FROM materials WHERE id=?',(e['material_id'],)).fetchone()
                e['validity']='missing' if not p.is_file() or material is None else ('valid' if hashlib.sha256(self._read_asset(p)).hexdigest()==e['sha256'] and material[0]==e['source_version'] else 'mismatch')
                result['evidence'].append(e)
            result['evidence_status']='available' if result['evidence'] and all(e['validity']=='valid' for e in result['evidence']) else ('missing' if not result['evidence'] else 'invalid')
            result['links']=[dict(r) for r in con.execute('SELECT * FROM knowledge_links WHERE claim_id=? OR other_id=? ORDER BY id',(claim_id,claim_id))]
            result['verified_truth']=False
            return result

    def list_claims(self, query='', status='', material_id=None):
        if not isinstance(query,str) or len(query)>2000:raise ValueError('Некорректный поиск')
        if not isinstance(status,str) or status and status not in STATUSES:raise ValueError('Неизвестный статус')
        with self._connect() as con:
            if material_id is not None:self._material(con,material_id)
            ids=[r[0] for r in con.execute('SELECT id FROM knowledge_claims WHERE (? IS NULL OR material_id=?) ORDER BY created_at DESC,id',(material_id,material_id))]
        result=[self.get_claim(cid) for cid in ids]
        terms=query.casefold().split()
        return [c for c in result if (not status or c['status']==status) and all(t in (c['text']+' '+c['original_text']).casefold() for t in terms)]

    def review_claim(self, claim_id, action, comment='', text=None):
        if not isinstance(action,str) or action not in (*ACTIONS,'comment'):raise ValueError('Неизвестное решение')
        if not isinstance(comment,str) or '\x00' in comment or len(comment)>20000:raise ValueError('Некорректный комментарий')
        if action=='correct':text=_plain(text,'Новая формулировка',20000)
        elif text is not None:raise ValueError('Формулировка допускается только для исправления')
        if action=='comment' and not comment.strip():raise ValueError('Комментарий пуст')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE');self._claim(con,claim_id)
            previous=con.execute('SELECT status FROM knowledge_reviews WHERE claim_id=? ORDER BY id DESC LIMIT 1',(claim_id,)).fetchone()
            status=ACTIONS.get(action,previous[0] if previous else 'unreviewed')
            revision_id=None
            if action=='correct':
                last=con.execute('SELECT id FROM knowledge_revisions WHERE claim_id=? ORDER BY id DESC LIMIT 1',(claim_id,)).fetchone()
                cur=con.execute('INSERT INTO knowledge_revisions(claim_id,parent_id,text,created_at) VALUES(?,?,?,?)',(claim_id,last[0] if last else None,text,_now()))
                revision_id=cur.lastrowid
            con.execute('INSERT INTO knowledge_reviews(claim_id,action,status,comment,revision_id,created_at) VALUES(?,?,?,?,?,?)',(claim_id,action,status,comment,revision_id,_now()))
        return self.get_claim(claim_id)

    def link_claims(self, claim_id, other_id, relation, method='user'):
        if not isinstance(relation,str) or not isinstance(method,str) or relation not in RELATIONS or method not in ('user','lexical_rule'):raise ValueError('Некорректный тип связи или метод')
        if claim_id==other_id:raise ValueError('Связь с собой запрещена')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE');self._claim(con,claim_id);self._claim(con,other_id)
            con.execute('INSERT OR IGNORE INTO knowledge_links(claim_id,other_id,relation,method,hypothesis,created_at) VALUES(?,?,?,?,?,?)',(claim_id,other_id,relation,method,int(method!='user' or relation=='potential_conflict'),_now()))
            return dict(con.execute('SELECT * FROM knowledge_links WHERE claim_id=? AND other_id=? AND relation=? AND method=?',(claim_id,other_id,relation,method)).fetchone())

    def suggest_links(self, claim_id):
        current=self.get_claim(claim_id)
        words=lambda text:set(w for w in re.findall(r'[^\W_]+',text.casefold()) if len(w)>=4)
        a=words(current['text']);result=[]
        for other in self.list_claims():
            if other['id']==claim_id:continue
            common=a&words(other['text'])
            if len(common)>=2:result.append({'claim_id':claim_id,'other_id':other['id'],'relation':'related','method':'lexical_rule','hypothesis':True,'matched_terms':sorted(common)})
        return result[:20]

    def export_knowledge(self, status=''):
        lines=['# Мои знания','', '> Статусы отражают решения пользователя. Независимая проверка истинности не проводилась. Автоизвлечение — точные фрагменты, не установленные факты.','']
        for c in self.list_claims(status=status):
            lines += [f"## {c['id']}",f"Статус: {c['status']}",f"Метод: {c['method']}",f"Источник: {c['material_id']} · версия {c['source_version']}",f"Создано: {c['created_at']}",'','### Исходная формулировка',_markdown_text(c['original_text']),'','### Текущая пользовательская редакция',_markdown_text(c['text']),'',f"Evidence: {c['evidence_status']}"]
            for frame in [e for e in c['evidence'] if e['kind']=='frame']:
                lines.append(f"- Frame {frame['id']} · {frame['validity']} · asset {frame['asset_id']} · {_clock(frame['time_seconds'])} · SHA256 {frame['sha256']}")
            for e in [e for e in c['evidence'] if e['kind']=='transcript']:lines += [f"- {e['id']} · {e['validity']} · {e['material_id']}#chunk-{e['chunk_id']} · {_clock(e['start_seconds'])} · SHA256 {e['text_sha256']}",'> '+_markdown_text(e['text']).replace('\n','\n> ')]
            lines += ['','### История редакций']
            for rev in c['revisions']:lines.append(f"- Редакция {rev['id']}, parent {rev['parent_id']}, {rev['created_at']}: {_markdown_text(rev['text'])}")
            lines += ['','### Решения пользователя']
            for review in c['reviews']:lines.append(f"- {review['created_at']} · {review['action']} · {review['status']}: {_markdown_text(review['comment'])}")
            lines += ['','### Связи']
            for link in c['links']:lines.append(f"- {link['claim_id']} → {link['other_id']} · {link['relation']} · hypothesis={bool(link['hypothesis'])}")
            lines.append('')
        return '\n'.join(lines)

    def add_frame_evidence(self, claim_id, material_id, filename, content_base64, time_seconds=None):
        from .learning import _time
        filename=_plain(filename,'Имя кадра',180)
        if '/' in filename or '\\' in filename or ':' in filename or Path(filename).suffix.lower() not in ('.png','.jpg','.jpeg'):
            raise ValueError('Допускаются PNG/JPEG без пути')
        if not isinstance(content_base64,str) or len(content_base64)>12*1024*1024:raise ValueError('Кадр превышает лимит')
        try:raw=base64.b64decode(content_base64,validate=True)
        except Exception as exc:raise ValueError('Некорректные данные кадра') from exc
        if len(raw)>8*1024*1024 or not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff')):raise ValueError('Ожидается JPEG или PNG до 8МиБ')
        stamp=_time(time_seconds);sha=hashlib.sha256(raw).hexdigest()
        suffix='.png' if raw.startswith(b'\x89PNG') else '.jpg';asset=sha+suffix
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE');self._claim(con,claim_id);self._material(con,material_id)
            version=con.execute('SELECT content_sha256 FROM materials WHERE id=?',(material_id,)).fetchone()[0]
            target=self._asset_path(asset)
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                if hashlib.sha256(self._read_asset(target)).hexdigest()!=sha:raise ValueError('Сохранённый кадр повреждён; перезапись запрещена')
            else:
                with target.open('xb') as stream:stream.write(raw)
            eid='frame_'+digest(claim_id+'|'+asset)[:28]
            con.execute('INSERT OR IGNORE INTO knowledge_frames VALUES(?,?,?,?,?,?,?,?,?)',(eid,claim_id,material_id,version,filename,asset,sha,stamp,_now()))
        return next(e for e in self.get_claim(claim_id)['evidence'] if e['id']==eid)

    def read_frame(self, asset_id):
        if not isinstance(asset_id,str) or not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg)',asset_id):raise ValueError('Некорректный asset')
        with self._connect() as con:
            row=con.execute('SELECT sha256 FROM knowledge_frames WHERE asset_id=?',(asset_id,)).fetchone()
        if row is None:raise KeyError('Кадр не зарегистрирован')
        path=self._asset_path(asset_id)
        if not path.is_file():raise ValueError('Кадр отсутствует')
        raw=self._read_asset(path)
        if hashlib.sha256(raw).hexdigest()!=row[0]:raise ValueError('Контрольная сумма кадра не совпадает')
        return raw

    def _asset_path(self, asset_id):
        if not isinstance(asset_id,str) or not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg)',asset_id):raise ValueError('Некорректный идентификатор кадра')
        folder=self.db_path.parent/'knowledge_assets'
        if folder.is_symlink() or (folder.exists() and folder.resolve().parent != self.db_path.parent.resolve()):raise ValueError('Перенаправленный каталог кадров запрещён')
        path=folder/asset_id
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):raise ValueError('Перенаправленный кадр запрещён')
        return path

    @staticmethod
    def _read_asset(path):
        with path.open('rb') as stream:raw=stream.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('Сохранённый кадр превышает лимит')
        return raw
