"""Local CLI diagnostics and consistent library snapshots. No network operations."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
from .rc_store import RCStore
from .rc_backup import create_backup,verify_backup,restore_backup
from .rc_diagnostics import diagnose


def main(argv=None):
    os.environ['XAGON_ALLOW_LIVE_API']='0'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['diagnose','backup','verify','restore'])
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1]
    data=args.data_dir or Path(os.environ.get('EXACORTEX_LEARNING_DIR','').strip() or root/'rc_data')
    try:
        if args.command=='verify':
            if args.backup is None:raise ValueError('Укажите --backup')
            result=verify_backup(args.backup)
        elif args.command=='restore':
            if args.backup is None or args.destination is None:raise ValueError('Укажите --backup и НОВЫЙ --destination')
            result=restore_backup(args.backup,args.destination)
        elif args.command=='diagnose':
            # No initialization/migration on diagnostic read. Existing RCStore
            # object is allocated without invoking its mutating constructor.
            store=None
            if (data/'learning.sqlite3').is_file():
                store=RCStore.__new__(RCStore);store.db_path=(data/'learning.sqlite3').resolve();store._validate_target()
                con=sqlite3.connect(store.db_path.as_uri()+'?mode=ro',uri=True)
                try:
                    from .learning import APP_ID
                    if con.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('Это не учебная БД ExaCortex')
                    store.fts_available=bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='learning_fts'").fetchone())
                finally:con.close()
            result=diagnose(store,root)
        else:
            # Back up an existing library; never create an empty one silently.
            if not (data/'learning.sqlite3').is_file():raise ValueError('Учебная БД не найдена')
            store=RCStore.__new__(RCStore);store.db_path=(data/'learning.sqlite3').resolve();store._validate_target()
            destination=args.destination or data/'backups'/('backup_'+secrets.token_hex(8))
            result=create_backup(store,destination)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        if args.command=='diagnose' and result.get('healthy') is False:return 1
        return 0
    except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
        # Exception payloads may include paths or SQL; keep CLI failures generic.
        print(json.dumps({'success':False,'error_type':type(exc).__name__,'error':'Действие не завершено. Проверьте параметры, целостность и доступность новой папки; исходники не заменялись.'}))
        return 1


if __name__=='__main__':raise SystemExit(main())
