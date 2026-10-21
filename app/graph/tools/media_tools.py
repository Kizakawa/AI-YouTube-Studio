"""
app.graph.tools.media_tools — Bộ công cụ (@tool) thông minh cho LLM Đạo diễn trong LangGraph.

Thiết kế theo triết lý Hybrid Viral Shorts & Adaptive Dual-Scope Search (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
1. Cơ chế Tự Động Phân Loại Ngữ Nghĩa Câu Văn Khi Chia Nhỏ (`search_scope`):
   - `SCENE_SPECIFIC` (Câu văn chia nhỏ VẪN CÓ NGHĨA ĐỘC LẬP):
     Khi tách câu thoại của phân cảnh ra để đi search riêng lẻ mà vẫn rõ chủ thể + hành động thị giác
     (VD: "stray golden retriever shivering in pouring rain", "vet warming tiny rescued kitten in clinic"),
     hệ thống cứ thế đi search trực tiếp theo ý câu văn nhỏ đó!
   - `SCRIPT_OVERALL` (Câu văn chia nhỏ BỊ VÔ NGHĨA / CỤT NGỦN / LỆCH CHỦ ĐỀ KHI ĐỨNG MỘT MÌNH):
     Khi kịch bản chia nhỏ thành các câu dẫn, câu chuyển ý, giải thích tâm lý, hoặc chi tiết phụ
     (VD: "For three days nobody knew why...", "When they looked inside the old leather boot...",
     "Canine behaviorists call this maternal imprinting..."), nếu đi search theo câu nhỏ sẽ vô nghĩa hoặc lạc đề.
     Khi đó hệ thống BỎ QUA từ ngữ vụn của câu nhỏ và ĐI SEARCH TỔNG THỂ XOAY QUANH KỊCH BẢN (`overall_script_query`)
     để lấy các đoạn video / hình ảnh xoay quanh câu chuyện chính ghép vào video!
2. `clone_youtube_short_peak_clip`:
   - Ưu tiên cho Cảnh 1 (Video Hook) và 2–3 cảnh Cao trào (Peak) xen kẽ trong video.
   - Hỗ trợ cả tìm kiếm theo cảnh (`SCENE_SPECIFIC`) lẫn kho video xoay quanh tổng thể kịch bản (`SCRIPT_OVERALL`),
     kèm cơ chế cắt các phân đoạn Peak không trùng lặp (`_used_short_windows`) và kiểm duyệt Gemini Vision.
3. `search_google_cinematic_image`:
   - Tìm ảnh Top Search Google Images theo câu nhỏ (`SCENE_SPECIFIC`) hoặc xoay quanh tổng thể kịch bản (`SCRIPT_OVERALL`).
"""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool

from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)
_yt_url_lock = threading.Lock()

# Bộ nhớ đệm kết quả tìm kiếm YouTube Shorts theo từ khóa & các khoảng thời gian đã cắt trên từng URL
_yt_flat_search_cache: Dict[str, List[Dict[str, Any]]] = {}
_used_short_windows: Dict[str, List[Tuple[float, float]]] = {}

# Chặn triệt để các video Podcast / Talking Head / Quảng cáo bán hàng / Đồ chơi / Game / Meme lệch chủ đề
_TALKING_HEAD_BLOCKLIST = (
    "podcast", "interview", "reaction", "reacts", "q&a", "livestream", "stream highlight",
    "talking", "lecture", "ted talk", "joe rogan", "lex fridman", "review", "unboxing",
    "vlog", "storytime", "asmr", "tutorial", "how to", "course", "lesson",
    "animation meme", "gacha", "roblox", "minecraft", "fortnite", "valorant", "lyrics", "karaoke",
    "ultrasonic", "chaser", "repeller", "bark control", "collar", "gadget", "device", "product",
    "amazon", "shop", "buy", "price", "discount", "commercial", "promo", "toy", " plush",
    "vet explains", "veterinarian", "doctor explains", "training tips", "compilation",
    "bootie", "michael kors", "fashion", "handbag", "leathercare", "shoe polish",
)

# Danh sách kênh & dấu hiệu có rủi ro bản quyền cao hoặc luôn in cứng watermark/caption (LatestSightings, MR. FRIEND, TikTok...)
_HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST = (
    "latestsightings", "latest sightings", "kruger sightings", "mr. friend", "mr friend", "mrfriend",
    "viralhog", "caters", "jukin", "newsflare", "bviral", "ladbible", "unilad", "pubity", "memezar",
    "daily dose", "the dodo", "dodo", "national geographic", "nat geo", "bbc earth", "bbc",
    "discovery", "animal planet", "smithsonian", "tmz", "inside edition",
    "tiktok", "douyin", "kwai", "capcut", "inshot", "kinemaster",
    "filmora", "alighty", "credit:", "credits:",
    "🎥", "📸", "top 10", "top 5", "tier list", "explained", "whatsapp status",
)

_YT_QUERY_FILLER_WORDS = {
    "cinematic", "4k", "8k", "hd", "edit", "edits", "visuals", "footage", "scene", "scenes",
    "dramatic", "vertical", "shot", "shots", "video", "clip", "clips", "short", "shorts",
    "movie", "still", "photo", "photograph", "picture", "image",
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "from", "and", "or",
    "retreating", "confused", "frustrated", "wary", "hesitating", "impassive", "futility",
    "superior", "dominance", "calculated", "caution", "acknowledging",
}

_GENERIC_NON_SUBJECT_WORDS = {
    "looking", "seeing", "watching", "waiting", "running", "walking", "sitting", "standing",
    "crying", "sleeping", "hiding", "holding", "pulling", "dragged", "refused", "left", "found",
    "secret", "hidden", "truth", "story", "moment", "reason", "suddenly", "finally", "never",
    "inside", "under", "behind", "dark", "cold", "warm", "tiny", "huge", "giant", "old", "new",
    "aggressive", "emotional", "sad", "happy", "scared", "terrified", "shocking", "heart",
}

# Các từ đạo cụ vụn vặt hoặc từ trừu tượng khiến câu nhỏ khi tách khỏi kịch bản bị lệch chủ đề khi search
_DISTRACTOR_PROP_OR_ABSTRACT_WORDS = {
    "boot", "boots", "bootie", "shoe", "shoes", "leather", "cardboard", "blanket", "towel",
    "porch", "deck", "doorstep", "hormone", "oxytocin", "chemical", "imprinting", "behaviorist",
    "behaviorists", "scientist", "scientists", "psychologist", "theory", "phenomenon",
    "days", "hours", "weeks", "months", "years", "nobody", "knew", "froze", "realized",
    "inch", "single", "forever", "everything", "changed", "reason", "truth", "secret",
}


@tool
def clone_youtube_short_peak_clip(
    scene_id: int,
    subject_entity: str,
    search_scope: str,
    youtube_search_query: str,
    overall_script_query: str = "",
    fallback_broad_query: str = "",
    clip_role: str = "PEAK",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    ƯU TIÊN SỐ 1 CHO CẢNH MỞ ĐẦU (HOOK - scene_id=1) VÀ 2-3 CẢNH CAO TRÀO (PEAK) XEN KẼ TRONG VIDEO.
    Công cụ này tìm kiếm video YouTube Shorts thực tế trên mạng, dò đoạn PEAK (Most Replayed / hành động cao trào),
    kiểm duyệt bằng Gemini Vision và cắt đoạn chuẩn 9:16.

    QUY TẮC QUAN TRỌNG NHẤT — CHỌN `search_scope` (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    1. `search_scope = "SCENE_SPECIFIC"`:
       - Dùng khi câu thoại của phân cảnh này tách riêng ra VẪN CÓ NGHĨA THỊ GIÁC RÕ RÀNG, ĐỘC LẬP
         (VD: "A drenched stray dog shivering in the rain", "A mother dog carrying a tiny kitten").
       - Khi đó `youtube_search_query` đi search trực tiếp hành động của câu văn nhỏ này!
    2. `search_scope = "SCRIPT_OVERALL"`:
       - Dùng khi câu thoại của phân cảnh này nếu tách lẻ ra sẽ BỊ VÔ NGHĨA, CỤT NGỦN, TRỪU TƯỢNG, CÂU DẪN CHUYỂN Ý,
         hoặc chỉ nhắc tới đồ vật vụn vặt dễ làm lệch kết quả search (VD: "For three days nobody knew why...",
         "When they looked inside the old leather boot...", "Behaviorists call this maternal imprinting...").
       - Khi đó TUYỆT ĐỐI KHÔNG search theo từ ngữ vụn của câu nhỏ, mà đặt `youtube_search_query` xoay quanh
         TỔNG THỂ NỘI DUNG KỊCH BẢN (`overall_script_query`)!
    3. `overall_script_query`: Câu truy vấn (3-5 từ tiếng Anh) mô tả cốt truyện tổng thể của cả kịch bản
       (VD: "stray dog protecting abandoned kitten", "golden retriever rescuing kitten rain").
    """
    return {
        "tool_name": "clone_youtube_short_peak_clip",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "search_scope": search_scope,
        "youtube_search_query": youtube_search_query,
        "overall_script_query": overall_script_query,
        "fallback_broad_query": fallback_broad_query,
        "clip_role": clip_role,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def search_google_cinematic_image(
    scene_id: int,
    subject_entity: str,
    search_scope: str,
    smart_image_query: str,
    overall_script_query: str = "",
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ TÌM ẢNH TOP SEARCH GOOGLE IMAGES SẮC NÉT CHO CÁC CẢNH XEN KẼ GIỮA CÁC VIDEO CLIP.

    QUY TẮC QUAN TRỌNG NHẤT — CHỌN `search_scope` (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    1. `search_scope = "SCENE_SPECIFIC"`:
       - Khi câu văn của phân cảnh này chia nhỏ ra VẪN CÓ NGHĨA ĐỘC LẬP, RÕ RÀNG khi đem đi search
         (VD: "vet examining rescued puppy and kitten on clinic table").
       - Cứ thế đặt `smart_image_query` theo đúng ý câu văn nhỏ đó (bắt buộc kèm `subject_entity`).
    2. `search_scope = "SCRIPT_OVERALL"`:
       - Khi câu văn của phân cảnh này chia nhỏ ra bị VÔ NGHĨA, MƠ HỒ, CÂU DẪN/KẾT LUẬN, hoặc nhắc đến
         đạo cụ phụ làm lạc đề (VD: "For three days it refused to move", "Inside the old boot...",
         "Scientists call this oxytocin bonding").
       - Khi đó KHÔNG search theo câu nhỏ vô nghĩa mà đặt `smart_image_query` xoay quanh TỔNG THỂ KỊCH BẢN
         (`overall_script_query`) với góc nhìn/khoảnh khắc phù hợp để ghép vào video!
    3. `overall_script_query`: Câu truy vấn (4-6 từ tiếng Anh) tóm tắt nội dung tổng thể xoay quanh kịch bản.
    """
    return {
        "tool_name": "search_google_cinematic_image",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "search_scope": search_scope,
        "smart_image_query": smart_image_query,
        "overall_script_query": overall_script_query,
        "camera_motion": camera_motion,
    }


@tool
def summon_epic_monster_cinematic_media(
    scene_id: int,
    creature_name: str,
    scale_anchor_object: str,
    monster_media_mode: str = "3D_CGI_VIDEO",
    search_scope: str = "SCENE_SPECIFIC",
    action_or_concept_query: str = "",
    overall_script_query: str = "",
    target_duration_sec: float = 4.5,
    camera_motion: str = "hyper_zoom",
) -> Dict[str, Any]:
    """
    CÔNG CỤ ĐẶC NHIỆM CHUYÊN BIỆT CHO QUÁI VẬT KHỔNG LỒ, THỦY QUÁI BIỂN SÂU, KHỦNG LONG TIỀN SỬ, THỰC THỂ VŨ TRỤ & HỘI CHỨNG SỢ VẬT KHỔNG LỒ (MEGALOPHOBIA / THALASSOPHOBIA).
    GỌI NGAY CÔNG CỤ NÀY KHI:
    - Kịch bản hoặc phân cảnh nói về các sinh vật/quái vật hùng vĩ, khổng lồ, hầm hố hoặc rùng rợn mà các model tạo ảnh AI miễn phí thường vẽ bị méo mó giải phẫu hoặc mất cảm giác quy mô khổng lồ:
      * Thủy quái & quái vật biển sâu: The Bloop, Kraken, Leviathan, Gargantuan Leviathan, Megalodon, Cthulhu, Loch Ness, Mosasaurus, Liopleurodon, Dunkleosteus, Charybdis, Jormungandr, Bigfin Squid (Magnapinna).
      * Quái thú tiền sử & Kaiju điện ảnh: Godzilla, King Kong, Titanoboa, Tyrannosaurus Rex (T-Rex), Spinosaurus, Indominus, Behemoth, Rồng (Dragon), Hydra, Cerberus, Fenrir.
      * Thực thể kinh dị / Cryptids / Vũ trụ: SCP, Siren Head, Wendigo, Mothman, Skinwalker, Eldritch Cosmic Entity, Megalophobia / Thalassophobia scale comparison.
    - CƠ CHẾ HOẠT ĐỘNG 3 TẦNG (3-Tier Megalophobia & Monster Engine):
      1. `monster_media_mode = "3D_CGI_VIDEO"` (Dùng cho Cảnh 1 Hook & các cảnh hành động cao trào):
         Săn trực tiếp các đoạn Video 3D CGI Unreal Engine 5 / Blender Megalophobia / VFX Movie Scene trên YouTube Shorts (VD: "{creature_name} {scale_anchor_object} 3d animation", "{creature_name} megalophobia cgi"), cắt đúng đoạn Peak hoành tráng nhất và kiểm duyệt bằng Gemini Vision (chấp nhận 3D CGI điện ảnh, chặn hoạt hình 2D trẻ em / đồ chơi / Minecraft).
      2. `monster_media_mode = "CONCEPT_ART_8K"` (Dùng cho các cảnh thân cần mật độ đa góc máy hoặc khi không có clip 3D):
         Săn bộ 3-4 bức Đại cảnh Concept Art 8K trên ArtStation / Wallhaven / Movie Stills có vật đối chiếu tỷ lệ (`scale_anchor_object` như tàu ngầm, tàu chiến, thợ lặn, tòa nhà chọc trời) để dựng hiệu ứng 3D Parallax + Rung chấn camera.
      3. Tham số bắt buộc:
         - `creature_name`: Tên quái vật/sinh vật (VD: "The Bloop Leviathan", "Megalodon", "Kraken", "Titanoboa").
         - `scale_anchor_object`: Vật thể con người nhỏ bé đặt cạnh để tôn quy mô khổng lồ (VD: "submarine", "battleship", "scuba diver", "skyscraper", "helicopter").
         - `action_or_concept_query`: Truy vấn mô tả hành động/quy mô (4-6 từ tiếng Anh, VD: "colossal leviathan swallowing submarine abyss").
    """
    return {
        "tool_name": "summon_epic_monster_cinematic_media",
        "scene_id": scene_id,
        "creature_name": creature_name,
        "subject_entity": creature_name,
        "scale_anchor_object": scale_anchor_object,
        "monster_media_mode": monster_media_mode,
        "search_scope": search_scope,
        "action_or_concept_query": action_or_concept_query,
        "overall_script_query": overall_script_query,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def fetch_clean_cinematic_portrait_or_broll(
    scene_id: int,
    subject_entity: str,
    human_action_query: str,
    media_preference: str = "VIDEO_BROLL",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ ĐẶC NHIỆM CHUYÊN BIỆT CHO TÂM LÝ HỌC (DARK PSYCHOLOGY), THAO TÚNG TÂM LÝ, ÁI KỶ (NARCISSISM), KHẮC KỶ (STOICISM), TRIẾT HỌC & CẢM XÚC CON NGƯỜI.
    GỌI NGAY CÔNG CỤ NÀY KHI:
    - Kịch bản hoặc phân cảnh nói về tâm lý con người, thao túng, đọc vị hành vi, ngôn ngữ cơ thể, ánh mắt, sự im lặng, tranh cãi, cô đơn, khắc kỷ, hoặc đấu trí nội tâm.
    - TẠI SAO PHẢI GỌI CÔNG CỤ NÀY THAY VÌ GOOGLE IMAGES:
      Khi tìm các chủ đề tâm lý/triết học trên Google Images, 95% kết quả web là ảnh Quote Card có chữ chằng chịt, bìa sách, hoặc infographic làm hỏng hoàn toàn video.
      Công cụ này tự động BỎ QUA Open Web và truy xuất trực tiếp kho Video B-Roll diễn viên thật & Ảnh chân dung nghệ thuật Chạng vạng (Chiaroscuro / Dark Moody Portrait) SẠCH 100% KHÔNG CHỮ từ Pexels HD & Pixabay HD!
    - Tham số quan trọng:
      * `human_action_query`: Cụm 2-4 từ tiếng Anh mô tả HÀNH ĐỘNG VẬT LÝ hoặc BIỂU CẢM KHUÔN MẶT cụ thể của con người (TUYỆT ĐỐI KHÔNG dùng từ trừu tượng như "narcissist", "cognitive dissonance", "manipulation").
        Ví dụ chuẩn: "close up eyes dark lighting", "couple argument tension", "man stressed alone dark room", "silhouette person walking night", "two men serious conversation".
      * `media_preference`:
        - `"VIDEO_BROLL"`: Ưu tiên tải đoạn video quay diễn viên thật chuyển động chuẩn dọc 9:16 (dùng cho Hook & các cảnh chính).
        - `"CLEAN_PORTRAIT_PHOTO"`: Tải bộ 3-4 bức ảnh chân dung nghệ thuật ánh sáng tối kịch tính sạch chữ 100% để cắt nhịp nhanh.
    """
    return {
        "tool_name": "fetch_clean_cinematic_portrait_or_broll",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "human_action_query": human_action_query,
        "media_preference": media_preference,
        "search_scope": search_scope,
        "overall_script_query": overall_script_query,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def search_pexels_pixabay_stock_video(
    scene_id: int,
    subject_entity: str,
    search_scope: str,
    primary_stock_query: str,
    secondary_stock_query: str = "",
    overall_script_query: str = "",
    expected_visual_content: str = "",
    forbidden_false_positives: str = "",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ GỌI API KHO VIDEO BẢN QUYỀN SẠCH (PEXELS HD & PIXABAY HD) KÈM KIỂM DUYỆT LINK & ẢNH PREVIEW BỞI LLM.
    CƠ CHẾ HOẠT ĐỘNG 2 BƯỚC CỦA TOOL:
    - Bước 1 (LLM Gọi API): Gửi `primary_stock_query` và `secondary_stock_query` (cụm 2–4 từ tiếng Anh mô tả danh từ chủ thể vật lý + môi trường/hành động đơn giản, VD: "hippopotamus river wildlife", "african lion savanna") tới API Pexels & Pixabay để lấy danh sách link ứng viên (`returned_url`, `slug_or_tags`) và ảnh chụp preview (`thumbnail`).
      * Lưu ý đặc tả API: Pexels/Pixabay chỉ lập chỉ mục theo danh từ vật lý cụ thể (2-4 từ), KHÔNG hiểu các từ cảm xúc/trừu tượng ("retreating", "frustrated", "impassive").
    - Bước 2 (LLM Kiểm Tra Lại Link & Ảnh Preview Trả Về):
      * Ngay khi nhận danh sách link từ API, LLM Auditor (`curate_candidate_videos`) sẽ đọc từng đường link `returned_url` kết hợp NHÌN TRỰC TIẾP ảnh `thumbnail` của từng video (kể cả khi link đặt tên chung chung).
      * Đối chiếu với `expected_visual_content` (nội dung bắt buộc phải thấy trong hình) và `forbidden_false_positives` (các kết quả nhầm lẫn cần loại bỏ ngay như mèo nhà, thú cưng phòng khách, lồng sắt sở thú) trước khi tải file MP4!
    """
    return {
        "tool_name": "search_pexels_pixabay_stock_video",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "search_scope": search_scope,
        "primary_stock_query": primary_stock_query,
        "secondary_stock_query": secondary_stock_query,
        "youtube_search_query": primary_stock_query,
        "fallback_broad_query": secondary_stock_query,
        "overall_script_query": overall_script_query,
        "expected_visual_content": expected_visual_content,
        "forbidden_false_positives": forbidden_false_positives,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


MEDIA_DIRECTOR_TOOLS = [
    clone_youtube_short_peak_clip,
    search_pexels_pixabay_stock_video,
    search_google_cinematic_image,
    summon_epic_monster_cinematic_media,
    fetch_clean_cinematic_portrait_or_broll,
]


# ──────────────────────────────────────────────────────────────────────────────
# CÁC HÀM HỖ TRỢ PHÂN TÍCH PHẠM VI TÌM KIẾM (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_subject_anchor_tokens(subject_entity: str, topic: str, search_query: str) -> Set[str]:
    """Trích xuất tập từ khóa Danh từ Chủ thể bắt buộc phải xuất hiện trong tiêu đề YouTube Short."""
    raw = f"{subject_entity or ''} {topic or ''}".strip()
    if not raw:
        raw = search_query or ""
    tokens = re.findall(r"[a-zA-Z]{3,}", raw.lower())
    anchors = {
        t for t in tokens
        if t not in _YT_QUERY_FILLER_WORDS
        and t not in _GENERIC_NON_SUBJECT_WORDS
        and t not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
    }
    if any(k in anchors for k in ("dog", "dogs", "puppy", "puppies", "canine", "retriever", "husky", "shepherd", "stray")):
        anchors.update({"dog", "dogs", "puppy", "puppies", "golden", "retriever", "stray", "shelter", "pet"})
    if any(k in anchors for k in ("cat", "cats", "kitten", "kittens", "feline")):
        anchors.update({"cat", "cats", "kitten", "kittens"})
    return anchors


def _clean_yt_search_phrase(
    q: str,
    subject_entity: str = "",
    max_words: int = 5,
    strip_distractor_props: bool = False,
) -> str:
    """Làm sạch câu truy vấn: bỏ từ nhiễu (4k, edit, cinematic), tùy chọn bỏ đạo cụ gây nhiễu và đảm bảo có chủ thể."""
    words = re.findall(r"[a-zA-Z0-9]+", q or "")
    core = []
    for w in words:
        wl = w.lower()
        if wl in _YT_QUERY_FILLER_WORDS or len(w) < 2:
            continue
        if strip_distractor_props and wl in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS:
            continue
        core.append(w)

    subj_words = [
        w for w in re.findall(r"[a-zA-Z0-9]+", subject_entity or "")
        if w.lower() not in _YT_QUERY_FILLER_WORDS
        and w.lower() not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
        and len(w) >= 3
    ]
    core_lower = {w.lower() for w in core}
    for sw in reversed(subj_words[:2]):
        if sw.lower() not in core_lower:
            core.insert(0, sw)
            core_lower.add(sw.lower())
    return " ".join(core[:max_words]).strip()


def _is_meaningless_or_prop_distracted_when_split(
    narration: str,
    scene_query: str,
    subject_entity: str,
) -> bool:
    """
    Kiểm tra xem câu văn của cảnh khi chia nhỏ ra có bị:
    1. Vô nghĩa / quá ngắn / chỉ là câu chuyển ý trừu tượng ("For three days nobody knew why...")
    2. Hoặc bị đạo cụ phụ làm lệch hướng tìm kiếm ("old leather boot", "oxytocin hormone", "behaviorists call this")
    Nếu trả về True -> Bắt buộc chuyển sang chế độ `SCRIPT_OVERALL` (đi search tổng thể xoay quanh kịch bản)!
    """
    q_tokens = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", scene_query or "")]
    narr_tokens = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", narration or "")]

    # Nếu câu truy vấn chứa các từ đạo cụ/trừu tượng dễ làm lệch kết quả search (VD: "boot", "hormone", "behaviorist")
    distractor_hits = sum(1 for t in q_tokens if t in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS)
    if distractor_hits >= 1:
        return True

    # Kiểm tra xem câu thoại sau khi bỏ các từ chung chung và từ chủ thể còn lại từ hành động/bối cảnh thực chất nào không
    subj_tokens = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", subject_entity or "")}
    meaningful_scene_words = [
        t for t in narr_tokens
        if t not in _YT_QUERY_FILLER_WORDS
        and t not in _GENERIC_NON_SUBJECT_WORDS
        and t not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
        and t not in subj_tokens
    ]
    if len(meaningful_scene_words) <= 1:
        return True

    return False


def _resolve_adaptive_search_queries(
    search_scope: str,
    scene_query: str,
    overall_script_query: str,
    fallback_query: str,
    subject_entity: str,
    topic: str,
    narration: str,
    max_words: int = 5,
) -> Tuple[str, List[str]]:
    """
    Trả về `(effective_scope, ordered_queries)` theo đúng nguyên tắc của người dùng:
    - Nếu chia nhỏ câu văn ra đi search mà VẪN CÓ NGHĨA (`SCENE_SPECIFIC`):
      Ưu tiên số 1 là `scene_query` (câu nhỏ), tiếp theo mới đến `overall_script_query` (tổng thể kịch bản).
    - Nếu chia nhỏ câu văn ra bị VÔ NGHĨA hoặc LỆCH CHỦ ĐỀ (`SCRIPT_OVERALL`):
      Ưu tiên số 1 là `overall_script_query` (xoay quanh tổng thể kịch bản), loại bỏ các từ đạo cụ gây nhiễu!
    """
    scope_norm = (search_scope or "SCENE_SPECIFIC").strip().upper()
    if scope_norm not in ("SCENE_SPECIFIC", "SCRIPT_OVERALL"):
        scope_norm = "SCENE_SPECIFIC"

    # Tự động phát hiện nếu câu nhỏ thực chất bị vô nghĩa hoặc vướng đạo cụ gây nhiễu (như "old leather boot")
    if scope_norm == "SCENE_SPECIFIC" and _is_meaningless_or_prop_distracted_when_split(
        narration=narration,
        scene_query=scene_query,
        subject_entity=subject_entity,
    ):
        scope_norm = "SCRIPT_OVERALL"

    overall_base = overall_script_query or topic or subject_entity
    q_overall = _clean_yt_search_phrase(
        overall_base,
        subject_entity=subject_entity,
        max_words=max_words,
        strip_distractor_props=True,
    )
    q_fallback = _clean_yt_search_phrase(
        fallback_query or f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        max_words=max_words,
        strip_distractor_props=True,
    )

    if scope_norm == "SCRIPT_OVERALL":
        # Câu nhỏ vô nghĩa khi tách lẻ -> Lấy nội dung xoay quanh TỔNG THỂ KỊCH BẢN làm gốc!
        q_scene_cleaned = _clean_yt_search_phrase(
            scene_query,
            subject_entity=subject_entity,
            max_words=max_words,
            strip_distractor_props=True,
        )
        ordered = [q for q in dict.fromkeys([q_overall, q_scene_cleaned, q_fallback]) if q]
    else:
        # Câu nhỏ vẫn có nghĩa rõ ràng -> Cứ thế search trực tiếp câu nhỏ trước, dự phòng bằng tổng thể kịch bản!
        q_scene = _clean_yt_search_phrase(
            scene_query,
            subject_entity=subject_entity,
            max_words=max_words,
            strip_distractor_props=False,
        )
        ordered = [q for q in dict.fromkeys([q_scene, q_overall, q_fallback]) if q]

    return scope_norm, ordered


def _find_best_peak_window(
    info: Dict[str, Any],
    clip_dur: float,
    role: str = "PEAK",
    existing_windows: Optional[List[Tuple[float, float]]] = None,
    max_peak_dur: float = 14.0,
) -> Optional[Tuple[float, float]]:
    """
    Xác định mốc thời gian (start_sec, end_sec) đắt giá nhất trong chiếc YouTube Short.
    KHÔNG KHÓA CỨNG 3 GIÂY:
    - Nếu đoạn Peak (Most Replayed heatmap hoặc cao trào hành động) kéo dài liên tục (ví dụ 6s - 14s),
      ưu tiên lấy TRỌN VẸN cả đoạn Peak dài đó (`max_peak_dur`, mặc định tới 14s) để ghép liền mạch
      qua nhiều phân cảnh liên tiếp thay vì cắt cụt ở 3 giây!
    - Đảm bảo KHÔNG TRÙNG LẶP với các khoảng `existing_windows` nếu cùng 1 video tổng thể được cắt
      nhiều phân đoạn khác nhau trong cùng 1 kịch bản.
    """
    total_dur = float(info.get("duration") or 15.0)
    min_dur = max(3.0, min(clip_dur, 8.0))
    max_dur = max(min_dur + 1.0, min(max_peak_dur, max(3.5, total_dur - 0.8)))
    used_wins = existing_windows or []

    def _overlaps(s: float, e: float) -> bool:
        for us, ue in used_wins:
            if max(s, us) < min(e, ue) - 0.4:
                return True
        return False

    if total_dur <= min_dur + 1.0:
        if used_wins:
            return None
        return 0.0, max(2.0, total_dur)

    # 1. Thử lấy từ đỉnh Most Replayed (heatmap) — GỘP LIỀN MẠCH TOÀN BỘ VÙNG PEAK DÀI NẾU ĐỈNH KÉO DÀI
    heatmap = info.get("heatmap")
    if isinstance(heatmap, list) and len(heatmap) >= 3:
        indexed_pts = [(idx, pt) for idx, pt in enumerate(heatmap) if isinstance(pt, dict)]
        sorted_pts = sorted(indexed_pts, key=lambda x: float(x[1].get("value", 0.0)), reverse=True)
        for best_idx, pt in sorted_pts[:4]:
            peak_val = float(pt.get("value", 0.0))
            if peak_val > 0.22:
                # Mở rộng sang trái & phải trên heatmap chừng nào độ cuốn hút vẫn cao (>= 52% đỉnh hoặc >= 0.25)
                cont_thresh = max(0.22, peak_val * 0.52)
                left_idx = best_idx
                right_idx = best_idx
                while left_idx > 0 and float(heatmap[left_idx - 1].get("value", 0.0)) >= cont_thresh:
                    if float(heatmap[right_idx].get("end_time", 0.0)) - float(heatmap[left_idx - 1].get("start_time", 0.0)) > max_dur:
                        break
                    left_idx -= 1
                while right_idx + 1 < len(heatmap) and float(heatmap[right_idx + 1].get("value", 0.0)) >= cont_thresh:
                    if float(heatmap[right_idx + 1].get("end_time", 0.0)) - float(heatmap[left_idx].get("start_time", 0.0)) > max_dur:
                        break
                    right_idx += 1

                raw_start = float(heatmap[left_idx].get("start_time", 0.0))
                raw_end = float(heatmap[right_idx].get("end_time", 0.0))
                span_len = raw_end - raw_start
                eff_len = max(min_dur, min(max_dur, span_len if span_len >= min_dur else max(min_dur, 8.5)))
                peak_center = (raw_start + raw_end) / 2.0
                start_s = max(0.4, min(raw_start if span_len >= min_dur else (peak_center - eff_len / 2.0), total_dur - eff_len - 0.2))
                end_s = min(total_dur, start_s + eff_len)
                if not _overlaps(start_s, end_s):
                    return round(start_s, 2), round(end_s, 2)

    # 2. Các mốc ứng viên theo vai trò: Cho phép cửa sổ rộng (tới min(max_dur, 11.5s)) để bộ quét CV lấy trọn đoạn Peak dài
    probe_span = min(max_dur, max(min_dur, min(11.5, total_dur * 0.65)))
    candidate_ratios = [0.05, 0.28, 0.52, 0.70] if (role or "").upper() == "HOOK" else [0.25, 0.50, 0.12, 0.68]
    for ratio in candidate_ratios:
        start_s = 0.6 if ratio <= 0.08 else max(0.8, min(total_dur * ratio, total_dur - min_dur - 0.3))
        end_s = min(total_dur, start_s + probe_span)
        if end_s - start_s >= min_dur and not _overlaps(start_s, end_s):
            return round(start_s, 2), round(end_s, 2)

    return None


def _verify_short_clip_with_gemini_vision(
    video_path: str,
    topic: str,
    narration: str,
    subject_entity: str,
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    video_title: str = "",
    allow_3d_cgi_monster: bool = False,
    strict_no_fallback: bool = True,
) -> Tuple[bool, float, str]:
    """
    Trích xuất 3 khung hình (tile=3x1) từ đoạn clip vừa cắt và nhờ Gemini Vision kiểm duyệt nghiêm ngặt:
    - Chặn 100% clip có watermark, logo kênh khác (LatestSightings, MR. FRIEND, @username), phụ đề TikTok/Shorts in cứng, hoặc chữ bị lật ngược.
    - Chặn clip mờ, vỡ hạt, lệch màu, tranh vẽ trộn lẫn cảnh thật, hoặc sai môi trường sống (vd: báo trong sở thú tuyết khi truyện ở thảo nguyên).
    """
    if not Path(video_path).exists():
        return False, 0.0, "File không tồn tại"

    from app.graph.evaluators.visual_critic import VisualCritic
    pre_gate = VisualCritic.inspect_clip_before_concat(
        video_path,
        min_short_edge=720,
        min_sharpness=5.8,
        check_watermark=True,
    )
    if not pre_gate.get("passed", False):
        return False, float(pre_gate.get("score", 2.0)), "; ".join(pre_gate.get("issues") or ["Failed pre-assembly clip gate"])

    with tempfile.TemporaryDirectory() as tmp_v:
        sheet_jpg = str(Path(tmp_v) / "verify_sheet.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "fps=1,scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
            "-frames:v", "1", "-q:v", "4",
            sheet_jpg,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=8, check=False)
        except Exception as e:
            return (False, 0.0, f"Sheet extract error ({e})") if strict_no_fallback else (True, 7.2, f"Skip sheet extract ({e})")

        if not Path(sheet_jpg).exists() or Path(sheet_jpg).stat().st_size < 3000:
            return (False, 0.0, "Sheet missing") if strict_no_fallback else (True, 7.2, "Sheet fallback")

        b64_img = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

    try:
        import requests
        from app.utils.key_rotator import KeyRotator

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if not rotator or not rotator.has_keys:
            return (False, 0.0, "No Gemini keys for strict vision check") if strict_no_fallback else (True, 7.5, "No Gemini keys")

        cgi_rule = (
            "3. 2D kids cartoon / chibi / Minecraft / Roblox / plastic toy / GMod (NOTE: Clean watermark-free photorealistic 3D CGI / Unreal Engine 5 / Blender / Thalassophobia underwater animation and dark murky abyssal silhouette scenes ARE APPROVED for this monster scene if they have ZERO watermarks, ZERO channel names, and ZERO burned-in subtitles!)\n"
            "6. Normal real-world tourist boats or weather towers in bright sunny daylight with zero monster/underwater tension"
            if allow_3d_cgi_monster
            else "3. 2D drawings, paintings, illustrations, cartoons, anime, video games, or CGI renders mixed into a live-action documentary"
        )

        scope_note = (
            f'NOTE: This scene line is part of an overall story ("{overall_script_query or topic}"). '
            f'APPROVE (passed=true, score>=7.5) ONLY IF the clip is 100% CLEAN (zero watermarks/logos/text), sharp HD, and matches the main subject ("{subject_entity or topic}") and natural environment of the story.'
            if search_scope == "SCRIPT_OVERALL"
            else f'APPROVE (passed=true, score>=7.5) ONLY IF the clip is 100% CLEAN (zero watermarks/logos/text), sharp HD, and shows the main subject ("{subject_entity or topic}") fitting the scene and natural environment.'
        )

        prompt = f"""You are a Strict Copyright, Watermark & Visual Quality Inspector for a professional YouTube Shorts studio.
Look carefully at all 3 frames in this strip extracted from a candidate video clip:
- Overall Script Topic: "{topic}"
- Core Story Theme & Habitat: "{overall_script_query or topic}"
- Main Subject: "{subject_entity or topic}"
- Current Scene Line ({search_scope}): "{narration}"
- Source Video Title: "{video_title}"

{scope_note}

REJECT IMMEDIATELY (passed=false, score < 4.0) IF YOU SEE ANY OF THESE IN ANY FRAME:
1. ANY channel watermark, logo, corner bug, username (@...), credit banner, burned-in TikTok/Shorts subtitle/caption, or mirrored/flipped text anywhere on the frame (e.g., "LatestSightings", "MR. FRIEND", TV logos, social handles, yellow/white captions).
2. Blurry, pixelated, grainy, low-resolution, or color-shifted/washed-out video quality.
{cgi_rule}
4. Habitat or environment mismatch with the script (e.g., an animal in a snowy zoo enclosure, behind cage bars/fences, or indoors when the story takes place in the wild African savanna, jungle, or ocean).
5. Wrong animal/subject species that does not match "{subject_entity or topic}", or a talking head / vlog / product commercial.

Return ONLY valid JSON:
{{"passed": true, "score": 8.5, "reason": "100% clean HD footage with zero watermarks/text matching subject and habitat"}}"""

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                ]
            }],
            "generationConfig": {"temperature": 0.1},
        }

        for model_name, api_key in rotator.iter_best_model_key_pairs(max_total_attempts=4):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=8)
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            score = float(data.get("score", 0.0))
                            passed = bool(data.get("passed", False)) and score >= 7.2
                            reason = str(data.get("reason", ""))
                            return passed, score, reason
                elif resp.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
            except Exception:
                continue
    except Exception as ex:
        logger.debug(f"[YouTubePeakTool] Vision check fallback: {ex}")

    if strict_no_fallback:
        return False, 0.0, "Strict vision gate: rejected unverified clip"
    return True, 7.2, "Fallback pass"


def _search_youtube_flat_cached(
    search_phrases: List[str],
    subject_anchors: Set[str],
    all_query_tokens: Set[str],
) -> List[Dict[str, Any]]:
    """
    Quét song song tối đa 5 nhánh ứng viên YouTube Shorts (`extract_flat="in_playlist"`)
    qua LangGraph `Send` API + `Annotated[List[Dict[str, Any]], operator.add]` có bộ nhớ đệm (cache)
    mở rộng lên 25 ứng viên/truy vấn để tăng tốc độ tìm kiếm gấp 3-5 lần!
    """
    import yt_dlp

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 8,
        "ignoreerrors": True,
    }

    def _fetch_single_yt_phrase(sp: str) -> List[Dict[str, Any]]:
        cache_key = f"{license_policy.license_mode()}::{sp.lower().strip()}"
        with _yt_url_lock:
            cached_entries = _yt_flat_search_cache.get(cache_key)
        if cached_entries:
            return cached_entries
        # Chế độ `cc_only` (mặc định): chỉ tìm video có giấy phép Creative Commons (free & hợp lệ để tái sử dụng)
        yt_query = license_policy.youtube_search_target(sp, max_results=12)
        try:
            with yt_dlp.YoutubeDL(license_policy.youtube_search_opts(flat_opts, max_results=12)) as ydl:
                res = ydl.extract_info(yt_query, download=False)
            cached_entries = [
                e for e in ((res or {}).get("entries") or [])
                if e and isinstance(e, dict)
            ]
        except Exception:
            cached_entries = []
        if cached_entries:
            with _yt_url_lock:
                _yt_flat_search_cache[cache_key] = cached_entries
        return cached_entries

    try:
        from app.graph.parallel_search import run_langgraph_parallel_youtube_flat_search
        parallel_buckets = run_langgraph_parallel_youtube_flat_search(
            search_phrases=search_phrases[:4],
            fetch_single_phrase_fn=_fetch_single_yt_phrase,
        )
    except Exception:
        parallel_buckets = [{"items": _fetch_single_yt_phrase(sp)} for sp in search_phrases[:3]]

    candidates: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for bucket in parallel_buckets:
        for entry in (bucket.get("items") or []):
            vid_id = entry.get("id") or ""
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            webpage_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}"
            if webpage_url.startswith("/"):
                webpage_url = f"https://www.youtube.com{webpage_url}"

            dur = float(entry.get("duration") or 0)
            if dur > 0 and (dur < 4 or dur > 300):
                continue

            title = str(entry.get("title") or "")
            uploader = str(entry.get("uploader") or entry.get("channel") or "")
            meta_lower = f"{title} {uploader}".lower()
            if any(bad in meta_lower for bad in _TALKING_HEAD_BLOCKLIST):
                continue
            if any(bad_ch in meta_lower for bad_ch in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST):
                continue

            title_lower = title.lower()
            anchor_hits = sum(1 for a in subject_anchors if a in title_lower)
            if subject_anchors and anchor_hits == 0:
                continue

            action_hits = sum(1 for tok in all_query_tokens if tok in title_lower)
            views = int(entry.get("view_count") or 0)
            score = (anchor_hits * 85.0) + (action_hits * 45.0) + min(views / 25000.0, 60.0)
            candidates.append({
                "id": vid_id,
                "url": webpage_url,
                "title": title,
                "uploader": uploader,
                "duration": dur,
                "score": score,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def execute_youtube_short_peak_clone_sync(
    search_query: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    clip_role: str = "PEAK",
    used_urls: Optional[Set[str]] = None,
    fallback_query: str = "",
    subject_entity: str = "",
    narration: str = "",
    topic: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    max_peak_duration_sec: float = 14.0,
    job_id: int = 0,
    scene_id: int = 0,
    channel_id: str = "",
    allow_3d_cgi_monster: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi tìm kiếm & cắt đoạn Peak từ YouTube Shorts tích hợp hệ thống Mini-RAG (`VideoMemoryRAG`):
    - Chống lặp lại 100% các cảnh cắt BÊN TRONG CÙNG 1 VIDEO (`job_id`) qua khóa nguyên tử ID/URL,
      so khớp ngữ nghĩa tiêu đề chống bản re-upload, và đối chiếu chữ ký thị giác 3-frame `dHash`
      (không cần lưu khóa cứng giữa các bài đăng khác nhau).
    - Xếp hạng ứng viên bằng Hybrid BM25/Cosine của Mini-RAG kết hợp trọng số giữ chân lịch sử (`stayed_to_watch_pct`).
    - Nếu đoạn Peak thực tế trong video nguồn kéo dài (ví dụ 6s - 14s), tự động trích xuất TRỌN VẸN cả đoạn Peak dài
      (`full_peak_path` & `full_peak_duration_sec`) để bộ lắp ghép Timeline (`node_fetch_media`) ưu tiên phủ liền mạch
      đoạn video Peak dài đó qua nhiều phân cảnh liên tiếp trước khi dùng đến ảnh tĩnh!
    """
    from app.services.media.media_history import (
        extract_media_identity,
        is_media_already_used,
        record_used_media,
    )
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()

    eff_scene_id = int(scene_id or 0)
    if eff_scene_id <= 0 and output_path:
        m_sc = re.search(r"scene_(\d+)", Path(output_path).name)
        if m_sc:
            eff_scene_id = int(m_sc.group(1))
        elif "hook_motion_001" in Path(output_path).name:
            eff_scene_id = 1

    effective_scope, search_phrases = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=search_query,
        overall_script_query=overall_script_query,
        fallback_query=fallback_query,
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=5,
    )
    if allow_3d_cgi_monster:
        c_core = _clean_yt_search_phrase(subject_entity or topic, max_words=3, strip_distractor_props=True) or "sea monster"
        monster_angle_variants = [
            f"{c_core} 3d animation",
            f"{c_core} thalassophobia animation",
            f"giant {c_core} megalophobia",
            f"{c_core} submarine attack animation",
            f"{c_core} underwater monster cgi",
        ]
        rot_idx = max(0, (eff_scene_id - 1)) % len(monster_angle_variants)
        rotated_angles = monster_angle_variants[rot_idx:] + monster_angle_variants[:rot_idx]
        monster_yt_phrases = [
            *rotated_angles[:2],
            *search_phrases,
            rotated_angles[2],
        ]
        search_phrases = [q for q in dict.fromkeys(monster_yt_phrases) if q][:4]

    subject_anchors = _extract_subject_anchor_tokens(subject_entity, topic, search_phrases[0] if search_phrases else search_query)
    if allow_3d_cgi_monster:
        subject_anchors.update({"monster", "leviathan", "bloop", "kraken", "megalodon", "cthulhu", "creature", "megalophobia", "thalassophobia"})

    logger.info(
        f"🎯 [YouTubePeakTool + MiniRAG] Scene=#{eff_scene_id} | Scope={effective_scope} | Role={clip_role} | "
        f"Queries={search_phrases} (raw_scene='{search_query}', overall='{overall_script_query}')"
    )

    all_query_tokens = {
        w.lower()
        for q in search_phrases
        for w in re.findall(r"[a-zA-Z]{3,}", q.lower())
        if w.lower() not in _YT_QUERY_FILLER_WORDS
    }

    # ── TẦNG 1 (ƯU TIÊN SỐ 1): SĂN & CẮT ĐOẠN PEAK TỪ VIDEO YOUTUBE RỦI RO THẤP (ĐÃ QUA 4 LỚP LỌC WATERMARK & >= 720P) ──
    allow_low_risk_yt = allow_3d_cgi_monster or (
        os.getenv("ALLOW_LOW_RISK_YOUTUBE_CLIPS", "true").strip().lower() not in ("0", "false", "no", "off")
    )
    if allow_low_risk_yt:
        try:
            import yt_dlp
            from app.graph.evaluators.visual_critic import VisualCritic

            candidates = _search_youtube_flat_cached(search_phrases, subject_anchors, all_query_tokens)
            if allow_3d_cgi_monster:
                _monster_yt_bad = ("tier list", "explained", "minecraft", "roblox", "gmod", "garry", "lego", "podcast", "reaction")
                candidates = [
                    c for c in candidates
                    if not any(b_tok in str(c.get("title") or "").lower() for b_tok in _monster_yt_bad)
                ]
            unused_cands = [
                c for c in candidates
                if not is_media_already_used(c["url"], used) and f"yt:{c['id']}" not in used
            ]
            ordered_cands = rag.rank_video_candidates_for_scene(
                job_id=job_id,
                candidates=unused_cands,
                scene_narration=narration,
                scene_query=search_query,
                topic=topic,
                subject_entity=subject_entity or topic,
                channel_id=channel_id,
            )

            max_claimed_probes = 3 if (clip_role or "").upper() == "HOOK" else 2
            claimed_probes = 0
            cand_pool = list(ordered_cands[:20])
            if len(cand_pool) > 4 and eff_scene_id > 1:
                rot_c = (eff_scene_id - 1) % min(5, len(cand_pool))
                cand_pool = cand_pool[rot_c:] + cand_pool[:rot_c]

            for cand in cand_pool:
                if claimed_probes >= max_claimed_probes:
                    break
                vid_url = cand["url"]
                vid_id = cand.get("id") or ""
                cand_title = str(cand.get("title") or "")

                if not rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=vid_id,
                    url=vid_url,
                    title=cand_title,
                    scene_id=eff_scene_id,
                ):
                    continue

                with _yt_url_lock:
                    if vid_url in used:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue
                    used.update(extract_media_identity(vid_url))

                claimed_probes += 1
                info_entry: Dict[str, Any] = cand
                license_verified = False
                try:
                    info_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 8}
                    with yt_dlp.YoutubeDL(info_opts) as ydl_info:
                        full_info = ydl_info.extract_info(vid_url, download=False)
                        if isinstance(full_info, dict):
                            license_verified = license_policy.is_reuse_allowed(full_info)
                            dur_full = float(full_info.get("duration") or 0)
                            if dur_full < 4 or dur_full > 300:
                                rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                continue
                            w_full = int(full_info.get("width") or 0)
                            h_full = int(full_info.get("height") or 0)
                            if w_full > 0 and h_full > 0 and min(w_full, h_full) < 720:
                                rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                continue
                            meta_full_lower = (
                                f"{full_info.get('title') or ''} {full_info.get('uploader') or ''} "
                                f"{full_info.get('channel') or ''} {(full_info.get('description') or '')[:300]}"
                            ).lower()
                            if any(bad in meta_full_lower for bad in _TALKING_HEAD_BLOCKLIST) or any(
                                bad_c in meta_full_lower for bad_c in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST
                            ):
                                rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                continue
                            info_entry = full_info
                except Exception:
                    pass

                if not license_verified:
                    logger.info(
                        f"🚫 [LicensePolicy] Bỏ qua '{cand_title[:45]}' ({vid_url}): "
                        f"không xác minh được giấy phép Creative Commons"
                    )
                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                    continue

                win_pair = _find_best_peak_window(
                    info_entry,
                    target_duration_sec + 0.5,
                    role=clip_role,
                    existing_windows=None,
                    max_peak_dur=max_peak_duration_sec,
                )
                if not win_pair:
                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                    continue
                start_s, end_s = win_pair

                with tempfile.TemporaryDirectory() as tmp_dir:
                    raw_dl_template = str(Path(tmp_dir) / "raw_short.%(ext)s")
                    dl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "format": "bestvideo[height>=720][height<=1920][ext=mp4]/best[height>=720][height<=1920][ext=mp4]",
                        "outtmpl": raw_dl_template,
                        "socket_timeout": 12,
                        "retries": 1,
                        "nopart": True,
                    }
                    # Chỉ dùng download_ranges khi video gốc dài > 75s; với YouTube Shorts (<= 75s), tải thẳng MP4 qua HTTP nhanh gấp 4-6 lần và tránh treo ffmpeg HTTP seek
                    cand_dur_est = float(info_entry.get("duration") or cand.get("duration") or 0)
                    if cand_dur_est > 75.0:
                        try:
                            from yt_dlp.utils import download_range_func
                            dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s + 1.0)])
                        except Exception:
                            pass

                    try:
                        with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                            ydl_dl.download([vid_url])
                    except Exception:
                        dl_opts.pop("download_ranges", None)
                        try:
                            with yt_dlp.YoutubeDL(dl_opts) as ydl_dl2:
                                ydl_dl2.download([vid_url])
                        except Exception:
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            continue

                    downloaded_files = list(Path(tmp_dir).glob("raw_short.*"))
                    if not downloaded_files and "download_ranges" in dl_opts:
                        dl_opts.pop("download_ranges", None)
                        try:
                            with yt_dlp.YoutubeDL(dl_opts) as ydl_dl2:
                                ydl_dl2.download([vid_url])
                        except Exception:
                            pass
                        downloaded_files = list(Path(tmp_dir).glob("raw_short.*"))

                    if not downloaded_files:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue
                    raw_file = str(downloaded_files[0])
                    if Path(raw_file).stat().st_size < 15000:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue

                    # Cổng kiểm duyệt clip nguồn trước khi cắt ghép: tối thiểu 720p, độ nét >= 5.8, độ sáng cân bằng, không watermark/caption
                    raw_gate = VisualCritic.inspect_clip_before_concat(
                        raw_file, min_short_edge=720, min_sharpness=5.8, check_watermark=True
                    )
                    if not raw_gate.get("passed", False):
                        logger.info(
                            f"🚫 [PreAssemblyClipGate] Loại bỏ clip nguồn YouTube '{cand_title[:45]}' trước khi cắt: "
                            f"{'; '.join(raw_gate.get('issues') or [])}"
                        )
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue

                    from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                    raw_dur_sec = max(0.0, get_duration_ms(raw_file) / 1000.0)
                    min_req_dur = max(3.0, target_duration_sec)
                    if "download_ranges" in dl_opts:
                        rel_start, natural_peak_len = find_hook_peak_window(
                            raw_file,
                            min_duration=min_req_dur,
                            max_duration=min(max_peak_duration_sec, max(min_req_dur, raw_dur_sec)),
                        )
                        actual_start_s = rel_start
                    else:
                        det_start, natural_peak_len = find_hook_peak_window(
                            raw_file,
                            min_duration=min_req_dur,
                            max_duration=max_peak_duration_sec,
                        )
                        actual_start_s = det_start if det_start > 0.0 else start_s

                    natural_peak_len = max(min_req_dur, min(natural_peak_len, max_peak_duration_sec))
                    scene_clip_len = min_req_dur

                    # [FRAME-0 ACTION BOOST]: Nếu là Cảnh 1 (HOOK), tự động dịch điểm cắt vào sâu +0.35s
                    # (nếu độ dài video cho phép) để triệt tiêu hoàn toàn khung hình tĩnh/fade-in ở giây 0.0s!
                    if (clip_role or "").upper() == "HOOK" and raw_dur_sec >= actual_start_s + scene_clip_len + 0.45:
                        actual_start_s = round(actual_start_s + 0.35, 2)

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    try:
                        trim_video(
                            input_path=raw_file,
                            duration_seconds=scene_clip_len,
                            output_path=output_path,
                            width=1080,
                            height=1920,
                            fps=30,
                            apply_grading=True,
                            is_hook_scene=((clip_role or "").upper() == "HOOK"),
                            fair_use_transform=False,
                            start_offset_sec=actual_start_s,
                            preserve_continuous_flow=True,
                        )
                    except Exception:
                        vf_filter = (
                            "scale=1080:1920:force_original_aspect_ratio=increase,"
                            "crop=1080:1920,fps=30,format=yuv420p"
                        )
                        cmd = [
                            "ffmpeg", "-y", "-v", "error",
                            "-ss", f"{actual_start_s:.2f}",
                            "-i", raw_file,
                            "-t", f"{scene_clip_len:.2f}",
                            "-an", "-vf", vf_filter,
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
                            "-pix_fmt", "yuv420p", "-color_range", "tv",
                            output_path,
                        ]
                        subprocess.run(cmd, capture_output=True, timeout=25, check=False)

                    if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                        ok_sig, dup_sid = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=eff_scene_id,
                            media_path=output_path,
                            vid_id=vid_id,
                            url=vid_url,
                            title=cand_title,
                            is_continuation_slice=False,
                        )
                        if not ok_sig:
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        passed_vis, vis_score, vis_reason = _verify_short_clip_with_gemini_vision(
                            video_path=output_path,
                            topic=topic,
                            narration=narration,
                            subject_entity=subject_entity or topic,
                            overall_script_query=overall_script_query or (search_phrases[0] if search_phrases else topic),
                            search_scope=effective_scope,
                            video_title=cand_title,
                            allow_3d_cgi_monster=allow_3d_cgi_monster,
                            strict_no_fallback=False,
                        )
                        if not passed_vis:
                            logger.info(
                                f"🚫 [YouTubePeakTool] Gemini Vision từ chối clip '{cand_title[:45]}' "
                                f"(điểm={vis_score}/10: {vis_reason}) -> Thử ứng viên tiếp theo!"
                            )
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        full_peak_path: Optional[str] = None
                        if natural_peak_len >= scene_clip_len + 1.8 and raw_dur_sec >= actual_start_s + scene_clip_len + 1.5:
                            cand_full_path = str(Path(output_path).parent / f"{Path(output_path).stem}_fullpeak.mp4")
                            try:
                                trim_video(
                                    input_path=raw_file,
                                    duration_seconds=natural_peak_len,
                                    output_path=cand_full_path,
                                    width=1080,
                                    height=1920,
                                    fps=30,
                                    apply_grading=True,
                                    is_hook_scene=False,
                                    fair_use_transform=False,
                                    start_offset_sec=actual_start_s,
                                    preserve_continuous_flow=True,
                                )
                                if Path(cand_full_path).exists() and Path(cand_full_path).stat().st_size > 25000:
                                    full_peak_path = cand_full_path
                            except Exception as e_fp:
                                logger.debug(f"[YouTubePeakTool] Lỗi xuất full_peak_path: {e_fp}")

                        win_start = (start_s + actual_start_s) if "download_ranges" in dl_opts else actual_start_s
                        win_end = win_start + (natural_peak_len if full_peak_path else scene_clip_len)

                        record_used_media(
                            url=vid_url,
                            query=search_phrases[0] if search_phrases else search_query,
                            job_id=job_id,
                            source="low_risk_clean_video",
                            media_path=output_path,
                        )
                        if license_policy.cc_only_enabled():
                            license_policy.record_credit(
                                job_id=job_id,
                                video_id=vid_id,
                                title=cand_title,
                                uploader=str(info_entry.get("channel") or info_entry.get("uploader") or cand.get("uploader") or ""),
                            )

                        logger.info(
                            f"🔥 [YouTubePeakTool + MiniRAG] Đã cắt & duyệt Vision ({vis_score}/10, scope={effective_scope}) "
                            f"đoạn {clip_role} ({win_start:.1f}s-{win_end:.1f}s, full_peak={natural_peak_len:.1f}s) "
                            f"từ nguồn YouTube sạch watermark: '{cand_title[:55]}' ({vid_url})"
                        )
                        return {
                            "media_path": output_path,
                            "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                            "source_url": vid_url,
                            "youtube_id": vid_id,
                            "title": cand_title,
                            "window": f"{win_start:.1f}s-{win_end:.1f}s",
                            "vision_score": vis_score,
                            "search_scope": effective_scope,
                            "full_peak_path": full_peak_path,
                            "full_peak_duration_sec": round(natural_peak_len if full_peak_path else scene_clip_len, 2),
                            "scene_clip_duration_sec": round(scene_clip_len, 2),
                        }
        except Exception as e_yt:
            logger.debug(f"[YouTubePeakTool] Lỗi nhánh YouTube video: {e_yt}")

    # ── TẦNG 2 (FALLBACK TỰ ĐỘNG KHI KHÔNG CÓ CLIP YOUTUBE ĐẠT CHUẨN): VIDEO HD DỌC TRÊN PEXELS / PIXABAY ──
    if not allow_3d_cgi_monster:
        try:
            from app.graph.evaluators.visual_critic import VisualCritic
            media_agent = MediaAgent()
            va_vid = {
                "subject": subject_entity or topic,
                "topic": overall_script_query or topic,
                "expected_visual_content": f"{subject_entity or topic} — {narration}",
                "search_queries": search_phrases,
                "is_hook_scene": ((clip_role or "").upper() == "HOOK"),
            }
            effective_narr = (
                f"{overall_script_query or topic}. {narration}"
                if effective_scope == "SCRIPT_OVERALL"
                else narration
            )
            for st_q in search_phrases[:2]:
                best_stock_v = media_agent.search_best_video_clip(
                    query=st_q,
                    used_urls=used,
                    visual_analysis=va_vid,
                    narration=effective_narr,
                    topic=overall_script_query or topic,
                )
                if not best_stock_v or not best_stock_v.get("url"):
                    continue
                v_url = best_stock_v["url"]
                st_title = str(best_stock_v.get("title") or "")
                if not rag.try_claim_in_job_video(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id):
                    continue
                with tempfile.TemporaryDirectory() as tmp_s:
                    raw_stock = str(Path(tmp_s) / "stock_raw.mp4")
                    media_agent.download_file(v_url, raw_stock)
                    if not Path(raw_stock).exists() or Path(raw_stock).stat().st_size <= 20000:
                        rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                        continue
                    # Kiểm tra độ phân giải >= 720p, độ nét >= 5.8, độ sáng và sạch watermark trên clip nguồn
                    raw_gate = VisualCritic.inspect_clip_before_concat(
                        raw_stock, min_short_edge=720, min_sharpness=5.8, check_watermark=True
                    )
                    if not raw_gate.get("passed", False):
                        logger.info(
                            f"🚫 [LicensedStockGate] Bỏ qua stock clip '{st_title[:45]}' vì không đạt chuẩn kỹ thuật: "
                            f"{'; '.join(raw_gate.get('issues') or [])}"
                        )
                        rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                        continue

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                    raw_st_dur = max(0.0, get_duration_ms(raw_stock) / 1000.0)
                    min_st_dur = max(3.0, target_duration_sec)
                    st_start, st_peak_len = find_hook_peak_window(
                        raw_stock,
                        min_duration=min_st_dur,
                        max_duration=min(max_peak_duration_sec, max(min_st_dur, raw_st_dur)),
                    )
                    trim_video(
                        input_path=raw_stock,
                        duration_seconds=min_st_dur,
                        output_path=output_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        apply_grading=True,
                        is_hook_scene=((clip_role or "").upper() == "HOOK"),
                        fair_use_transform=False,
                        start_offset_sec=st_start,
                        preserve_continuous_flow=True,
                    )
                    if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                        ok_st_sig, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=eff_scene_id,
                            media_path=output_path,
                            url=v_url,
                            title=st_title,
                            is_continuation_slice=False,
                        )
                        if not ok_st_sig:
                            rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        passed_st_vis, st_vis_score, st_vis_reason = _verify_short_clip_with_gemini_vision(
                            video_path=output_path,
                            topic=topic,
                            narration=narration,
                            subject_entity=subject_entity or topic,
                            overall_script_query=overall_script_query or st_q,
                            search_scope=effective_scope,
                            video_title=st_title,
                            allow_3d_cgi_monster=False,
                            strict_no_fallback=False,
                        )
                        if not passed_st_vis:
                            logger.info(
                                f"🚫 [LicensedStockVision] Gemini Vision từ chối stock clip '{st_title[:45]}' "
                                f"({st_vis_score}/10: {st_vis_reason}) -> Thử ứng viên tiếp theo!"
                            )
                            rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        full_peak_path_st = None
                        if st_peak_len >= min_st_dur + 1.8 and raw_st_dur >= st_start + min_st_dur + 1.5:
                            cand_full_st = str(Path(output_path).parent / f"{Path(output_path).stem}_fullpeak.mp4")
                            try:
                                trim_video(
                                    input_path=raw_stock,
                                    duration_seconds=st_peak_len,
                                    output_path=cand_full_st,
                                    width=1080,
                                    height=1920,
                                    fps=30,
                                    apply_grading=True,
                                    is_hook_scene=False,
                                    fair_use_transform=False,
                                    start_offset_sec=st_start,
                                    preserve_continuous_flow=True,
                                )
                                if Path(cand_full_st).exists() and Path(cand_full_st).stat().st_size > 25000:
                                    full_peak_path_st = cand_full_st
                            except Exception:
                                pass

                        used.update(extract_media_identity(v_url))
                        record_used_media(
                            url=v_url,
                            query=st_q,
                            job_id=job_id,
                            source="pexels_stock_video",
                            media_path=output_path,
                        )
                        logger.info(
                            f"🎬 [LicensedStockVideo HD Fallback] Đã tải & duyệt video Pexels/Pixabay "
                            f"(scope={effective_scope}, peak={st_peak_len:.1f}s, vis={st_vis_score}/10) cho {clip_role}: '{st_title[:50]}'"
                        )
                        return {
                            "media_path": output_path,
                            "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                            "source_url": v_url,
                            "title": st_title or "HD Portrait Stock Video",
                            "window": f"{st_start:.1f}s-{st_start + (st_peak_len if full_peak_path_st else min_st_dur):.1f}s",
                            "vision_score": st_vis_score,
                            "search_scope": effective_scope,
                            "full_peak_path": full_peak_path_st,
                            "full_peak_duration_sec": round(st_peak_len if full_peak_path_st else min_st_dur, 2),
                            "scene_clip_duration_sec": round(min_st_dur, 2),
                        }
        except Exception as e_st:
            logger.debug(f"[LicensedStockVideo] Lỗi tìm stock video có giấy phép: {e_st}")

    return None


def execute_google_cinematic_image_sync(
    smart_query: str,
    output_path: str,
    narration: str = "",
    topic: str = "",
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    subject_entity: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    job_id: int = 0,
    scene_id: int = 0,
    target_image_count: int = 2,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ tìm ảnh Top Search Google Images theo cơ chế kép (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`)
    kết hợp Multi-Image Harvest (tải 2-3 ảnh thật độc lập/phân cảnh để đạt mật độ 15-17 ảnh/video ~40s):
    - Nếu câu nhỏ có nghĩa độc lập (`SCENE_SPECIFIC`): Tìm trực tiếp theo ý câu nhỏ (`smart_query`), dự phòng bằng `overall_script_query`.
    - Nếu câu nhỏ khi tách lẻ bị vô nghĩa / cụt ngủn / vướng đạo cụ phụ (`SCRIPT_OVERALL`):
      Tìm trực tiếp các hình ảnh xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`) và lọc trùng lặp (`used_urls` + Mini-RAG 64-bit dHash)
      để ghép vào video mượt mà, 100% đúng chủ đề kịch bản!
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    effective_scope, search_qs = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=smart_query,
        overall_script_query=overall_script_query,
        fallback_query=f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=6,
    )

    subj = (subject_entity or "").strip()
    if not subj or (len(subj.split()) == 1 and subj.lower() in _GENERIC_NON_SUBJECT_WORDS):
        subj = _clean_yt_search_phrase(overall_script_query or topic, max_words=3, strip_distractor_props=True) or search_qs[0]

    primary_q = search_qs[0] if search_qs else smart_query
    logger.info(
        f"🖼️ [GoogleImageTool] Scene #{scene_id or '?'} | Scope={effective_scope} | Primary='{primary_q}' | "
        f"TargetImages={target_image_count} | Pool={search_qs[:3]}"
    )

    va_photo = {
        "subject": subj,
        "literal_subject": (overall_script_query or subj) if effective_scope == "SCRIPT_OVERALL" else subj,
        "search_queries": search_qs[:3],
        "google_image_queries": search_qs[:3],
        "is_hook_scene": is_hook_scene,
    }

    effective_narration = (
        f"Overall story: {overall_script_query or topic} ({subj}). Context: {narration}"
        if effective_scope == "SCRIPT_OVERALL"
        else narration
    )

    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=primary_q,
        used_urls=used,
        visual_analysis=va_photo,
        narration=effective_narration,
        topic=overall_script_query or topic,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    # Khi cần >= 2 ảnh/cảnh, bồi thêm nhanh ứng viên góc nhìn phụ thứ 2 (ưu tiên Pexels/Pixabay HD trước khi gọi WebImageFetcher)
    if target_image_count >= 2 and len(all_candidate_urls) < max(8, target_image_count * 2):
        sec_q = search_qs[1] if len(search_qs) > 1 else f"{subj} wildlife photography"
        try:
            for q_extra in [sec_q, *(search_qs[2:3] if len(search_qs) > 2 else [])]:
                for px_it in (media_agent.search_photos(q_extra, per_page=5, orientation="portrait", topic=overall_script_query or topic, narration=narration, used_urls=used) or []):
                    u_px = px_it.get("url")
                    if u_px and u_px not in all_candidate_urls and not is_media_already_used(u_px, used):
                        all_candidate_urls.append(u_px)
                for pxb_it in (media_agent._stock_fetcher.search_pixabay_photos(q_extra, per_page=5) or []):
                    u_pxb = pxb_it.get("url")
                    if u_pxb and u_pxb not in all_candidate_urls and not is_media_already_used(u_pxb, used):
                        all_candidate_urls.append(u_pxb)
        except Exception:
            pass
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, bypass_copyright_enabled
            if bypass_copyright_enabled() or len(all_candidate_urls) < target_image_count + 2:
                extra_web = WebImageFetcher().search_all(
                    query=sec_q,
                    per_source=6,
                    subject=subj,
                    extra_queries=search_qs[2:3] if len(search_qs) > 2 else None,
                )
                for item_ex in (extra_web or []):
                    u_ex = item_ex.get("url")
                    if u_ex and u_ex not in all_candidate_urls and not is_media_already_used(u_ex, used):
                        all_candidate_urls.append(u_ex)
        except Exception:
            pass

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    primary_selected_url = ""
    remaining_urls: List[str] = []

    for idx_u, cand_u in enumerate(all_candidate_urls):
        if idx_u > 0 and is_media_already_used(cand_u, used):
            continue
        try:
            media_agent.download_file(cand_u, output_path)
        except Exception as _dl_err:
            logger.debug(f"[GoogleImageTool] Bỏ qua URL ảnh lỗi ({cand_u[:70]}): {_dl_err}")
            Path(output_path).unlink(missing_ok=True)
            continue
        if Path(output_path).exists() and Path(output_path).stat().st_size > 3000:
            if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=output_path, url=cand_u):
                primary_selected_url = cand_u
                remaining_urls = [u for u in all_candidate_urls[idx_u + 1:] if u != cand_u and not is_media_already_used(u, used)]
                break
            else:
                Path(output_path).unlink(missing_ok=True)

    if not primary_selected_url or not Path(output_path).exists():
        return None

    used.update(extract_media_identity(primary_selected_url))
    record_used_media(
        url=primary_selected_url,
        query=primary_q,
        job_id=job_id,
        source=best_photo.get("source", "google_image"),
        media_path=output_path,
    )

    pose_images: List[str] = [output_path]
    desired_extra = max(0, int(target_image_count) - 1)

    if desired_extra > 0 and remaining_urls:
        out_p = Path(output_path)
        stem = out_p.stem
        parent = out_p.parent
        cand_slice = remaining_urls[: max(desired_extra + 6, 8)]

        def _dl_cand(pair: Tuple[int, str]) -> Optional[Tuple[str, str]]:
            c_idx, c_url = pair
            tmp_path = str(parent / f"{stem}_cand{c_idx + 2}.jpg")
            try:
                media_agent.download_file(c_url, tmp_path)
                if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 3000:
                    return tmp_path, c_url
            except Exception:
                pass
            Path(tmp_path).unlink(missing_ok=True)
            return None

        with ThreadPoolExecutor(max_workers=min(4, len(cand_slice))) as pool:
            dl_results = list(pool.map(_dl_cand, enumerate(cand_slice)))

        unused_after_multi: List[str] = []
        for res_pair in dl_results:
            if not res_pair:
                continue
            tmp_p, c_url = res_pair
            if len(pose_images) < target_image_count:
                final_cut_p = str(parent / f"{stem}_cut{len(pose_images) + 1}.jpg")
                try:
                    Path(tmp_p).replace(final_cut_p)
                except Exception:
                    final_cut_p = tmp_p
                if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=final_cut_p, url=c_url):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=primary_q,
                        job_id=job_id,
                        source=best_photo.get("source", "google_image"),
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)
                unused_after_multi.append(c_url)

        remaining_urls = unused_after_multi + remaining_urls[len(cand_slice):]

    return {
        "media_path": output_path,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.0),
        "alternate_urls": remaining_urls,
        "search_scope": effective_scope,
    }


def execute_epic_monster_cinematic_media_sync(
    creature_name: str,
    scale_anchor_object: str,
    output_path: str,
    monster_media_mode: str = "3D_CGI_VIDEO",
    action_or_concept_query: str = "",
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    narration: str = "",
    topic: str = "",
    target_duration_sec: float = 4.5,
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    job_id: int = 0,
    scene_id: int = 0,
    channel_id: str = "",
    target_image_count: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ `@tool summon_epic_monster_cinematic_media` (3-Tier Epic Monster & Megalophobia Engine):
    - Tầng 1 (`monster_media_mode == "3D_CGI_VIDEO"`):
      Tìm kiếm video 3D CGI Unreal Engine 5 / Blender Megalophobia / VFX Movie Scene trên YouTube Shorts
      (VD: "{creature_name} {scale_anchor_object} 3d animation", "{creature_name} megalophobia cgi"),
      kiểm duyệt với `allow_3d_cgi_monster=True` và cắt đúng đoạn Peak hoành tráng nhất.
    - Tầng 2 (`monster_media_mode == "CONCEPT_ART_8K"` hoặc khi Tầng 1 không có clip):
      Truy xuất bộ 3-4 bức Đại cảnh Concept Art 8K trên ArtStation / Wallhaven / Movie Stills có vật đối chiếu tỷ lệ
      (`scale_anchor_object`) với `creature_mode=True` để dựng 3D Multi-Cut Parallax.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.services.media.image_quality import creature_queries
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    c_name = (creature_name or topic or "colossal sea monster").strip()
    s_anchor = (scale_anchor_object or "submarine").strip()
    mode_norm = (monster_media_mode or "3D_CGI_VIDEO").strip().upper()

    # 1. TẦNG 1: Săn Video 3D CGI / Unreal Engine 5 / Megalophobia Shorts Peak
    if mode_norm == "3D_CGI_VIDEO" or is_hook_scene:
        yt_monster_q = (
            action_or_concept_query
            if action_or_concept_query and any(k in action_or_concept_query.lower() for k in ("3d", "cgi", "animation", "megalophobia"))
            else f"{c_name} {s_anchor} 3d animation"
        )
        yt_fallback_q = f"{c_name} megalophobia cgi"
        vid_out = (
            output_path
            if output_path.lower().endswith(".mp4")
            else str(Path(output_path).parent / f"scene_{scene_id:03d}_monster_cgi.mp4")
        )
        yt_res = execute_youtube_short_peak_clone_sync(
            search_query=yt_monster_q,
            output_path=vid_out,
            target_duration_sec=target_duration_sec,
            clip_role="HOOK" if is_hook_scene else "PEAK",
            used_urls=used,
            fallback_query=yt_fallback_q,
            subject_entity=c_name,
            narration=narration,
            topic=topic,
            search_scope=search_scope,
            overall_script_query=overall_script_query or f"{c_name} {s_anchor}",
            max_peak_duration_sec=14.0,
            job_id=job_id,
            scene_id=scene_id,
            channel_id=channel_id,
            allow_3d_cgi_monster=True,
        )
        if yt_res and yt_res.get("media_path") and Path(yt_res["media_path"]).exists():
            logger.info(
                f"🐉 [MonsterTool 3D CGI Video] Scene #{scene_id}: Đã triệu hồi clip 3D CGI Peak cho '{c_name}' "
                f"(anchor='{s_anchor}'): {yt_res.get('title', '')[:50]}"
            )
            return yt_res

    # 2. TẦNG 2: Săn bộ 3-4 bức Đại cảnh Concept Art 8K (ArtStation / Wallhaven / Movie VFX Stills) có vật đối chiếu tỷ lệ
    c_qs = creature_queries(
        subject=c_name,
        topic=topic,
        narration=narration,
        scale_anchor=s_anchor,
        action_query=action_or_concept_query,
    )
    img_out = (
        output_path
        if output_path.lower().endswith((".jpg", ".jpeg", ".png"))
        else str(Path(output_path).parent / f"scene_{scene_id:03d}_monster_8k.jpg")
    )
    va_monster = {
        "subject": c_name,
        "creature_mode": True,
        "creature_queries": c_qs,
        "google_image_queries": c_qs[:4],
        "search_queries": c_qs[:3],
        "is_hook_scene": is_hook_scene,
        "preferred_engines": ["google"],
    }
    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=c_qs[0] if c_qs else f"giant {c_name} {s_anchor} concept art 8k",
        used_urls=used,
        visual_analysis=va_monster,
        narration=f"Colossal {c_name} towering over {s_anchor}. {narration}",
        topic=overall_script_query or topic,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    if target_image_count >= 2 and len(all_candidate_urls) < max(10, target_image_count * 3):
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher
            extra_web = WebImageFetcher().search_all(
                query=c_qs[1] if len(c_qs) > 1 else f"giant {c_name} movie scene cgi",
                per_source=8,
                subject=c_name,
                extra_queries=c_qs[2:4],
                creature_mode=True,
            )
            for item_ex in (extra_web or []):
                u_ex = item_ex.get("url")
                if u_ex and u_ex not in all_candidate_urls and not is_media_already_used(u_ex, used):
                    all_candidate_urls.append(u_ex)
        except Exception:
            pass

    Path(img_out).parent.mkdir(parents=True, exist_ok=True)
    primary_selected_url = ""
    remaining_urls: List[str] = []

    for idx_u, cand_u in enumerate(all_candidate_urls):
        if idx_u > 0 and is_media_already_used(cand_u, used):
            continue
        try:
            media_agent.download_file(cand_u, img_out)
        except Exception:
            Path(img_out).unlink(missing_ok=True)
            continue
        if Path(img_out).exists() and Path(img_out).stat().st_size > 3000:
            if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=img_out, url=cand_u):
                primary_selected_url = cand_u
                remaining_urls = [u for u in all_candidate_urls[idx_u + 1:] if u != cand_u and not is_media_already_used(u, used)]
                break
            else:
                Path(img_out).unlink(missing_ok=True)

    if not primary_selected_url or not Path(img_out).exists():
        return None

    used.update(extract_media_identity(primary_selected_url))
    record_used_media(
        url=primary_selected_url,
        query=c_qs[0] if c_qs else c_name,
        job_id=job_id,
        source=best_photo.get("source", "monster_concept_art_8k"),
        media_path=img_out,
    )

    pose_images: List[str] = [img_out]
    desired_extra = max(0, int(target_image_count) - 1)
    if desired_extra > 0 and remaining_urls:
        out_p = Path(img_out)
        stem = out_p.stem
        parent = out_p.parent
        cand_slice = remaining_urls[: max(desired_extra + 6, 8)]

        def _dl_m_cand(pair: Tuple[int, str]) -> Optional[Tuple[str, str]]:
            c_idx, c_url = pair
            tmp_path = str(parent / f"{stem}_cand{c_idx + 2}.jpg")
            try:
                media_agent.download_file(c_url, tmp_path)
                if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 3000:
                    return tmp_path, c_url
            except Exception:
                pass
            Path(tmp_path).unlink(missing_ok=True)
            return None

        with ThreadPoolExecutor(max_workers=min(4, len(cand_slice))) as pool:
            dl_results = list(pool.map(_dl_m_cand, enumerate(cand_slice)))

        unused_after_multi: List[str] = []
        for res_pair in dl_results:
            if not res_pair:
                continue
            tmp_p, c_url = res_pair
            if len(pose_images) < target_image_count:
                final_cut_p = str(parent / f"{stem}_cut{len(pose_images) + 1}.jpg")
                try:
                    Path(tmp_p).replace(final_cut_p)
                except Exception:
                    final_cut_p = tmp_p
                if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=final_cut_p, url=c_url):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=c_qs[0] if c_qs else c_name,
                        job_id=job_id,
                        source="monster_concept_art_8k",
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)
                unused_after_multi.append(c_url)
        remaining_urls = unused_after_multi + remaining_urls[len(cand_slice):]

    logger.info(
        f"🐉 [MonsterTool ConceptArt 8K] Scene #{scene_id}: Đã thu thập {len(pose_images)} ảnh đại cảnh '{c_name}' "
        f"(anchor='{s_anchor}')!"
    )
    return {
        "media_path": img_out,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.2),
        "alternate_urls": remaining_urls,
        "search_scope": search_scope,
        "creature_mode": True,
    }


def execute_clean_cinematic_portrait_or_broll_sync(
    subject_entity: str,
    human_action_query: str,
    output_path: str,
    media_preference: str = "VIDEO_BROLL",
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    narration: str = "",
    topic: str = "",
    target_duration_sec: float = 4.5,
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    job_id: int = 0,
    scene_id: int = 0,
    target_image_count: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ `@tool fetch_clean_cinematic_portrait_or_broll`:
    - Bỏ qua 100% Open Web Google Images (tránh triệt để Quote Card có chữ, bìa sách, infographic).
    - Khi `media_preference == "VIDEO_BROLL"` (hoặc cảnh Hook):
      Truy xuất trực tiếp Video B-Roll quay diễn viên thật chuẩn dọc 9:16 từ Pexels HD / Pixabay HD,
      cắt đoạn Peak cảm xúc và trả về cả `full_peak_path` nếu đoạn Peak dài.
    - Khi `media_preference == "CLEAN_PORTRAIT_PHOTO"` (hoặc dự phòng):
      Truy xuất bộ 3-4 bức ảnh chân dung nghệ thuật ánh sáng tối (Chiaroscuro / Moody Portrait) sạch chữ 100%
      từ Pexels & Pixabay Photo API.
    """
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    media_agent = MediaAgent()
    clean_act_q = (human_action_query or subject_entity or "dramatic portrait dark lighting").strip()
    pref_norm = (media_preference or "VIDEO_BROLL").strip().upper()

    # 1. Nhánh Video B-Roll diễn viên thật Pexels / Pixabay HD
    if pref_norm == "VIDEO_BROLL" or is_hook_scene:
        vid_out = (
            output_path
            if output_path.lower().endswith(".mp4")
            else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_broll.mp4")
        )
        va_clean_vid = {
            "subject": subject_entity or clean_act_q,
            "search_queries": [
                clean_act_q,
                "close up eyes dark lighting",
                "silhouette person walking night",
                "dramatic portrait lighting",
            ],
            "clean_stock_only": True,
            "is_hook_scene": is_hook_scene,
        }
        best_stock_v = media_agent.search_best_video_clip(
            query=clean_act_q,
            used_urls=used,
            visual_analysis=va_clean_vid,
            narration=narration,
            topic=overall_script_query or topic,
        )
        if best_stock_v and best_stock_v.get("url"):
            v_url = best_stock_v["url"]
            st_title = str(best_stock_v.get("title") or "")
            if rag.try_claim_in_job_video(job_id=job_id, url=v_url, title=st_title, scene_id=scene_id):
                with tempfile.TemporaryDirectory() as tmp_s:
                    raw_stock = str(Path(tmp_s) / "stock_raw.mp4")
                    media_agent.download_file(v_url, raw_stock)
                    if Path(raw_stock).exists() and Path(raw_stock).stat().st_size > 20000:
                        Path(vid_out).parent.mkdir(parents=True, exist_ok=True)
                        from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                        raw_st_dur = max(0.0, get_duration_ms(raw_stock) / 1000.0)
                        min_st_dur = max(3.0, target_duration_sec)
                        st_start, st_peak_len = find_hook_peak_window(
                            raw_stock,
                            min_duration=min_st_dur,
                            max_duration=min(14.0, max(min_st_dur, raw_st_dur)),
                        )
                        trim_video(
                            input_path=raw_stock,
                            duration_seconds=min_st_dur,
                            output_path=vid_out,
                            width=1080,
                            height=1920,
                            fps=30,
                            apply_grading=True,
                            is_hook_scene=is_hook_scene,
                            fair_use_transform=False,
                            start_offset_sec=st_start,
                            preserve_continuous_flow=True,
                        )
                        if Path(vid_out).exists() and Path(vid_out).stat().st_size > 20000:
                            ok_st_sig, _ = rag.verify_and_commit_in_job_clip(
                                job_id=job_id,
                                scene_id=scene_id,
                                media_path=vid_out,
                                url=v_url,
                                title=st_title,
                                is_continuation_slice=False,
                            )
                            if ok_st_sig:
                                full_peak_path = None
                                if st_peak_len >= min_st_dur + 1.8 and raw_st_dur >= st_start + min_st_dur + 1.5:
                                    cand_full_st = str(Path(vid_out).parent / f"{Path(vid_out).stem}_fullpeak.mp4")
                                    try:
                                        trim_video(
                                            input_path=raw_stock,
                                            duration_seconds=st_peak_len,
                                            output_path=cand_full_st,
                                            width=1080,
                                            height=1920,
                                            fps=30,
                                            apply_grading=True,
                                            is_hook_scene=False,
                                            fair_use_transform=False,
                                            start_offset_sec=st_start,
                                            preserve_continuous_flow=True,
                                        )
                                        if Path(cand_full_st).exists() and Path(cand_full_st).stat().st_size > 25000:
                                            full_peak_path = cand_full_st
                                    except Exception:
                                        pass
                                used.update(extract_media_identity(v_url))
                                record_used_media(
                                    url=v_url,
                                    query=clean_act_q,
                                    job_id=job_id,
                                    source="clean_portrait_broll_video",
                                    media_path=vid_out,
                                )
                                logger.info(
                                    f"🎭 [CleanPortraitTool Video] Scene #{scene_id}: Đã tải B-Roll diễn viên thật sạch chữ "
                                    f"cho '{clean_act_q}' ({st_title[:45]})"
                                )
                                return {
                                    "media_path": vid_out,
                                    "media_type": "hook_ready_video" if is_hook_scene else "video",
                                    "source_url": v_url,
                                    "title": st_title or "Clean Portrait B-Roll Video",
                                    "window": f"{st_start:.1f}s-{st_start + (st_peak_len if full_peak_path else min_st_dur):.1f}s",
                                    "search_scope": search_scope,
                                    "full_peak_path": full_peak_path,
                                    "full_peak_duration_sec": round(st_peak_len if full_peak_path else min_st_dur, 2),
                                    "scene_clip_duration_sec": round(min_st_dur, 2),
                                    "clean_stock_only": True,
                                }
                            else:
                                Path(vid_out).unlink(missing_ok=True)

    # 2. Nhánh Bộ 3-4 Ảnh Chân Dung Nghệ Thuật Pexels / Pixabay HD Sạch Chữ 100% (`clean_stock_only=True`)
    img_out = (
        output_path
        if output_path.lower().endswith((".jpg", ".jpeg", ".png"))
        else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_portrait.jpg")
    )
    va_clean_photo = {
        "subject": subject_entity or clean_act_q,
        "search_queries": [
            clean_act_q,
            "dramatic portrait dark lighting",
            "man intense eyes dark shadow",
            "person silhouette shadow",
        ],
        "clean_stock_only": True,
        "is_hook_scene": is_hook_scene,
    }
    best_photo = media_agent.search_best_photo(
        query=clean_act_q,
        used_urls=used,
        visual_analysis=va_clean_photo,
        narration=narration,
        topic="dramatic portrait",
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    if len(all_candidate_urls) < max(8, target_image_count * 2):
        try:
            from app.graph.parallel_search import run_langgraph_parallel_tasks
            supp_queries = [clean_act_q, "dramatic portrait dark shadow", "intense eyes close up dark", "person silhouette night"]
            px_tasks = [{"q": q_s} for q_s in supp_queries]
            px_completed = run_langgraph_parallel_tasks(
                tasks=px_tasks,
                worker_fn=lambda t: media_agent.search_photos(
                    t["q"], per_page=5, orientation="portrait", topic="dramatic portrait", narration="", used_urls=used
                ) or [],
                task_group=f"PortraitPexelsSearch({len(px_tasks)} queries)",
            )
            for px_res in px_completed:
                for p_it in (px_res.get("result") or []):
                    u_px = p_it.get("url")
                    if u_px and u_px not in all_candidate_urls and not is_media_already_used(u_px, used):
                        all_candidate_urls.append(u_px)
        except Exception:
            pass

    Path(img_out).parent.mkdir(parents=True, exist_ok=True)
    primary_selected_url = ""
    remaining_urls: List[str] = []
    for idx_u, cand_u in enumerate(all_candidate_urls):
        if idx_u > 0 and is_media_already_used(cand_u, used):
            continue
        try:
            media_agent.download_file(cand_u, img_out)
        except Exception:
            Path(img_out).unlink(missing_ok=True)
            continue
        if Path(img_out).exists() and Path(img_out).stat().st_size > 3000:
            if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=img_out, url=cand_u):
                primary_selected_url = cand_u
                remaining_urls = [u for u in all_candidate_urls[idx_u + 1:] if u != cand_u and not is_media_already_used(u, used)]
                break
            else:
                Path(img_out).unlink(missing_ok=True)

    if not primary_selected_url or not Path(img_out).exists():
        return None

    used.update(extract_media_identity(primary_selected_url))
    record_used_media(
        url=primary_selected_url,
        query=clean_act_q,
        job_id=job_id,
        source="clean_portrait_photo",
        media_path=img_out,
    )

    pose_images: List[str] = [img_out]
    out_p = Path(img_out)
    desired_extra = max(0, int(target_image_count) - 1)
    if desired_extra > 0 and remaining_urls:
        from app.graph.parallel_search import run_langgraph_parallel_tasks
        cand_slice = remaining_urls[: max(desired_extra + 5, 6)]
        dl_tasks = [
            {"idx_r": idx_r, "c_url": c_url, "tmp_p": str(out_p.parent / f"{out_p.stem}_cand{idx_r + 2}.jpg")}
            for idx_r, c_url in enumerate(cand_slice)
        ]

        def _dl_portrait_item(t: Dict[str, Any]) -> Optional[Tuple[str, str]]:
            tmp_p = t["tmp_p"]
            c_url = t["c_url"]
            try:
                media_agent.download_file(c_url, tmp_p)
                if Path(tmp_p).exists() and Path(tmp_p).stat().st_size > 3000:
                    return tmp_p, c_url
            except Exception:
                pass
            Path(tmp_p).unlink(missing_ok=True)
            return None

        dl_completed = run_langgraph_parallel_tasks(
            tasks=dl_tasks,
            worker_fn=_dl_portrait_item,
            task_group=f"PortraitDownload({len(dl_tasks)} images)",
        )
        for dl_res in dl_completed:
            pair_out = dl_res.get("result")
            if not pair_out:
                continue
            tmp_p, c_url = pair_out
            if len(pose_images) < target_image_count:
                final_cut_p = str(out_p.parent / f"{out_p.stem}_cut{len(pose_images) + 1}.jpg")
                try:
                    Path(tmp_p).replace(final_cut_p)
                except Exception:
                    final_cut_p = tmp_p
                if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=final_cut_p, url=c_url):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=clean_act_q,
                        job_id=job_id,
                        source="clean_portrait_photo",
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)

    logger.info(
        f"🎭 [CleanPortraitTool Photo] Scene #{scene_id}: Đã thu thập {len(pose_images)} ảnh chân dung sạch chữ "
        f"cho '{clean_act_q}'!"
    )
    return {
        "media_path": img_out,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.0),
        "alternate_urls": remaining_urls[len(pose_images):],
        "search_scope": search_scope,
        "clean_stock_only": True,
    }


# ──────────────────────────────────────────────────────────────────────────────
# BỘ ĐẠO DIỄN LLM BIND_TOOLS (PHÂN TÍCH KỊCH BẢN TỔNG THỂ VS CÂU NHỎ TỰ ĐỘNG)
# ──────────────────────────────────────────────────────────────────────────────

def plan_media_tools_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    channel_niche: str = "cinematic",
) -> List[Dict[str, Any]]:
    """
    Sử dụng Gemini gắn `bind_tools(MEDIA_DIRECTOR_TOOLS)` gồm 4 công cụ chuyên biệt:
      1. `summon_epic_monster_cinematic_media`: Cho các cảnh quái vật khổng lồ, thủy quái biển sâu, khủng long, kaiju, thực thể kinh dị, megalophobia.
      2. `fetch_clean_cinematic_portrait_or_broll`: Cho các cảnh tâm lý học, thao túng tâm lý, khắc kỷ, cảm xúc con người (sạch chữ 100%).
      3. `clone_youtube_short_peak_clip`: Cho các cảnh động vật hoang dã, thiên nhiên, khoa học, đời thực cần cắt Peak YouTube Shorts.
      4. `search_google_cinematic_image`: Cho các cảnh tìm kiếm đa ảnh Top Google Images thông thường.
    """
    total_scenes = len(scenes)
    if total_scenes == 0:
        return []

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    scenes_brief = [
        {
            "scene_id": s.get("scene_id"),
            "narration": s.get("narration", ""),
            "visual_keyword": s.get("visual_keyword", ""),
            "estimated_seconds": s.get("estimated_seconds", 4.0),
        }
        for s in scenes
    ]

    prompt = f"""You are an Elite Viral YouTube Shorts Visual Director with 4 specialized media tools.
Topic: "{topic}"
Primary Subject/Entity: "{primary_entity or topic}"
Channel Niche: "{channel_niche}"
Total Scenes: {total_scenes}

FULL SCRIPT STORY:
"{full_script_text}"

Individual Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR MISSION:
Call EXACTLY ONE tool for EACH scene (from scene_id=1 to scene_id={total_scenes}) based on semantic understanding of the script (NO brittle keyword matching):

1. CHOOSE THE RIGHT SPECIALIZED TOOL BASED ON TOPIC & SCENE MEANING:
   - IF THE SCRIPT OR SCENE IS ABOUT COLOSSAL MONSTERS, DEEP-SEA LEVIATHANS (The Bloop, Kraken, Megalodon, Cthulhu, Gargantuan Leviathan), PREHISTORIC MEGAFAUNA (Titanoboa, Mosasaurus, T-Rex), KAIJU (Godzilla), CRYPTIDS (SCP, Siren Head, Wendigo), OR MEGALOPHOBIA/THALASSOPHOBIA:
     -> ALWAYS call `summon_epic_monster_cinematic_media`!
     -> Provide `creature_name` (e.g., "The Bloop Leviathan", "Megalodon", "Kraken") and `scale_anchor_object` (a human object like "submarine", "battleship", "scuba diver", "skyscraper" to show colossal scale).
     -> Use `monster_media_mode="3D_CGI_VIDEO"` for Scene 1 (Hook) and 2-3 climax action scenes, and `monster_media_mode="CONCEPT_ART_8K"` for the remaining body scenes so the video combines 3D CGI motion with 8K ArtStation/Movie Still scale shots.
   - IF THE SCRIPT OR SCENE IS ABOUT HUMAN PSYCHOLOGY, DARK PSYCHOLOGY, MANIPULATION, NARCISSISM, GASLIGHTING, STOICISM, PHILOSOPHY, OR INTERPERSONAL EMOTIONAL TENSION:
     -> ALWAYS call `fetch_clean_cinematic_portrait_or_broll`! (NEVER call `search_google_cinematic_image` for psychology because open-web Google Images return text quote cards and book covers!).
     -> Write `human_action_query` as a concrete 2-4 word physical human action or facial expression (e.g., "close up eyes dark lighting", "couple argument tension", "man stressed alone dark room", "silhouette person walking night").
     -> Use `media_preference="VIDEO_BROLL"` for Scene 1 and 3-4 body scenes, and `media_preference="CLEAN_PORTRAIT_PHOTO"` for the other scenes.
   - FOR REAL-WORLD WILDLIFE, NATURE, SCIENCE, HISTORY, OR DOCUMENTARY TOPICS:
     -> Call `clone_youtube_short_peak_clip` (`clip_role="HOOK"` for Scene 1, and `clip_role="PEAK"` for 3-4 action body scenes) + `search_google_cinematic_image` for the remaining scenes.

2. DECIDE `search_scope` FOR EACH INDIVIDUAL SCENE (`"SCENE_SPECIFIC"` vs `"SCRIPT_OVERALL"`):
   - `"SCENE_SPECIFIC"`: The split sentence describes a clear, standalone visual action of the subject.
   - `"SCRIPT_OVERALL"`: The split sentence becomes vague, transitional, abstract, or mentions a minor distracting prop when isolated -> search around `overall_script_query`!

IMPORTANT: Emit tool calls for ALL {total_scenes} scenes in a single response!"""

    decisions_by_scene: Dict[int, Dict[str, Any]] = {}

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            func_declarations = []
            for t_obj in MEDIA_DIRECTOR_TOOLS:
                schema = t_obj.args_schema.model_json_schema() if getattr(t_obj, "args_schema", None) else {}
                props = {}
                for k_prop, v_prop in (schema.get("properties") or {}).items():
                    p_type = (v_prop.get("type") or "string").upper()
                    if p_type == "NUMBER":
                        p_type = "NUMBER"
                    elif p_type == "INTEGER":
                        p_type = "INTEGER"
                    else:
                        p_type = "STRING"
                    props[k_prop] = {
                        "type": p_type,
                        "description": v_prop.get("description") or k_prop,
                    }
                func_declarations.append({
                    "name": t_obj.name,
                    "description": t_obj.description,
                    "parameters": {
                        "type": "OBJECT",
                        "properties": props,
                        "required": schema.get("required") or ["scene_id"],
                    },
                })

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"function_declarations": func_declarations}],
                "tool_config": {"function_calling_config": {"mode": "ANY"}},
                "generationConfig": {"temperature": 0.2},
            }

            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, timeout=22)
                    if resp.status_code == 200:
                        data = resp.json()
                        parts = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [])
                        )
                        for p in parts:
                            fc = p.get("functionCall")
                            if fc and isinstance(fc, dict):
                                t_name = fc.get("name", "")
                                t_args = dict(fc.get("args") or {})
                                sid = int(t_args.get("scene_id") or 0)
                                if sid > 0:
                                    t_args["tool_name"] = t_name
                                    decisions_by_scene[sid] = t_args
                        if decisions_by_scene:
                            scope_summary = ", ".join(
                                f"S{sid}:{d.get('tool_name')}[{d.get('search_scope', 'SCENE_SPECIFIC')}]"
                                for sid, d in sorted(decisions_by_scene.items())
                            )
                            logger.info(
                                f"🧠 [LLM Tool Director] Đã chọn Tool & Scope cho {len(decisions_by_scene)}/{total_scenes} cảnh "
                                f"qua `{model_name}` ({scope_summary})"
                            )
                            break
                    elif resp.status_code in (429, 403, 404, 503):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as req_err:
                    logger.debug(f"[LLM Tool Director] {model_name} error: {req_err}")
                    continue
    except Exception as ex:
        logger.warning(f"[LLM Tool Director] bind_tools fallback sang heuristic thông minh: {ex}")

    final_plan: List[Dict[str, Any]] = []
    motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    niche_norm_dir = str(channel_niche or "").strip().lower()
    is_rescue_or_wildlife = any(k in niche_norm_dir for k in ("rescue", "animal", "pet", "wildlife", "predator"))
    # Với kênh Cứu hộ động vật (Paws & Hearts) & Động vật hoang dã (Wild Odyssey):
    # Bắt buộc ưu tiên 100% Video chuyển động thật liên hoàn ở 3 cảnh đầu (Cảnh 1, 2, 3) + Cảnh 5, 6
    # để giữ chân người xem qua mốc 8 giây đầu và đẩy APV% vượt 75%!
    peak_indices = {1, 2, 3, 5, 6} if is_rescue_or_wildlife else {1, 2, 4, 6, 8}
    max_video_peak_scenes = max(5 if is_rescue_or_wildlife else 4, (total_scenes + 1) // 2)
    video_peak_count = 0
    default_subj = _clean_yt_search_phrase(primary_entity or topic, max_words=3, strip_distractor_props=True)
    default_overall_q = _clean_yt_search_phrase(topic, subject_entity=default_subj, max_words=5, strip_distractor_props=True)

    # Nếu LLM đã chọn công cụ chuyên biệt (`summon_epic_monster_cinematic_media` hoặc `fetch_clean_cinematic_portrait_or_broll`)
    # cho kịch bản này nhưng chỉ trả về functionCall cho một số cảnh đầu, kế thừa quyết định công cụ của LLM cho các cảnh còn lại!
    llm_monster_ref = next(
        (d for d in decisions_by_scene.values() if d.get("tool_name") == "summon_epic_monster_cinematic_media"),
        None,
    )
    llm_portrait_ref = next(
        (d for d in decisions_by_scene.values() if d.get("tool_name") == "fetch_clean_cinematic_portrait_or_broll"),
        None,
    )

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        if sid in decisions_by_scene:
            d = decisions_by_scene[sid]
            t_chosen = d.get("tool_name", "")
            if not d.get("overall_script_query"):
                d["overall_script_query"] = default_overall_q
            if not d.get("search_scope"):
                d["search_scope"] = "SCENE_SPECIFIC"
            if not d.get("camera_motion"):
                d["camera_motion"] = "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)]

            # Tôn trọng tuyệt đối khi LLM đã chọn 2 công cụ chuyên biệt (`summon_epic_monster_cinematic_media` hoặc `fetch_clean_cinematic_portrait_or_broll`)
            # Đồng thời: Nếu kịch bản tổng thể đã được LLM xác nhận là Quái Vật (`llm_monster_ref`) hoặc Tâm Lý Học (`llm_portrait_ref`),
            # đồng bộ hóa luôn các phân cảnh chuyển ý (như "NOAA sensors", "Naval sonar") về cùng công cụ chuyên biệt với `search_scope="SCRIPT_OVERALL"`
            # để tránh việc cảnh phụ bị lọt sang Pexels lấy video tàu thuyền/tháp thời tiết ban ngày lạc tông!
            if t_chosen != "summon_epic_monster_cinematic_media" and llm_monster_ref is not None:
                c_name_inh = llm_monster_ref.get("creature_name") or default_subj
                s_anchor_inh = llm_monster_ref.get("scale_anchor_object") or "submarine"
                m_mode_force = "3D_CGI_VIDEO" if sid in (1, 2, 4, 5, 6) else "CONCEPT_ART_8K"
                d = {
                    "tool_name": "summon_epic_monster_cinematic_media",
                    "scene_id": sid,
                    "creature_name": c_name_inh,
                    "subject_entity": c_name_inh,
                    "scale_anchor_object": s_anchor_inh,
                    "monster_media_mode": m_mode_force,
                    "search_scope": "SCRIPT_OVERALL",
                    "action_or_concept_query": f"{c_name_inh} {s_anchor_inh} 3d animation",
                    "overall_script_query": llm_monster_ref.get("overall_script_query") or default_overall_q,
                    "target_duration_sec": float(s.get("estimated_seconds") or 4.5),
                    "camera_motion": d.get("camera_motion") or motion_cycle[idx % len(motion_cycle)],
                    "scope_audited": True,
                }
                final_plan.append(d)
                continue

            if t_chosen != "fetch_clean_cinematic_portrait_or_broll" and llm_portrait_ref is not None and llm_monster_ref is None:
                d = {
                    "tool_name": "fetch_clean_cinematic_portrait_or_broll",
                    "scene_id": sid,
                    "subject_entity": default_subj,
                    "human_action_query": llm_portrait_ref.get("human_action_query") or "dramatic portrait dark lighting",
                    "media_preference": "VIDEO_BROLL" if sid in (1, 2, 4, 6) else "CLEAN_PORTRAIT_PHOTO",
                    "search_scope": "SCRIPT_OVERALL",
                    "overall_script_query": llm_portrait_ref.get("overall_script_query") or default_overall_q,
                    "target_duration_sec": float(s.get("estimated_seconds") or 4.5),
                    "camera_motion": d.get("camera_motion") or motion_cycle[idx % len(motion_cycle)],
                    "scope_audited": True,
                }
                final_plan.append(d)
                continue

            if t_chosen == "summon_epic_monster_cinematic_media":
                d.setdefault("subject_entity", d.get("creature_name") or default_subj)
                # Đảm bảo ít nhất 5/7 phân cảnh (Cảnh 1, 2, 4, 5, 6) ưu tiên săn Video 3D CGI YouTube Shorts Reup trước khi dùng ảnh tĩnh!
                if sid in (1, 2, 4, 5, 6):
                    d["monster_media_mode"] = "3D_CGI_VIDEO"
                d["scope_audited"] = True
                final_plan.append(d)
                continue
            if t_chosen == "fetch_clean_cinematic_portrait_or_broll":
                d.setdefault("subject_entity", default_subj)
                d["scope_audited"] = True
                final_plan.append(d)
                continue

            if sid in peak_indices and video_peak_count < max_video_peak_scenes and t_chosen != "clone_youtube_short_peak_clip":
                q_vid = d.get("youtube_search_query") or d.get("smart_image_query") or s.get("visual_keyword") or default_overall_q
                d = {
                    "tool_name": "clone_youtube_short_peak_clip",
                    "scene_id": sid,
                    "subject_entity": d.get("subject_entity") or default_subj,
                    "search_scope": d.get("search_scope") or "SCENE_SPECIFIC",
                    "youtube_search_query": q_vid,
                    "overall_script_query": d.get("overall_script_query") or default_overall_q,
                    "fallback_broad_query": default_overall_q,
                    "clip_role": "HOOK" if sid == 1 else "PEAK",
                    "target_duration_sec": float(s.get("estimated_seconds") or 4.5),
                    "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                }
            if d.get("tool_name") == "clone_youtube_short_peak_clip":
                if video_peak_count < max_video_peak_scenes:
                    video_peak_count += 1
                else:
                    q_conv = d.get("youtube_search_query") or d.get("smart_image_query") or s.get("visual_keyword") or default_overall_q
                    d = {
                        "tool_name": "search_google_cinematic_image",
                        "scene_id": sid,
                        "subject_entity": d.get("subject_entity") or default_subj,
                        "search_scope": d.get("search_scope") or "SCENE_SPECIFIC",
                        "smart_image_query": q_conv,
                        "overall_script_query": d.get("overall_script_query") or default_overall_q,
                        "camera_motion": d.get("camera_motion") or motion_cycle[idx % len(motion_cycle)],
                    }
            d["scope_audited"] = True
            final_plan.append(d)
            continue

        narr = s.get("narration", "")
        kw = s.get("visual_keyword") or narr[:50]
        is_meaningless_split = _is_meaningless_or_prop_distracted_when_split(narr, kw, default_subj)
        scope = "SCRIPT_OVERALL" if is_meaningless_split else "SCENE_SPECIFIC"
        clean_kw = (
            default_overall_q
            if is_meaningless_split
            else _clean_yt_search_phrase(kw, subject_entity=default_subj, max_words=5)
        )
        dur = float(s.get("estimated_seconds") or 4.5)

        if llm_monster_ref is not None:
            c_name_inh = llm_monster_ref.get("creature_name") or default_subj
            s_anchor_inh = llm_monster_ref.get("scale_anchor_object") or "submarine"
            m_mode_inh = "3D_CGI_VIDEO" if sid in (1, 2, 4, 5, 6) else "CONCEPT_ART_8K"
            final_plan.append({
                "tool_name": "summon_epic_monster_cinematic_media",
                "scene_id": sid,
                "creature_name": c_name_inh,
                "subject_entity": c_name_inh,
                "scale_anchor_object": s_anchor_inh,
                "monster_media_mode": m_mode_inh,
                "search_scope": scope,
                "action_or_concept_query": clean_kw,
                "overall_script_query": llm_monster_ref.get("overall_script_query") or default_overall_q,
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": True,
            })
        elif llm_portrait_ref is not None:
            m_pref_inh = "VIDEO_BROLL" if sid in (1, 2, 4, 6) else "CLEAN_PORTRAIT_PHOTO"
            final_plan.append({
                "tool_name": "fetch_clean_cinematic_portrait_or_broll",
                "scene_id": sid,
                "subject_entity": llm_portrait_ref.get("subject_entity") or default_subj,
                "human_action_query": clean_kw,
                "media_preference": m_pref_inh,
                "search_scope": scope,
                "overall_script_query": llm_portrait_ref.get("overall_script_query") or default_overall_q,
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": True,
            })
        elif sid in peak_indices and video_peak_count < max_video_peak_scenes:
            video_peak_count += 1
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "youtube_search_query": clean_kw,
                "overall_script_query": default_overall_q,
                "fallback_broad_query": default_overall_q,
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
            })
        else:
            final_plan.append({
                "tool_name": "search_google_cinematic_image",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "smart_image_query": clean_kw,
                "overall_script_query": default_overall_q,
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
            })

    try:
        from app.services.video_memory_rag import get_video_memory_rag
        final_plan = get_video_memory_rag().diversify_intra_video_scene_queries(
            job_id=0,
            scenes_strategy=final_plan,
            topic=topic,
            primary_entity=primary_entity or topic,
            scenes_raw=scenes,
        )
    except Exception:
        pass
    return final_plan


def audit_and_decide_search_scopes_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    proposed_decisions: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    LLM Node Đánh Giá & Phê Duyệt Quyết Định Tìm Kiếm (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    Đọc toàn bộ kịch bản gốc (`full_script_text`) đối chiếu với từng câu văn đã chia nhỏ (`scenes`)
    và bản kế hoạch `proposed_decisions`.
    """
    if not scenes or not proposed_decisions:
        return proposed_decisions

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    review_items = []
    dec_map = {int(d.get("scene_id") or 0): dict(d) for d in proposed_decisions if isinstance(d, dict)}

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        d = dec_map.get(sid, {})
        q_cur = (
            d.get("youtube_search_query")
            or d.get("smart_image_query")
            or d.get("action_or_concept_query")
            or d.get("human_action_query")
            or ""
        )
        review_items.append({
            "scene_id": sid,
            "split_sentence": s.get("narration", ""),
            "proposed_tool": d.get("tool_name", "search_google_cinematic_image"),
            "proposed_scope": d.get("search_scope", "SCENE_SPECIFIC"),
            "proposed_query": q_cur,
            "overall_script_query": d.get("overall_script_query", ""),
        })

    audit_prompt = f"""You are the Chief LLM Search Scope Judge & Visual Auditor in a LangGraph video pipeline.
Topic: "{topic}"
Primary Subject: "{primary_entity or topic}"

FULL SCRIPT STORY:
"{full_script_text}"

PROPOSED SCENE DECISIONS TO AUDIT:
{json.dumps(review_items, ensure_ascii=False, indent=2)}

YOUR TASK:
Evaluate each scene (from scene_id=1 to {len(scenes)}) and decide whether searching the split sentence on its own makes sense:
1. If `split_sentence` when isolated from the script still has a clear, concrete, self-contained visual action of the main subject:
   - Set `"approved_scope": "SCENE_SPECIFIC"`
   - Keep or refine `"approved_query"` so it searches directly for that sentence's visual meaning.
2. If `split_sentence` when isolated from the script becomes MEANINGLESS, VAGUE, TRANSITIONAL, SCIENTIFIC/ABSTRACT, or focuses on a minor inanimate prop that would hijack search results:
   - Set `"approved_scope": "SCRIPT_OVERALL"`
   - Rewrite `"approved_query"` so it searches for content REVOLVING AROUND THE OVERALL SCRIPT STORY.

Return ONLY a valid JSON object in this exact format:
{{
  "overall_script_query": "3-5 word core story search query",
  "subject_entity": "2-4 word main subject",
  "scenes_audit": [
    {{
      "scene_id": 1,
      "approved_scope": "SCENE_SPECIFIC",
      "approved_query": "stray golden retriever shivering in rain",
      "reason": "Sentence clearly describes the dog shivering in rain"
    }}
  ]
}}"""

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            payload = {
                "contents": [{"parts": [{"text": audit_prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, timeout=18)
                    if resp.status_code == 200:
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                parsed = json.loads(m.group(0))
                                top_overall_q = str(parsed.get("overall_script_query") or "").strip()
                                top_subj = str(parsed.get("subject_entity") or "").strip()
                                audits = parsed.get("scenes_audit") or []
                                audit_by_sid = {
                                    int(a.get("scene_id") or 0): a
                                    for a in audits
                                    if isinstance(a, dict) and a.get("scene_id")
                                }
                                if audit_by_sid:
                                    audited_list: List[Dict[str, Any]] = []
                                    for d in proposed_decisions:
                                        sid = int(d.get("scene_id") or 0)
                                        item = dict(d)
                                        if top_overall_q:
                                            item["overall_script_query"] = top_overall_q
                                        if top_subj and not item.get("creature_name"):
                                            item["subject_entity"] = top_subj
                                        if sid in audit_by_sid:
                                            au = audit_by_sid[sid]
                                            app_scope = str(au.get("approved_scope") or item.get("search_scope") or "SCENE_SPECIFIC").upper()
                                            app_query = str(au.get("approved_query") or "").strip()
                                            item["search_scope"] = app_scope if app_scope in ("SCENE_SPECIFIC", "SCRIPT_OVERALL") else "SCENE_SPECIFIC"
                                            item["scope_audit_reason"] = str(au.get("reason") or "")
                                            if app_query:
                                                t_n = item.get("tool_name")
                                                if t_n == "clone_youtube_short_peak_clip":
                                                    item["youtube_search_query"] = app_query
                                                elif t_n == "summon_epic_monster_cinematic_media":
                                                    item["action_or_concept_query"] = app_query
                                                elif t_n == "fetch_clean_cinematic_portrait_or_broll":
                                                    item["human_action_query"] = app_query
                                                else:
                                                    item["smart_image_query"] = app_query
                                        item["scope_audited"] = True
                                        audited_list.append(item)

                                    try:
                                        from app.services.video_memory_rag import get_video_memory_rag
                                        audited_list = get_video_memory_rag().diversify_intra_video_scene_queries(
                                            job_id=0,
                                            scenes_strategy=audited_list,
                                            topic=topic,
                                            primary_entity=primary_entity or topic,
                                            scenes_raw=scenes,
                                        )
                                    except Exception:
                                        pass

                                    verdict_log = ", ".join(
                                        f"S{x.get('scene_id')}:{x.get('tool_name')}[{x.get('search_scope')}]"
                                        for x in audited_list
                                    )
                                    logger.info(
                                        f"⚖️ [LLM Scope Judge Node + MiniRAG] Đã thẩm định & đa dạng hóa xong {len(audited_list)} cảnh "
                                        f"qua `{model_name}` -> {verdict_log}"
                                    )
                                    return audited_list
                    elif resp.status_code in (429, 403, 404, 503):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as e_req:
                    logger.debug(f"[LLM Scope Judge] {model_name} error: {e_req}")
                    continue
    except Exception as ex:
        logger.debug(f"[LLM Scope Judge] Fallback pass: {ex}")

    return proposed_decisions


