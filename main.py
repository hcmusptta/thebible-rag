import asyncio
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, List

import chainlit as cl
from chainlit.input_widget import Select, TextInput
from loaddocs.document_types import DOCUMENT_LABELS, source_details, source_location
from loaddocs.source_documents import SUPPORTED_EXTENSIONS, UPLOAD_ACCEPT

from services.ans import answer_question, get_llm_service
from services.readdocs import IngestCancelledError, IngestResult, get_max_document_size_mb, process_uploaded_document
from services.runtime_config import write_public_runtime_config
from vectordb.vector_store import CollectionUnavailableError, default_db


logger = logging.getLogger(__name__)
CHAT_HISTORY_KEY = "chat_history"


async def _document_settings():
    catalog = await asyncio.to_thread(default_db.get_source_catalog)
    editions = {f"{name} — {details.get('translation') or 'bản dịch chưa khai báo'}": details["edition_id"]
                for name, details in catalog.items() if details.get("edition_id")}
    settings = await cl.ChatSettings([
        Select(id="upload_type", label="Loại tài liệu sẽ nạp", items={"Tự động nhận diện": "auto", **{v: k for k, v in DOCUMENT_LABELS.items()}}, initial_value="auto"),
        Select(id="search_type", label="Nguồn dùng để trả lời", items={"Tất cả": "all", **{v: k for k, v in DOCUMENT_LABELS.items()}}, initial_value="all"),
        Select(id="edition_id", label="Bản tài liệu / bản dịch dùng để tra cứu", items={"Các bản đã nạp (hiển thị riêng từng bản)": "all", **editions}, initial_value="all"),
        TextInput(id="translation", label="Tên bản dịch của tài liệu (để trống nếu chưa biết)", initial=""),
    ]).send()
    await update_document_settings(settings)


@cl.on_settings_update
async def update_document_settings(settings):
    cl.user_session.set("document_settings", {
        "upload_type": settings.get("upload_type") or "auto",
        "search_type": settings.get("search_type") or "all",
        "edition_id": "" if settings.get("edition_id") == "all" else settings.get("edition_id", ""),
        "translation": settings.get("translation", ""),
    })


write_public_runtime_config(
    Path(__file__).resolve().parent / "public" / "runtime-config.json"
)


def _main_actions() -> List[cl.Action]:
    return [
        cl.Action(
            name="upload_pdf",
            label="Thêm tài liệu",
            icon="upload",
            payload={},
            tooltip="Nạp hoặc cập nhật PDF, Word hoặc TXT",
        ),
        cl.Action(
            name="show_documents",
            label="Kho tài liệu",
            icon="library",
            payload={},
            tooltip="Xem, tóm tắt hoặc xóa tài liệu",
        ),
        cl.Action(
            name="run_benchmark",
            label="Đánh giá RAG",
            icon="chart-no-axes-column",
            payload={},
            tooltip="Chạy benchmark RAGAS",
        ),
        cl.Action(
            name="reset_database",
            label="Xóa toàn bộ",
            icon="trash-2",
            payload={},
            tooltip="Xóa toàn bộ kho vector",
        ),
    ]


def _history() -> list:
    history = cl.user_session.get(CHAT_HISTORY_KEY)
    if history is None:
        history = []
        cl.user_session.set(CHAT_HISTORY_KEY, history)
    return history


def _source_elements(sources: list) -> List[cl.Text]:
    elements = []
    for index, source in enumerate(sources, start=1):
        metadata = source.get("metadata") or {}
        score = source.get("rerank_score")
        score_name = "rerank"
        if score is None:
            score = source.get("hybrid_score", 0.0)
            score_name = "hybrid"
        header_lines = [
            f"Tài liệu: {metadata.get('source', 'không rõ')}",
            source_location(metadata),
        ]
        header_lines.extend(source_details(metadata))
        if metadata.get("reference"):
            header_lines.append(f"Tọa độ bản nguồn: {metadata['reference']}")
        if metadata.get("book"):
            header_lines.append(f"Sách: {metadata['book']}")
        if metadata.get("chapter") is not None:
            header_lines.append(f"Chương: {metadata['chapter']}")
        verse_start = metadata.get("verse_start")
        verse_end = metadata.get("verse_end")
        if verse_start is not None and not metadata.get("reference"):
            verse_label = str(verse_start)
            if verse_end is not None and verse_end != verse_start:
                verse_label = f"{verse_start}-{verse_end}"
            header_lines.append(f"Câu: {verse_label}")
        header_lines.append(f"Điểm {score_name}: {float(score):.4f}")
        header = "\n".join(header_lines) + "\n\n"
        elements.append(
            cl.Text(
                name=f"Nguồn {index}",
                content=header + source.get("content", ""),
                display="side",
            )
        )
    return elements


async def _send_home_message(content: str) -> None:
    await cl.Message(content=content, actions=_main_actions()).send()


def _render_ingest_progress(file_name: str, percent: int, detail: str) -> str:
    width = 24
    filled = round(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return (
        f"Đang nạp `{file_name}`...\n\n"
        f"`{bar} {percent:3d}%`\n\n"
        f"{detail}"
    )


async def _run_ingest_with_progress(file: Any, status: cl.Message, settings: dict | None = None) -> IngestResult:
    progress_lock = threading.Lock()
    latest_progress = None
    cancel_event = threading.Event()

    def report_progress(percent: int, detail: str) -> None:
        nonlocal latest_progress
        # A large TXT can report over 100,000 lines; retain only the latest state.
        with progress_lock:
            latest_progress = (percent, detail)

    settings = settings or {}
    upload_type = settings.get("upload_type", "auto")
    if upload_type not in ("auto", *DOCUMENT_LABELS):
        upload_type = "auto"
    worker = asyncio.create_task(
        asyncio.to_thread(
            process_uploaded_document,
            file.path,
            file.name,
            progress_callback=report_progress,
            cancel_event=cancel_event,
            document_type=upload_type,
            metadata={"translation": settings.get("translation", "")},
        )
    )

    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=0.25)
            with progress_lock:
                latest, latest_progress = latest_progress, None
            if latest is None or worker.done():
                continue
            percent, detail = latest
            status.content = _render_ingest_progress(file.name, percent, detail)
            await status.update()
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        # A GPU call already running cannot be interrupted; wait for rollback acknowledgement.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except IngestCancelledError:
                break
        try:
            # A cancellation after the commit decision reports the completed ingest.
            return worker.result()
        except IngestCancelledError:
            raise asyncio.CancelledError("Ingest stopped and rolled back.") from None


async def _end_ask_task() -> None:
    """Balance the task_start emitted by Chainlit after an Ask* response."""
    try:
        await cl.context.emitter.task_end()
    except Exception:
        logger.exception("Unable to end Chainlit ask task")


async def _ingest_file(file: Any) -> None:
    status = cl.Message(
        content=_render_ingest_progress(
            file.name,
            0,
            "Đang khởi tạo bộ xử lý...",
        )
    )
    await status.send()
    try:
        result = await _run_ingest_with_progress(file, status, cl.user_session.get("document_settings"))
    except (ValueError, CollectionUnavailableError) as error:
        status.content = f"Không thể nạp `{file.name}`: {error}"
    except asyncio.CancelledError:
        status.content = f"Đã dừng nạp `{file.name}`."
        status.actions = _main_actions()
        await status.update()
        raise
    except Exception:
        logger.exception("Unexpected document ingest error")
        status.content = (
            f"Không thể nạp `{file.name}` do lỗi hệ thống. "
            "Chi tiết đã được ghi vào log."
        )
    else:
        if result.duplicate:
            status.content = (
                f"`{result.source}` đã có trong kho, không tạo dữ liệu trùng."
            )
        else:
            unit = {'page':'trang','line':'dòng','paragraph':'đoạn'}.get(result.location_unit,'đoạn')
            status.content = (
                f"Đã nạp `{result.source}` — {DOCUMENT_LABELS.get(result.document_type, result.document_type)}: {result.page_count} "
                f"{unit}, "
                f"{result.chunk_count} chunk."
            )
            if result.replaced_chunk_count:
                status.content += (
                    f" Đã thay thế {result.replaced_chunk_count} chunk của phiên bản cũ."
                )
            if result.warning:
                status.content += f" Cảnh báo: {result.warning}"
    status.actions = _main_actions()
    await status.update()


async def _answer(question: str, document_type: str | None = None, edition_id: str | None = None) -> None:
    question = " ".join(str(question or "").split()).strip()
    if not question:
        await cl.Message(content="Vui lòng nhập câu hỏi về tài liệu.").send()
        return
    try:
        vector_count = await asyncio.to_thread(default_db.count)
    except CollectionUnavailableError as error:
        await _send_home_message(str(error))
        return
    if vector_count == 0:
        await _send_home_message("Kho tài liệu đang trống. Hãy nạp PDF, Word hoặc TXT trước khi đặt câu hỏi.")
        return

    history = _history()
    history.append({"role": "user", "content": question})
    response = cl.Message(content="Đang tìm thông tin trong tài liệu...")
    await response.send()
    diagnostics = {}
    try:
        answer, sources = await asyncio.to_thread(
            answer_question, question, list(history),
            diagnostics=diagnostics,
            document_type=document_type,
            edition_id=edition_id or (cl.user_session.get("document_settings") or {}).get("edition_id") or None,
        )
    except CollectionUnavailableError as error:
        history.pop()
        response.content = str(error)
        response.actions = _main_actions()
        await response.update()
        return
    except Exception:
        logger.exception("Unable to answer question")
        history.pop()
        response.content = "Không thể tạo câu trả lời lúc này. Vui lòng kiểm tra cấu hình LLM và thử lại."
        response.actions = _main_actions()
        await response.update()
        return

    history.append({"role": "assistant", "content": answer,
                    "person_resolution": diagnostics.get('person_resolution', {})})
    logger.info("RAG answer: route=%s validation=%s cause=%s sources=%s",
                diagnostics.get('retrieval_route'), diagnostics.get('answer_validation'),
                diagnostics.get('validation_reason'), len(sources))
    response.content = answer
    response.elements = _source_elements(sources)
    response.actions = _main_actions()
    await response.update()


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set(CHAT_HISTORY_KEY, [])
    try:
        vector_count = await asyncio.to_thread(default_db.count)
        await _document_settings()
    except CollectionUnavailableError as error:
        await _send_home_message(str(error))
        return
    await _send_home_message(
        "Chào bạn. Hãy đặt câu hỏi về tài liệu hoặc dùng các nút bên dưới để quản lý tài liệu PDF, Word và TXT.\n\n"
        f"Kho hiện có **{vector_count} vector**."
    )


@cl.on_message
async def on_message(message: cl.Message) -> None:
    files = [
        element
        for element in (message.elements or [])
        if getattr(element,'path',None)
    ]
    if files:
        for file in files:
            if Path(getattr(file,'name','') or file.path).suffix.lower() in SUPPORTED_EXTENSIONS:
                await _ingest_file(file)
            else:
                await cl.Message(content='Chỉ hỗ trợ tài liệu PDF, DOCX, DOC và TXT.').send()
        return
    await _answer(message.content, (cl.user_session.get("document_settings") or {}).get("search_type", "all"))


@cl.action_callback("upload_pdf")
async def upload_pdf(action: cl.Action) -> None:
    if cl.user_session.get("pending_pdf_upload"):
        return
    request = cl.AskFileMessage(
        content="Chọn loại tài liệu trong Cài đặt, sau đó tải PDF, Word (.docx/.doc) hoặc TXT Unicode. Bấm Hủy nếu chưa muốn thêm tài liệu.",
        accept=UPLOAD_ACCEPT,
        max_size_mb=get_max_document_size_mb(),
        max_files=1,
        timeout=180,
    )
    cancel = cl.Action(
        name="cancel_pdf_upload", label="Hủy", icon="x",
        payload={"request_id": request.id},
        tooltip="Đóng hộp chọn file và quay lại trò chuyện",
    )
    state = {"request_id": request.id, "task": asyncio.create_task(request.send()),
             "cancelled": False, "finished": asyncio.Event()}
    cl.user_session.set("pending_pdf_upload", state)
    try:
        try:
            await cancel.send(for_id=request.id)
            try:
                files = await state["task"]
            except asyncio.CancelledError:
                if not state["cancelled"]:
                    raise
                files = None
        finally:
            # Await the ask's cancellation first: Chainlit emits task_start in its finally.
            if not state["task"].done():
                state["task"].cancel()
            await asyncio.gather(state["task"], return_exceptions=True)
            await cancel.remove()
            await request.remove()
        if state["cancelled"]:
            await _send_home_message("Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện.")
        elif files:
            await _ingest_file(files[0])
        else:
            await _send_home_message("Đã đóng hộp chọn file vì chưa có tài liệu được chọn.")
    finally:
        cl.user_session.set("pending_pdf_upload", None)
        await _end_ask_task()
        state["finished"].set()


@cl.action_callback("cancel_pdf_upload")
async def cancel_pdf_upload(action: cl.Action) -> None:
    state = cl.user_session.get("pending_pdf_upload")
    if not state or action.payload.get("request_id") != state["request_id"]:
        return
    # A late click must not cancel an ingest that has already started.
    if state["task"].done():
        return
    state["cancelled"] = True
    state["task"].cancel()
    await state["finished"].wait()


@cl.action_callback("show_documents")
async def show_documents(action: cl.Action) -> None:
    try:
        catalog = await asyncio.to_thread(default_db.get_source_catalog)
        sources = sorted(catalog)
    except CollectionUnavailableError as error:
        await _send_home_message(str(error))
        return
    if not sources:
        await _send_home_message("Kho tài liệu đang trống.")
        return

    actions = []
    for source in sources:
        actions.extend(
            [
                cl.Action(
                    name="summarize_document",
                    label=f"Tóm tắt: {source}",
                    icon="file-text",
                    payload={"source": source},
                ),
                cl.Action(
                    name="delete_document",
                    label=f"Xóa: {source}",
                    icon="trash-2",
                    payload={"source": source},
                ),
            ]
        )
    groups = []
    for kind, label in DOCUMENT_LABELS.items():
        names = [name for name in sources if catalog[name]["document_type"] == kind]
        if names:
            groups.append(f"**{label}**\n" + "\n".join(f"- `{name}`" for name in names))
    await cl.Message(
        content="Các tài liệu trong kho:\n\n" + "\n\n".join(groups),
        actions=actions,
    ).send()


@cl.action_callback("summarize_document")
async def summarize_document(action: cl.Action) -> None:
    source = str(action.payload.get("source", ""))
    try:
        catalog = await asyncio.to_thread(default_db.get_source_catalog)
        details = catalog.get(source, {})
        if details.get("document_type") == "scripture":
            request = await cl.AskUserMessage(content=f"Chọn đoạn trong `{source}` để tóm tắt, ví dụ St 1 hoặc Ga 3:1–21.", timeout=180).send()
            if request and request.get("output", "").strip():
                from loaddocs.scripture_references import parse_references
                selected = request["output"].strip()
                try:
                    if not parse_references(selected):
                        raise ValueError("Cần tên sách và số chương/câu.")
                except ValueError as error:
                    await cl.Message(content=str(error)).send()
                    return
                await _answer(f"Tóm tắt {selected}", "scripture", details.get("edition_id"))
            return
        content = await asyncio.to_thread(default_db.get_document_content, source)
    except CollectionUnavailableError as error:
        await _send_home_message(str(error))
        return
    if not content:
        await cl.Message(content=f"Không tìm thấy nội dung của `{source}`.").send()
        return

    status = cl.Message(content=f"Đang tóm tắt `{source}`...")
    await status.send()
    try:
        summary = await asyncio.to_thread(
            get_llm_service().summarize_document, content
        )
    except Exception:
        logger.exception("Unable to summarize %s", source)
        status.content = "Không thể tóm tắt tài liệu. Vui lòng kiểm tra cấu hình LLM."
    else:
        status.content = f"## Tóm tắt `{source}`\n\n{summary}"
    status.actions = _main_actions()
    await status.update()


@cl.action_callback("delete_document")
async def delete_document(action: cl.Action) -> None:
    source = str(action.payload.get("source", ""))
    try:
        confirmation = await cl.AskActionMessage(
            content=f"Xác nhận xóa tài liệu `{source}`?",
            actions=[
                cl.Action(name="confirm", label="Xóa", icon="trash-2", payload={"value": "yes"}),
                cl.Action(name="cancel", label="Hủy", icon="x", payload={"value": "no"}),
            ],
            timeout=60,
        ).send()
        if not confirmation or confirmation.get("payload", {}).get("value") != "yes":
            return
        try:
            deleted = await asyncio.to_thread(default_db.delete_data_select, source)
        except Exception:
            logger.exception("Unable to delete %s", source)
            await cl.Message(content=f"Không thể xóa `{source}`.").send()
            return
        await _send_home_message(f"Đã xóa `{source}` và {deleted} chunk liên quan.")
    finally:
        await _end_ask_task()


@cl.action_callback("reset_database")
async def reset_database(action: cl.Action) -> None:
    try:
        confirmation = await cl.AskActionMessage(
            content="Thao tác này sẽ xóa toàn bộ kho tài liệu. Bạn có chắc chắn?",
            actions=[
                cl.Action(name="confirm", label="Xóa toàn bộ", icon="trash-2", payload={"value": "yes"}),
                cl.Action(name="cancel", label="Hủy", icon="x", payload={"value": "no"}),
            ],
            timeout=60,
        ).send()
        if not confirmation or confirmation.get("payload", {}).get("value") != "yes":
            return
        try:
            await asyncio.to_thread(default_db.reset)
        except Exception:
            logger.exception("Unable to reset vector database")
            await cl.Message(content="Không thể xóa kho dữ liệu.").send()
            return
        await _send_home_message("Đã xóa toàn bộ kho tài liệu.")
    finally:
        await _end_ask_task()


@cl.action_callback("run_benchmark")
async def run_benchmark(action: cl.Action) -> None:
    try:
        mode = await cl.AskActionMessage(
            content="Chọn chế độ benchmark RAGAS.",
            actions=[
                cl.Action(name="light", label="Nhanh", icon="gauge", payload={"full": False}),
                cl.Action(name="full", label="Đầy đủ", icon="chart-no-axes-column", payload={"full": True}),
            ],
            timeout=60,
        ).send()
        if not mode:
            return
        full_metrics = bool(mode.get("payload", {}).get("full", False))
        status = cl.Message(content="Đang chạy benchmark RAGAS...")
        await status.send()
        try:
            from services.eval_ragas import run_ragas_benchmark

            report = await asyncio.to_thread(
                run_ragas_benchmark, None, full_metrics, None
            )
            from services.benchmark_manifest import save_benchmark_report
            report_path = Path(default_db.db_path)/'evaluation'/f'ragas-{uuid.uuid4().hex}.json'
            await asyncio.to_thread(save_benchmark_report,report,report_path)
        except Exception:
            logger.exception("RAGAS benchmark failed")
            status.content = (
                "Benchmark thất bại. Hãy kiểm tra golden testset, "
                "kho dữ liệu và Ollama."
            )
        else:
            summary = report.get("summary", {})
            lines = [f"- **{name}**: {value:.4f}" for name, value in summary.items()]
            retrieval_summary = report.get("retrieval_summary", {})
            retrieval_lines = [
                f"- **{name}**: {value:.4f}"
                for name, value in retrieval_summary.items()
            ]
            rows = report.get("rows", [])
            empty_context_ids = [
                str(row.get("id") or index + 1)
                for index, row in enumerate(rows)
                if not row.get("retrieved_contexts")
            ]
            status.content = (
                "## Kết quả RAGAS\n\n"
                + ("\n".join(lines) or "Không có metric hợp lệ.")
                + (
                    f"\n\n**Không lấy được context: {len(empty_context_ids)}/{len(rows)} mẫu** "
                    f"({', '.join(empty_context_ids)}). Các mẫu này vẫn được tính vào kết quả; "
                    "các metric phụ thuộc context nhận điểm 0."
                    if empty_context_ids else ""
                )
                + (
                    "\n\n### Retrieval stages\n\n" + "\n".join(retrieval_lines)
                    if retrieval_lines else ""
                )
            )
            status.content += f"\n\nBáo cáo có cấu hình và dấu vết chạy: `{report_path.name}` trong thư mục evaluation của kho."
        status.actions = _main_actions()
        await status.update()
    finally:
        await _end_ask_task()
