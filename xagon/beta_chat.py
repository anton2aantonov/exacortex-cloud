"""Persistent extractive Chat Lite. No SDK, network transport or model execution.

History is a record of retrieval, never an indexed or verified knowledge source.
Saved citation snapshots are checked against immutable chunks on every read.
"""
from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from .learning import _clock, _now, _plain

MAX_HISTORY = 200
_CITATION_FIELDS = (
    'id', 'material_id', 'ordinal', 'text', 'start_seconds', 'end_seconds',
    'segment_ids', 'title', 'source_name',
)
DISCLAIMER = 'История поиска не является проверенным знанием. Цитаты — слова источника; внешняя проверка не выполнялась.'


@runtime_checkable
class GenerativeChatAdapter(Protocol):
    """Future provider boundary; implementations must return explicit citations.

    The 0.7 release has no live implementation and never selects one from env.
    """
    def generate(self, question: str, sources: list[dict]) -> dict:
        ...


class BlockedGenerativeAdapter:
    name = 'disabled'
    available = False

    def generate(self, question: str, sources: list[dict]) -> dict:
        # Deliberately unconditional, including when legacy live opt-in is set.
        raise PermissionError('Генеративный Chat отключён в 0.7: бюджет $0, реальные API запрещены. Используйте Chat Lite.')


class ChatService:
    def __init__(self, store):
        self.store = store
        self.adapter: GenerativeChatAdapter = BlockedGenerativeAdapter()
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS chat_schema(version INTEGER NOT NULL)')
            versions = con.execute('SELECT version FROM chat_schema').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise ValueError('Версия истории Chat несовместима; данные не изменены.')
            con.execute('''CREATE TABLE IF NOT EXISTS chat_conversations(
                id INTEGER PRIMARY KEY,
                material_id TEXT REFERENCES materials(id),
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode='extractive'),
                sources_json TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            expected = {'id', 'material_id', 'question', 'answer', 'mode', 'sources_json', 'created_at'}
            columns = {r[1] for r in con.execute('PRAGMA table_info(chat_conversations)')}
            if columns != expected:
                raise ValueError('Схема истории Chat несовместима; данные не изменены.')
            con.execute('CREATE INDEX IF NOT EXISTS chat_material_history ON chat_conversations(material_id,id)')
            if not versions:
                con.execute('INSERT INTO chat_schema VALUES(1)')

    @staticmethod
    def _material_id(material_id):
        return None if material_id is None else _plain(material_id, 'Материал', 200)

    @staticmethod
    def _answer(sources):
        if not sources:
            return 'В материалах не найдено подходящих фрагментов. Попробуйте другие ключевые слова. Ответ ИИ не генерировался.'
        lines = ['Найдены фрагменты источников. Это поиск и цитаты, а не сгенерированный ответ ИИ.']
        for i, item in enumerate(sources, 1):
            lines.append(f"[{i}] {item['title']} · {_clock(item['start_seconds'])} · фрагмент {item['id']}\n{item['text']}")
        return '\n\n'.join(lines)

    def _validate_sources(self, con, sources, material_id):
        if not isinstance(sources, list) or len(sources) > 5:
            raise ValueError('Некорректные ссылки Chat; ответ не подтверждён источниками.')
        validated, ids = [], set()
        for source in sources:
            if not isinstance(source, dict) or type(source.get('id')) is not int or source['id'] in ids:
                raise ValueError('Некорректная ссылка Chat; ответ не подтверждён источниками.')
            row = con.execute('''SELECT c.*,m.title,m.source_name FROM chunks c
                JOIN materials m ON m.id=c.material_id WHERE c.id=?''', (source['id'],)).fetchone()
            if row is None:
                raise ValueError('Источник истории Chat отсутствует; восстановите учебную библиотеку.')
            canonical = self.store._chunk(row)
            if (material_id is not None and canonical['material_id'] != material_id
                    or any(field not in source or source[field] != canonical[field] for field in _CITATION_FIELDS)):
                raise ValueError('Цитата или таймкод Chat не совпадает с источником; ответ не показан.')
            validated.append({key: canonical[key] for key in _CITATION_FIELDS})
            ids.add(source['id'])
        return validated

    def ask(self, question, material_id=None):
        question = _plain(question, 'Вопрос', 2000)
        material_id = self._material_id(material_id)
        if material_id is not None:
            self.store.get_material(material_id)
        found = self.store.search(question, material_id=material_id, limit=5)
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            sources = self._validate_sources(con, found, material_id)
            answer, created = self._answer(sources), _now()
            cursor = con.execute('''INSERT INTO chat_conversations
                (material_id,question,answer,mode,sources_json,created_at) VALUES(?,?,?,'extractive',?,?)''',
                (material_id, question, answer, json.dumps(sources, ensure_ascii=False, allow_nan=False), created))
            conversation_id = cursor.lastrowid
        return {'mode': 'extractive', 'answer': answer, 'sources': sources,
                'conversation_id': conversation_id, 'created_at': created,
                'disclaimer': DISCLAIMER, 'provider': 'offline', 'verified_knowledge': False}

    def history(self, material_id=None):
        """Latest 200 entries, newest first. Older entries remain on disk."""
        material_id = self._material_id(material_id)
        if material_id is not None:
            self.store.get_material(material_id)
        with self.store._connect() as con:
            sql = 'SELECT * FROM chat_conversations'
            args = []
            if material_id is not None:
                sql += ' WHERE material_id=?'
                args.append(material_id)
            sql += ' ORDER BY id DESC LIMIT ?'
            args.append(MAX_HISTORY)
            result = []
            for row in con.execute(sql, args).fetchall():
                try:
                    sources = self._validate_sources(con, json.loads(row['sources_json']), row['material_id'])
                except (json.JSONDecodeError, TypeError, RecursionError) as exc:
                    raise ValueError('История Chat повреждена; исходные данные не изменены.') from exc
                if row['mode'] != 'extractive' or row['answer'] != self._answer(sources):
                    raise ValueError('Ответ истории Chat не совпадает с цитатами; исходные данные не изменены.')
                result.append({'id': row['id'], 'material_id': row['material_id'],
                               'question': row['question'], 'answer': row['answer'],
                               'mode': row['mode'], 'sources': sources, 'created_at': row['created_at'],
                               'disclaimer': DISCLAIMER, 'verified_knowledge': False})
        return result
