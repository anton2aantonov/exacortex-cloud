"""RC3 additive registration, atomic saved-XAGON integration and manual exchange."""
import hashlib
import json
from pathlib import Path
import sqlite3
from .rc_store import RCStore
from .fusion_store import canonical, fingerprint
from .learning import _transcript_chunks, _time, _now, _plain
from .manual_exchange import parse_manual_response


class ProductStore(RCStore):
    def __init__(self,db_path):
        self.db_path=Path(db_path).resolve();self._validate_target()
        if self.db_path.is_file():
            con=sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True)
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='rc3_schema'").fetchone() and con.execute('SELECT version FROM rc3_schema').fetchall()!=[(1,)]:raise ValueError('Future RC3 schema; unchanged')
            finally:con.close()
        super().__init__(db_path)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS rc3_schema(version INTEGER NOT NULL)')
            if not con.execute('SELECT 1 FROM rc3_schema').fetchone():con.execute('INSERT INTO rc3_schema VALUES(1)')
            con.execute('CREATE TABLE IF NOT EXISTS rc3_saved_xagon(id TEXT PRIMARY KEY,material_id TEXT NOT NULL REFERENCES materials(id),vision_material_id TEXT REFERENCES materials(id),payload TEXT NOT NULL,created_at TEXT NOT NULL)')
            for action in ('UPDATE','DELETE'):
                con.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_rc3_saved_{action} BEFORE {action} ON rc3_saved_xagon BEGIN SELECT RAISE(ABORT,'Saved provenance append-only'); END")

    def import_saved_xagon(self,bundle):
        if not isinstance(bundle,dict) or bundle.get('schema_version')!=1 or bundle.get('new_api_requests')!=0:
            raise ValueError('Invalid saved-XAGON bundle')
        if len(canonical(bundle).encode('utf8'))>16*1024*1024:raise ValueError('Bundle exceeds 16 MiB')
        source=_plain(bundle.get('source_id'),'Source ID',100)
        merged=bundle.get('merged');segments=bundle.get('segments')
        if not isinstance(merged,dict) or merged.get('source_id')!=source or not isinstance(segments,list) or not 1<=len(segments)<=100:raise ValueError('Invalid source/segments')
        if any(not isinstance(s,dict) for s in segments):raise ValueError('Invalid segment')
        if len({s.get('segment_id') for s in segments})!=len(segments):raise ValueError('Duplicate segments')
        if fingerprint(merged)!=bundle.get('merged_content_sha256'):raise ValueError('Merged snapshot checksum mismatch')
        if sorted(s.get('segment_id','') for s in segments)!=sorted(merged.get('segment_ids',[])):raise ValueError('Segment list mismatch')
        if sorted(s.get('transcript_sha256','') for s in segments)!=sorted(merged.get('source_transcript_sha256',[])):raise ValueError('Transcript hashes mismatch')
        transcript_chunks=_transcript_chunks(merged);vision_chunks=[];metadata=[]
        for segment in segments:
            sid=_plain(segment.get('segment_id'),'Segment',100)
            if len(segment.get('transcript_sha256',''))!=64:raise ValueError('Invalid transcript hash')
            vision=segment.get('vision')
            row={k:v for k,v in segment.items() if k!='vision'}
            if vision is not None:
                if fingerprint(vision)!=segment.get('vision_content_sha256'):raise ValueError('Vision snapshot checksum mismatch')
                if not isinstance(vision,dict) or vision.get('source_id')!=source or vision.get('segment_id')!=sid or not isinstance(vision.get('observations'),list):raise ValueError('Invalid Vision identity')
                if len(vision['observations'])>100:raise ValueError('Too many Vision observations')
                row['observations']=vision['observations'];row['vision_status']='saved_unverified'
                row['vision_metadata']={k:v for k,v in vision.items() if k!='observations'}
                for observation in vision['observations']:
                    text=_plain(observation.get('screen_summary'),'Vision text',20000)
                    visible=observation.get('visible_text',[])
                    if not isinstance(visible,list) or any(not isinstance(v,str) for v in visible):raise ValueError('Invalid visible text')
                    text+='\nВидимый текст: '+'; '.join(visible)
                    text+='\n[Сохранённое AI-наблюдение; человеком не подтверждено.]'
                    stamp=_time(observation.get('source_seconds_estimated'))
                    vision_chunks.append({'text':_plain(text,'Vision observation',20000),'start_seconds':stamp,'end_seconds':stamp,'segment_ids':[sid]})
            else:row['vision_status']='missing'
            metadata.append(row)
        payload={'source_id':source,'segments':metadata,'merged_sha256':bundle.get('merged_sha256'),
                 'transcriptions':len(metadata),'vision_done':sum(s['vision_status']!='missing' for s in metadata),
                 'remaining_vision':[s['segment_id'] for s in metadata if s['vision_status']=='missing'],
                 'verification':'imported file snapshot; not independent truth','frames_copied':False}
        key='saved_'+fingerprint(payload)[:28]
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            mid=self._save_in_connection(con,transcript_chunks,'Вебинар: агентная аналитика','XAGON_Merged_Transcript.json','transcript',source)
            vid=self._save_in_connection(con,vision_chunks,'XAGON Vision — сохранённые наблюдения','saved_vision.json','transcript',source) if vision_chunks else None
            con.execute('INSERT OR IGNORE INTO rc3_saved_xagon VALUES(?,?,?,?,?)',(key,mid,vid,canonical(payload),_now()))
        return self.saved_xagon_status(key)

    def import_manual_response(self,package_id,text,*,mock=False):
        if type(mock) is not bool:raise ValueError('Mock flag must be boolean')
        payload=parse_manual_response(text)
        payload['origin']='mock' if mock else payload.get('origin','manual_ai')
        payload['original_response']=text
        payload.setdefault('model','User-supplied manual response; model not verified')
        return self.import_synthesis(package_id,payload)

    def saved_xagon_status(self,key=None):
        with self._connect() as con:
            row=con.execute('SELECT * FROM rc3_saved_xagon WHERE (? IS NULL OR id=?) ORDER BY created_at DESC,id DESC LIMIT 1',(key,key)).fetchone()
        if row is None:return {'registered':False}
        return dict(json.loads(row['payload']),registered=True,id=row['id'],material_id=row['material_id'],vision_material_id=row['vision_material_id'])
