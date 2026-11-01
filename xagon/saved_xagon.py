"""Explicit read-only cache snapshot. No legacy DB initialization or API calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re

MAX_JSON=16*1024*1024
ID=re.compile(r'^[A-Za-z0-9_\-]{1,100}$')


def read_json(path, root=None):
    path=Path(path)
    if path.is_symlink() or (root and not path.resolve().is_relative_to(Path(root).resolve())):
        raise ValueError('Redirected saved-result path')
    if path.stat().st_size>MAX_JSON:raise ValueError('Saved JSON exceeds 16 MiB')
    raw=path.read_bytes()
    if len(raw)>MAX_JSON:raise ValueError('Saved JSON too large')
    result=json.loads(raw.decode('utf-8-sig'))
    if not isinstance(result,dict):raise ValueError('Expected saved JSON object')
    return result,hashlib.sha256(raw).hexdigest()


def snapshot(root, merged_path):
    root=Path(root).resolve();merged,merged_sha=read_json(merged_path)
    source=merged.get('source_id');ids=merged.get('segment_ids')
    if not isinstance(source,str) or not ID.fullmatch(source) or not isinstance(ids,list) or not 1<=len(ids)<=100:
        raise ValueError('Invalid saved source/segment identities')
    if len(ids)!=len(set(ids)) or any(not isinstance(s,str) or not ID.fullmatch(s) for s in ids):raise ValueError('Invalid segment IDs')
    segments=[]
    for sid in ids:
        folder=root/'media'/source/sid
        transcript_path=folder/'transcripts/gemini-3.5-transcribe/transcript.json'
        transcript,sha=read_json(transcript_path,root)
        if (transcript.get('source_id'),transcript.get('segment_id'))!=(source,sid) or not transcript.get('transcript_text'):
            raise ValueError('Transcript identity/content mismatch')
        candidates=sorted((folder/'vision/vision_pilot_v3_resilient').glob('vision_*.json'))
        if len(candidates)>1:raise ValueError('Ambiguous Vision versions; select explicitly before import')
        vision=None;vision_sha=None;vision_name=None
        if candidates:
            vision,vision_sha=read_json(candidates[0],root)
            if (vision.get('source_id'),vision.get('segment_id'))!=(source,sid) or not isinstance(vision.get('observations'),list):
                raise ValueError('Vision identity mismatch')
            vision_name=candidates[0].relative_to(root).as_posix()
        segments.append({'segment_id':sid,'transcript_sha256':sha,
            'transcript_ref':transcript_path.relative_to(root).as_posix(),
            'start_seconds':transcript.get('source_start_seconds'),
            'duration_seconds':transcript.get('source_duration_seconds'),
            'word_count':transcript.get('word_count'),'vision_sha256':vision_sha,
            'vision_ref':vision_name,'vision':vision,
            'vision_content_sha256':hashlib.sha256(json.dumps(vision,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() if vision else None,
            'vision_status':'saved_unverified' if vision else 'missing'})
    if sorted(s['transcript_sha256'] for s in segments)!=sorted(merged.get('source_transcript_sha256',[])):
        raise ValueError('Merged export is not derived from these transcript files')
    return {'schema_version':1,'source_id':source,'merged_sha256':merged_sha,'merged':merged,
        'merged_content_sha256':hashlib.sha256(json.dumps(merged,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'segments':segments,'verification':'read_only_files_and_hashes','new_api_requests':0,
        'notice':'Saved AI observations are unverified; frame images are not copied.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--merged',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    from .rc_store import RCStore
    guard=RCStore.__new__(RCStore);guard.db_path=args.output.resolve();guard._validate_target()
    if args.output.resolve().is_relative_to(args.root.resolve()):raise ValueError('Output must be outside legacy storage')
    result=snapshot(args.root,args.merged)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,allow_nan=False)
    print(json.dumps({'transcripts':len(result['segments']),'vision':sum(s['vision'] is not None for s in result['segments']),'new_api_requests':0}))


if __name__=='__main__':main()
