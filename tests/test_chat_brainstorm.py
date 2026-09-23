"""Test verification for conversational AI brainstorming & script conversion."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.content_agent import ContentAgent
from app.schemas.contracts import ScriptContract

def test_chat_and_convert():
    print("🧪 [TEST] Kiểm tra tính năng Chat Brainstorm & Convert Script...")
    agent = ContentAgent()
    session_id = "test_session_123"

    # 1. Thử chat brainstorm
    print("1. Gửi tin nhắn thảo luận tới Gemini AI...")
    reply = agent.chat_brainstorm(
        session_id=session_id,
        user_message="Mình muốn làm video về 3 dấu hiệu của người có EQ cực cao, cho mình góc nhìn bất ngờ.",
        channel_niche="psychology",
        language="vi"
    )
    assert reply and len(reply) > 20, "Phản hồi chat quá ngắn hoặc rỗng!"
    print(f"   -> AI Director phản hồi ({len(reply)} ký tự):")
    print(f"      {reply[:120]}...\n")

    # 2. Kiểm tra lịch sử hội thoại được lưu
    assert session_id in agent.conversations, "Lịch sử phiên không được lưu!"
    assert len(agent.conversations[session_id]) == 2, f"Số lượt chat mong đợi 2, nhận được {len(agent.conversations[session_id])}"
    print("2. Lịch sử hội thoại: Đã lưu chính xác 2 lượt (user + model) - OK\n")

    # 3. Thử chuyển đổi cuộc thảo luận thành kịch bản phân cảnh chuẩn
    print("3. Chuyển đổi cuộc thảo luận thành kịch bản phân cảnh chuẩn ScriptContract...")
    script = agent.convert_discussion_to_script(
        session_id=session_id,
        channel_niche="psychology",
        language="en"
    )
    assert isinstance(script, ScriptContract), "Kết quả không phải là ScriptContract!"
    assert len(script.scenes) >= 1, "Kịch bản không có phân cảnh nào!"
    print(f"   -> Tiêu đề kịch bản: {script.title}")
    print(f"   -> Hook 3s: \"{script.hook}\"")
    print(f"   -> Số phân cảnh: {len(script.scenes)}, Thời lượng ước tính: {script.total_estimated_duration:.1f}s")
    print("   -> Pydantic Contract Validation: PASSED - OK\n")

    print("=" * 60)
    print("🎉 TẤT CẢ KIỂM THỬ CHAT BRAINSTORM & CONVERT ĐỀU THÀNH CÔNG!")
    print("=" * 60)

if __name__ == "__main__":
    test_chat_and_convert()
