"""Read-only diagnostics, without API keys, paths, questions or private text."""
import json
from pathlib import Path
import platform
import sqlite3
from .intelligence_chat import BudgetGuard
from .rc_readiness import saved_readiness
from .cloud_policy import require_live_api


def diagnose(store=None,root=None):
    root=Path(root) if root else Path(__file__).resolve().parents[1]
    try:BudgetGuard.check('cloud');cloud_blocked=False
    except PermissionError:cloud_blocked=True
    try:require_live_api();legacy_blocked=False
    except PermissionError:legacy_blocked=True
    result={'release':'1.0.0-rc3','final_release':False,'python':platform.python_version(),
        'sqlite':sqlite3.sqlite_version,'cloud_blocked':cloud_blocked and legacy_blocked,'legacy_cloud_blocked':legacy_blocked,
        'capabilities':{'offline':['imports','FTS retrieval','claims/evidence/reviews','learning','local Refresh','backup/restore'],
            'manual_ai':['Fusion export/import; user decides what to transmit'],
            'disabled':['real cloud LLM','new transcription/Vision','automatic Internet verification']},
        'saved_xagon':saved_readiness(root),'database':{'opened':False},'warnings':[]}
    result['saved_bundle_available']=(root/'XAGON_Saved_Results.json').is_file()
    if store is not None and hasattr(store,'saved_xagon_status'):
        registered=store.saved_xagon_status()
        result['registered_xagon']={k:v for k,v in registered.items() if k!='segments'}
    if store is None:
        result['warnings'].append('Учебная БД не открыта; диагностика её не создаёт')
        return result
    with store._connect() as con:
        integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=len(con.execute('PRAGMA foreign_key_check').fetchall())
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions={}
        for table in ('beta','knowledge_schema','fusion_schema','intelligence_schema','chat_schema','intelligence_chat_schema','rc_schema','rc3_schema'):
            if table=='beta':versions['user_version']=con.execute('PRAGMA user_version').fetchone()[0]
            elif table in tables:versions[table]=[r[0] for r in con.execute('SELECT version FROM '+table)]
        counts={name:con.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in (
            'materials','chunks','knowledge_claims','knowledge_reviews','fusion_packages','fusion_syntheses','intelligence_tasks','rc_synthesis_tasks') if name in tables}
        frames=con.execute('SELECT asset_id FROM knowledge_frames').fetchall() if 'knowledge_frames' in tables else []
    invalid=0
    for row in frames[:500]:
        try:store.read_frame(row[0])
        except (ValueError,KeyError,OSError):invalid+=1
    required={'materials','chunks','knowledge_claims','knowledge_reviews','fusion_packages','fusion_syntheses','intelligence_tasks','rc_synthesis_tasks'}
    compatible=required.issubset(tables) and versions.get('rc_schema')==[1] and versions.get('user_version')==1 and all(v==[1] for k,v in versions.items() if k!='user_version')
    result['database']={'opened':True,'integrity':integrity,'foreign_key_errors':foreign,'versions':versions,'schema_compatible':compatible,
        'counts':counts,'fts_available':store.fts_available,'frames_checked':min(len(frames),500),
        'frame_errors':invalid,'frame_scan_complete':len(frames)<=500}
    if integrity!='ok' or foreign or invalid:result['warnings'].append('Нужна проверка целостности библиотеки/кадров')
    if not compatible:result['warnings'].append('Схема не соответствует RC; диагностика миграции не выполняет')
    if len(frames)>500:result['warnings'].append('Проверены первые500 frames; scan неполный')
    if not store.fts_available:result['warnings'].append('FTS5 недоступен; используется медленный fallback')
    if result['saved_xagon']['status']!='complete':result['warnings'].append('Сохранённый вебинар XAGON готов частично или отчёты недоступны')
    result['healthy']=integrity=='ok' and not foreign and not invalid and compatible and len(frames)<=500 and result['cloud_blocked']
    return result
