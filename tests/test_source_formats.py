import os
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from loaddocs.source_documents import open_source, iter_text_sections
from loaddocs.scripture_text import parse_scripture_text, export_scripture_text
from loaddocs.scripture_workflow import prepare_scripture
from loaddocs.scripture_references import Reference
from loaddocs.scripture_store import ScriptureStore
from loaddocs.document_types import detect_document_type, source_location
from services.readdocs import process_uploaded_document, IngestCancelledError
from vectordb.vector_store import VectorDatabase


def write_docx(path, body, extras=None):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')
        for name,text in (extras or {}).items():
            z.writestr(name,text)


def p(text):
    from xml.sax.saxutils import escape
    return '<w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p>'


class SourceFormatTests(unittest.TestCase):
    def test_normalized_export_keeps_coordinates_parts_bridges_and_text(self):
        source = self.root/'Kinh-Thanh.txt'
        source.write_text('Sáng Thế\nChương 1\n1 Bản văn nguồn.\n2a Phần a.\n2b Phần b.\n3-4 Câu gộp.\n5+ Phần thêm.\nGioan\nChương 3\n16 Bản văn sách khác.',encoding='utf-8')
        with open_source(source,source.name) as doc:
            data = prepare_scripture(parse_scripture_text(doc,source.name))
        target = self.root/'Kinh-Thanh-Chuan-Hoa.txt'
        self.assertEqual(6,export_scripture_text(data,target))
        result = self.ingest(target)
        self.assertEqual(6,result.chunk_count)
        rows = ScriptureStore(self.db).lookup([Reference('JHN',3,'16')])
        self.assertIn('Bản văn sách khác',rows[0]['content'])
        self.assertIn('3-4 Câu gộp.',target.read_text(encoding='utf-8'))

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        env = patch.dict(os.environ,{'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3','EMBEDDING_BATCH_SIZE':'2'})
        env.start(); self.addCleanup(env.stop)
        self.db = VectorDatabase('formats',db_path=str(self.root/'db'))
        self.addCleanup(self.db.client.close)
        self.embedder = Mock()
        self.embedder.embed_documents.side_effect = lambda texts:[[1.,0.,0.] for _ in texts]

    def ingest(self,path,**kwargs):
        with patch('services.readdocs.get_default_embedder',return_value=self.embedder):
            return process_uploaded_document(str(path),path.name,self.db,**kwargs)

    def test_unicode_txt_encodings_ingest_with_real_line_locators(self):
        for encoding in ('utf-8-sig','utf-16'):
            path=self.root/f'giao-ly-{encoding}.txt'
            path.write_text('Giáo lý\n\n224. Bí tích là gì?\nBí tích là dấu chỉ trong nguồn.',encoding=encoding)
            result=self.ingest(path)
            self.assertEqual('catechism',result.document_type)
            self.assertEqual('line',result.location_unit)
            rows=[r for r in self.db.get_all_documents() if r['metadata']['source']==path.name and r['metadata'].get('section_number')=='224']
            self.assertTrue(rows)
            self.assertEqual('Dòng: 3-4',source_location(rows[0]['metadata']))
            self.assertEqual('txt',rows[0]['metadata']['source_format'])
            self.assertTrue(self.ingest(path).duplicate)

    def test_docx_preserves_table_numbering_and_separates_notes(self):
        path=self.root/'giao-ly.docx'
        body=p('Giáo lý')+'<w:tbl><w:tr><w:tc><w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="5"/></w:numPr></w:pPr><w:r><w:t>Bí tích là gì?</w:t></w:r></w:p>'+p('Câu trả lời của mục này.')+'</w:tc></w:tr></w:tbl>'
        extras={'word/numbering.xml':'<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="7"/><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum><w:num w:numId="5"><w:abstractNumId w:val="1"/></w:num></w:numbering>',
                'word/footnotes.xml':'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1">'+p('Chú thích tách riêng.')+'</w:footnote></w:footnotes>'}
        write_docx(path,body,extras)
        with open_source(path,path.name) as document:
            self.assertEqual('catechism',detect_document_type(document,path.name))
            sections=list(iter_text_sections(document,'catechism'))
            question=next(s for s in sections if s['section_number']=='7')
            self.assertIn('Câu trả lời',question['text'])
            self.assertNotIn('Chú thích',question['text'])
            self.assertEqual('footnotes 1',sections[-1]['heading'])
        result=self.ingest(path)
        self.assertEqual('paragraph',result.location_unit)
        self.assertTrue(any(r['metadata'].get('section_number')=='7' for r in self.db.get_all_documents()))

    def test_text_scripture_uses_canonical_store_parts_bridges_and_original_file(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Sáng Thế\nChương 1\n1 Bản văn thứ nhất\ncòn tiếp dòng sau.\n2a Phần a.\n2b Phần b.\n3-4 Câu gộp.\n5+ Nhãn bổ sung.',encoding='utf-8')
        result=self.ingest(path)
        self.assertEqual('scripture',result.document_type)
        self.assertEqual(5,result.chunk_count)
        rows=ScriptureStore(self.db).lookup([Reference('GEN',1,'2')])
        self.assertIn('2a',rows[0]['content']); self.assertIn('2b',rows[0]['content'])
        self.assertEqual('line',rows[0]['metadata']['location_unit'])
        rows=ScriptureStore(self.db).lookup([Reference('GEN',1,'3',None,'4')])
        self.assertIn('3-4',rows[0]['content'])
        rows=ScriptureStore(self.db).lookup([Reference('GEN',1,'5+')])
        self.assertIn('5+',rows[0]['content'])
        original=self.db.db_path/'scripture_sources'/result.document_id/'source.txt'
        self.assertEqual(path.read_bytes(),original.read_bytes())

    def test_docx_superscript_verses_and_explicit_txt_coordinates(self):
        path=self.root/'kinh-thanh.docx'
        body=p('Gioan')+p('Chương 3')+'<w:p><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>16</w:t></w:r><w:r><w:t>Bản văn một.</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>17</w:t></w:r><w:r><w:t>Bản văn hai.</w:t></w:r></w:p>'
        write_docx(path,body)
        result=self.ingest(path)
        self.assertEqual(2,result.chunk_count)
        self.assertIn('Bản văn hai',ScriptureStore(self.db).lookup([Reference('JHN',3,'17')])[0]['content'])
        txt=self.root/'other.txt'; txt.write_text('Ga 3:16 Nội dung có tọa độ rõ.',encoding='utf-8')
        result=self.ingest(txt,document_type='scripture')
        self.assertEqual(1,result.chunk_count)

    def test_embedded_reference_and_page_marker_do_not_change_bible_coordinates(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Sáng Thế\nChương 1\n1 Câu nguồn có nhắc Ga 3:16 làm đối chiếu.\n===== TRANG 2 =====\ncòn tiếp.\n2 Câu thứ hai.',encoding='utf-8')
        result=self.ingest(path)
        self.assertEqual(2,result.chunk_count)
        rows=ScriptureStore(self.db).lookup([Reference('GEN',1,'1')])
        self.assertIn('Ga 3:16',rows[0]['content'])
        self.assertNotIn('TRANG 2',rows[0]['content'])
        self.assertIn('còn tiếp',rows[0]['content'])

    def test_cross_book_quote_does_not_reassign_following_verses(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Sáng Thế\nChương 1\n1 Bản văn sách gốc.\nGa 3:16 Một lời trích đối chiếu.\n2 Bản văn tiếp theo.',encoding='utf-8')
        self.ingest(path)
        rows=ScriptureStore(self.db).lookup([Reference('GEN',1,'2')])
        self.assertIn('Bản văn tiếp theo',rows[0]['content'])
        self.assertEqual({'GEN'},{r['metadata']['book_code'] for r in self.db.get_all_documents()})

    def test_lost_inline_verse_markers_are_quarantined_before_embedding(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Sáng Thế\nChương 1\n1 Bản văn bị dính.2Nhãn câu không rõ.',encoding='utf-8')
        with self.assertRaises(ValueError):self.ingest(path)
        self.embedder.embed_documents.assert_not_called()
        self.assertEqual(0,self.db.count())

    def test_reference_with_verse_parts_is_not_a_glued_marker(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Sáng Thế\nChương 1\n1 Bản văn có đối chiếu (Is 37:9b-20; Gr 52:3b-11).\n2 Bản văn tiếp theo.',encoding='utf-8')
        self.assertEqual(2,self.ingest(path).chunk_count)

    def test_export_separates_trusted_glued_labels_without_changing_words(self):
        from loaddocs.scripture_data import ScriptureData
        target = self.root/'Kinh-Thanh-Chuan-Hoa.txt'
        verses = [dict(book_code='LUK',book='Luca',chapter=1,verse_label='79',text='79soi sáng'),
                  dict(book_code='PSA',book='Thánh Vịnh',chapter=1,verse_label='1',text='(1) Bản văn Thánh Vịnh.')]
        self.assertEqual(2,export_scripture_text(ScriptureData(verses=verses),target))
        self.assertIn('79 soi sáng',target.read_text(encoding='utf-8'))

    def test_unassigned_chapters_cannot_silently_publish_only_the_remaining_book(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Chương 1\n1 Chưa biết sách.\nSáng Thế\nChương 2\n1 Phần có tên sách.',encoding='utf-8')
        progress = Mock()
        with self.assertRaisesRegex(ValueError, 'tiêu đề chương chưa có tên sách'):
            self.ingest(path,progress_callback=progress)
        self.embedder.embed_documents.assert_not_called()
        self.assertEqual(0,self.db.count())
        self.assertIn(58,[call.args[0] for call in progress.call_args_list])

    def test_book_name_in_a_verse_continuation_is_not_a_new_book(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Isaia\nChương 1\n1 Câu này có cụm từ\nKhải Huyền\ntrong phần tiếp nối.\n2 Câu tiếp theo.',encoding='utf-8')
        self.ingest(path)
        rows=ScriptureStore(self.db).lookup([Reference('ISA',1,'1')])
        self.assertIn('Khải Huyền',rows[0]['content'])
        self.assertEqual({'ISA'},{r['metadata']['book_code'] for r in self.db.get_all_documents()})

    def test_psalm_second_number_stays_supplementary(self):
        path=self.root/'Kinh-Thanh.txt'
        path.write_text('Thánh Vịnh\nThánh Vịnh 48 (47)\n(1) Bản văn theo số đầu tiên.',encoding='utf-8')
        self.ingest(path)
        rows=ScriptureStore(self.db).lookup([Reference('PSA',48,'1')])
        self.assertIn('Bản văn theo số đầu tiên',rows[0]['content'])

    def test_bad_uploads_and_unlabelled_bible_do_not_replace_previous_version(self):
        path=self.root/'giao-ly.txt'; path.write_text('Giáo lý\n1. Nội dung đúng trước đó.',encoding='utf-8')
        self.ingest(path)
        count=self.db.count()
        for suffix,data in (('.docx',b'not a zip'),('.doc',b'not Word'),('.txt',b'\x80\x00')):
            bad=self.root/('bad'+suffix); bad.write_bytes(data)
            with self.assertRaises(ValueError):self.ingest(bad)
            self.assertEqual(count,self.db.count())
        bible=self.root/'Kinh-Thanh.txt'; bible.write_text('Một đoạn văn không có số chương hoặc câu.',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'không đoán'):
            self.ingest(bible)
        self.assertEqual(count,self.db.count())

    def test_legacy_doc_reader_enters_same_ingest_pipeline(self):
        path=self.root/'giao-ly.doc'; path.write_bytes(bytes.fromhex('D0CF11E0A1B11AE1')+b'test')
        response=Mock(returncode=0,stdout=json.dumps({'body':'Giáo lý\n1. Câu hỏi?\nCâu trả lời của nguồn.'}))
        with patch('loaddocs.source_documents.shutil.which',return_value='node'), patch('loaddocs.source_documents.subprocess.run',return_value=response) as convert:
            result=self.ingest(path)
        self.assertEqual(60,convert.call_args.kwargs['timeout'])
        self.assertIsInstance(convert.call_args.args[0],list)
        self.assertEqual('paragraph',result.location_unit)
        self.assertEqual({'doc'},{r['metadata']['source_format'] for r in self.db.get_all_documents()})

    def test_failed_embedding_and_cancel_leave_committed_txt_unchanged(self):
        path=self.root/'giao-ly.txt'; path.write_text('Giáo lý\n1. Nội dung cũ.',encoding='utf-8')
        self.ingest(path)
        before=self.db.get_all_documents()
        path.write_text('Giáo lý\n1. Nội dung mới.',encoding='utf-8')
        self.embedder.embed_documents.side_effect=RuntimeError('embedding failed')
        with self.assertRaisesRegex(RuntimeError,'embedding failed'):self.ingest(path)
        self.assertEqual(before,self.db.get_all_documents())
        event=threading.Event();event.set()
        with self.assertRaises(IngestCancelledError):self.ingest(path,cancel_event=event)
        self.assertEqual(before,self.db.get_all_documents())


if __name__ == '__main__':unittest.main()
