"""Full source-only suite; no package installation or real AI transport."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-missing-node',action='store_true');args=parser.parse_args()
    out=ROOT/'test_artifacts';out.mkdir(exist_ok=True)
    (out/'cloud_checks.json').write_text(json.dumps({'success':False,'phase':'preflight'},indent=2),encoding='utf-8')
    os.environ['XAGON_ALLOW_LIVE_API']='0'
    for key in ('GEMINI_API_KEY','GOOGLE_API_KEY','OPENAI_API_KEY'):os.environ.pop(key,None)
    from scripts.audit_source import audit
    security=audit()
    if not security['success']:print(json.dumps(security));return 1
    if sys.version_info<(3,11) or not hasattr(sqlite3.Connection,'deserialize'):raise SystemExit('Python3.11+ and SQLite deserialize required')
    node=shutil.which('node')
    if not node and not args.allow_missing_node:raise SystemExit('Full checks require an existing Node runtime. No install attempted; --allow-missing-node runs Python-only checks.')
    def reject(*args,**kwargs):raise AssertionError('External network forbidden in unit checks')
    with patch.object(socket.socket,'connect',reject),patch.object(socket.socket,'connect_ex',reject):
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py',top_level_dir=str(ROOT))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'success':False,'unit_tests':result.testsRun,'unit_skips':len(result.skipped),'platform':sys.platform,'javascript':'NOT TESTED','ai_calls':0,'source_audit':security['success']}
    (out/'cloud_checks.json').write_text(json.dumps(dict(report,phase='unit complete; HTTP pending'),indent=2),encoding='utf-8')
    if not result.wasSuccessful():return 1
    subprocess.run([sys.executable,'-B',str(ROOT/'scripts/acceptance_rc3.py')],cwd=ROOT,check=True,timeout=180)
    report['http_assertions']=25
    if node:
        for filename in ('app.js','intelligence.js','rc.js'):subprocess.run([node,'--check',str(ROOT/'xagon/alpha_static'/filename)],check=True,timeout=30)
        for filename in ('test_alpha_ui_dom.js','test_beta_drafts.js'):subprocess.run([node,str(ROOT/'tests'/filename)],check=True,timeout=30)
        report['javascript']='PASS: 3 syntax checks / 28 assertions'
    report['success']=True;out=ROOT/'test_artifacts';out.mkdir(exist_ok=True)
    (out/'cloud_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report));return 0

if __name__=='__main__':
    try:code=main()
    except Exception as exc:
        out=ROOT/'test_artifacts';out.mkdir(exist_ok=True)
        previous=out/'cloud_checks.json'
        report=json.loads(previous.read_text('utf-8')) if previous.is_file() else {}
        report.update(success=False,failure_type=type(exc).__name__,automatic_retry=False)
        previous.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report));code=1
    raise SystemExit(code)
