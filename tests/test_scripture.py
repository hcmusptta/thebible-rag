import unittest

from loaddocs.scripture import (
    ScriptureParser,
    extract_scripture_query_filters,
)


class ScriptureFilenameTests(unittest.TestCase):
    def test_book_aliases_only_classify_filename(self):
        for source, code in {'Sang_The.pdf': 'GEN', 'Tin_Mung_Marco.pdf': 'MRK', 'Thu_1_Corinto.pdf': '1CO', 'Cac_Vua_Quyen_1.pdf': '1KI'}.items():
            self.assertEqual(code, ScriptureParser(source).current_book.code)
        self.assertFalse(ScriptureParser('annual-report.pdf').enabled)


class ScriptureQueryFilterTests(unittest.TestCase):
    def test_full_book_name_and_chapter(self) -> None:
        self.assertEqual(
            {
                "document_type": {"$in": ["scripture", "bible"]},
                "book_code": "MAT",
                "chapter": 5,
            },
            extract_scripture_query_filters("Mátthêu chương 5 nói gì?"),
        )

    def test_abbreviated_reference(self) -> None:
        self.assertEqual(
            {"$and": [
                {"document_type": {"$in": ["scripture", "bible"]}},
                {"book_code": "MAT"},
                {"chapter": 5},
                {"verse_start": {"$lte": 12}},
                {"verse_end": {"$gte": 1}},
            ]},
            extract_scripture_query_filters("Giải thích Mt 5:1-12"),
        )

    def test_gospel_of_john_is_not_third_epistle(self) -> None:
        self.assertEqual(
            {"$and": [
                {"document_type": {"$in": ["scripture", "bible"]}},
                {"book_code": "JHN"},
                {"chapter": 3},
                {"verse_start": {"$lte": 16}},
                {"verse_end": {"$gte": 16}},
            ]},
            extract_scripture_query_filters("Gioan 3:16 có ý nghĩa gì?"),
        )

    def test_spelled_out_first_corinthians_is_recognized(self) -> None:
        self.assertEqual(
            {
                "document_type": {"$in": ["scripture", "bible"]},
                "book_code": "1CO",
            },
            extract_scripture_query_filters(
                "Theo thư thứ nhất gửi tín hữu Côrintô, đức mến là gì?"
            ),
        )
    def test_generic_question_has_no_filter(self) -> None:
        self.assertEqual(
            {},
            extract_scripture_query_filters("Tài liệu nói gì về lòng thương xót?"),
        )

    def test_ordinary_words_and_character_names_do_not_set_book_scope(self):
        for question in ('Mô-sê dẫn dân qua biển như thế nào?', 'Gióp là ai?', 'Lòng can đảm là gì?', 'Đời sống của dân 40 năm trong sa mạc'):
            with self.subTest(question=question):
                self.assertEqual({}, extract_scripture_query_filters(question))
        self.assertEqual('JOB', extract_scripture_query_filters('Theo sách Gióp, đau khổ được nói thế nào?')['book_code'])

    def test_explicit_accents_do_not_turn_ordinary_words_into_references(self):
        from loaddocs.scripture_references import parse_references
        self.assertEqual([],parse_references('dân 40 năm'))
        self.assertEqual([],parse_references('gà 3 con'))
        self.assertEqual('DAN',parse_references('Đanien 3:1')[0].book)
        self.assertEqual('DAN',parse_references('Đn 3:1')[0].book)
        self.assertEqual('DAN',parse_references('dan 3:1')[0].book)


if __name__ == "__main__":
    unittest.main()
