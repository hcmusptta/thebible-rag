"""Read bookmarked Scripture PDFs without treating page breaks as verse breaks."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import json
import re
import unicodedata

from loaddocs.scripture import ScriptureParser, normalize_scripture_text, match_book_title

SCRIPTURE_PROCESSING_VERSION = 4


_book_title = match_book_title


@dataclass
class ScriptureData:
    verses: list = field(default_factory=list)
    supplementary: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    books: list = field(default_factory=list)
    quarantined: list = field(default_factory=list)
    raw_blocks: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    passages: list = field(default_factory=list)
    relations: list = field(default_factory=list)

    def select(self, chapters):
        if not chapters:
            return self
        available = {(v["book_code"], v["chapter"]) for v in self.verses + self.quarantined}
        if not chapters <= available:
            raise ValueError(f"Không tìm thấy chương: {sorted(chapters - available)}")
        return ScriptureData(
            verses=[v for v in self.verses if (v["book_code"], v["chapter"]) in chapters],
            supplementary=self.supplementary,
            quarantined=[v for v in self.quarantined if (v["book_code"], v["chapter"]) in chapters],
            issues=[i for i in self.issues if (i.get("book"), i.get("chapter")) in chapters
                    or (i.get("book") in {b for b, _ in chapters} and i.get("chapter") is None)],
            books=[b for b in self.books if b in {code for code, _ in chapters}],
            raw_blocks=self.raw_blocks, profile={**self.profile, "complete": False},
            passages=[], relations=self.relations,
        )

    def validate(self):
        errors = [i for i in self.issues if i["severity"] == "error" and i.get("disposition") != "quarantined"]
        if not self.verses:
            raise ValueError("Không còn câu Kinh Thánh đủ tin cậy để nạp; xem report.json và quarantined.jsonl.")
        if errors:
            raise ValueError(f"Chưa xác định được cấu trúc Kinh Thánh: {len(errors)} lỗi; xem report.json. Không tạo embedding cho dữ liệu này.")

    def export(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for name, rows in (("verses", self.verses), ("supplementary", self.supplementary), ("quarantined", self.quarantined),
                           ("raw_blocks", self.raw_blocks), ("passages", self.passages), ("relations", self.relations)):
            with (directory / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        report = {"processing_version": SCRIPTURE_PROCESSING_VERSION,
                  "profile": self.profile,
                  "books": self.books, "verse_records": len(self.verses),
                  "issues": self.issues,
                  "quarantined_records": len(self.quarantined),
                  "quarantined_chapters": sorted({f"{v['book_code']}:{v['chapter']}" for v in self.quarantined
                                                   if v.get("quarantine_reason") == "conflicting_chapter"}),
                  "cross_page_verses": sum(v["page_end"] > v["page"] for v in self.verses)}
        location_unit = next((row.get('location_unit') for row in self.raw_blocks if row.get('location_unit')), 'page')
        if location_unit != 'page':
            report['location_unit'] = location_unit
            report['cross_unit_verses'] = report.pop('cross_page_verses')
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "profile.json").write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_records(data):
    """Deduplicate exact copies; keep conflicting chapters out of the searchable corpus."""
    unique = {}
    conflicts = set()
    unsafe_books = set()
    for issue in data.issues:
        if issue["severity"] == "error" and issue["kind"] in {"note_needs_review", "ocr_needs_review", "ocr_unavailable", "text_needs_review"}:
            if issue.get("chapter") is not None:
                conflicts.add((issue["book"], issue["chapter"]))
            else:
                unsafe_books.add(issue["book"])
            issue["disposition"] = "quarantined"
    conflicts.update((v["book_code"], v["chapter"]) for v in data.verses if v["book_code"] in unsafe_books)
    for verse in data.verses:
        if not re.search(r"[^\W\d_]", verse["text"], re.UNICODE):
            data.quarantined.append({**verse, "quarantine_reason": "empty_verse_label"})
            continue
        key = (verse["book_code"], verse["chapter"], verse["verse_label"])
        previous = unique.get(key)
        if previous is None:
            unique[key] = verse
        elif previous["text"] == verse["text"]:
            data.supplementary.append({**verse, "type": "duplicate_verse", "original_page": previous["page"]})
            data.issues.append({"severity": "warning", "kind": "identical_duplicate",
                                "book": key[0], "chapter": key[1], "verse": key[2],
                                "page": verse["page"], "original_page": previous["page"]})
        else:
            conflicts.add(key[:2])
            data.issues.append({"severity": "error", "kind": "conflicting_verse", "disposition": "quarantined",
                                "book": key[0], "chapter": key[1], "verse": key[2],
                                "page": verse["page"], "original_page": previous["page"]})
    # A missing chapter heading can mislabel subsequent, non-duplicate verses too.
    data.quarantined.extend({**verse, "quarantine_reason": "conflicting_chapter"}
                            for verse in data.verses
                            if (verse["book_code"], verse["chapter"]) in conflicts
                            and re.search(r"[^\W\d_]", verse["text"], re.UNICODE))
    data.verses = [verse for key, verse in unique.items() if key[:2] not in conflicts]


def parse_scripture_pdf(document, source, *, check_cancel=lambda: None, progress=None, document_type=None):
    from loaddocs.document_types import detect_document_type
    if detect_document_type(document, source, document_type) != "scripture":
        return None
    toc = document.get_toc()
    boundaries = sorted({(page - 1, book.code): book
                         for level, title, page in toc
                         if level == 1 and (book := _book_title(title))
                         and 1 <= page <= len(document)}.items())
    has_book_bookmarks = bool(boundaries)
    if not boundaries:
        parser = ScriptureParser(source)
        if parser.current_book:
            boundaries = [((0, parser.current_book.code), parser.current_book)]
        elif parser.enabled or document_type == "scripture":
            raise ValueError("PDF Kinh Thánh cần bookmark từng sách hoặc tên file xác định một sách; chưa thể đoán cấu trúc an toàn. Nếu đây là giáo lý hoặc chú giải, hãy chọn đúng loại tài liệu hoặc Tự động nhận diện trong Cài đặt.")
        else:
            return None
    if len({book.code for _, book in boundaries}) != len(boundaries):
        raise ValueError("Bookmark lặp sách ở nhiều vị trí; cần kiểm tra PDF trước khi nạp.")

    data = ScriptureData(books=[book.code for _, book in boundaries])
    top_level = sorted({page - 1 for level, title, page in toc
                        if has_book_bookmarks and level == 1 and 1 <= page <= len(document)})
    # An unrecognized book must never be absorbed into the preceding book.
    for level, title, page in toc:
        if has_book_bookmarks and level == 1 and page - 1 >= boundaries[0][0][0] and not _book_title(title):
            if re.match(r"^\s*\d+\.", title):
                data.issues.append({"severity": "error", "kind": "unknown_book_title",
                                    "title": title, "page": page})

    first_page = boundaries[0][0][0]
    for page in range(first_page):
        check_cancel()
        text = document[page].get_text().strip()
        if text:
            data.supplementary.append({"source": source, "page": page + 1,
                                       "type": "front_matter", "text": text})

    for index, ((start, _), book) in enumerate(boundaries):
        stop = next((page for page in top_level if page > start), len(document))
        chapter = None
        pending = None
        chapter_numbers = []
        previous_number = 0
        heading = ""
        heading_id = ""

        def finish():
            nonlocal pending
            if pending is not None:
                pending["raw_text"] = pending["text"].rstrip()
                pending["display_text"] = unicodedata.normalize("NFC", pending["raw_text"])
                pending["text"] = unicodedata.normalize("NFC", " ".join(pending["text"].split()))
                if not re.search(r"[^\W\d_]", pending["text"], re.UNICODE):
                    data.issues.append({"severity": "warning", "kind": "empty_verse_label",
                                        "book": book.code, "chapter": chapter, "verse": pending["verse_label"], "page": pending["page"]})
                data.verses.append(pending)
                pending = None

        for page_index in range(start, stop):
            check_cancel()
            page = document[page_index]
            lines = [{**line, "paragraph_id": f"p{page_index + 1}:b{block_index}"}
                     for block_index, block in enumerate(page.get_text("dict")["blocks"])
                     for line in block.get("lines", [])]
            if not any(s["text"].strip() for line in lines for s in line["spans"]) and page.get_images():
                try:
                    textpage = page.get_textpage_ocr(language="vie+eng", full=True)
                    lines = [line for block in page.get_text("dict", textpage=textpage)["blocks"] for line in block.get("lines", [])]
                    data.issues.append({"severity": "error", "kind": "ocr_needs_review", "book": book.code, "page": page_index + 1})
                except Exception:
                    data.issues.append({"severity": "error", "kind": "ocr_unavailable", "book": book.code, "page": page_index + 1})
            sizes = Counter()
            for line in lines:
                for span in line["spans"]:
                    if not span["flags"] & 16:
                        sizes[round(span["size"], 1)] += len(span["text"].strip())
            body_size = sizes.most_common(1)[0][0] if sizes else 14.4
            for line_index, line in enumerate(lines):
                spans = line["spans"]
                text = "".join(span["text"] for span in spans).strip()
                if not text:
                    continue
                block_id = f"p{page_index + 1}:l{line_index}"
                data.raw_blocks.append({"id": block_id, "source": source, "page": page_index + 1,
                                        "bbox": list(line["bbox"]), "text": text,
                                        "spans": [{"text": s["text"], "bbox": list(s["bbox"]), "font": s["font"], "size": s["size"], "flags": s["flags"]} for s in spans]})
                normalized = normalize_scripture_text(text)
                if normalized in {"muc luc", "muic luic"}:
                    continue
                if text.isdigit() and line["bbox"][1] > page.rect.height * 0.92:
                    continue
                chapter_match = re.fullmatch(r"c?huong (\d+)", normalized)
                if book.code == "PSA":
                    chapter_match = (re.match(r"thanh vinh (\d+)(?:\b|$)", normalized)
                                     or re.fullmatch(r"(\d{1,3})\s*\(\d{1,3}(?:[ABab]|[-–]\d{1,3})?\)", text)
                                     or chapter_match)
                if chapter_match:
                    if chapter == int(chapter_match[1]):
                        continue  # repeated title/running header; keep an unfinished verse
                    finish()
                    chapter = int(chapter_match[1])
                    if book.code == "PSA" and re.search(r"\(\d", text):
                        data.supplementary.append({"type": "numbering_heading", "book_code": book.code, "chapter": chapter,
                                                   "page": page_index + 1, "text": text, "provenance": block_id})
                    if chapter in chapter_numbers:
                        data.issues.append({"severity": "warning", "kind": "repeated_chapter_heading",
                                            "book": book.code, "chapter": chapter, "page": page_index + 1})
                    chapter_numbers.append(chapter)
                    previous_number = 0
                    continue
                # Typography distinguishes section titles from inline bold emphasis.
                is_heading = (
                    all(span["flags"] & 16 for span in spans if span["text"].strip())
                    and any("Verdana" in span["font"] or span["size"] > body_size + 0.1 for span in spans)
                )
                if is_heading or chapter is None:
                    if is_heading:
                        finish()
                        heading = text
                        heading_id = block_id
                    data.supplementary.append({"source": source, "page": page_index + 1,
                                               "book_code": book.code, "type": "heading" if is_heading else "introduction",
                                               "text": text, "provenance": block_id})
                    continue
                if text.startswith("(") and text.endswith(")"):
                    from loaddocs.scripture_references import parse_references
                    refs = parse_references(text)
                    if refs:
                        data.supplementary.append({"source": source, "page": page_index + 1, "book_code": book.code,
                                                   "chapter": chapter, "heading_id": heading_id, "type": "cross_reference",
                                                   "text": text, "references": [r.display() for r in refs], "provenance": block_id})
                        continue
                for span_index, span in enumerate(spans):
                    value = span["text"]
                    number_match = re.fullmatch(r"\s*(\d{1,3})([a-zđ+]{0,2})(?:[-–](\d{1,3})([a-zđ+]{0,2}))?\s*", value)
                    is_marker = number_match and span["size"] < body_size * 0.85
                    if book.code == "PSA" and span_index == 0:
                        parenthesized = re.match(r"^\s*\((\d{1,3})([a-zđ]{0,2})\)(?=\s|$)", value)
                        if parenthesized:
                            number_match, is_marker = parenthesized, True
                    if is_marker:
                        finish()
                        number = int(number_match[1])
                        suffix = number_match[2]
                        if not suffix and span_index + 1 < len(spans):
                            extension = re.match(r"\s*([a-zđ]{1,2})\)", spans[span_index + 1]["text"])
                            suffix = extension[1] if extension else ""
                        label = str(number) + suffix
                        verse_end = int(number_match[3]) if number_match.re.groups >= 3 and number_match[3] else number
                        if verse_end != number:
                            label += "-" + str(verse_end) + (number_match[4] or "")
                        if number < previous_number:
                            data.issues.append({"severity": "warning", "kind": "verse_order",
                                                "book": book.code, "chapter": chapter, "verse": label,
                                                "page": page_index + 1})
                        if number > previous_number + 1:
                            data.issues.append({"severity": "warning", "kind": "verse_gap",
                                                "book": book.code, "chapter": chapter,
                                                "after": previous_number, "before": number, "page": page_index + 1})
                        previous_number = verse_end
                        pending = {"source": source, "book_code": book.code, "book": book.name,
                                   "testament": book.testament, "chapter": chapter, "verse": number,
                                   "verse_label": label, "verse_end": verse_end, "page": page_index + 1, "page_end": page_index + 1,
                                   "heading": heading, "heading_id": heading_id, "paragraph_id": line.get("paragraph_id", block_id),
                                   "provenance": [block_id], "text": value.strip()}
                    elif pending is not None:
                        if span["size"] < body_size * 0.85 and value.strip():
                            data.supplementary.append({"type": "note_candidate", "book_code": book.code, "chapter": chapter,
                                                       "page": page_index + 1, "text": value, "provenance": block_id})
                            data.issues.append({"severity": "error", "kind": "note_needs_review", "book": book.code, "chapter": chapter, "page": page_index + 1})
                            continue
                        pending["text"] += value
                        if block_id not in pending["provenance"]:
                            pending["provenance"].append(block_id)
                        pending["page_end"] = page_index + 1
                    elif value.strip():
                        data.supplementary.append({"source": source, "page": page_index + 1,
                                                   "book_code": book.code, "type": "unassigned", "text": value})
                if pending is not None and not pending["text"].endswith("-"):
                    pending["text"] += "\n"
            if progress:
                progress(page_index + 1, len(document))
        finish()
        if not any(v["book_code"] == book.code for v in data.verses):
            data.issues.append({"severity": "error", "kind": "no_verses", "book": book.code})
        if not chapter_numbers:
            data.issues.append({"severity": "error", "kind": "no_chapters", "book": book.code})
        if chapter_numbers and chapter_numbers != list(range(1, max(chapter_numbers) + 1)):
            data.issues.append({"severity": "warning", "kind": "chapter_sequence", "book": book.code,
                                "chapters": chapter_numbers})
    for i, start in enumerate(top_level):
        if start < first_page or any(position[0] == start for position, _ in boundaries):
            continue
        stop = top_level[i + 1] if i + 1 < len(top_level) else len(document)
        for page_index in range(start, stop):
            check_cancel()
            text = document[page_index].get_text().strip()
            if text:
                data.supplementary.append({"source": source, "page": page_index + 1,
                                           "type": "unassigned_section", "text": text})
    _resolve_records(data)
    return data
