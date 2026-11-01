"""Synthetic loopback Evolution workflow; no external connections."""
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.acceptance_rc3 import running

def main():
    connect=socket.socket.connect
    def local(sock,address):
        if not isinstance(address,tuple) or address[0]!='127.0.0.1':raise AssertionError('External transport forbidden')
        return connect(sock,address)
    checks=0
    def check(ok):
        nonlocal checks
        assert ok
        checks+=1
    with tempfile.TemporaryDirectory(prefix='evolution_') as temp,patch.object(socket.socket,'connect',local):
        db=Path(temp)/'library.sqlite3'
        with running(db) as (req,server):
            materials=[];claims=[]
            for i,text in enumerate(['MCP requires evidence.','MCP does not require evidence.']):
                code,m=req('/api/import',{'name':f'synthetic{i}.md','content':text});check(code==200);materials.append(m)
                code,c=req('/api/claim',{'material_id':m['id'],'chunk_id':m['chunks'][0]['id'],'text':text});check(code==200);claims.append(c)
            code,p=req('/api/context-package',{'topic':'MCP','material_ids':[m['id'] for m in materials],'task':'Synthetic comparison'})
            check(code==200 and p['evolution']['pairs'][0]['relation']=='potential_conflict')
            check(req('/api/fusion-export?id='+p['id'])[1]==p)
            payload={'package_id':p['id'],'origin':'mock','items':[{'text':'Synthetic hypothesis','claim_ids':[claims[0]['id']], 'evidence_ids':[claims[0]['evidence'][0]['id']],'citation_ids':[p['citations'][0]['citation_id']]}]}
            code,a=req('/api/fusion-import',{'package_id':p['id'],'payload':payload});check(code==200)
            check(req('/api/synthesis-task',{'synthesis_id':a['id'],'item_id':'item_1'})[0]==400)
            payload['parent_synthesis_id']=a['id'];payload['items'][0]['text']='Revised synthetic hypothesis'
            code,b=req('/api/fusion-import',{'package_id':p['id'],'payload':payload});check(code==200 and b['parent_synthesis_id']==a['id'])
            code,diff=req('/api/synthesis-compare?left_id='+a['id']+'&right_id='+b['id']);check(code==200 and len(diff['added'])==1)
            payload['items'][0]['evidence_ids']=[claims[1]['evidence'][0]['id']]
            check(req('/api/fusion-import',{'package_id':p['id'],'payload':payload})[0]==400)
            check(req('/api/fusion-review',{'synthesis_id':b['id'],'item_id':'item_1','action':'accept'})[0]==200)
            code,t=req('/api/synthesis-task',{'synthesis_id':b['id'],'item_id':'item_1'});check(code==200 and t['synthesis_id']==b['id'])
            check(req('/api/learning-task-state',{'task_id':t['id'],'completed':True})[1]['status']=='completed')
        with running(db) as (req,server):
            check(req('/api/fusion-synthesis?id='+a['id'])[1]['items'][0]['text']=='Synthetic hypothesis')
            check(len(req('/api/fusion-syntheses?package_id='+p['id'])[1])==2)
            check(req('/api/learning-tasks')[1][0]['status']=='completed')
    print(json.dumps({'evolution_http_assertions':checks,'ai_calls':0,'synthetic_only':True}))
if __name__=='__main__':main()
