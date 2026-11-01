"""Offline, source-grounded learning library. No legacy DB or network imports.

Chat Lite is retrieval and quotation, not language-model generation. Every
learning write goes to an independent database supplied by the caller.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 12000
APP_ID = 0x4558434C
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_STOP = set("а и или но в во на по о об от до из за к ко с со у для как что это эта этот эти то ли же бы быть есть мне нам вы мы он она они его её еще уже где когда почему зачем какие какой какая такое такой такие какую каковы объясни расскажи пожалуйста можно нужно the a an and or to of in is are how what why".split())
_SUFFIXES = ("овать", "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "ить", "ать", "ять", "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ий", "ый", "ов", "ев", "ам", "ям", "ах", "ях", "ом", "ем", "ую", "юю", "а", "я", "ы", "и", "у", "ю", "е")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text):
    return text.casefold().replace("ё", "е")


def _terms(query):
    terms = []
    for word in _WORDS.findall(_normalize(query)):
        if word in _STOP or len(word) < 2:
            continue
        if len(word) >= 5 and re.fullmatch("[а-я]+", word):
            for suffix in _SUFFIXES:
                if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                    word = word[:-len(suffix)]
                    break
        if word not in terms:
            terms.append(word)
    return terms[:16]


def _plain(value, label, max_length):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: требуется непустой текст")
    if "\x00" in value or len(value) > max_length:
        raise ValueError(f"{label}: недопустимый размер или нулевой символ")
    return value.strip()


def _time(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 604800 or not math.isfinite(value):
        raise ValueError("Некорректный таймкод: ожидается конечное неотрицательное число")
    return float(value)


def _clock(seconds):
    if seconds is None:
        return "без таймкода"
    t = int(seconds)
    return f"{t // 3600:02}:{t // 60 % 60:02}:{t % 60:02}"


def _source_name(value):
    name = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    return _plain(name, "Имя источника", 255)


def _markdown_text(text):
    # Prevent source text from creating active HTML/images/links on export.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")


def _text_chunks(text):
    """Keep paragraph/heading boundaries, bound long paragraphs and join short ones."""
    out, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if paragraph.startswith("#") and current:
            out.append(current)
            current = ""
        words = paragraph.split()
        pieces = [" ".join(words[i:i + 100]) for i in range(0, len(words), 100)]
        for piece in pieces:
            if current and len(current) + len(piece) > 1000:
                out.append(current)
                current = ""
            current = current + "\n\n" + piece if current else piece
    if current:
        out.append(current)
    return [{"text": t, "start_seconds": None, "end_seconds": None, "segment_ids": []} for t in out]


def _transcript_chunks(doc):
    if not isinstance(doc, dict):
        raise ValueError("Транскрипция JSON должна быть объектом XAGON")
    words = doc.get("words")
    if words is not None and not isinstance(words, list):
        raise ValueError("Поле words должно быть массивом")
    source_start = _time(doc.get("source_start_seconds"))
    source_end = _time(doc.get("source_end_seconds"))
    duration = _time(doc.get("source_duration_seconds"))
    if source_end is None and source_start is not None and duration is not None:
        source_end = _time(source_start + duration)
    if source_start is not None and source_end is not None and source_end < source_start:
        raise ValueError("Конец источника предшествует началу")
    if not words:
        return _text_chunks(_plain(doc.get("transcript_text"), "Текст транскрипции", MAX_FILE_BYTES))
    if len(words) > 300000:
        raise ValueError("Слишком много слов в транскрипции")
    chunks, group = [], []

    def finish():
        if not group:
            return
        starts = [w[1] for w in group if w[1] is not None]
        ends = [w[2] for w in group if w[2] is not None]
        chunks.append({"text": " ".join(w[0] for w in group),
                       "start_seconds": min(starts) if starts else None,
                       "end_seconds": max(ends) if ends else None,
                       "segment_ids": list(dict.fromkeys(w[3] for w in group if w[3]))})
        group.clear()

    for word in words:
        if not isinstance(word, dict):
            raise ValueError("Некорректная аннотация слова")
        text = _plain(word.get("text"), "Слово", 20000)
        start, end = _time(word.get("source_start_seconds")), _time(word.get("source_end_seconds"))
        if start is not None and end is not None and end < start:
            raise ValueError("Конец слова предшествует началу")
        if any(t is not None and (source_start is not None and t < source_start - .05
                or source_end is not None and t > source_end + 3) for t in (start, end)):
            raise ValueError("Таймкод слова находится вне диапазона источника")
        # Preserve invalid-timing text but do not invent an anchor for it.
        if word.get("timestamp_valid") is False:
            start = end = None
        segment = word.get("segment_id", doc.get("segment_id", ""))
        if not isinstance(segment, str) or len(segment) > 200:
            raise ValueError("Некорректный идентификатор сегмента")
        if group and (len(group) >= 100 or sum(len(w[0]) for w in group) > 1800):
            finish()
        group.append((text, start, end, segment))
        if len(group) >= 45 and text.endswith((".", "!", "?", "…")):
            finish()
    finish()
    return chunks


class LearningStore:
    """A small, independently persisted library; connections are per operation."""

    def __init__(self, db_path):
        self.db_path = Path(db_path).expanduser().resolve()
        self._validate_target()
        if self.db_path.exists():
            # Inspect an existing file read-only before any initializing writes.
            con = None
            try:
                con = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
                marker = con.execute("PRAGMA application_id").fetchone()[0]
                if marker != APP_ID:
                    raise ValueError("Это не учебная БД ExaCortex; файл не изменён")
            except sqlite3.DatabaseError as exc:
                raise ValueError("Не удалось открыть учебную БД; файл не изменён") from exc
            finally:
                if con is not None:
                    con.close()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(f"PRAGMA application_id={APP_ID}")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS materials(
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, source_name TEXT NOT NULL,
                  kind TEXT NOT NULL, content_sha256 TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS chunks(
                  id INTEGER PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id),
                  ordinal INTEGER NOT NULL, text TEXT NOT NULL, start_seconds REAL,
                  end_seconds REAL, segment_ids TEXT NOT NULL, UNIQUE(material_id,ordinal));
                CREATE INDEX IF NOT EXISTS chunks_material ON chunks(material_id,ordinal);
            """)
            try:
                con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS learning_fts USING fts5(text, title, tokenize='unicode61')")
                self.fts_available = True
                for row in con.execute("SELECT c.id,c.text,m.title FROM chunks c JOIN materials m ON m.id=c.material_id WHERE c.id NOT IN (SELECT rowid FROM learning_fts)").fetchall():
                    con.execute("INSERT INTO learning_fts(rowid,text,title) VALUES(?,?,?)", (row[0], _normalize(row[1]), _normalize(row[2])))
            except sqlite3.OperationalError as exc:
                if "fts5" not in str(exc).lower():
                    raise
                self.fts_available = False

    def _validate_target(self):
        if os.name != 'nt' and any(re.match(r'^[A-Za-z]:(?:\\|$)',part) for part in self.db_path.parts):
            raise ValueError('Use a native absolute path, not a Windows drive path on Linux')
        if self.db_path.name.casefold().startswith("xagon.sqlite") or self.db_path.is_dir():
            raise ValueError("Нельзя использовать рабочую БД XAGON для обучения")
        roots = []
        override = os.environ.get("XAGON_DATA_DIR", "").strip()
        if override:
            roots.append(Path(override).expanduser().resolve())
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        roots.append((local / "XAGON").resolve())
        packages = local / "Packages"
        if packages.is_dir():
            roots.extend(p.resolve() for p in packages.glob("PythonSoftwareFoundation.Python.*/LocalCache/Local/XAGON"))
        for parent in self.db_path.parents:
            if (parent / "xagon.sqlite3").is_file():
                roots.append(parent)
        if any(self.db_path == r or self.db_path.is_relative_to(r) for r in roots):
            raise ValueError("Учебная БД должна находиться вне production-хранилища XAGON")

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def import_file(self, path, title=None, *, source_name=None):
        path = Path(path)
        suffix = path.suffix.casefold()
        if suffix not in (".txt", ".md", ".markdown", ".json"):
            raise ValueError("Поддерживаются TXT, Markdown и JSON-транскрипции XAGON")
        if not path.is_file():
            raise ValueError("Файл материала не найден")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Материал превышает лимит 16 МиБ")
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("Материал превышает лимит 16 МиБ")
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("Сохраните материал в кодировке UTF-8") from exc
        name = _source_name(path.name if source_name is None else source_name)
        label = _plain(title if title is not None else path.stem, "Название", 300)
        if suffix != ".json":
            return self.import_text(text, label, name)
        try:
            doc = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Неконечное число JSON")))
            chunks = _transcript_chunks(doc)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Некорректный JSON транскрипции") from exc
        source = doc.get("source_id", "")
        if not isinstance(source, str) or len(source) > 200:
            raise ValueError("Некорректный source_id")
        return self._save(chunks, label, name, "transcript", source)

    def import_text(self, text, title, source_name="manual.md"):
        text = _plain(text, "Материал", MAX_FILE_BYTES)
        if len(text.encode("utf8")) > MAX_FILE_BYTES:
            raise ValueError("Материал превышает лимит 16 МиБ")
        label = _plain(title, "Название", 300)
        name = _source_name(source_name)
        return self._save(_text_chunks(text), label, name, "markdown" if name.casefold().endswith((".md", ".markdown")) else "text", "")

    def _save(self, chunks, title, name, kind, source):
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            mid = self._save_in_connection(con, chunks, title, name, kind, source)
        return self.get_material(mid)

    def _save_in_connection(self, con, chunks, title, name, kind, source):
        """Validated material insert, reusable by an atomic multi-source import."""
        if not chunks or len(chunks) > MAX_CHUNKS:
            raise ValueError("Материал пуст или содержит слишком много фрагментов")
        payload = json.dumps({"kind": kind, "source_id": source, "chunks": chunks}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf8")).hexdigest()
        mid = "mat_" + digest[:24]
        old = con.execute("SELECT id FROM materials WHERE content_sha256=?", (digest,)).fetchone()
        if not old:
            con.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?)", (mid, title, name, kind, digest, _now(), source))
            for ordinal, item in enumerate(chunks):
                cur = con.execute("INSERT INTO chunks(material_id,ordinal,text,start_seconds,end_seconds,segment_ids) VALUES(?,?,?,?,?,?)", (mid, ordinal, item["text"], item["start_seconds"], item["end_seconds"], json.dumps(item["segment_ids"])))
                if self.fts_available:
                    con.execute("INSERT INTO learning_fts(rowid,text,title) VALUES(?,?,?)", (cur.lastrowid, _normalize(item["text"]), _normalize(title)))
        return mid

    def list_materials(self):
        with self._connect() as con:
            return [dict(r) for r in con.execute("SELECT m.*, COUNT(c.id) AS chunk_count FROM materials m LEFT JOIN chunks c ON c.material_id=m.id GROUP BY m.id ORDER BY m.created_at DESC,m.id")]

    def get_material(self, material_id):
        with self._connect() as con:
            material = con.execute("SELECT * FROM materials WHERE id=?", (str(material_id),)).fetchone()
            if material is None:
                raise KeyError("Материал не найден")
            result = dict(material)
            result["chunks"] = [self._chunk(r) for r in con.execute("SELECT c.*,m.title,m.source_name FROM chunks c JOIN materials m ON m.id=c.material_id WHERE m.id=? ORDER BY c.ordinal", (material_id,))]
            result["chunk_count"] = len(result["chunks"])
            return result

    @staticmethod
    def _chunk(row):
        item = dict(row)
        item["segment_ids"] = json.loads(item["segment_ids"])
        return item

    def search(self, query, material_id=None, limit=12):
        if not isinstance(query, str) or len(query) > 2000:
            raise ValueError("Запрос должен быть текстом длиной до 2000 символов")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Лимит результатов должен быть от 1 до 50")
        terms = _terms(query)
        if not terms:
            return []
        if material_id is not None:
            self.get_material(material_id)
        with self._connect() as con:
            if self.fts_available:
                match = " OR ".join('"' + t + '"*' for t in terms)
                sql = "SELECT c.*,m.title,m.source_name FROM learning_fts JOIN chunks c ON c.id=learning_fts.rowid JOIN materials m ON m.id=c.material_id WHERE learning_fts MATCH ?"
                args = [match]
                if material_id is not None:
                    sql += " AND c.material_id=?"
                    args.append(material_id)
                sql += " ORDER BY bm25(learning_fts),c.id LIMIT ?"
                args.append(min(200, limit * 5))
                candidates = [self._chunk(r) for r in con.execute(sql, args)]
            else:
                sql = "SELECT c.*,m.title,m.source_name FROM chunks c JOIN materials m ON m.id=c.material_id"
                args = ()
                if material_id is not None:
                    sql += " WHERE m.id=?"
                    args = (material_id,)
                candidates = [self._chunk(r) for r in con.execute(sql, args)]
            # Favor coverage of the question's terms, then retain FTS ranking.
            scored = [(sum(any(word.startswith(t) for word in _WORDS.findall(_normalize(x["text"]))) for t in terms), i, x) for i, x in enumerate(candidates)]
            return [x for score, _, x in sorted(scored, key=lambda z: (-z[0], z[1])) if score][:limit]

    def ask(self, question, material_id=None):
        sources = self.search(question, material_id, limit=5)
        if not sources:
            answer = "В материалах не найдено подходящих фрагментов. Попробуйте другие ключевые слова. Ответ ИИ не генерировался."
        else:
            lines = ["Найдены фрагменты источников. Это поиск и цитаты, а не сгенерированный ответ ИИ."]
            for i, item in enumerate(sources, 1):
                lines.append(f"[{i}] {item['title']} · {_clock(item['start_seconds'])} · фрагмент {item['id']}\n{item['text']}")
            answer = "\n\n".join(lines)
        return {"mode": "extractive", "answer": answer, "sources": sources}

    @staticmethod
    def _reference(chunk):
        return f"{chunk['material_id']}#chunk-{chunk['id']} · {_clock(chunk['start_seconds'])} · {_markdown_text(chunk['source_name'])}"

    def outline(self, material_id):
        m = self.get_material(material_id)
        lines = [f"# Учебный конспект: {_markdown_text(m['title'])}", "", "> Автоматическая подборка исходных фрагментов, не AI-пересказ. Текст и таймкоды машинной транскрипции требуют проверки.", "", f"Источник: {_markdown_text(m['source_name'])}", f"ID: `{m['id']}`", "", "## Последовательность материала", ""]
        for chunk in m["chunks"]:
            # Extract the first source sentence, not a fabricated semantic heading.
            excerpt = re.split(r"(?<=[.!?…])\s+", chunk["text"], maxsplit=1)[0]
            if len(excerpt) > 500:
                excerpt = excerpt[:500] + " … [цитата сокращена]"
            lines.extend([f"### Фрагмент {chunk['ordinal'] + 1} · {_clock(chunk['start_seconds'])}", "", "> " + _markdown_text(excerpt).replace("\n", "\n> "), "", "Источник: " + self._reference(chunk), ""])
        return "\n".join(lines)

    def practice(self, material_id):
        m = self.get_material(material_id)
        chunks = m["chunks"]
        selected = chunks[::max(1, len(chunks) // 5)][:5]
        lines = [f"# Практика: {_markdown_text(m['title'])}", "", "> Шаблоны самостоятельной работы. Это не инструкции, извлечённые из видео, и не проверка правильности ваших ответов.", "", "## Учебный план", "", "1. Прочитайте материал и отметьте незнакомые термины.", "2. Найдите определения поиском и сохраните ссылки на фрагменты.", "3. Проверьте ключевые утверждения по оригинальному источнику.", "4. Выполните задания ниже и запишите свой ответ.", "5. Повторите трудные темы через день и через неделю.", "", "## Задания по источникам", ""]
        for i, chunk in enumerate(selected, 1):
            lines.extend([f"### Задание {i}", "", "Прочитайте указанный фрагмент. Сформулируйте главный тезис своими словами, приведите короткую точную цитату и отметьте, что требует дополнительной проверки.", "", "Источник: " + self._reference(chunk), "", "Мой ответ: …", "Доказательство / цитата: …", "Вопрос для проверки: …", ""])
        return "\n".join(lines)

    def export_markdown(self, material_id):
        m = self.get_material(material_id)
        lines = [self.outline(material_id), self.practice(material_id), "## Полный текст по фрагментам", ""]
        for chunk in m["chunks"]:
            lines.extend([f"### Фрагмент {chunk['ordinal'] + 1} · {_clock(chunk['start_seconds'])}", "", _markdown_text(chunk["text"]), "", "Источник: " + self._reference(chunk), ""])
        return "\n".join(lines)
