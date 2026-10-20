"""Explicit references in source numbering; never guess an equivalent translation."""
import re
import unicodedata
from dataclasses import dataclass

from loaddocs.scripture import _QUERY_ALIASES, _BOOKS_BY_CODE
from loaddocs.scripture_workflow import label_bounds


def _fold(value):
    value = unicodedata.normalize("NFD", str(value).lower().replace("đ", "d").replace("ð", "d"))
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"(?<=[a-z])[-–](?=[a-z])", " ", value)
    return value.replace("–", "-").replace("—", "-").replace("−", "-")


_ALIASES = {**{a: b.code for a, b in _QUERY_ALIASES}, **{c.lower(): c for c in _BOOKS_BY_CODE}}
_BOOK_PATTERN = "|".join(re.escape(a).replace(r"\ ", r"\s*") for a in sorted(_ALIASES, key=len, reverse=True))
_COMPACT_ALIASES = {re.sub(r"\s+", "", alias): code for alias, code in _ALIASES.items()}
_LABEL = r"\d{1,3}[a-z+]{0,2}"
_PATTERN = re.compile(rf"\b(?P<book>{_BOOK_PATTERN})\s*(?:chuong\s+)?(?P<chapter>\d{{1,3}})(?:\s*[:,]\s*(?P<start>{_LABEL})(?:\s*-\s*(?:(?P<end_chapter>\d{{1,3}})\s*:\s*)?(?P<end>{_LABEL}))?|\s*-\s*(?P<chapter_end>\d{{1,3}}))?(?![\w+])")


def _compatible_book_spelling(given, code):
    given = re.sub(r'[\W_]', '', given.casefold().replace('ð', 'đ'))
    book = _BOOKS_BY_CODE[code]
    for alias in (*book.aliases, *book.query_aliases, book.code):
        actual = re.sub(r'[\W_]', '', alias.casefold().replace('ð', 'đ'))
        if _fold(given) == _fold(actual) and all(a == b or a == _fold(b) for a, b in zip(given, actual)):
            return True
    return False


@dataclass(frozen=True)
class Reference:
    book: str
    chapter: int
    start: str | None = None
    end_chapter: int | None = None
    end: str | None = None

    def __post_init__(self):
        if self.chapter < 1 or (self.end_chapter or self.chapter) < self.chapter:
            raise ValueError("Tham chiếu có chương không hợp lệ hoặc đảo thứ tự.")
        if self.start:
            if self.end and (("+" in self.start) != ("+" in self.end)):
                raise ValueError("Khoảng câu trộn nhãn có dấu + và nhãn thường; hãy tách thành các tham chiếu theo nguồn.")
            first, last = label_bounds(self.start)[0], label_bounds(self.end or self.start)[1]
            if (self.end_chapter or self.chapter) == self.chapter and last < first:
                raise ValueError("Khoảng câu bị đảo thứ tự; không tự đổi yêu cầu.")

    def includes(self, verse):
        if verse["book_code"] != self.book:
            return False
        first, last = label_bounds(verse["verse_label"])
        chapter = verse["chapter"]
        if not self.chapter <= chapter <= (self.end_chapter or self.chapter):
            return False
        if not self.start:
            return True
        if ("+" in verse["verse_label"]) != ("+" in self.start or "+" in (self.end or "")):
            return False
        lower = label_bounds(self.start)[0]
        upper = label_bounds(self.end or self.start)[1]
        # An unsuffixed reference selects all parts; a bridge stays one original record.
        if not upper[1]:
            upper = (upper[0], "zz")
        return (chapter, last) >= (self.chapter, lower) and (chapter, first) <= ((self.end_chapter or self.chapter), upper)

    def display(self):
        value = f"{self.book} {self.chapter}"
        if self.start:
            value += f":{self.start}"
        if self.end_chapter and self.end_chapter != self.chapter:
            value += f"-{self.end_chapter}" + (f":{self.end}" if self.end else "")
        elif self.end and self.end != self.start:
            value += f"-{self.end}"
        return value


def parse_references(question):
    question = unicodedata.normalize('NFC', question)
    folded = _fold(question)
    if re.search(rf"\b(?:{_BOOK_PATTERN})\s*\d+\s*\(\d", folded):
        raise ValueError("Tham chiếu có hai hệ đánh số. Hãy chọn số của bản dịch đang tra cứu; không tự quy đổi số trong ngoặc.")
    references = []
    for match in _PATTERN.finditer(folded):
        book = _COMPACT_ALIASES[re.sub(r"\s+", "", match["book"])]
        if not _compatible_book_spelling(question[match.start('book'):match.end('book')], book):
            continue
        if re.match(r"\s*(?::\s*\w|-\s*\d)", folded[match.end():]):
            raise ValueError("Tham chiếu chưa hợp lệ; không bỏ qua phần số câu hoặc khoảng câu chưa đọc được.")
        chapter = int(match["chapter"])
        references.append(Reference(book, chapter, match["start"],
                                    int(match["end_chapter"] or match["chapter_end"] or chapter), match["end"] or match["start"]))
        # Comma/semicolon continuation without repeating a book, e.g. Ga 1:1,3; 2:4.
        cursor = match.end()
        while True:
            extra = re.match(rf"\s*[,;]\s*(?:(\d{{1,3}})\s*:\s*)?({_LABEL})(?:\s*-\s*({_LABEL}))?(?!\w|\s*:)", folded[cursor:])
            if not extra or not match["start"]:
                break
            references.append(Reference(book, int(extra[1] or chapter), extra[2], None, extra[3] or extra[2]))
            cursor += extra.end()
    return list(dict.fromkeys(references))
