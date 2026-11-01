"""Entirely synthetic, anonymized XAGON-shaped results for portable QA."""
import hashlib
import json
from pathlib import Path


def synthetic_cache(root):
    root=Path(root);source='src_demo';ids=[f'seg_{i:03d}' for i in range(18)]
    words=[];hashes=[]
    for i,sid in enumerate(ids):
        folder=root/'media'/source/sid
        doc={'source_id':source,'segment_id':sid,'source_start_seconds':i*30,
             'source_duration_seconds':30,'transcript_text':f'MCP demo segment {i}.','word_count':4}
        path=folder/'transcripts/gemini-3.5-transcribe/transcript.json';path.parent.mkdir(parents=True)
        raw=json.dumps(doc).encode();path.write_bytes(raw);hashes.append(hashlib.sha256(raw).hexdigest())
        words.append({'text':f'MCP demo {i}.','source_start_seconds':i*30,'source_end_seconds':i*30+1,'segment_id':sid})
        if i<16:
            vision={'source_id':source,'segment_id':sid,'model':'synthetic fixture','uncertainties':['Synthetic observation is not independently verified'],'observations':[{'frame_id':'frame_1','frame_sha256':'a'*64,
                    'source_seconds_estimated':i*30+1,'screen_summary':f'MCP synthetic screen {i}',
                    'visible_text':['DEMO ONLY'],'human_verified':False}]}
            path=folder/'vision/vision_pilot_v3_resilient/vision_demo.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps(vision),'utf-8')
    merged={'source_id':source,'segment_ids':ids,'source_transcript_sha256':hashes,
            'source_end_seconds':540,'words':words,'transcript_text':'MCP demo material.'}
    path=root/'merged.json';path.write_text(json.dumps(merged),'utf-8')
    return path
