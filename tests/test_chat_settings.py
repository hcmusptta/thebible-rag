import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ChatSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_flood_is_coalesced_and_worker_error_reaches_ui(self):
        def ingest(*args, progress_callback, **kwargs):
            for n in range(109537):
                progress_callback(10 + int(40*n/109537), f'Line {n}')
            progress_callback(58, 'Checking structure')
            raise ValueError('Missing book titles')

        status = AsyncMock()
        # Progress must not schedule one event-loop callback per source line.
        loop = asyncio.get_running_loop()
        with patch.object(self.main, 'process_uploaded_document', side_effect=ingest), \
             patch.object(loop, 'call_soon_threadsafe', wraps=loop.call_soon_threadsafe) as schedule:
            with self.assertRaisesRegex(ValueError, 'Missing book titles'):
                await asyncio.wait_for(self.main._run_ingest_with_progress(
                    SimpleNamespace(path='unused', name='Kinh-Thanh.txt'), status), timeout=5)
        self.assertLess(schedule.call_count, 10)
        self.assertLess(status.update.await_count, 10)

    async def test_word_and_txt_uploads_use_original_name_when_temp_path_has_no_extension(self):
        for name in ('sample.docx','sample.doc','sample.txt','sample.PDF'):
            file=SimpleNamespace(path='temporary-upload-id',name=name)
            with self.subTest(name=name), patch.object(self.main,'_ingest_file',new=AsyncMock()) as ingest, \
                 patch.object(self.main,'_answer',new=AsyncMock()) as answer:
                await self.main.on_message(SimpleNamespace(content='',elements=[file]))
                ingest.assert_awaited_once_with(file)
                answer.assert_not_awaited()

    async def test_upload_picker_accepts_all_document_formats(self):
        from loaddocs.source_documents import UPLOAD_ACCEPT
        self.assertEqual({'.pdf','.docx','.doc','.txt'},{x for values in UPLOAD_ACCEPT.values() for x in values})
        # Validate against the installed Chainlit schema, without sending a UI request.
        with patch('chainlit.message.MessageBase.__post_init__'):
            request=self.main.cl.AskFileMessage(content='Choose',accept=UPLOAD_ACCEPT)
        self.assertEqual(UPLOAD_ACCEPT,request.accept)
    def setUp(self):
        with patch('services.runtime_config.write_public_runtime_config'):
            import main
        self.main = main
        self.session = {}

    async def test_select_payload_has_no_empty_options_and_all_editions_has_no_filter(self):
        for catalog in ({}, {'Kinh-Thanh.pdf':{'edition_id':'edition-hash','translation':'Bản A'}}):
            with self.subTest(catalog=catalog):
                payload = []
                async def capture_send(widget):
                    payload.extend(widget._inputs_as_dicts())
                    return widget.settings()
                with (
                    patch('chainlit.chat_settings.ChatSettings.send', new=capture_send),
                    patch.object(self.main.default_db, 'get_source_catalog', return_value=catalog),
                    patch.object(self.main.cl.user_session, 'set', side_effect=self.session.__setitem__),
                ):
                    await self.main._document_settings()
                for widget in payload:
                    if widget['type'] == 'select':
                        values = [item['value'] for item in widget['items']]
                        self.assertTrue(all(values), widget)
                        self.assertIn(widget['initial'], values)
                edition = next(widget for widget in payload if widget['id'] == 'edition_id')
                self.assertEqual('all', edition['initial'])
                self.assertEqual('', self.session['document_settings']['edition_id'])
                self.assertEqual('auto', self.session['document_settings']['upload_type'])
                self.assertEqual('all', self.session['document_settings']['search_type'])
                if catalog:
                    self.assertIn('edition-hash', [item['value'] for item in edition['items']])

    async def test_settings_update_maps_all_editions_and_preserves_specific_selection(self):
        for selected, expected in (('all',''), ('edition-hash','edition-hash'), ('','')):
            with self.subTest(selected=selected), patch.object(self.main.cl.user_session, 'set', side_effect=self.session.__setitem__):
                settings = {'upload_type':'catechism','search_type':'catechism','edition_id':selected,'translation':'Bản A'}
                await self.main.update_document_settings(settings)
                self.assertEqual({**settings,'edition_id':expected}, self.session['document_settings'])
                self.assertEqual(selected, settings['edition_id'])

    async def test_all_editions_reaches_answering_without_literal_all_filter(self):
        for selected, expected in (('all',None), ('edition-hash','edition-hash')):
            with (
                self.subTest(selected=selected),
                patch.object(self.main.cl.user_session, 'set', side_effect=self.session.__setitem__),
                patch.object(self.main.cl.user_session, 'get', side_effect=self.session.get),
                patch.object(self.main.default_db, 'count', return_value=1),
                patch.object(self.main, 'answer_question', return_value=('Đáp án.',[])) as answer,
                patch.object(self.main.cl, 'Message') as message,
                patch.object(self.main, '_main_actions', return_value=[]),
            ):
                message.return_value.send = AsyncMock()
                message.return_value.update = AsyncMock()
                await self.main.update_document_settings({'edition_id':selected})
                await self.main._answer('Câu hỏi?')
                self.assertEqual(expected, answer.call_args.kwargs['edition_id'])

    async def test_upload_defaults_to_auto_and_keeps_explicit_document_type(self):
        for selected, expected in ((None,'auto'), ('auto','auto'), ('unknown','auto'), ('catechism','catechism'), ('scripture','scripture'), ('commentary','commentary')):
            with self.subTest(selected=selected), patch.object(self.main, 'process_uploaded_document', return_value='ingested') as ingest:
                settings = {} if selected is None else {'upload_type':selected}
                result = await self.main._run_ingest_with_progress(
                    SimpleNamespace(path='test.pdf',name='toat-yeu-giao-ly-htcg.pdf'), AsyncMock(), settings)
                self.assertEqual('ingested', result)
                self.assertEqual(expected, ingest.call_args.kwargs['document_type'])


if __name__ == '__main__':
    unittest.main()
