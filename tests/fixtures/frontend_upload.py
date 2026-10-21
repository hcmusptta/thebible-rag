"""Native upload UI fixture; no production database, model or document processing."""
import asyncio
import chainlit as cl
from chainlit.input_widget import Select


def actions():
    return [cl.Action(name="upload_pdf", label="Thêm tài liệu", payload={})]


@cl.on_chat_start
async def start():
    await cl.ChatSettings([Select(id="upload_type", label="Loại tài liệu sẽ nạp", values=["Tự nhận diện", "Bản văn Kinh Thánh", "Giáo lý"], initial_index=0)]).send()
    await cl.Message(content="Kiểm thử tải tài liệu, không dùng kho dữ liệu thật.", actions=actions()).send()


@cl.action_callback("upload_pdf")
async def upload(action):
    request = cl.AskFileMessage(
        content="Chọn loại tài liệu trong Cài đặt, sau đó tải PDF, Word (.docx/.doc) hoặc TXT Unicode. Bấm Hủy nếu chưa muốn thêm tài liệu.",
        accept=["text/plain", "application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        max_size_mb=100, timeout=180,
    )
    cancel = cl.Action(name="cancel_pdf_upload", label="Hủy", payload={"request_id": request.id})
    state = {"task": asyncio.create_task(request.send()), "cancelled": False}
    cl.user_session.set("upload_fixture", state)
    await cancel.send(for_id=request.id)
    try:
        files = await state["task"]
    except asyncio.CancelledError:
        files = None
    await cancel.remove()
    await request.remove()
    if state["cancelled"]:
        await cl.Message(content="Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện.", actions=actions()).send()
    elif files:
        status = cl.Message(content=f"Đang nạp `{files[0].name}`...\n\nTiến trình kiểm thử 0%.")
        await status.send()
        await asyncio.sleep(1)
        status.content = f"Đang nạp `{files[0].name}`...\n\nTiến trình kiểm thử 50%."
        await status.update()
        await asyncio.sleep(1)
        status.content = (f"Không thể nạp `{files[0].name}`: lỗi kiểm thử." if "error" in files[0].name
                          else f"Đã nạp `{files[0].name}` — dữ liệu kiểm thử: 1 dòng, 1 chunk.")
        status.actions = actions()
        await status.update()
    else:
        await cl.Message(content="Đã đóng hộp chọn file vì chưa có tài liệu được chọn.", actions=actions()).send()
    cl.user_session.set("upload_fixture", None)
    await cl.context.emitter.task_end()


@cl.action_callback("cancel_pdf_upload")
async def cancel(action):
    state = cl.user_session.get("upload_fixture")
    if state and not state["task"].done():
        state["cancelled"] = True
        state["task"].cancel()


@cl.on_message
async def answer(message):
    await cl.Message(content="Có thể tiếp tục trò chuyện sau khi hủy.", actions=actions()).send()
