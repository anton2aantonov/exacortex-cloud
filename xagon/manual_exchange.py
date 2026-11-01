"""Parse user-pasted text only. No ChatGPT connection or browser automation."""
import json
import re


def parse_manual_response(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>768*1024 or '\x00' in text:
        raise ValueError('Вставьте ответ: не более 768 КиБ, без нулевых символов')
    clean=text.strip().lstrip('\ufeff')
    if clean.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```',clean,re.DOTALL|re.IGNORECASE)
        if not match:raise ValueError('Незавершённый JSON code fence')
        clean=match.group(1).strip()
    if clean.startswith(('{','[')):
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Повторяющийся ключ JSON')
                result[key]=value
            return result
        try:payload=json.loads(clean,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NaN/Infinity запрещены')))
        except (ValueError,RecursionError) as exc:raise ValueError('Некорректный структурированный ответ JSON') from exc
        if not isinstance(payload,dict):raise ValueError('Ожидается JSON объект с items')
        payload['parse_method']='user_pasted_json'
        return payload
    paragraphs=[p.strip() for p in re.split(r'\n\s*\n',clean) if p.strip()]
    if not 1<=len(paragraphs)<=30:raise ValueError('Разделите ответ на 1–30 абзацев')
    return {'origin':'manual_ai','parse_method':'user_pasted_paragraphs_explicit_citation_ids',
        'items':[{'text':p,'kind':'synthesis','citation_ids':list(dict.fromkeys(re.findall(r'\bcite_[A-Za-z0-9_-]+\b',p)))} for p in paragraphs]}
