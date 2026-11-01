"""A-G real loopback acceptance, identity transport, restart and restore."""
import contextlib
import hashlib
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


@contextlib.contextmanager
def running(db):
    from xagon.alpha import create_server
    server=create_server(port=0,db_path=db);worker=threading.Thread(target=server.serve_forever);worker.start()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def req(path,payload=None):
        headers={'X-ExaCortex-Token':server.token,'Accept-Encoding':'identity'}
        if payload is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers=headers)
        try:response=opener.open(request,timeout=15)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            raw=response.read();assert len(raw)==int(response.headers['Content-Length'])
            assert response.headers.get('Content-Encoding') is None
            return response.status,json.loads(raw.decode())
    try:yield req,server
    finally:server.shutdown();server.server_close();worker.join(5);assert not worker.is_alive()


def main():
    from xagon.rc_backup import restore_backup
    from xagon.saved_xagon import snapshot,read_json
    from tests.rc3_fixtures import synthetic_cache
    checks=[]
    def check(ok,label):
        if not ok:raise AssertionError(label)
        checks.append(label)
    connect=socket.socket.connect
    def local(sock,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):raise AssertionError('External network forbidden')
        return connect(sock,address)
    with tempfile.TemporaryDirectory(prefix='rc3_acceptance_') as temp,patch.object(socket.socket,'connect',local):
        root=Path(temp);db=root/'library/learning.sqlite3'
        source=ROOT/'XAGON_Saved_Results.json'
        bundle=read_json(source)[0] if source.is_file() else snapshot(root/'synthetic',synthetic_cache(root/'synthetic'))
        with running(db) as (req,server):
            check(req('/api/materials')==(200,[]),'A: clean first launch')
            code,material=req('/api/import',{'name':'demo.md','content':'MCP requires evidence. Use original sources.'})
            check(code==200 and req('/api/intelligence-search?q=MCP')[1],'A: document imports and searches')
            code,claim=req('/api/claim',{'material_id':material['id'],'text':material['chunks'][0]['text'],'chunk_id':material['chunks'][0]['id']})
            check(code==200 and claim['evidence'][0]['validity']=='valid','A: claim links real Evidence')
            # The bundle is a local copy, never the production SQLite database.
            registered=server.store.import_saved_xagon(bundle)
            check((registered['transcriptions'],registered['vision_done'],len(registered['remaining_vision']))==(18,16,2),'B: physical snapshot binds18 transcripts and16 Vision with2 missing')
            check(server.store.import_saved_xagon(bundle)['id']==registered['id'],'B: registration deduplicates')
            transcript=server.store.get_material(registered['material_id'])
            vision=server.store.get_material(registered['vision_material_id'])
            check(len(transcript['chunks'])>0 and len(vision['chunks'])>0 and vision['chunks'][0]['start_seconds'] is not None,'B: transcript and Vision Evidence indexed with saved times')
            mids=[material['id'],registered['material_id'],registered['vision_material_id']]
            code,pkg=req('/api/context-package',{'topic':'MCP','task':'Compare source provenance','material_ids':mids})
            check(code==200 and len(pkg['sources'])==3,'A/B: Fusion spans new document, transcript and saved Vision')
            exported=req('/api/fusion-export?id='+pkg['id'])[1]
            export_path=root/'isolated_exchange.json';export_path.write_text(json.dumps(exported,ensure_ascii=False),'utf-8')
            check(exported['privacy_notice'] and json.loads(export_path.read_text('utf-8'))==exported,'C: structured exchange file roundtrip in isolated folder')
            ref=pkg['citations'][0]['citation_id'];raw=f'Fixture-only manual synthesis [{ref}].\n\nUnsupported fixture paragraph.'
            check(req('/api/manual-response',{'package_id':pkg['id'],'text':'Fake [cite_missing]','mock':True})[0]==400,'C: unknown citation rejected')
            code,syn=req('/api/manual-response',{'package_id':pkg['id'],'text':raw,'mock':True})
            check(code==200 and syn['original_response']==raw and syn['origin']=='mock' and syn['items'][1]['citation_status']=='unsupported','C: raw response retained, mock labelled, missing refs unsupported')
            req('/api/fusion-review',{'synthesis_id':syn['id'],'item_id':'item_1','action':'accept','comment':'Personal fixture decision'})
            code,task=req('/api/synthesis-task',{'synthesis_id':syn['id'],'item_id':'item_1'})
            check(code==200 and not task['verified_truth'],'A/C: accepted result creates practice task, never truth')
            req('/api/learning-task-state',{'task_id':task['id'],'completed':True})
            # Original failing path: large full POST, no gzip, no compact reply.
            text=('MCP '+('0123456789abcdef '*55)+'\n\n')*1400
            code,large=req('/api/import',{'name':'large.txt','content':text})
            check(code==200 and len(large['chunks'])>1000,'F: large full POST identity completes')
            for iteration in range(3):
                code,loaded=req('/api/material?id='+large['id'])
                check(code==200 and loaded['chunks']==large['chunks'],f'F: exact large identity GET #{iteration+1}')
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=15)
            conn.request('GET','/api/material?id='+large['id'],headers={'X-ExaCortex-Token':server.token,'Accept-Encoding':'identity'})
            response=conn.getresponse();data=bytearray()
            while True:
                part=response.read(8192)
                if not part:break
                data.extend(part);time.sleep(.001)
            check(len(data)==int(response.headers['Content-Length']) and json.loads(data)['chunks']==large['chunks'],'F: slow client receives exact full identity response')
            conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('GET','/api/material?id='+large['id'],headers={'X-ExaCortex-Token':server.token});response=conn.getresponse();response.read(10);conn.close()
            check(req('/api/materials')[0]==200,'F: disconnected reader does not damage next request')
            before=len(req('/api/materials')[1])
            conn=socket.create_connection(('127.0.0.1',server.server_port),timeout=5)
            conn.sendall((f'POST /api/import HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\nX-ExaCortex-Token: {server.token}\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n'+ '{"name":"partial.txt"').encode())
            conn.shutdown(socket.SHUT_WR);conn.recv(8192);conn.close()
            check(len(req('/api/materials')[1])==before,'F: interrupted upload leaves no partial material')
            check(req('/api/import',{'name':'large.txt','content':text})[1]['id']==large['id'],'F: repeat large import is idempotent')
            check(req('/api/intelligence-chat',{'question':'MCP','model':'cloud'})[0]==403,'G: cloud rejected before transport')
            code,backup=req('/api/backup',{});check(code==200 and backup['verified'],'E: complete DB backup verified')
            backup_path=db.parent/'backups'/backup['name']
        with running(db) as (req,server):
            check(req('/api/fusion-synthesis?id='+syn['id'])[1]['original_response']==raw,'D: raw manual response survives restart')
            check(any(t['id']==task['id'] and t['status']=='completed' for t in req('/api/learning-tasks')[1]),'D: completed practice survives restart')
        restored=root/'restored';restore_backup(backup_path,restored)
        with running(restored/'learning.sqlite3') as (req,server):
            check(req('/api/diagnostics')[1]['healthy'],'E: restored database healthy')
            check(server.store.saved_xagon_status()['vision_done']==16 and server.store.get_synthesis(syn['id'])['original_response']==raw,'E: restore retains Vision provenance and manual response')
    result={'success':True,'checks':checks,'count':len(checks),'surface':'real loopback HTTP, identity encoding','scenarios':['A','B','C','D','E','F','G'],'real_api_calls':0}
    out=ROOT/'test_artifacts';out.mkdir(exist_ok=True);(out/'acceptance_rc3.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'success':True,'count':len(checks)}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        out=ROOT/'test_artifacts';out.mkdir(exist_ok=True);(out/'acceptance_rc3.json').write_text(json.dumps({'success':False,'error_type':type(exc).__name__}),'utf-8');raise
