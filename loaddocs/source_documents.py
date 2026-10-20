"""Read text sources without inventing physical pagination."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
import re
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import fitz

SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}
UPLOAD_ACCEPT = {'application/pdf':['.pdf'],
                 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':['.docx'],
                 'application/msword':['.doc'], 'text/plain':['.txt']}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


@dataclass
class TextUnit:
    text: str
    heading: bool = False
    part: str = 'body'
    note_id: str = ''
    raw_text: str = ''

    def get_text(self):
        return self.text


@dataclass
class TextDocument:
    units: list
    source_format: str
    metadata: dict = field(default_factory=dict)

    @property
    def location_unit(self):
        return 'line' if self.source_format == 'txt' else 'paragraph'

    def __len__(self):
        return len(self.units)

    def __getitem__(self, index):
        return self.units[index]

    @property
    def cover_text(self):
        return '\n'.join(u.text for u in self.units[:8] if u.part == 'body')[:2500]

    def __iter__(self):
        return iter(self.units)

    @cached_property
    def book_headings(self):
        from loaddocs.scripture import match_book_title, normalize_scripture_text
        lines=[(n,i,u,line.strip().lstrip('# ').strip()) for n,u in enumerate(self.units,1)
               for i,line in enumerate(u.text.splitlines())]
        result={}
        for position,(n,i,u,text) in enumerate(lines):
            book=match_book_title(text) if u.part == 'body' else None
            if not book:
                continue
            confirmed=u.heading
            for _,_,next_unit,next_text in lines[position+1:position+25] if not confirmed else []:
                normalized=normalize_scripture_text(next_text)
                if next_unit.part != 'body':
                    break
                if (re.fullmatch(r'chuong \d+',normalized) or
                    (next_unit.heading and re.match(r'^chuong \d+\b',normalized)) or
                    (book.code == 'PSA' and re.match(r'^thanh vinh \d+\b',normalized))):
                    confirmed=True
                    break
                if re.match(r'^\(?\d{1,3}[a-zđ+]*(?:[-–]\d{1,3}[a-zđ+]*)?\)?[.)]?\s',next_text):
                    break
            if confirmed:
                result[(n,i)]=book
        return result

    def get_toc(self):
        return [[1,book.name,n] for (n,_),book in self.book_headings.items()]


def _read_xml(archive, name, required=False):
    if name not in archive.namelist():
        if required:
            raise ValueError(f'DOCX thiếu {name}.')
        return None
    raw = archive.read(name)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DOCX có khai báo XML không được hỗ trợ.')
    return ET.fromstring(raw)


def _paragraph_text(paragraph):
    text, raw = [], []
    for run in paragraph.iter(W+'r'):
        value = ''.join((node.text or '') if node.tag == W+'t' else
                        '\t' if node.tag == W+'tab' else '\n' if node.tag in (W+'br',W+'cr') else ''
                        for node in run.iter())
        raw.append(value)
        vert = run.find(W+'rPr/'+W+'vertAlign')
        if vert is not None and vert.get(W+'val') == 'superscript' and re.fullmatch(r'\d{1,3}[a-zđ+]*(?:[-–]\d{1,3}[a-zđ+]*)?',value.strip()):
            value = '\n' + value.strip() + ' '
        text.append(value)
    return ''.join(text).strip(), ''.join(raw).strip()


def read_docx(path, check_cancel=lambda: None):
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('Nội dung giải nén DOCX vượt 100 MB.')
            root = _read_xml(archive,'word/document.xml',required=True)
            styles = _read_xml(archive,'word/styles.xml')
            style_map = {s.get(W+'styleId'):s for s in styles or []}
            numbering = _read_xml(archive,'word/numbering.xml')
            abstract = {n.get(W+'abstractNumId'):n for n in numbering or [] if n.tag == W+'abstractNum'}
            nums = {n.get(W+'numId'):n for n in numbering or [] if n.tag == W+'num'}
            counters = {}
            units = []
            parts = [('word/document.xml',root,'body')]
            for name in ('footnotes','endnotes'):
                note_root = _read_xml(archive,f'word/{name}.xml')
                if note_root is not None:
                    parts.append((f'word/{name}.xml',note_root,name))
            for part_name, part, kind in parts:
                paragraphs = [(p,'') for p in part.iter(W+'p')] if kind == 'body' else [
                    (p,note.get(W+'id','')) for note in part if int(note.get(W+'id','0'))>0 for p in note.iter(W+'p')]
                for paragraph,note_id in paragraphs:
                    check_cancel()
                    text, raw = _paragraph_text(paragraph)
                    ppr = paragraph.find(W+'pPr')
                    style_node = ppr.find(W+'pStyle') if ppr is not None else None
                    style_id = style_node.get(W+'val','') if style_node is not None else ''
                    style = style_map.get(style_id)
                    outline = ppr.find(W+'outlineLvl') if ppr is not None else None
                    if outline is None and style is not None:
                        outline = style.find(W+'pPr/'+W+'outlineLvl')
                    heading = outline is not None or bool(re.match(r'^(?:Heading|Title|Tiêu_?đề)\d*$',style_id,re.I))
                    numpr = ppr.find(W+'numPr') if ppr is not None else None
                    if numpr is None and style is not None:
                        numpr = style.find(W+'pPr/'+W+'numPr')
                    if numpr is not None:
                        num_id = numpr.find(W+'numId')
                        level = numpr.find(W+'ilvl')
                        num_id = num_id.get(W+'val') if num_id is not None else None
                        level = level.get(W+'val','0') if level is not None else '0'
                        num = nums.get(num_id)
                        aid = num.find(W+'abstractNumId') if num is not None else None
                        template = abstract.get(aid.get(W+'val')) if aid is not None else None
                        lvl = next((x for x in template or [] if x.get(W+'ilvl') == level),None)
                        fmt = lvl.find(W+'numFmt') if lvl is not None else None
                        label_node = lvl.find(W+'lvlText') if lvl is not None else None
                        label_template = label_node.get(W+'val','%1.') if label_node is not None else '%1.'
                        # Only render list labels whose complete numbering is known.
                        if fmt is not None and fmt.get(W+'val') == 'decimal' and level == '0' and set(re.findall(r'%\d+',label_template)) <= {'%1'}:
                            start = lvl.find(W+'start')
                            override = num.find(W+'lvlOverride'+f'[@{W}ilvl="{level}"]')
                            start_override = override.find(W+'startOverride') if override is not None else None
                            start = int((start_override if start_override is not None else start).get(W+'val','1')) if start_override is not None or start is not None else 1
                            key = (num_id,level)
                            counters[key] = counters.get(key,start-1)+1
                            text = label_template.replace('%1',str(counters[key]))+' '+text
                    units.append(TextUnit(text,heading,kind,note_id,raw_text=raw))
                    if len(units)>200000:
                        raise ValueError('Tài liệu vượt giới hạn 200.000 đoạn.')
            props = _read_xml(archive,'docProps/core.xml')
            metadata = {}
            if props is not None:
                dc = '{http://purl.org/dc/elements/1.1/}'
                metadata = {'title':props.findtext(dc+'title') or '', 'author':props.findtext(dc+'creator') or ''}
            return TextDocument(units,'docx',metadata)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as error:
        raise ValueError('Không đọc được cấu trúc DOCX; hãy kiểm tra file hoặc mật khẩu bảo vệ.') from error


def read_doc(path, check_cancel=lambda: None):
    node = shutil.which('node')
    if not node:
        raise ValueError('Đọc DOC cần Node.js và bộ đọc DOC của project; hãy cài theo hướng dẫn hoặc lưu thành DOCX.')
    script = Path(__file__).with_name('doc_reader')/'read.cjs'
    try:
        result = subprocess.run([node,'--max-old-space-size=256',str(script),str(path.resolve())],
            capture_output=True,text=True,encoding='utf-8',timeout=60,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            if 'Cannot find module' in result.stderr:
                raise ValueError('Chưa cài bộ đọc DOC; chạy npm ci trong loaddocs/doc_reader theo hướng dẫn.')
            raise ValueError('Không đọc được DOC; file có thể có mật khẩu hoặc cấu trúc không được hỗ trợ. Hãy lưu lại thành DOCX.')
        parts = json.loads(result.stdout)
    except (subprocess.TimeoutExpired,UnicodeError,json.JSONDecodeError) as error:
        raise ValueError('Bộ đọc DOC không trả được văn bản hợp lệ trong 60 giây; hãy kiểm tra file hoặc lưu thành DOCX.') from error
    units = []
    for kind,text in parts.items():
        for line in text.splitlines():
            check_cancel()
            units.append(TextUnit(line,part=kind))
    if len(units)>200000:
        raise ValueError('DOC vượt giới hạn 200.000 đoạn.')
    return TextDocument(units,'doc')


@contextmanager
def open_source(path, source_name, check_cancel=lambda: None):
    path = Path(path)
    ext = Path(source_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError('Chỉ hỗ trợ PDF, DOCX, DOC và TXT.')
    check_cancel()
    if ext == '.pdf':
        with fitz.open(path) as document:
            yield document
        return
    if ext == '.doc':
        with path.open('rb') as handle:
            signature=handle.read(8)
        if signature != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise ValueError('File DOC không có định dạng Word nhị phân hợp lệ; hãy lưu đúng DOC hoặc DOCX.')
        document = read_doc(path,check_cancel)
    elif ext == '.docx':
        document = read_docx(path,check_cancel)
    else:
        raw = path.read_bytes()
        encoding = 'utf-32' if raw.startswith((b'\xff\xfe\x00\x00',b'\x00\x00\xfe\xff')) else 'utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        try:
            text = raw.decode(encoding)
        except UnicodeError as error:
            raise ValueError('TXT cần lưu dạng Unicode UTF-8 hoặc UTF-16 có BOM; không tự đoán bảng mã cũ.') from error
        if '\x00' in text or '\ufffd' in text:
            raise ValueError('TXT có ký tự lỗi hoặc dữ liệu nhị phân; hãy lưu lại dưới dạng UTF-8.')
        if len(text)>20000000 or text.count('\n')>200000:
            raise ValueError('TXT vượt giới hạn 20 triệu ký tự hoặc 200.000 dòng.')
        document = TextDocument([TextUnit(line, bool(re.match(r'^#{1,6}\s',line))) for line in text.splitlines()],'txt')
    if not any(u.text.strip() for u in document):
        raise ValueError('Tài liệu không có văn bản đọc được; tài liệu chỉ chứa ảnh cần OCR trước.')
    yield document


def iter_text_sections(document, kind, check_cancel=lambda: None):
    heading = number = ''
    parts = []
    start = end = 1
    for index, unit in enumerate(document,1):
        check_cancel()
        text = unit.text.strip()
        if not text:
            continue
        section = re.match(r'^(\d{1,4})[.)]\s*',text) if kind == 'catechism' else None
        boundary = section or unit.heading or unit.part != 'body'
        if boundary and parts:
            yield {'text':'\n\n'.join(parts),'heading':heading,'section_number':number,'page':start,'page_end':end}
            parts = []
        if unit.part != 'body':
            yield {'text':text,'heading':f'{unit.part} {unit.note_id}'.strip(),'section_number':'','page':index,'page_end':index}
            continue
        if section:
            number = section[1]
        elif unit.heading:
            heading,number = text.lstrip('# ').strip(),''
        if not parts:
            start = index
        parts.append(text)
        end = index
    if parts:
        yield {'text':'\n\n'.join(parts),'heading':heading,'section_number':number,'page':start,'page_end':end}
