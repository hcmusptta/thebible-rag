"""Read explicitly labelled Scripture text; never infer coordinates from prose."""
import re
import unicodedata
from pathlib import Path
from loaddocs.scripture import BOOKS, ScriptureParser, match_book_title, normalize_scripture_text
from loaddocs.scripture_data import ScriptureData, _resolve_records
from loaddocs.scripture_references import parse_references, _PATTERN, _fold
from loaddocs.scripture_workflow import label_bounds


def _has_glued_markers(text):
    # Verse parts in valid references such as Is 37:9b are not lost verse markers.
    text = _PATTERN.sub(' ', _fold(text))
    return re.search(r'(?<=[^\W\d_).?!;:,])\d{1,3}(?=[^\W\d_])|(?<=[).?!;:,])\d{1,3}(?=[^\W\d_])',text)


def export_scripture_text(data, path):
    """Export only validated verses, checking every coordinate and text on re-read."""
    from loaddocs.source_documents import TextDocument, TextUnit
    data.validate()
    lines = ['Bản văn Kinh Thánh có cấu trúc',
             'Chỉ gồm các câu qua kiểm tra cấu trúc của nguồn; không xác nhận trọn bộ.',
             f'{len(data.quarantined)} bản ghi được tách riêng; xem quarantined.jsonl trong cùng thư mục.', '']
    def body(verse):
        marker = re.match(r'^\(?' + re.escape(verse['verse_label']) + r'\)?(?!\d)',verse['text'])
        if marker is None:
            raise ValueError('Nhãn câu không khớp bản văn nguồn; chưa xuất TXT.')
        return verse['text'][marker.end():].lstrip()
    current = None
    for verse in data.verses:
        key = (verse['book_code'], verse['chapter'])
        if key != current:
            if current is None or key[0] != current[0]:
                lines.extend(['', '# ' + verse['book']])
            lines.extend(['', f'Chương {verse["chapter"]}'])
            current = key
        lines.append(verse['verse_label'] + ' ' + body(verse))
    # Standardize marker spacing/parentheses, preserving coordinates and every word.
    document = TextDocument([TextUnit(line, line.startswith('# ')) for line in lines], 'txt')
    checked = parse_scripture_text(document, Path(path).name)
    checked.validate()
    def records(verses):
        return [(v['book_code'],v['chapter'],v['verse_label'],
                 unicodedata.normalize('NFC',' '.join(body(v).split()))) for v in verses]
    if records(checked.verses) != records(data.verses):
        raise ValueError('Bản TXT xuất ra không giữ nguyên toàn bộ tọa độ và bản văn; chưa ghi file.')
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(checked.verses)


def parse_scripture_text(document, source, *, check_cancel=lambda: None, progress=None):
    data = ScriptureData()
    book = ScriptureParser(source).current_book
    chapter = None
    heading = heading_id = ''
    pending = None
    previous = {}
    books = {b.code:b for b in BOOKS}
    book_headings = document.book_headings
    labelled_chapters = any(re.match(r'^(?:chuong|thanh vinh) \d+\b',normalize_scripture_text(line))
                            for unit in document if unit.part == 'body' for line in unit.text.splitlines())

    def finish():
        nonlocal pending
        if pending is not None:
            pending['raw_text'] = pending['text'].rstrip()
            pending['display_text'] = unicodedata.normalize('NFC',pending['raw_text'])
            pending['text'] = unicodedata.normalize('NFC',' '.join(pending['text'].split()))
            data.verses.append(pending)
            pending = None

    for number, unit in enumerate(document,1):
        check_cancel()
        block = f'{document.location_unit}:{number}'
        data.raw_blocks.append({'id':block,'source':source,'page':number,'text':unit.raw_text or unit.text,
                                'source_part':unit.part,'location_unit':document.location_unit})
        if unit.part != 'body':
            finish()
            if unit.text.strip():
                data.supplementary.append({'type':unit.part,'source':source,'page':number,'text':unit.text,'provenance':block})
            continue
        for line_index,raw in enumerate(unit.text.splitlines()):
            text = raw.strip().lstrip('# ').strip()
            if not text:
                continue
            candidate = book_headings.get((number,line_index))
            if candidate:
                finish()
                book,chapter = candidate,None
                heading,heading_id = text,block
                continue
            normalized = normalize_scripture_text(text)
            if normalized in {'muc luc','muic luic'} or re.fullmatch(r'trang(?: goc)? \d+',normalized):
                data.supplementary.append({'type':'page_marker','source':source,'page':number,'text':text,'provenance':block})
                continue
            cm = re.fullmatch(r'chuong (\d+)',normalized) or (re.match(r'^chuong (\d+)\b',normalized) if unit.heading else None)
            if book and book.code == 'PSA':
                cm = re.match(r'^thanh vinh (\d+)(?:\b|$)',normalized) or re.fullmatch(r'(\d{1,3})\s*\(\d{1,3}[^)]*\)',text) or cm
            if cm:
                finish()
                chapter = int(cm[1])
                if book is None:
                    data.issues.append({'severity':'error','kind':'unknown_book_for_chapter',
                        'chapter':chapter,'page':number,'detail':'Có chương nhưng chưa xác nhận được tên sách; cần tiêu đề sách rõ.'})
                heading,heading_id = text,block
                if book and book.code == 'PSA' and '(' in text:
                    data.supplementary.append({'type':'numbering_heading','book_code':book.code,'chapter':chapter,'page':number,'text':text,'provenance':block})
                continue
            explicit = re.match(r'^(.+?\s+\d{1,3}\s*[:,]\s*\d{1,3}[a-zđ+]*(?:[-–]\d{1,3}[a-zđ+]*)?)\s+(.+)$',text)
            label = body = None
            if explicit and _PATTERN.fullmatch(_fold(explicit[1])):
                refs = parse_references(explicit[1])
                if len(refs) == 1 and refs[0].start:
                    ref = refs[0]
                    if labelled_chapters and (book is None or (book.code,chapter) != (ref.book,ref.chapter)):
                        data.supplementary.append({'type':'quoted_reference','source':source,'page':number,
                            'text':text,'references':[ref.display()],'provenance':block})
                        continue
                    if book is None or (book.code,chapter) != (ref.book,ref.chapter):
                        finish()
                        heading,heading_id = '',f'{ref.book}:{ref.chapter}'
                    book,chapter = books[ref.book],ref.chapter
                    label = ref.start + ('-'+ref.end if ref.end and ref.end != ref.start else '')
                    body = explicit[2]
            if label is None and book and chapter:
                vm = re.match(r'^\(?(\d{1,3}[a-zđ+]*(?:[-–]\d{1,3}[a-zđ+]*)?)\)?[.)]?\s+(.*)$',text)
                if vm:
                    label,body = vm.groups()
            if label is not None:
                finish()
                lower,upper = label_bounds(label)
                key = (book.code,chapter)
                last = previous.get(key,0)
                if lower[0] > last+1:
                    data.issues.append({'severity':'warning','kind':'verse_gap','book':book.code,'chapter':chapter,'after':last,'before':lower[0],'page':number})
                previous[key] = upper[0]
                if book.code not in data.books:
                    data.books.append(book.code)
                pending = {'source':source,'book_code':book.code,'book':book.name,'testament':book.testament,
                    'chapter':chapter,'verse':lower[0],'verse_end':upper[0],'verse_label':label,
                    'page':number,'page_end':number,'heading':heading,'heading_id':heading_id,
                    'location_unit':document.location_unit,'source_format':document.source_format,
                    'paragraph_id':heading_id or f'{book.code}:{chapter}',
                    'provenance':[block],'text':label+' '+body}
                if _has_glued_markers(body):
                    data.issues.append({'severity':'error','kind':'text_needs_review','book':book.code,
                        'chapter':chapter,'page':number,'detail':'Số chen giữa chữ; có thể đã mất định dạng nhãn câu hoặc chú thích.'})
            elif unit.heading:
                finish()
                if book is not None and re.match(r'^(?:sach|tin mung|thu)\b',normalized) and not match_book_title(text):
                    data.issues.append({'severity':'error','kind':'unknown_book_title','title':text,'page':number})
                    book,chapter = None,None
                heading,heading_id = text,block
                data.supplementary.append({'type':'heading','source':source,'page':number,'text':text,'provenance':block})
            elif text.startswith('(') and text.endswith(')') and book and chapter:
                refs = parse_references(text)
                data.supplementary.append({'type':'cross_reference' if refs else 'note','source':source,
                    'page':number,'text':text,'provenance':block,'book_code':book.code,'chapter':chapter,
                    'heading_id':heading_id,'references':[r.display() for r in refs]})
            elif re.match(r'^(?:chu thich|ghi chu)\b',normalized):
                finish()
                data.supplementary.append({'type':'note','source':source,'page':number,'text':text,'provenance':block})
            elif pending is not None:
                if _has_glued_markers(text):
                    data.issues.append({'severity':'error','kind':'text_needs_review','book':book.code,
                        'chapter':chapter,'page':number,'detail':'Số chen giữa chữ; cần đối chiếu nhãn câu/chú thích.'})
                pending['text'] += '\n'+text
                pending['page_end'] = number
                pending['provenance'].append(block)
            else:
                data.supplementary.append({'type':'introduction','source':source,'page':number,'text':text,'provenance':block})
        if progress:
            progress(number,len(document))
    finish()
    if not data.verses:
        raise ValueError('Kinh Thánh Word/TXT cần tên sách, dòng Chương N và nhãn câu, hoặc tọa độ rõ như Ga 3:16 kèm bản văn; không đoán số câu từ văn xuôi.')
    _resolve_records(data)
    return data
