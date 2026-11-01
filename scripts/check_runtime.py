"""No installation/network: verify the runtime required by portable checks."""
import json
import platform
import shutil
import sqlite3
import sys


def main():
    con=sqlite3.connect(':memory:')
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('CREATE TABLE sample(text TEXT)');con.execute('INSERT INTO sample VALUES(?)',('Русский / English',))
        assert con.execute('SELECT text FROM sample').fetchone()[0]=='Русский / English'
        serial=hasattr(con,'serialize') and hasattr(con,'deserialize') and hasattr(con,'setlimit')
        try:con.execute('CREATE VIRTUAL TABLE runtime_fts USING fts5(text)');fts=True
        except sqlite3.OperationalError:fts=False
    finally:con.close()
    result={'python':platform.python_version(),'platform':platform.system(),'sqlite':sqlite3.sqlite_version,
            'python_311_plus':sys.version_info>=(3,11),'sqlite_snapshot_support':serial,
            'sqlite_utf8':True,'fts5':fts,'node_available':bool(shutil.which('node')),'network_used':False}
    result['core_ready']=result['python_311_plus'] and serial
    print(json.dumps(result,indent=2));return 0 if result['core_ready'] else 1


if __name__=='__main__':raise SystemExit(main())
