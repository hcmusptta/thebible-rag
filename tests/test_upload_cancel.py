import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class UploadCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("services.runtime_config.write_public_runtime_config"):
            import main
        self.main = main
        self.session = {}
        self.events = []
        self.started = asyncio.Event()
        self.reply = asyncio.Future()
        async def ask():
            self.started.set()
            try:
                return await self.reply
            finally:
                self.events.append("ask_finished")
        async def remove():
            self.events.append("picker_removed")
        async def end():
            self.events.append("task_end")
        self.request = SimpleNamespace(id="request-1", send=ask, remove=AsyncMock(side_effect=remove))
        self.cancel = Mock(send=AsyncMock(), remove=AsyncMock())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for context in [
            patch.object(main.cl.user_session, "get", side_effect=self.session.get),
            patch.object(main.cl.user_session, "set", side_effect=self.session.__setitem__),
            patch.object(main.cl, "AskFileMessage", return_value=self.request),
            patch.object(main.cl, "Action", return_value=self.cancel),
        ]:
            self.stack.enter_context(context)
        self.ingest = self.stack.enter_context(patch.object(main, "_ingest_file", new_callable=AsyncMock))
        self.home = self.stack.enter_context(patch.object(main, "_send_home_message", new_callable=AsyncMock))
        self.end = self.stack.enter_context(patch.object(main, "_end_ask_task", new=AsyncMock(side_effect=end)))

    async def start(self):
        task = asyncio.create_task(self.main.upload_pdf(Mock()))
        await asyncio.wait_for(self.started.wait(), 1)
        return task

    async def test_cancel_closes_picker_and_unlocks_after_ask_finally(self):
        task = await self.start()
        await asyncio.wait_for(self.main.cancel_pdf_upload(SimpleNamespace(payload={"request_id":"request-1"})), 1)
        await task
        self.ingest.assert_not_awaited()
        self.request.remove.assert_awaited_once()
        self.cancel.remove.assert_awaited_once()
        self.assertIsNone(self.session["pending_pdf_upload"])
        self.assertEqual(["ask_finished", "picker_removed", "task_end"], self.events)
        self.assertIn("Đã hủy", self.home.call_args.args[0])
        # Can open a new chooser immediately after cancel.
        self.reply = asyncio.Future(); self.started.clear()
        reopened = await self.start()
        self.reply.set_result(None)
        await reopened
        self.assertEqual(2, self.end.await_count)

    async def test_file_selection_ingests_once_and_cleans_picker(self):
        task = await self.start()
        file = SimpleNamespace(name="file.pdf")
        self.reply.set_result([file])
        await task
        self.ingest.assert_awaited_once_with(file)
        self.home.assert_not_awaited()
        self.request.remove.assert_awaited_once()
        self.assertIsNone(self.session["pending_pdf_upload"])

    async def test_timeout_returns_home_and_unlocks(self):
        task = await self.start()
        self.reply.set_result(None)
        await task
        self.ingest.assert_not_awaited()
        self.end.assert_awaited_once()
        self.assertIn("Đã đóng", self.home.call_args.args[0])
        self.assertIsNone(self.session["pending_pdf_upload"])

    async def test_old_cancel_and_duplicate_open_do_not_affect_active_request(self):
        task = await self.start()
        original = self.session["pending_pdf_upload"]
        await self.main.upload_pdf(Mock())
        await self.main.cancel_pdf_upload(SimpleNamespace(payload={"request_id":"old-request"}))
        self.assertIs(original, self.session["pending_pdf_upload"])
        self.assertFalse(self.reply.done())
        await self.main.cancel_pdf_upload(SimpleNamespace(payload={"request_id":"request-1"}))
        await task

    async def test_external_cancellation_cleans_and_propagates(self):
        task = await self.start()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.request.remove.assert_awaited_once()
        self.end.assert_awaited_once()
        self.assertIsNone(self.session["pending_pdf_upload"])
        self.ingest.assert_not_awaited()

    async def test_late_cancel_does_not_interrupt_ingest(self):
        processing = asyncio.Event(); finish = asyncio.Event()
        async def ingest(file):
            processing.set()
            await finish.wait()
        self.ingest.side_effect = ingest
        task = await self.start()
        self.reply.set_result([Mock()])
        await asyncio.wait_for(processing.wait(), 1)
        await self.main.cancel_pdf_upload(SimpleNamespace(payload={"request_id":"request-1"}))
        self.assertFalse(task.done())
        finish.set()
        await task
        self.ingest.assert_awaited_once()

if __name__ == "__main__":
    unittest.main()
