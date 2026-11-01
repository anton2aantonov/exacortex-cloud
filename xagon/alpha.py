"""Offline ExaCortex UI. Loopback HTTP only; no external dependencies or APIs."""
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import webbrowser

APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = Path(__file__).resolve().parent / 'alpha_static'
MAX_BODY = 24 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024


def learning_directory() -> Path:
    """Choose an independent store; never silently use legacy XAGON_DATA_DIR."""
    configured = os.environ.get('EXACORTEX_LEARNING_DIR', '').strip()
    root = Path(configured).expanduser().resolve() if configured else APP_ROOT / 'rc_data'
    # Defense in depth; LearningStore independently validates the DB target.
    legacy = os.environ.get('XAGON_DATA_DIR', '').strip()
    if legacy:
        old = Path(legacy).expanduser().resolve()
        if root == old or root.is_relative_to(old):
            raise ValueError('Учебная база должна находиться вне рабочего каталога XAGON.')
    if (root / 'xagon.sqlite3').exists() or root.name.lower() == 'xagon':
        raise ValueError('Нельзя использовать рабочее хранилище XAGON для учебной базы.')
    return root


class AlphaServer(ThreadingHTTPServer):
    # Graceful close waits for bounded request handlers to finish their writes.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, store, *, port=8765, existing_transcript=None):
        self.store = store
        self.chat = None
        self.token = secrets.token_urlsafe(32)
        self.store_lock = threading.RLock()
        self.existing_transcript = Path(existing_transcript) if existing_transcript else APP_ROOT / 'XAGON_Merged_Transcript.json'
        super().__init__(('127.0.0.1', port), AlphaHandler)


class AlphaHandler(BaseHTTPRequestHandler):
    server: AlphaServer
    server_version = 'ExaCortex/1.0-RC3'

    def _intelligence_chat(self):
        if not hasattr(self.server, 'intelligence_chat'):
            from .intelligence_chat import IntelligenceChat
            self.server.intelligence_chat = IntelligenceChat(self.server.store)
        return self.server.intelligence_chat

    def _chat(self):
        if self.server.chat is None:
            from .beta_chat import ChatService
            self.server.chat = ChatService(self.server.store)
        return self.server.chat

    def setup(self):
        # Bound idle time while reading request lines and headers as well as
        # bodies. Otherwise a half-open local connection keeps a thread alive.
        self.request.settimeout(15)
        super().setup()

    def log_message(self, format, *args):
        # Do not log query strings, document contents or tokens.
        pass

    def _send(self, status, data, content_type='application/json; charset=utf-8', *, download=None):
        # On Windows, closing with an unread small POST body can reset the
        # connection before the browser receives our error response.
        if status >= 400 and self.command == 'POST' and not getattr(self, '_body_consumed', False):
            # Do not trust Content-Length on rejected requests: duplicate or
            # malformed values must also drain already queued small bodies on
            # Windows, otherwise close may reset before the error is delivered.
            try:
                self.connection.settimeout(.1)
                self.rfile.read1(65536)
            except OSError:
                pass
            self._body_consumed = True
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf8')
        # Standard negotiated compression reduces large text transfers and
        # transient buffers on constrained laptops. Never compress on q=0.
        accepts_gzip = any(part.strip().lower() == 'gzip' for part in
                           self.headers.get('Accept-Encoding', '').split(','))
        compressed = accepts_gzip and len(data) >= 32768 and not content_type.startswith('image/')
        if compressed:
            data = gzip.compress(data, compresslevel=1, mtime=0)
        self._response_started = True
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        # A single large sendall stalled the final TCP tail on the tested
        # Windows runtime. Bounded writes also avoid one huge pending send.
        view = memoryview(data)
        for offset in range(0, len(view), 32768):
            self.wfile.write(view[offset:offset + 32768])

    def _allowed(self, api=False):
        expected = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected:
            self._send(403, {'error': 'Недопустимый адрес сервера. Откройте ссылку 127.0.0.1 из окна запуска.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + expected:
            self._send(403, {'error': 'Запрос с другого сайта запрещён.'})
            return False
        if api:
            supplied_token = self.headers.get('X-ExaCortex-Token', '')
            # HTTP header decoding permits Latin-1, but str compare_digest only
            # accepts ASCII. Malformed tokens must fail closed with an HTTP
            # response, not escape the handler as TypeError.
            if not supplied_token.isascii() or not hmac.compare_digest(supplied_token, self.server.token):
                self._send(403, {'error': 'Сеанс изменился. Обновите страницу.'})
                return False
        return True

    def _body(self):
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Ожидается запрос JSON.')
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Потоковая загрузка не поддерживается.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise ValueError('Требуется единственный заголовок Content-Length.')
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY:
            raise ValueError('Пустой запрос или файл превышает ограничение 16 МиБ.')
        self.connection.settimeout(15)
        raw = self.rfile.read(length)
        self._body_consumed = True
        if len(raw) != length:
            raise ValueError('Загрузка прервана.')
        data = json.loads(raw.decode('utf8'))
        if not isinstance(data, dict):
            raise ValueError('Ожидается объект JSON.')
        return data

    def _error(self, exc):
        if getattr(self, '_response_started', False):
            # A peer disconnect cannot be turned into another HTTP response
            # after headers/body have already started. Never append error JSON.
            self.close_connection = True
            return
        if isinstance(exc, PermissionError) and exc.errno is None:
            self._send(403, {'error': 'Облачные модели отключены: бюджет $0. Используйте offline или явный mock.'})
        elif isinstance(exc, PermissionError):
            self._send(403, {'error': 'Нет доступа к учебной папке или файлу резервной копии. Права автоматически не изменяются.'})
        elif isinstance(exc, KeyError):
            self._send(404, {'error': 'Материал не найден. Обновите библиотеку.'})
        elif isinstance(exc, (ValueError, UnicodeError)):
            self._send(400, {'error': str(exc)[:300]})
        elif isinstance(exc, FileNotFoundError):
            self._send(404, {'error': 'Сохранённая транскрипция не найдена рядом с приложением. Импортируйте JSON вручную.'})
        else:
            # Never echo SQL, private paths or unknown exception payloads to UI.
            self._send(500, {'error': 'Не удалось выполнить действие. Проверьте доступность учебного каталога и свободное место.'})

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if not self._allowed(api=path.startswith('/api/')):
                return
            if path == '/':
                page = (STATIC_ROOT / 'index.html').read_text(encoding='utf8')
                self._send(200, page.replace('__SESSION_TOKEN__', self.server.token).encode('utf8'), 'text/html; charset=utf-8')
                return
            static = {'/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/intelligence.js': ('intelligence.js', 'text/javascript; charset=utf-8'), '/rc.js': ('rc.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in static:
                name, mime = static[path]
                self._send(200, (STATIC_ROOT / name).read_bytes(), mime)
                return
            query = parse_qs(urlsplit(self.path).query)
            arg = lambda key, default='': query.get(key, [default])[0]
            with self.server.store_lock:
                store = self.server.store
                if path == '/api/status':
                    result = {'mode': 'offline', 'live_api': False, 'provider': 'disabled', 'version': '1.0.0-rc3', 'final_release': False, 'existing_transcript_available': self.server.existing_transcript.is_file()}
                elif path == '/api/diagnostics':
                    from .rc_diagnostics import diagnose
                    result = diagnose(store,APP_ROOT)
                elif path == '/api/intelligence-search':
                    result = store.retrieve(arg('q'), material_ids=arg('material_ids').split(',') if arg('material_ids') else None, limit=int(arg('limit','20')))
                elif path == '/api/intelligence-history':
                    result = self._intelligence_chat().history()
                elif path == '/api/models':
                    result = self._intelligence_chat().models()
                elif path == '/api/fusion-runs':
                    result = store.package_runs(arg('package_id'))
                elif path == '/api/synthesis-compare':
                    result = store.compare_syntheses(arg('left_id'), arg('right_id'))
                elif path == '/api/learning-tasks':
                    result = store.list_learning_tasks()
                elif path == '/api/refresh-report':
                    result = store.refresh_report()
                elif path == '/api/fusion-packages':
                    result = store.list_packages()
                elif path == '/api/fusion-package':
                    result = store.get_package(arg('id'))
                elif path == '/api/fusion-export':
                    self._send(200, store.export_package(arg('id')).encode('utf8'), 'application/json; charset=utf-8', download='ExaCortex_AI_Package.json')
                    return
                elif path == '/api/fusion-syntheses':
                    result = store.list_syntheses(package_id=arg('package_id') or None)
                elif path == '/api/fusion-synthesis':
                    result = store.get_synthesis(arg('id'))
                elif path == '/api/fusion-synthesis-export':
                    self._send(200, store.export_synthesis(arg('id')).encode('utf8'), 'text/markdown; charset=utf-8', download='ExaCortex_Synthesis.md')
                    return
                elif path == '/api/external-checks':
                    result = store.get_external_checks(arg('claim_id'))
                elif path == '/api/evidence-frame':
                    content = store.read_frame(arg('id'))
                    mime = 'image/png' if content.startswith(b'\x89PNG') else 'image/jpeg'
                    self._send(200, content, mime)
                    return
                elif path == '/api/claims':
                    result = store.list_claims(query=arg('q'), status=arg('status'), material_id=arg('material_id') or None)
                elif path == '/api/claim':
                    result = store.get_claim(arg('id'))
                elif path == '/api/claim-suggestions':
                    result = store.suggest_links(arg('id'))
                elif path == '/api/knowledge-export':
                    self._send(200, store.export_knowledge(status=arg('status')).encode('utf8'), 'text/markdown; charset=utf-8', download='ExaCortex_Knowledge.md')
                    return
                elif path == '/api/materials':
                    result = store.list_library() if hasattr(store,'list_library') else store.list_materials()
                    if not hasattr(store,'list_library') and hasattr(store, 'get_tags'):
                        result = [dict(m, tags=store.get_tags(m['id']), progress=store.get_progress(m['id'])['status']) for m in result]
                elif path == '/api/material':
                    result = store.get_material(arg('id'))
                elif path == '/api/notes':
                    result = store.list_notes(arg('id'))
                elif path == '/api/bookmarks':
                    result = store.list_bookmarks(arg('id'))
                elif path == '/api/progress':
                    result = store.get_progress(arg('id'))
                elif path == '/api/tags':
                    result = store.get_tags(arg('id'))
                elif path == '/api/study-plan':
                    result = store.study_plan(arg('id'))
                elif path == '/api/history':
                    result = self._chat().history(material_id=arg('material_id') or None)
                elif path == '/api/search':
                    result = store.search(arg('q'), material_id=arg('material_id') or None)
                elif path == '/api/outline':
                    result = {'markdown': store.outline(arg('id'))}
                elif path == '/api/practice':
                    result = {'markdown': store.practice(arg('id'))}
                elif path == '/api/export':
                    markdown = store.export_markdown(arg('id'))
                    self._send(200, markdown.encode('utf8'), 'text/markdown; charset=utf-8', download='ExaCortex_Notes.md')
                    return
                else:
                    self._send(404, {'error': 'Страница не найдена.'})
                    return
            self._send(200, result)
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        if not self._allowed(api=True):
            return
        try:
            data = self._body()
            path = urlsplit(self.path).path
            with self.server.store_lock:
                store = self.server.store
                if path == '/api/backup':
                    from .rc_backup import create_backup
                    name='backup_'+secrets.token_hex(8)
                    result=dict(create_backup(store,store.db_path.parent/'backups'/name),name=name)
                elif path == '/api/synthesis-task':
                    result = store.create_synthesis_task(data.get('synthesis_id'),data.get('item_id'))
                elif path == '/api/intelligence-chat':
                    result = self._intelligence_chat().ask(data.get('question'), material_ids=data.get('material_ids'), model=data.get('model','offline'))
                elif path == '/api/context-package':
                    result = store.context_package(data.get('topic'), data.get('material_ids'), data.get('task'))
                elif path == '/api/fusion-reprocess':
                    result = store.reprocess_package(data.get('package_id'), task=data.get('task'))
                elif path == '/api/learning-task':
                    result = store.create_learning_task(data.get('claim_id'))
                elif path == '/api/learning-task-state':
                    result = store.set_learning_task(data.get('task_id'), data.get('completed'))
                elif path == '/api/document-revision':
                    result = store.import_revision(data.get('old_material_id'), data.get('text'), data.get('title'), source_name=data.get('source_name','revision.md'))
                elif path == '/api/material-compare':
                    result = store.compare_materials(data.get('old_id'), data.get('new_id'))
                elif path == '/api/refresh-check':
                    result = store.record_refresh(data.get('claim_ids'), data.get('note'))
                elif path == '/api/project-import':
                    result = store.import_project(data.get('text'), data.get('title'))
                elif path == '/api/ask':
                    chat = self._chat() if hasattr(store, '_connect') else store
                    result = chat.ask(data.get('question', ''), material_id=data.get('material_id') or None)
                elif path == '/api/fusion-package':
                    result = store.create_package(data.get('topic'), data.get('material_ids'), data.get('task'))
                elif path == '/api/fusion-import':
                    result = store.import_synthesis(data.get('package_id'), data.get('payload'))
                elif path == '/api/fusion-review':
                    result = store.review_synthesis(data.get('synthesis_id'), data.get('item_id'), data.get('action'), comment=data.get('comment', ''), text=data.get('text'))
                elif path == '/api/external-check':
                    result = store.add_external_check(data.get('claim_id'), data.get('source_uri'), data.get('checked_at'), data.get('result'), limitations=data.get('limitations', ''))
                elif path == '/api/claim':
                    result = store.create_claim(data.get('material_id'), data.get('text'), chunk_id=data.get('chunk_id'))
                elif path == '/api/claims-extract':
                    result = store.extract_claims(data.get('material_id'))
                elif path == '/api/frame-evidence':
                    result = store.add_frame_evidence(data.get('claim_id'), data.get('material_id'), data.get('filename'), data.get('content_base64'), time_seconds=data.get('time_seconds'))
                elif path == '/api/evidence':
                    result = store.add_evidence(data.get('claim_id'), data.get('material_id'), data.get('chunk_id'))
                elif path == '/api/review':
                    result = store.review_claim(data.get('claim_id'), data.get('action'), comment=data.get('comment', ''), text=data.get('text'))
                elif path == '/api/claim-link':
                    result = store.link_claims(data.get('claim_id'), data.get('other_id'), data.get('relation'))
                elif path == '/api/notes':
                    result = store.add_note(data.get('material_id'), data.get('text'), chunk_id=data.get('chunk_id'))
                elif path == '/api/bookmark':
                    result = store.set_bookmark(data.get('material_id'), data.get('chunk_id'), enabled=data.get('enabled', True))
                elif path == '/api/progress':
                    result = store.set_progress(data.get('material_id'), data.get('status'))
                elif path == '/api/tags':
                    result = store.set_tags(data.get('material_id'), data.get('tags'))
                elif path == '/api/study-step':
                    result = store.set_step(data.get('material_id'), data.get('step_id'), data.get('completed'))
                elif path == '/api/import-saved-xagon':
                    from .saved_xagon import read_json
                    if not (APP_ROOT/'XAGON_Saved_Results.json').is_file():
                        raise ValueError('Локальный снимок XAGON не подготовлен. Используйте обычный импорт JSON или инструкцию RC3.')
                    bundle,_ = read_json(APP_ROOT/'XAGON_Saved_Results.json',APP_ROOT)
                    result = store.import_saved_xagon(bundle)
                elif path == '/api/manual-response':
                    result = store.import_manual_response(data.get('package_id'),data.get('text'),mock=data.get('mock',False))
                elif path == '/api/import-existing':
                    result = store.import_file(self.server.existing_transcript, title='Вебинар: агентная аналитика')
                elif path == '/api/import':
                    summary_only = data.get('summary_only', False)
                    if type(summary_only) is not bool:
                        raise ValueError('summary_only должен быть true/false.')
                    name = data.get('name', '')
                    content = data.get('content')
                    if not isinstance(name, str):
                        raise ValueError('Выберите текстовый файл.')
                    # Cross-platform traversal and Windows ADS prevention.
                    device_stem = name.split('.', 1)[0].rstrip(' .').upper()
                    devices = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                               *(f'COM{i}' for i in range(1, 10)),
                               *(f'LPT{i}' for i in range(1, 10))}
                    if (not name or len(name) > 180 or
                            any(ch in name for ch in '/\\:<>"|?*') or
                            any(ord(ch) < 32 or ord(ch) == 127 for ch in name) or
                            name.endswith((' ', '.')) or device_stem in devices or name in ('.', '..')):
                        raise ValueError('Некорректное имя файла.')
                    suffix = Path(name).suffix.lower()
                    if suffix not in ('.txt', '.md', '.json', '.docx', '.pdf'):
                        raise ValueError('Поддерживаются TXT, Markdown, DOCX, текстовый PDF и JSON транскрипции XAGON.')
                    if suffix in ('.docx', '.pdf'):
                        encoded = data.get('content_base64')
                        if not isinstance(encoded, str) or len(encoded) > ((MAX_TEXT_BYTES + 2) // 3) * 4:
                            raise ValueError('Некорректный размер документа.')
                        try:
                            payload = base64.b64decode(encoded, validate=True)
                        except (ValueError, binascii.Error):
                            raise ValueError('Некорректные данные документа.') from None
                    else:
                        if not isinstance(content, str):
                            raise ValueError('Выберите текстовый файл.')
                        payload = content.encode('utf8')
                    if len(payload) > MAX_TEXT_BYTES:
                        raise ValueError('Файл превышает ограничение 16 МиБ.')
                    title = data.get('title') or Path(name).stem
                    if not isinstance(title, str) or len(title) > 300:
                        raise ValueError('Название должно быть короче 300 символов.')
                    with tempfile.TemporaryDirectory(prefix='exacortex_upload_') as temp:
                        # Never use the user-controlled filename for disk I/O.
                        uploaded = Path(temp) / ('payload' + Path(name).suffix.lower())
                        uploaded.write_bytes(payload)
                        result = store.import_file(uploaded, title=title, source_name=name)
                    if summary_only:
                        # The UI opens content through GET /api/material next.
                        # Avoid reflecting a multi-MiB upload in its POST reply.
                        result = {key:value for key,value in result.items() if key != 'chunks'}
                else:
                    self._send(404, {'error': 'Действие не найдено.'})
                    return
            self._send(200, result)
        except Exception as exc:
            self._error(exc)


def create_server(*, port=8765, db_path=None, existing_transcript=None, store=None):
    if store is None:
        from .product_store import ProductStore
        store = ProductStore(Path(db_path) if db_path else learning_directory() / 'learning.sqlite3')
    return AlphaServer(store, port=port, existing_transcript=existing_transcript)


def main(argv=None):
    parser = argparse.ArgumentParser(description='ExaCortex1.0 Release Candidate — offline local application')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    os.environ['XAGON_ALLOW_LIVE_API'] = '0'
    try:
        server = create_server(port=args.port)
    except Exception as exc:
        if isinstance(exc, OSError):
            print('ExaCortex не запущен: порт занят или учебный каталог недоступен. Закройте старое окно приложения либо используйте --port 8766.', flush=True)
        else:
            print('ExaCortex не запущен: проверьте отдельный учебный каталог EXACORTEX_LEARNING_DIR.', flush=True)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/'
    print('ExaCortex 1.0 RC3 — not final; offline, no cloud API', flush=True)
    print(url, flush=True)
    print('Keep this window open. Ctrl+C stops the application.', flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            print('Open the URL above manually in your browser.', flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        print('\nExaCortex stopped.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
