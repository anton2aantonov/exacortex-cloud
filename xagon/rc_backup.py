"""Portable SQLite + frame snapshots. Existing destinations are never replaced."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from .learning import APP_ID, _now

MAX_DB=256*1024*1024
MAX_ASSET=8*1024*1024
MAX_FILES=5000


def _hash(path,limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:
        raise ValueError('Недопустимый файл snapshot')
    h=hashlib.sha256();size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            size+=len(block)
            if size>limit:raise ValueError('Файл превышает ограничение')
            h.update(block)
    return h.hexdigest()


def _safe_asset(name):
    if not isinstance(name,str) or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg)',name):
        raise ValueError('Недопустимое имя asset')
    return name


def _validate_destination(destination):
    from .rc_store import RCStore
    # A lightweight guard call performs no initialization/writes.
    guard=RCStore.__new__(RCStore);guard.db_path=destination/'learning.sqlite3';guard._validate_target()
    if destination.exists():raise ValueError('Назначение уже существует; перезапись запрещена')


def _db_info(path):
    if path.stat().st_size>MAX_DB:raise ValueError('БД превышает256МиБ snapshot limit')
    con=sqlite3.connect(':memory:')
    try:
        con.deserialize(path.read_bytes());con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,16*1024*1024)
        con.execute('PRAGMA query_only=ON');con.execute('PRAGMA temp_store=MEMORY')
        if con.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('Snapshot не является учебной БД')
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Snapshot SQLite повреждён')
        exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_frames'").fetchone()
        assets={}
        if exists:
            for name,sha in con.execute('SELECT asset_id,sha256 FROM knowledge_frames LIMIT ?', (MAX_FILES+1,)):
                _safe_asset(name)
                if name in assets and assets[name]!=sha:raise ValueError('Конфликт хешей frame')
                assets[name]=sha
            if len(assets)>MAX_FILES:raise ValueError('Слишком много frames')
        return assets
    finally:con.close()


def create_backup(store,destination):
    """Caller serializes writers (HTTP store_lock). Partial failed dirs have no manifest."""
    destination=Path(destination).expanduser().resolve();_validate_destination(destination)
    if destination==store.db_path.parent or store.db_path.parent.is_relative_to(destination):
        raise ValueError('Snapshot не должен содержать исходную папку')
    destination.mkdir(parents=True,exist_ok=False)
    snapshot=destination/'learning.sqlite3'
    src=sqlite3.connect(store.db_path.as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(snapshot)
    try:
        src.backup(dst)
        # Normalize only the new snapshot, never the live source. A portable
        # snapshot has no WAL dependency and can be inspected in memory.
        dst.execute('PRAGMA journal_mode=DELETE')
    finally:src.close();dst.close()
    assets=_db_info(snapshot)
    if len(assets)>MAX_FILES:raise ValueError('Слишком много кадров')
    files=[{'path':'learning.sqlite3','sha256':_hash(snapshot,MAX_DB),'bytes':snapshot.stat().st_size}]
    asset_root=store.db_path.parent/'knowledge_assets'
    if asset_root.is_symlink():raise ValueError('Перенаправленный каталог кадров запрещён')
    if assets:(destination/'knowledge_assets').mkdir()
    for name,expected in assets.items():
        source=asset_root/name
        if _hash(source,MAX_ASSET)!=expected:raise ValueError('Кадр отсутствует или повреждён; snapshot не завершён')
        target=destination/'knowledge_assets'/name
        shutil.copyfile(source,target)
        if _hash(target,MAX_ASSET)!=expected:raise ValueError('Ошибка копирования кадра')
        files.append({'path':'knowledge_assets/'+name,'sha256':expected,'bytes':target.stat().st_size})
    # Commit marker last. Never publish a success manifest before verification.
    manifest={'format':'exacortex-backup-v1','created_at':_now(),'files':files}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    return verify_backup(destination)


def verify_backup(backup):
    backup=Path(backup).expanduser().resolve()
    manifest_path=backup/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size>2*1024*1024:
        raise ValueError('Нет завершённого manifest snapshot')
    try:manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    except (ValueError,RecursionError) as exc:raise ValueError('Повреждённый manifest snapshot') from exc
    if not isinstance(manifest,dict) or manifest.get('format')!='exacortex-backup-v1':raise ValueError('Неизвестный формат snapshot')
    files=manifest.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES+1:raise ValueError('Некорректный manifest')
    paths=set()
    for row in files:
        if not isinstance(row,dict):raise ValueError('Некорректная запись manifest')
        name=row.get('path')
        if name=='learning.sqlite3':limit=MAX_DB
        elif isinstance(name,str) and name.startswith('knowledge_assets/'):
            _safe_asset(name[len('knowledge_assets/'):]);limit=MAX_ASSET
        else:raise ValueError('Небезопасный путь snapshot')
        if name in paths:raise ValueError('Повторяющийся путь manifest')
        paths.add(name);target=backup/name
        if target.parent.is_symlink() or not target.resolve().is_relative_to(backup):raise ValueError('Snapshot перенаправлен')
        if _hash(target,limit)!=row.get('sha256') or type(row.get('bytes')) is not int or target.stat().st_size!=row['bytes']:
            raise ValueError('Контрольная сумма snapshot не совпадает')
    entries=list(backup.rglob('*'))
    for p in entries:
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) or not p.resolve().is_relative_to(backup):
            raise ValueError('Перенаправленные записи snapshot запрещены')
        if p.is_dir() and p.relative_to(backup).as_posix()!='knowledge_assets':raise ValueError('Неизвестный каталог snapshot')
    actual={p.relative_to(backup).as_posix() for p in entries if p.is_file()}
    if actual!=paths|{'manifest.json'}:raise ValueError('Состав snapshot не совпадает с manifest')
    if 'learning.sqlite3' not in paths:raise ValueError('Snapshot не содержит БД')
    assets=_db_info(backup/'learning.sqlite3')
    if paths!={'learning.sqlite3'}|{'knowledge_assets/'+n for n in assets}:raise ValueError('Manifest не совпадает с frames БД')
    for name,sha in assets.items():
        if _hash(backup/'knowledge_assets'/name,MAX_ASSET)!=sha:raise ValueError('Хеш frame не совпадает с БД')
    return {'verified':True,'files':len(paths),'frames':len(assets),'integrity':'ok','created_at':manifest.get('created_at')}


def restore_backup(backup,destination):
    backup=Path(backup).expanduser().resolve();destination=Path(destination).expanduser().resolve()
    if destination.is_relative_to(backup) or backup.is_relative_to(destination):raise ValueError('Папки restore и backup должны быть независимы')
    result=verify_backup(backup);_validate_destination(destination)
    # copytree refuses existing target, never overlays a library.
    shutil.copytree(backup,destination)
    restored=verify_backup(destination)
    return dict(restored,restored=True)
