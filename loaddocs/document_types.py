"""Document classification and section metadata; no model calls."""
import re
from pathlib import Path
from loaddocs.scripture import ScriptureParser, normalize_scripture_text, extract_scripture_query_filters, match_book_title

DOCUMENT_LABELS = {"scripture": "Bản văn Kinh Thánh", "commentary": "Chú giải", "catechism": "Giáo lý", "generic": "Tài liệu khác"}
CLASSIFICATION_VERSION = 1


def document_type_filter(kind):
    if kind in (None, "all", "auto"):
        return {}
    if kind not in DOCUMENT_LABELS:
        raise ValueError("Loại tài liệu không hợp lệ.")
    return {"document_type": {"$in": ["scripture", "bible"]}} if kind == "scripture" else {"document_type": kind}


def detect_document_type(document, source, requested=None):
    if requested not in (None, "auto"):
        document_type_filter(requested)
        return requested
    # Title/cover only: a quotation in the body must not classify the whole PDF.
    filename = re.sub(r'([a-z])([A-Z])', r'\1 \2', Path(source).stem)
    cover = getattr(document,'cover_text',None)
    title = normalize_scripture_text(filename + " " + (cover if cover is not None else document[0].get_text()[:2500] if len(document) else ""))
    if re.search(r"\b(giao ly|catechism|catechisme)\b", title):
        return "catechism"
    if re.search(r"\b(chu giai|giai thich|commentary|commentaries|exegesis)\b", title):
        return "commentary"
    for level, heading, page in document.get_toc():
        if level == 1 and match_book_title(heading):
            return "scripture"
    return "scripture" if ScriptureParser(source).enabled else "generic"


def document_metadata(document, source, kind, supplied=None):
    supplied = supplied or {}
    allowed = {"title", "author", "translation", "topic"}
    if set(supplied) - allowed:
        raise ValueError("Metadata chỉ nhận title, author, translation, topic.")
    values = {}
    for key in allowed:
        value = supplied.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError(f"Metadata {key} phải là văn bản tối đa 500 ký tự.")
        values[key] = " ".join(value.split())
    pdf_meta = document.metadata or {}
    author_field = 'file_author' if hasattr(document,'source_format') else 'pdf_author'
    return {"document_type": kind, "title": values["title"] or pdf_meta.get("title") or Path(source).stem,
            "author": values["author"], author_field: pdf_meta.get("author") or "",
            "translation": values["translation"], "topic": values["topic"],
            "classification_version": CLASSIFICATION_VERSION}


def iter_prose_sections(document, kind, check_cancel=lambda: None):
    """Keep heading and paragraph boundaries across pages, preserving page spans."""
    if hasattr(document,'source_format'):
        from loaddocs.source_documents import iter_text_sections
        yield from iter_text_sections(document,kind,check_cancel)
        return
    toc = {}
    for level, title, page in document.get_toc():
        toc.setdefault(page, []).append(title)
    heading = ""
    number = ""
    parts = []
    start = end = 1
    for page_index, page in enumerate(document, 1):
        check_cancel()
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block:
                continue
            lines = block["lines"]
            section_in_block = False
            for line_index, line in enumerate(lines):
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                standalone_number = bool(re.fullmatch(r"\d{1,4}", text))
                page_number = text == str(page_index) and line['bbox'][1] > page.rect.height * 0.85
                if standalone_number and (kind != "catechism" or page_number or line["bbox"][1] < 50 or line["bbox"][3] > page.rect.height - 50):
                    continue
                section = re.match(r"^(\d{1,4})(?:[.)]\s*|\s+(?=[^\W\d_]))", text) if kind == "catechism" else None
                # Accented glyphs may use a fallback font without the bold flag.
                bold_chars = sum(len(s["text"].strip()) for s in spans if s.get("flags", 0) & 16)
                bold = bold_chars >= max(1, sum(len(s["text"].strip()) for s in spans) / 2)
                is_heading = len(text) < 180 and not section_in_block and (text in toc.get(page_index, []) or (bold and not section))
                if section:
                    section_in_block = True
                if is_heading or section:
                    if parts:
                        yield {"text": " ".join(parts), "heading": heading, "section_number": number, "page": start, "page_end": end}
                        parts = []
                    if is_heading:
                        heading = text
                        number = ""
                    else:
                        number = section.group(1)
                if not parts:
                    start = page_index
                parts.append(text)
                if line_index == len(lines) - 1:
                    parts.append("\n\n")
                end = page_index
    if parts:
        yield {"text": " ".join(parts), "heading": heading, "section_number": number, "page": start, "page_end": end}


def scripture_references(text):
    pattern = r"\b(?:[1-3]\s*)?[^\W\d_]+(?:[- ][^\W\d_]+){0,3}\s+\d{1,3}\s*[:,]\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?"
    references = []
    for match in re.finditer(pattern, text, re.UNICODE):
        ref = match.group(0)
        try:
            valid = extract_scripture_query_filters(ref)
        except ValueError:
            # Uncertain prose references stay in the original text; do not guess coordinates.
            continue
        if valid:
            references.append(ref)
    return "; ".join(dict.fromkeys(references))


def source_location(metadata):
    start = metadata.get('page','không rõ')
    end = metadata.get('page_end',start)
    label = {'line':'Dòng','paragraph':'Đoạn'}.get(metadata.get('location_unit'),'Trang')
    return f'{label}: {start}-{end}' if end != start else f'{label}: {start}'


def source_details(metadata):
    kind = metadata.get("document_type", "generic")
    kind = "scripture" if kind == "bible" else kind
    fields = [("Loại", DOCUMENT_LABELS.get(kind, kind)), ("Tên tài liệu", metadata.get("title"))]
    if metadata.get('source_format'):
        fields += [('Định dạng',metadata['source_format'].upper()),('Vị trí nguồn',source_location(metadata))]
    if kind == "scripture":
        fields += [("Bản dịch", metadata.get("translation") or "Chưa xác định")]
    elif kind == "commentary":
        fields += [("Tác giả", metadata.get("author") or "Chưa xác định"),
                   ("Tham chiếu Kinh Thánh", metadata.get("scripture_references"))]
    elif kind == "catechism":
        fields += [("Số mục", metadata.get("section_number")), ("Chủ đề", metadata.get("topic"))]
    fields += [("Đề mục", metadata.get("heading"))]
    return [f"{label}: {value}" for label, value in fields if value]
