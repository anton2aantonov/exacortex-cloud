"""Versioned personal learning state, separate from immutable source chunks."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .learning import LearningStore, _now, _plain, _source_name, _markdown_text
from .document_import import extract_document

SCHEMA_VERSION = 1
STEPS = (('read', 'Прочитать материал'), ('find', 'Найти и проверить источник'),
         ('practice', 'Выполнить упражнение и сохранить заметку'), ('review', 'Повторить и проверить свой результат'))
STATUSES = ('new', 'reading', 'completed')


class BetaStore(LearningStore):
    def __init__(self, db_path):
        # Refuse future schemas before the inherited initialization writes.
        self.db_path = Path(db_path).expanduser().resolve()
        self._validate_target()
        if self.db_path.is_file():
            con = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
            try:
                if con.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
                    raise ValueError('Учебная БД создана более новой версией. Файл не изменён.')
            except sqlite3.DatabaseError as exc:
                raise ValueError('Не удалось прочитать учебную БД; файл не изменён') from exc
            finally:
                con.close()
        super().__init__(db_path)
        self._migrate()

    def _migrate(self):
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('Более новая схема учебной БД не поддерживается')
            if version == 0:
                statements = [
                    'CREATE UNIQUE INDEX IF NOT EXISTS chunks_id_material ON chunks(id,material_id)',
                    '''CREATE TABLE beta_notes(id INTEGER PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id),
                       chunk_id INTEGER, text TEXT NOT NULL, created_at TEXT NOT NULL,
                       FOREIGN KEY(chunk_id,material_id) REFERENCES chunks(id,material_id))''',
                    '''CREATE TABLE beta_bookmarks(material_id TEXT NOT NULL REFERENCES materials(id), chunk_id INTEGER NOT NULL,
                       created_at TEXT NOT NULL, PRIMARY KEY(material_id,chunk_id),
                       FOREIGN KEY(chunk_id,material_id) REFERENCES chunks(id,material_id))''',
                    '''CREATE TABLE beta_progress(material_id TEXT PRIMARY KEY REFERENCES materials(id),
                       status TEXT NOT NULL CHECK(status IN ('new','reading','completed')), updated_at TEXT NOT NULL)''',
                    '''CREATE TABLE beta_tags(material_id TEXT NOT NULL REFERENCES materials(id), tag TEXT NOT NULL,
                       PRIMARY KEY(material_id,tag))''',
                    '''CREATE TABLE beta_steps(material_id TEXT NOT NULL REFERENCES materials(id),
                       step_id TEXT NOT NULL CHECK(step_id IN ('read','find','practice','review')),
                       completed INTEGER NOT NULL CHECK(completed IN (0,1)), updated_at TEXT NOT NULL,
                       PRIMARY KEY(material_id,step_id))''',
                ]
                for sql in statements:
                    con.execute(sql)
                con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @staticmethod
    def _material(con, material_id):
        if not isinstance(material_id, str) or not con.execute('SELECT 1 FROM materials WHERE id=?', (material_id,)).fetchone():
            raise KeyError('Материал не найден')

    @staticmethod
    def _chunk_id(con, material_id, chunk_id):
        if isinstance(chunk_id, bool) or not isinstance(chunk_id, int) or chunk_id <= 0:
            raise ValueError('Некорректный номер фрагмента')
        if not con.execute('SELECT 1 FROM chunks WHERE id=? AND material_id=?', (chunk_id, material_id)).fetchone():
            raise ValueError('Фрагмент не принадлежит выбранному материалу')

    def add_note(self, material_id, text, chunk_id=None):
        text = _plain(text, 'Заметка', 20000)
        now = _now()
        with self._connect() as con:
            self._material(con, material_id)
            if chunk_id is not None:
                self._chunk_id(con, material_id, chunk_id)
            cur = con.execute('INSERT INTO beta_notes(material_id,chunk_id,text,created_at) VALUES(?,?,?,?)', (material_id, chunk_id, text, now))
            return {'id': cur.lastrowid, 'material_id': material_id, 'chunk_id': chunk_id, 'text': text, 'created_at': now}

    def list_notes(self, material_id):
        with self._connect() as con:
            self._material(con, material_id)
            return [dict(r) for r in con.execute('SELECT * FROM beta_notes WHERE material_id=? ORDER BY id', (material_id,))]

    def set_bookmark(self, material_id, chunk_id, enabled=True):
        if not isinstance(enabled, bool):
            raise ValueError('Состояние закладки должно быть true/false')
        with self._connect() as con:
            self._material(con, material_id)
            self._chunk_id(con, material_id, chunk_id)
            if enabled:
                con.execute('INSERT OR IGNORE INTO beta_bookmarks VALUES(?,?,?)', (material_id, chunk_id, _now()))
            else:
                con.execute('DELETE FROM beta_bookmarks WHERE material_id=? AND chunk_id=?', (material_id, chunk_id))
        return {'material_id': material_id, 'chunk_id': chunk_id, 'enabled': enabled}

    def list_bookmarks(self, material_id):
        with self._connect() as con:
            self._material(con, material_id)
            return [self._chunk(r) for r in con.execute('''SELECT c.*,m.title,m.source_name FROM beta_bookmarks b
                JOIN chunks c ON c.id=b.chunk_id JOIN materials m ON m.id=c.material_id
                WHERE b.material_id=? ORDER BY c.ordinal''', (material_id,))]

    def set_progress(self, material_id, status):
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError('Статус должен быть new, reading или completed')
        with self._connect() as con:
            self._material(con, material_id)
            con.execute('INSERT INTO beta_progress VALUES(?,?,?) ON CONFLICT(material_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at', (material_id, status, _now()))
        return {'material_id': material_id, 'status': status}

    def get_progress(self, material_id):
        with self._connect() as con:
            self._material(con, material_id)
            row = con.execute('SELECT status FROM beta_progress WHERE material_id=?', (material_id,)).fetchone()
        return {'material_id': material_id, 'status': row[0] if row else 'new'}

    def set_tags(self, material_id, tags):
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError('Ожидается список не более 20 тегов')
        normalized = list(dict.fromkeys(_plain(tag, 'Тег', 40).casefold() for tag in tags))
        if any('\n' in t or '\r' in t for t in normalized):
            raise ValueError('Тег должен занимать одну строку')
        with self._connect() as con:
            self._material(con, material_id)
            con.execute('BEGIN IMMEDIATE')
            con.execute('DELETE FROM beta_tags WHERE material_id=?', (material_id,))
            con.executemany('INSERT INTO beta_tags VALUES(?,?)', ((material_id, tag) for tag in normalized))
        return normalized

    def get_tags(self, material_id):
        with self._connect() as con:
            self._material(con, material_id)
            return [r[0] for r in con.execute('SELECT tag FROM beta_tags WHERE material_id=? ORDER BY tag', (material_id,))]

    def study_plan(self, material_id):
        with self._connect() as con:
            self._material(con, material_id)
            values = {r[0]: bool(r[1]) for r in con.execute('SELECT step_id,completed FROM beta_steps WHERE material_id=?', (material_id,))}
        return {'material_id': material_id, 'steps': [{'id': key, 'title': title, 'status': 'completed' if values.get(key) else 'pending'} for key, title in STEPS],
                'disclaimer': 'Учебный шаблон и личные отметки. Не оценка знаний моделью.'}

    def set_step(self, material_id, step_id, completed):
        if not isinstance(step_id, str) or step_id not in dict(STEPS) or not isinstance(completed, bool):
            raise ValueError('Некорректный шаг или состояние учебного маршрута')
        with self._connect() as con:
            self._material(con, material_id)
            con.execute('INSERT INTO beta_steps VALUES(?,?,?,?) ON CONFLICT(material_id,step_id) DO UPDATE SET completed=excluded.completed,updated_at=excluded.updated_at', (material_id, step_id, int(completed), _now()))
        return self.study_plan(material_id)

    def import_file(self, path, title=None, *, source_name=None):
        path = Path(path)
        if path.suffix.casefold() not in ('.docx', '.pdf'):
            return super().import_file(path, title, source_name=source_name)
        name = _source_name(path.name if source_name is None else source_name)
        label = _plain(title if title is not None else Path(name).stem, 'Название', 300)
        # Parse entirely before a database write. Canonical text deduplication
        # keeps import behavior compatible with existing TXT imports.
        return self.import_text(extract_document(path), label, name)

    def export_markdown(self, material_id):
        original = super().export_markdown(material_id)
        lines = [original, '', '## Личные заметки и прогресс', '',
                 '> Пользовательские записи и отметки, не утверждения источника и не проверка знаний ИИ.', '',
                 'Статус: ' + self.get_progress(material_id)['status'],
                 'Теги: ' + _markdown_text(', '.join(self.get_tags(material_id))), '', '### Учебный маршрут', '']
        for step in self.study_plan(material_id)['steps']:
            lines.append(f"- {'Выполнено' if step['status'] == 'completed' else 'Не отмечено'}: {step['title']}")
        lines.extend(['', '### Мои заметки', ''])
        for note in self.list_notes(material_id):
            lines.extend([f"Заметка {note['id']} · {note['created_at']}", '', '> ' + _markdown_text(note['text']).replace('\n', '\n> '), ''])
            if note['chunk_id'] is not None:
                lines.append(f"Ссылка заметки: {material_id}#chunk-{note['chunk_id']}")
        lines.extend(['', '### Закладки', ''])
        for chunk in self.list_bookmarks(material_id):
            lines.append('Источник: ' + self._reference(chunk))
        return '\n'.join(lines)
