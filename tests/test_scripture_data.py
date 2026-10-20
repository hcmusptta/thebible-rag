import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import fitz
from loaddocs.scripture_data import parse_scripture_pdf, SCRIPTURE_PROCESSING_VERSION
from services.readdocs import process_uploaded_pdf
from vectordb.vector_store import VectorDatabase


class ScriptureDataTests(unittest.TestCase):
    def make_pdf(self, duplicate=False):
        pdf = fitz.open()
        pages = [
            '<h1>Mục Lục</h1><p>Ôvađia Chương 50 123</p>',
            '<h1>Sáng Thế Ký</h1><h2>Chương 1</h2><p><sup>1</sup> Nội dung bắt đầu và còn tiếp</p>',
            '<p>ở trang sau. <sup>2</sup> Nội dung thứ hai.</p>',
        ]
        if duplicate:
            pages.append('<p><sup>1</sup> Nội dung bị đánh số lặp.</p>')
        for html in pages:
            page = pdf.new_page()
            page.insert_htmlbox(fitz.Rect(30, 30, 560, 800), html,
                                css='body {font-size:16pt} sup {font-size:12pt} h1,h2 {font-weight:bold}')
        pdf.set_toc([[1, 'Mục Lục', 1], [1, '01. Sáng Thế Ký', 2]])
        return pdf

    def test_real_pdf_excludes_toc_and_joins_verse_across_pages(self):
        with self.make_pdf() as pdf:
            data = parse_scripture_pdf(pdf, 'Bible.pdf')
        data.validate()
        self.assertEqual(2, len(data.verses))
        self.assertEqual({'GEN'}, {v['book_code'] for v in data.verses})
        self.assertEqual((2, 3), (data.verses[0]['page'], data.verses[0]['page_end']))
        self.assertIn('còn tiếp ở trang sau.', data.verses[0]['text'])
        self.assertNotIn('Chương', data.verses[0]['text'])
        self.assertTrue(any('Mục Lục' in row['text'] for row in data.supplementary))

    def test_bad_numbering_is_preserved_reported_and_blocks_ingestion(self):
        with self.make_pdf(duplicate=True) as pdf:
            data = parse_scripture_pdf(pdf, 'Bible.pdf')
        self.assertEqual(0, len(data.verses))
        self.assertEqual(3, len(data.quarantined))
        self.assertTrue(any(i['kind'] == 'conflicting_verse' for i in data.issues))
        with self.assertRaises(ValueError):
            data.validate()
        with tempfile.TemporaryDirectory() as folder:
            data.export(folder)
            self.assertTrue((Path(folder) / 'report.json').is_file())

    def test_missing_book_boundaries_fail_instead_of_guessing(self):
        with self.make_pdf() as pdf:
            pdf.set_toc([])
            with self.assertRaises(ValueError):
                parse_scripture_pdf(pdf, 'Bible.pdf')

    def test_real_ingest_embeds_reference_but_stores_original_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {
            'EMBEDDING_MODEL_NAME': 'test/model', 'EMBEDDING_DIMENSION': '3',
        }):
            path = Path(folder) / 'Bible.pdf'
            with self.make_pdf() as pdf:
                pdf.save(path)
            database = VectorDatabase('structured-test', db_path=str(Path(folder) / 'db'))
            embedder = Mock()
            embedder.embed_documents.side_effect = lambda texts: [[1., 0., 0.] for _ in texts]
            try:
                with patch('services.readdocs.get_default_embedder', return_value=embedder):
                    result = process_uploaded_pdf(str(path), path.name, database)
                    duplicate = process_uploaded_pdf(str(path), path.name, database)
                self.assertEqual(2, result.chunk_count)
                self.assertTrue(duplicate.duplicate)
                texts = embedder.embed_documents.call_args.args[0]
                self.assertTrue(texts[0].startswith('Sáng Thế Ký 1:1'))
                rows = database.get_all_documents()
                self.assertNotIn('Sáng Thế Ký 1:1', rows[0]['content'])
                self.assertEqual({SCRIPTURE_PROCESSING_VERSION}, {r['metadata']['processing_version'] for r in rows})
                self.assertEqual({'scripture'}, {r['metadata']['document_type'] for r in rows})
                with self.make_pdf(duplicate=True) as pdf:
                    pdf.save(Path(folder) / 'broken.pdf')
                with patch('services.readdocs.get_default_embedder') as load:
                    with self.assertRaises(ValueError):
                        process_uploaded_pdf(str(Path(folder) / 'broken.pdf'), path.name, database)
                    load.assert_not_called()
                self.assertEqual(2, database.count())
            finally:
                database.client.close()


class ScripturePdfVariantTests(unittest.TestCase):
    def make_pdf(self, pages, toc):
        pdf = fitz.open()
        for html in pages:
            page = pdf.new_page()
            page.insert_htmlbox(fitz.Rect(30, 30, 560, 800), html,
                                css='body {font-size:16pt} sup {font-size:12pt} h1,h2 {font-weight:bold}')
        if toc:
            pdf.set_toc(toc)
        return pdf

    def test_book_titles_match_separators_eth_and_full_names(self):
        from loaddocs.scripture import match_book_title
        expected = {'6. Ðệ Nhị Luật':'DEU', '10. Sa-mu-en 1':'1SA',
                    '14. Sử Biên Niên 1':'1CH', '16. Vua Ét-ra':'EZR',
                    '24. SÁCH THÁNH VỊNH':'PSA', '67. Thư Dothái':'HEB',
                    '39.Ô-va-đi-a':'OBA', '35. Ða-ni-en':'DAN'}
        for title, code in expected.items():
            with self.subTest(title=title):
                self.assertEqual(code, match_book_title(title).code)
        self.assertIsNone(match_book_title('Giới thiệu Sáng Thế Ký'))
        self.assertNotEqual(match_book_title('Sa-mu-en 1'),match_book_title('Sa-mu-en 2'))

    def test_unrecognized_book_blocks_and_does_not_leak_into_previous_book(self):
        pages=['<h2>Chương 1</h2><p><sup>1</sup> Văn bản A.</p>',
               '<h2>Chương 1</h2><p><sup>1</sup> Văn bản B khác sách.</p>']
        with self.make_pdf(pages,[[1,'Thư Philêmon',1],[1,'67. Sách chưa biết',2]]) as pdf:
            data=parse_scripture_pdf(pdf,'Bible.pdf')
        self.assertEqual(1,len(data.verses))
        self.assertNotIn('Văn bản B',data.verses[0]['text'])
        self.assertTrue(any('Văn bản B' in r['text'] for r in data.supplementary))
        with self.assertRaisesRegex(ValueError,'Chưa xác định'):
            data.validate()

    def test_repeated_chapter_header_keeps_cross_page_verse(self):
        pages=['<h2>Chương 1</h2><h2>Chương 1</h2><p><sup>1</sup> Câu còn tiếp</p>',
               '<h2>Chương 1</h2><p>ở trang sau. <sup>2</sup> Câu thứ hai.</p>']
        with self.make_pdf(pages,[[1,'Thư Philêmon',1]]) as pdf:
            data=parse_scripture_pdf(pdf,'Bible.pdf')
        data.validate()
        self.assertEqual(2,len(data.verses))
        self.assertIn('còn tiếp ở trang sau',data.verses[0]['text'])
        self.assertEqual(2,data.verses[0]['page_end'])

    def test_chapter_expectations_come_from_edition_profile(self):
        pages=['<h2>Chương 1</h2><p><sup>1</sup> Câu thứ nhất.</p>',
               '<h2>Chương 2</h2><p><sup>1</sup> Câu không hợp lệ.</p>']
        with self.make_pdf(pages,[[1,'Thư Philêmon',1]]) as pdf:
            data=parse_scripture_pdf(pdf,'Bible.pdf')
        from loaddocs.scripture_workflow import prepare_scripture
        data.validate()
        prepare_scripture(data, {'expected_chapters': {'PHM': [1]}})
        with self.assertRaises(ValueError):
            data.validate()

    def test_parenthesized_psalm_verses_preserve_original_text(self):
        pages=['<h2>Thánh Vịnh 1: Hai con đường</h2><p>(1) Phúc thay người công chính.</p><p>(2) Vui với lề luật Chúa.</p>']
        with self.make_pdf(pages,[[1,'SÁCH THÁNH VỊNH',1]]) as pdf:
            data=parse_scripture_pdf(pdf,'Bible.pdf')
        data.validate()
        self.assertEqual([1,2],[v['verse'] for v in data.verses])
        self.assertTrue(data.verses[0]['text'].startswith('(1) Phúc'))

    def test_single_book_filename_with_chapter_bookmarks_reads_all_chapters(self):
        pages=['<h2>Chương 1</h2><p><sup>1</sup> Văn bản chương đầu.</p>',
               '<h2>Chương 2</h2><p><sup>1</sup> Văn bản chương sau.</p>']
        with self.make_pdf(pages,[[1,'Chương 1',1],[1,'Chương 2',2]]) as pdf:
            data=parse_scripture_pdf(pdf,'Sáng Thế.pdf')
        data.validate()
        self.assertEqual([1,2],[v['chapter'] for v in data.verses])

    def test_alias_book_boundary_and_interbook_introduction(self):
        pages=['<h2>Chương 1</h2><p><sup>1</sup> Lời chào.</p>',
               '<p>Phần dẫn nhập không phải câu Kinh Thánh.</p>',
               '<h2>Chương 1</h2><p><sup>1</sup> Lời mở đầu sách mới.</p>']
        with self.make_pdf(pages,[[1,'Thư Philêmon',1],[1,'Dẫn Nhập',2],[1,'Thư Dothái',3]]) as pdf:
            data=parse_scripture_pdf(pdf,'Bible.pdf')
        data.validate()
        self.assertEqual(['PHM','HEB'],[v['book_code'] for v in data.verses])
        self.assertNotIn('dẫn nhập',data.verses[0]['text'])
        self.assertTrue(any('dẫn nhập' in r['text'] for r in data.supplementary))


if __name__ == '__main__':
    unittest.main()
