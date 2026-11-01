"""Read only fixed saved XAGON reports; never infer missing processing output."""
import hashlib
import json
from pathlib import Path


def saved_readiness(root):
    root=Path(root)
    reports={};errors=[];hashes={}
    for key,name in [('readiness','XAGON_Run_Readiness.json'),('session','XAGON_Session_Report.json')]:
        p=root/name
        try:
            if not p.is_file() or p.stat().st_size>1024*1024:raise ValueError('Отчёт отсутствует или слишком велик')
            raw=p.read_bytes();doc=json.loads(raw.decode('utf-8-sig'))
            if not isinstance(doc,dict):raise ValueError('Некорректный отчёт')
            reports[key]=doc;hashes[name]=hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError) as exc:errors.append(name+': '+str(exc))
    ready=reports.get('readiness',{});session=reports.get('session',{})
    for key in ('source_id','batch_id'):
        if ready.get(key) is not None and session.get(key) is not None and ready[key]!=session[key]:
            errors.append('Отчёты относятся к разным '+key)
    counts={}
    for out,a,b in [('total_segments','total_segments','total'),('transcribed','audio_transcribed','transcribed'),('vision_done','vision_done','vision_done')]:
        value=ready.get(a)
        if type(value) is not int or value<0:value=None
        counts[out]=value
        other=session.get(b)
        if value is not None and type(other) is int and other!=value:errors.append('Отчёты расходятся: '+out)
    total=counts['total_segments'];vision=counts['vision_done'];audio=counts['transcribed']
    valid=total is not None and total>0 and audio is not None and vision is not None and audio<=total and vision<=total
    if not valid:errors.append('Полнота обработки неизвестна')
    synthesis_pending=ready.get('synthesis_request_pending')
    complete=valid and audio==total and vision==total and synthesis_pending==0 and not errors
    return dict(counts,remaining_vision=total-vision if valid else None,
        synthesis_pending=synthesis_pending if type(synthesis_pending) is int else None,
        status='unknown' if errors else 'complete' if complete else 'partial',
        generated_at=ready.get('generated_at'),report_sha256=hashes,errors=errors,
        live_verified=False,cloud_allowed=False,
        notice='Снимок сохранённых отчётов XAGON, не текущая проверка production. Отсутствующие Vision/синтез не создаются.')
