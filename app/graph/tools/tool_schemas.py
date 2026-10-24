"""app.graph.tools.tool_schemas — Định nghĩa 5 @tool chuẩn LangGraph & hàm chuẩn hóa truy vấn."""
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
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)

_yt_url_lock = threading.Lock()
_yt_search_sem = threading.Semaphore(2)

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
    "naturefootage", "nature footage", "endangered siberian tiger",
    "pond5", "shutterstock", "getty", "gettyimages", "istock", "storyblocks",
    "dissolve", "criticalpast", "framepool", "envato", "videvo", "adobe stock",
    "wildfilmhistory", "british pathe", "ap archive", "stock footage", "stock video",
    "tiktok", "douyin", "kwai", "capcut", "inshot", "kinemaster",
    "filmora", "alighty", "credit:", "credits:",
    "🎥", "📸", "top 10", "top 5", "tier list", "explained", "whatsapp status",
)

_YT_QUERY_FILLER_WORDS = {
    "cinematic", "4k", "8k", "hd", "1080p", "edit", "edits", "visuals", "footage", "scene", "scenes",
    "vertical", "shot", "shots", "video", "clip", "clips", "short", "shorts",
    "movie", "still", "photo", "photograph", "picture", "image",
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "from", "and", "or",
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
    """Trích xuất tập token ngữ cảnh từ `subject_entity` / `topic` (không hardcode danh sách loài hay từ khóa cục bộ)."""
    raw = f"{subject_entity or ''} {topic or ''}".strip()
    if not raw:
        raw = search_query or ""
    tokens = re.findall(r"[a-zA-Z]{3,}", raw.lower())
    return {t for t in tokens if t not in _YT_QUERY_FILLER_WORDS}


def _clean_yt_search_phrase(
    q: str,
    subject_entity: str = "",
    max_words: int = 5,
    strip_distractor_props: bool = False,
) -> str:
    """
    Chuẩn hóa nhẹ câu truy vấn do LLM sinh ra:
    - Chỉ loại bỏ các tag kỹ thuật thừa (`4k`, `8k`, `hd`, `short`, `shorts`) và giới hạn độ dài `max_words`.
    - TÔN TRỌNG 100% câu truy vấn của LLM: Không dùng danh sách từ khóa cứng để ép ghép `subject_entity` đè lên truy vấn của LLM
      (chỉ dùng `subject_entity` làm dự phòng khi câu truy vấn `q` bị rỗng).
    """
    _ = strip_distractor_props
    words = re.findall(r"[a-zA-Z0-9]+", q or "")
    core = [w for w in words if w.lower() not in _YT_QUERY_FILLER_WORDS and len(w) >= 2]
    if not core and subject_entity:
        core = [
            w for w in re.findall(r"[a-zA-Z0-9]+", subject_entity)
            if w.lower() not in _YT_QUERY_FILLER_WORDS and len(w) >= 2
        ]
    return " ".join(core[:max_words]).strip()


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
    Trả về `(effective_scope, ordered_queries)` dựa 100% trên quyết định ngữ nghĩa của LLM Director & LLM Scope Judge:
    - `SCENE_SPECIFIC`: Ưu tiên số 1 là `scene_query` do LLM viết cho cảnh đó, dự phòng bằng `overall_script_query`.
    - `SCRIPT_OVERALL`: Ưu tiên số 1 là `overall_script_query` (xoay quanh tổng thể kịch bản), dự phòng bằng `scene_query`.
    Tuyệt đối không dùng từ khóa cứng trong code để ghi đè `search_scope` của LLM.
    """
    _ = narration
    scope_norm = (search_scope or "SCENE_SPECIFIC").strip().upper()
    if scope_norm not in ("SCENE_SPECIFIC", "SCRIPT_OVERALL"):
        scope_norm = "SCENE_SPECIFIC"

    overall_base = overall_script_query or topic or subject_entity
    q_overall = _clean_yt_search_phrase(overall_base, subject_entity=subject_entity, max_words=max_words)
    q_scene = _clean_yt_search_phrase(scene_query, subject_entity=subject_entity, max_words=max_words)
    q_fallback = _clean_yt_search_phrase(
        fallback_query or f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        max_words=max_words,
    )

    if scope_norm == "SCRIPT_OVERALL":
        ordered = [q for q in dict.fromkeys([q_overall, q_scene, q_fallback]) if q]
    else:
        ordered = [q for q in dict.fromkeys([q_scene, q_overall, q_fallback]) if q]

    return scope_norm, ordered


def _sanitize_tool_arg(val: Any, fallback: str = "") -> str:
    """Loại bỏ các token lỗi từ parser functionCall (VD: ',creature_name:', '<|...|>', v.v.)."""
    s = str(val or "").strip()
    if not s:
        return fallback
    s_clean = re.sub(r"^[,:;\"'<>\s]+|[,:;\"'<>\s]+$", "", s).strip()
    bad_markers = (
        "creature_name",
        "scale_anchor_object",
        "subject_entity",
        "action_or_concept_query",
        "overall_script_query",
        "tool_call",
        "function",
    )
    if not s_clean or len(s_clean) < 2 or any(m in s_clean.lower() for m in bad_markers):
        return fallback
    return s_clean
