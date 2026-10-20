import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class BibleBook:
    code: str
    name: str
    testament: str
    aliases: tuple[str, ...]
    query_aliases: tuple[str, ...] = ()


def _book(
    code: str,
    name: str,
    testament: str,
    *aliases: str,
    query_aliases: Iterable[str] = (),
) -> BibleBook:
    return BibleBook(
        code=code,
        name=name,
        testament=testament,
        aliases=tuple(dict.fromkeys((name, *aliases))),
        query_aliases=tuple(query_aliases),
    )


BOOKS: tuple[BibleBook, ...] = (
    _book("GEN", "Sáng Thế Ký", "old", "Sáng Thế", query_aliases=("St",)),
    _book("EXO", "Xuất Hành", "old", query_aliases=("Xh",)),
    _book("LEV", "Lêvi", "old", "Lê Vi", query_aliases=("Lv",)),
    _book("NUM", "Dân Số", "old", query_aliases=("Ds",)),
    _book("DEU", "Đệ Nhị Luật", "old", query_aliases=("Đnl",)),
    _book("JOS", "Giôsuê", "old", "Giô Suê", query_aliases=("Gs",)),
    _book("JDG", "Thủ Lãnh", "old", query_aliases=("Tl",)),
    _book("RUT", "Rút", "old"),
    _book("1SA", "Samuen 1", "old", "1 Samuen", query_aliases=("1 Sm",)),
    _book("2SA", "Samuen 2", "old", "2 Samuen", query_aliases=("2 Sm",)),
    _book("1KI", "Vua 1", "old", "1 Vua", query_aliases=("1 V",)),
    _book("2KI", "Vua 2", "old", "2 Vua", query_aliases=("2 V",)),
    _book("1CH", "Sử Biên 1", "old", "1 Sử Biên", "Sử Biên Niên 1", query_aliases=("1 Sb",)),
    _book("2CH", "Sử Biên 2", "old", "2 Sử Biên", "Sử Biên Niên 2", query_aliases=("2 Sb",)),
    _book("EZR", "Étra", "old", "Vua Étra", query_aliases=("Er",)),
    _book("NEH", "Nơkhemia", "old", query_aliases=("Nkm",)),
    _book("TOB", "Tôbia", "old", query_aliases=("Tb",)),
    _book("JDT", "Giuđitha", "old", query_aliases=("Gđt",)),
    _book("EST", "Étte", "old", "Étte Hy Lạp", query_aliases=("Et",)),
    _book("1MA", "Macabê 1", "old", "1 Macabê", query_aliases=("1 Mcb",)),
    _book("2MA", "Macabê 2", "old", "2 Macabê", query_aliases=("2 Mcb",)),
    _book("JOB", "Gióp", "old"),
    _book("PSA", "Thánh Vịnh", "old", "Thánh Vịnh", query_aliases=("Tv",)),
    _book("PRO", "Châm Ngôn", "old", query_aliases=("Cn",)),
    _book("ECC", "Giảng Viên", "old", query_aliases=("Gv",)),
    _book("SNG", "Diễm Ca", "old", query_aliases=("Dc",)),
    _book("WIS", "Khôn Ngoan", "old", query_aliases=("Kn",)),
    _book("SIR", "Huấn Ca", "old", query_aliases=("Hc",)),
    _book("ISA", "Isaia", "old", query_aliases=("Is",)),
    _book("JER", "Giêrêmia", "old", query_aliases=("Gr",)),
    _book("LAM", "Ai Ca", "old", "Aica", query_aliases=("Ac",)),
    _book("BAR", "Barúc", "old", query_aliases=("Br",)),
    _book("EZK", "Êdêkien", "old", query_aliases=("Ed",)),
    _book("DAN", "Đanien", "old", query_aliases=("Đn",)),
    _book("HOS", "Hôsê", "old", query_aliases=("Hs",)),
    _book("JOL", "Giôen", "old", query_aliases=("Ge",)),
    _book("AMO", "Amốt", "old", query_aliases=("Am",)),
    _book("OBA", "Ôvađia", "old", query_aliases=("Ov",)),
    _book("JON", "Giôna", "old", query_aliases=("Gn",)),
    _book("MIC", "Mikha", "old", query_aliases=("Mk",)),
    _book("NAM", "Nakhum", "old", query_aliases=("Nk",)),
    _book("HAB", "Khabacúc", "old", "Khabarúc", "Kha-ba-cúc", query_aliases=("Kb",)),
    _book("ZEP", "Xôphônia", "old", query_aliases=("Xp",)),
    _book("HAG", "Khácgai", "old", query_aliases=("Kg",)),
    _book("ZEC", "Dacaria", "old", query_aliases=("Dcr",)),
    _book("MAL", "Malakhi", "old", query_aliases=("Ml",)),
    _book(
        "MAT",
        "Mátthêu",
        "new",
        "Tin Mừng Mátthêu",
        "Tin Mừng Theo Thánh Mátthêu",
        query_aliases=("Mt",),
    ),
    _book(
        "MRK",
        "Máccô",
        "new",
        "Tin Mừng Máccô",
        "Tin Mừng Theo Thánh Máccô",
        query_aliases=("Mc",),
    ),
    _book(
        "LUK",
        "Luca",
        "new",
        "Tin Mừng Luca",
        "Tin Mừng Theo Thánh Luca",
        query_aliases=("Lc",),
    ),
    _book(
        "JHN",
        "Gioan",
        "new",
        "Tin Mừng Gioan",
        "Tin Mừng Theo Thánh Gioan",
        query_aliases=("Ga",),
    ),
    _book(
        "ACT",
        "Tông Đồ Công Vụ",
        "new",
        "Công Vụ Tông Đồ",
        query_aliases=("Cv",),
    ),
    _book("ROM", "Thư Rôma", "new", "Rôma", query_aliases=("Rm",)),
    _book(
        "1CO",
        "Thư Côrintô 1",
        "new",
        "Côrintô 1",
        "1 Côrintô",
        query_aliases=(
            "1 Cr",
            "Thư thứ nhất gửi tín hữu Côrintô",
            "Thư thứ nhất Côrintô",
        ),
    ),
    _book("2CO", "Thư Côrintô 2", "new", "Côrintô 2", "2 Côrintô", query_aliases=("2 Cr",)),
    _book("GAL", "Thư Galát", "new", "Galát", query_aliases=("Gl",)),
    _book("EPH", "Thư Êphêsô", "new", "Êphêsô", query_aliases=("Ep",)),
    _book("PHP", "Thư Philípphê", "new", "Philípphê", query_aliases=("Pl",)),
    _book("COL", "Thư Côlôxê", "new", "Côlôxê", query_aliases=("Cl",)),
    _book("1TH", "Thư Thêxalônica 1", "new", "Thêxalônica 1", "1 Thêxalônica", query_aliases=("1 Tx",)),
    _book("2TH", "Thư Thêxalônica 2", "new", "Thêxalônica 2", "2 Thêxalônica", query_aliases=("2 Tx",)),
    _book("1TI", "Thư Timôthê 1", "new", "Timôthê 1", "1 Timôthê", query_aliases=("1 Tm",)),
    _book("2TI", "Thư Timôthê 2", "new", "Timôthê 2", "2 Timôthê", query_aliases=("2 Tm",)),
    _book("TIT", "Thư Titô", "new", "Titô", query_aliases=("Tt",)),
    _book("PHM", "Thư Philêmon", "new", "Philêmon", query_aliases=("Plm",)),
    _book("HEB", "Thư Do Thái", "new", "Do Thái", query_aliases=("Dt",)),
    _book("JAS", "Thư Giacôbê", "new", "Giacôbê", query_aliases=("Gc",)),
    _book("1PE", "Thư Phêrô 1", "new", "Phêrô 1", "1 Phêrô", query_aliases=("1 Pr",)),
    _book("2PE", "Thư Phêrô 2", "new", "Phêrô 2", "2 Phêrô", query_aliases=("2 Pr",)),
    _book("1JN", "Thư Gioan 1", "new", "1 Gioan", query_aliases=("1 Ga",)),
    _book("2JN", "Thư Gioan 2", "new", "2 Gioan", query_aliases=("2 Ga",)),
    _book("3JN", "Thư Gioan 3", "new", "3 Gioan", query_aliases=("3 Ga",)),
    _book("JUD", "Thư Giuđa", "new", "Giuđa", query_aliases=("Gđ",)),
    _book("REV", "Khải Huyền", "new", query_aliases=("Kh",)),
)


def normalize_scripture_text(value: str) -> str:
    value = str(value or "").replace("đ", "d").replace("Đ", "D").replace("ð", "d").replace("Ð", "D")
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = re.sub(r"[^0-9A-Za-z]+", " ", value).lower()
    return " ".join(value.split())


_BOOK_TITLE_LOOKUP = {}
for _title_book in BOOKS:
    for _title_alias in _title_book.aliases:
        _BOOK_TITLE_LOOKUP.setdefault(normalize_scripture_text(_title_alias).replace(' ',''),_title_book)


def match_book_title(title: str) -> BibleBook | None:
    """Match a whole bookmark title, allowing spelling separators, not substrings."""
    title = re.sub(r"^\s*\d+\.\s*", "", str(title))
    key = normalize_scripture_text(title)
    key = re.sub(r"^sach\s+", "", key).replace(" ", "")
    return _BOOK_TITLE_LOOKUP.get(key)


_HEADER_ALIASES = sorted(
    (
        (normalize_scripture_text(alias), book)
        for book in BOOKS
        for alias in book.aliases
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)
_QUERY_ALIASES = sorted(
    (
        (normalize_scripture_text(alias), book)
        for book in BOOKS
        for alias in (*book.aliases, *book.query_aliases)
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)

_BOOKS_BY_CODE = {book.code: book for book in BOOKS}
_SOURCE_ALIASES = sorted(
    (
        *_HEADER_ALIASES,
        *(
            (normalize_scripture_text(alias), _BOOKS_BY_CODE[code])
            for alias, code in {
                'thu 1 corinto': '1CO',
                'thu 2 corinto': '2CO',
                'cac vua quyen 1': '1KI',
                'cac vua quyen 2': '2KI',
                'samuel quyen 1': '1SA',
                'samuel quyen 2': '2SA',
                'tin mung marco': 'MRK',
            }.items()
        ),
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)


class ScriptureParser:
    def __init__(self, source_name: str) -> None:
        normalized_source = normalize_scripture_text(source_name)
        compact_source = normalized_source.replace(" ", "")
        self.enabled = "kinhthanh" in compact_source or "bible" in compact_source
        if "tanuoc" in compact_source:
            self.current_testament: str | None = "new"
        elif "cuuoc" in compact_source or "cuuuoc" in compact_source:
            self.current_testament = "old"
        else:
            self.current_testament = None
        self.current_book: BibleBook | None = None
        for alias, book in _SOURCE_ALIASES:
            if re.search(rf'\b{re.escape(alias)}\b', normalized_source):
                self.enabled = True
                self.current_book = book
                self.current_testament = book.testament
                break
        self.current_chapter: int | None = None

def extract_scripture_query_filters(question: str) -> dict[str, object]:
    from loaddocs.scripture_references import parse_references, _fold, _BOOK_PATTERN, _COMPACT_ALIASES, _compatible_book_spelling
    from loaddocs.scripture_workflow import label_bounds
    refs = parse_references(question)
    type_clause = {"document_type": {"$in": ["scripture", "bible"]}}
    if refs:
        scopes = []
        for ref in refs:
            if ref.end_chapter != ref.chapter and ref.end_chapter is not None:
                scope = {"$and": [{"book_code": ref.book}, {"chapter": {"$gte": ref.chapter}}, {"chapter": {"$lte": ref.end_chapter}}]}
            else:
                scope = {"book_code": ref.book, "chapter": ref.chapter}
            if ref.start and (not ref.end_chapter or ref.end_chapter == ref.chapter):
                scope = {"$and": [{k: v} for k, v in scope.items()] + [
                    {"verse_start": {"$lte": label_bounds(ref.end or ref.start)[1][0]}},
                    {"verse_end": {"$gte": label_bounds(ref.start)[0][0]}},
                ]}
            scopes.append(scope)
        if len(scopes) == 1 and "$and" not in scopes[0]:
            return {**type_clause, **scopes[0]}
        if len(scopes) == 1:
            return {"$and": [type_clause] + scopes[0]["$and"]}
        return {"$and": [type_clause, {"$or": [{"$and": [{k: v} for k, v in scope.items()]} if "$and" not in scope else scope for scope in scopes]}]}
    original = unicodedata.normalize('NFC', question)
    books = []
    # A book name is a scope only when requested; ordinary words and character names are not filters.
    for match in re.finditer(rf"\b(?:sach|theo|trong)\s+(?P<book>{_BOOK_PATTERN})\b", _fold(original)):
        code = _COMPACT_ALIASES[re.sub(r'\s+', '', match['book'])]
        if _compatible_book_spelling(original[match.start('book'):match.end('book')], code):
            books.append(code)
    books = list(dict.fromkeys(books))
    if not books:
        return {}
    return {**type_clause, "book_code": books[0]} if len(books) == 1 else {"$and": [type_clause, {"book_code": {"$in": books}}]}
