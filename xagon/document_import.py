"""Local document text extraction. Never follows links or runs office software."""
from __future__ import annotations

import io
from pathlib import Path
import zipfile
from xml.etree import ElementTree

MAX_INPUT = 16 * 1024 * 1024
MAX_TEXT = 8 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_PAGES = 200
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _read(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Файл документа не найден')
    if path.stat().st_size > MAX_INPUT:
        raise ValueError('Документ превышает 16 МиБ')
    with path.open('rb') as stream:
        data = stream.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError('Документ превышает 16 МиБ')
    return data


def _checked(text):
    if not text.strip():
        raise ValueError('Текст не найден. Для сканированного документа требуется OCR; он не запускается.')
    if '\x00' in text or len(text.encode('utf8')) > MAX_TEXT:
        raise ValueError('Извлечённый текст слишком велик или содержит недопустимые символы')
    return text.strip()


def extract_docx(path):
    """Read only main-body paragraphs/tables; ignore images, links and macros."""
    raw = _read(path)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 1000 or len(names) != len(set(names)):
                raise ValueError('DOCX содержит слишком много или повторяющиеся записи')
            if sum(item.file_size for item in entries) > 64 * 1024 * 1024:
                raise ValueError('Распакованный DOCX превышает безопасный размер')
            if any(item.flag_bits & 1 for item in entries):
                raise ValueError('Зашифрованный DOCX не поддерживается')
            info = archive.getinfo('word/document.xml')
            if info.file_size > MAX_XML or info.file_size > max(info.compress_size, 1) * 200:
                raise ValueError('Содержимое DOCX превышает безопасный размер/степень сжатия')
            with archive.open(info) as stream:
                xml = stream.read(MAX_XML + 1)
            if len(xml) > MAX_XML:
                raise ValueError('XML DOCX слишком велик')
        # UTF-8 only prevents alternate encodings hiding DTD/entity declarations.
        decoded = xml.decode('utf-8-sig')
        if '\x00' in decoded or '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
            raise ValueError('DTD и XML entities в DOCX запрещены')
        root = ElementTree.fromstring(decoded)
        if root.tag != W + 'document':
            raise ValueError('DOCX не содержит основной Word-документ')
        paragraphs = []
        for paragraph in root.iter(W + 'p'):
            parts = []
            for node in paragraph.iter():
                if node.tag == W + 't':
                    parts.append(node.text or '')
                elif node.tag == W + 'tab':
                    parts.append('\t')
                elif node.tag in (W + 'br', W + 'cr'):
                    parts.append('\n')
            if parts:
                paragraphs.append(''.join(parts))
        return _checked('\n\n'.join(paragraphs))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError('Некорректный или неподдерживаемый DOCX') from exc


def extract_pdf(path):
    """Optional installed pypdf; text only, no OCR or external resources."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError('Импорт PDF недоступен: pypdf не установлен. Сохраните текст как TXT/Markdown; установка не выполняется.') from exc
    data = _read(path)
    if not data.startswith(b'%PDF-'):
        raise ValueError('Некорректный PDF')
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError('Зашифрованный PDF не поддерживается')
        if len(reader.pages) > MAX_PAGES:
            raise ValueError('PDF превышает лимит 200 страниц')
        pieces, total = [], 0
        for page in reader.pages:
            text = page.extract_text() or ''
            total += len(text.encode('utf8'))
            if total > MAX_TEXT:
                raise ValueError('Извлечённый PDF-текст превышает 8 МиБ')
            pieces.append(text)
        return _checked('\n\n'.join(pieces))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не удалось извлечь текст PDF. Сохраните текст как TXT/Markdown.') from exc


def extract_document(path):
    suffix = Path(path).suffix.casefold()
    if suffix == '.docx':
        return extract_docx(path)
    if suffix == '.pdf':
        return extract_pdf(path)
    raise ValueError('Поддерживаются DOCX и текстовый PDF')
