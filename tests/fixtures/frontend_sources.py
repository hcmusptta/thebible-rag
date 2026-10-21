"""Isolated native Chainlit source-panel fixture; no RAG, LLM or document database."""
import chainlit as cl


@cl.on_chat_start
async def start():
    cl.user_session.set("fixture_turn", 0)
    await cl.Message(content="Môi trường kiểm thử giao diện nguồn, không dùng dữ liệu thật.").send()


@cl.on_message
async def answer(message: cl.Message):
    turn = cl.user_session.get("fixture_turn", 0) + 1
    cl.user_session.set("fixture_turn", turn)
    await cl.Message(
        content=f"Câu trả lời kiểm thử {turn}. Đối chiếu [Nguồn 1] và [Nguồn 2].",
        elements=[
            cl.Text(name="Nguồn 1", content=f"Nguồn kiểm thử thứ nhất, lượt {turn}.", display="side"),
            cl.Text(name="Nguồn 2", content=f"Nguồn kiểm thử thứ hai, lượt {turn}.", display="side"),
        ],
    ).send()
