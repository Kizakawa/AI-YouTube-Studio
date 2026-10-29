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
    "filmora", "alighty", "alight motion", "credit:", "credits:",
    "🎥", "📸", "top 10", "top 5", "tier list", "explained", "whatsapp status",
)

# Bộ lọc 0ms (biên từ \b an toàn, không bắt nhầm chuỗi con như 'caiman' hay '#airforce') chặn ổ video #edit so sánh, quiz & AI Veo/Sora/Kling
_EDIT_COMPARISON_SPAM_RE = re.compile(
    r"(?:^|[\s|/])#(?:edit|edits|vsedit|vsedits|editz|capcut|alightmotion|aftereffects|phonk|veo|veo2|veo3|sora|kling|klingai|hailuo|pika|runwayml)(?:\b|$)"
    r"|\b(?:who\s+would\s+win|who\s+wins|who\s+won|who\s+is\s+(?:the\s+)?(?:real\s+)?king|vs\s+edit|edit\s+audio|animal\s+revolt|battle\s+simulator|power\s+scaling|power\s+level|debunked)\b",
    re.IGNORECASE,
)

_AI_SLOP_REAL_FOOTAGE_RE = re.compile(
    r"(?:^|[\s|/])#(?:ai|aivideo|aishorts|aiart|aianimation|aigenerated|midjourney)(?:\b|$)"
    r"|\b(?:ai[\s\-]+generated|ai[\s\-]+video|made[\s\-]+with[\s\-]+ai|google[\s\-]+veo|veo[\s\-]+[23]\b|kling[\s\-]+ai|hailuo[\s\-]+ai|sora[\s\-]+ai)\b",
    re.IGNORECASE,
)

_VERSUS_TOKEN_RE = re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE)


def has_versus_pattern(text: str) -> bool:
    """Kiểm tra nhanh câu truy vấn hoặc tiêu đề có chứa mẫu đối đầu 'A vs B' / 'versus' hay không."""
    return bool(_VERSUS_TOKEN_RE.search(str(text or "")))


def is_edit_or_ai_slop_video_metadata(
    title: str,
    uploader: str = "",
    allow_3d_cgi_monster: bool = False,
    channel_niche: str = "",
) -> bool:
    """
    Chặn ngay ở tầng Metadata (0ms, trước khi gọi yt-dlp tải file), đồng bộ trực tiếp với
    `llm_production_blueprint.video_search_strategy` trong `config/niches/*.yaml`:
    - Ổ video fan-edit so sánh (`forbidden_edit_markers` trong YAML: `#edit`, `#vsedit`, `Who would win`, `Who won`, `#veo`...)
    - Ổ video AI tạo sinh (`forbidden_ai_slop_markers` trong YAML: `#sora`, `#kling`, `#ai`, `AI generated`...) khi cảnh yêu cầu quay thật.
    """
    meta = f"{title or ''} {uploader or ''}".strip()
    if not meta:
        return False
    if _EDIT_COMPARISON_SPAM_RE.search(meta):
        return True

    yaml_edit_re = None
    yaml_ai_re = None
    yaml_allow_3d = False
    try:
        from app.services.niche_manager import get_niche_forbidden_video_metadata_regexes
        yaml_edit_re, yaml_ai_re, yaml_allow_3d = get_niche_forbidden_video_metadata_regexes(channel_niche or "general")
    except Exception:
        pass

    if yaml_edit_re is not None and yaml_edit_re.search(meta):
        return True

    effective_allow_3d = bool(allow_3d_cgi_monster or (yaml_allow_3d and channel_niche))
    if not effective_allow_3d:
        if _AI_SLOP_REAL_FOOTAGE_RE.search(meta):
            return True
        if yaml_ai_re is not None and yaml_ai_re.search(meta):
            return True
    return False

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

    QUY TẮC BẮT BUỘC CHO 4 THAM SỐ TRUY VẤN (BÁM SÁT `video_search_strategy` TRONG `config/niches/*.yaml`):
    1. `subject_entity`: Tên chủ thể thực tế xuất hiện trong khung hình của phân cảnh này.
       - Nếu kịch bản đối đầu 2 thực thể (`Entity A vs Entity B`), ghi đúng thực thể đang hành động ở câu đó hoặc ghi `"Entity A or Entity B"` để giám khảo Vision chấp nhận cảnh quay hợp lệ của một trong hai chủ thể.
    2. `youtube_search_query` (3–5 từ tiếng Anh — CHIẾN LƯỢC SONG SONG "DUAL-TRACK" TỪ YAML):
       - Với các cảnh thân bài mô tả riêng từng chủ thể/vũ khí/cơ chế/môi trường: CHỈ search đúng **`[Single Canonical Entity] + [Real Physical Action / Environment]`** theo mẫu `single_entity_query_examples` của YAML (TUYỆT ĐỐI KHÔNG nhét `"A vs B"` vào cảnh đơn chủ thể để né sạch ổ `#edit` và AI Veo!).
       - Với cảnh giao chiến/chạm trán trực tiếp (Hook / Climax): có thể đặt truy vấn chạm trán thật theo `clash_query_examples` của YAML; hệ thống sẽ search song song cả nhánh đối đầu lẫn `fallback_broad_query` đơn chủ thể và tự động Fast-Drop nhánh `"A vs B"` ngay nếu không có cảnh thật sạch chữ!
       - CẤM TUYỆT ĐỐI viết cụm kể chuyện văn học dài dòng không ai dùng đặt tiêu đề video.
    3. `fallback_broad_query` (2–4 từ tiếng Anh, BẮT BUỘC ĐƠN CHỦ THỂ — CẤM `"vs"`): Truy vấn dự phòng chuẩn chỉ mục Stock/Documentary theo `stock_fallback_examples` của YAML (`[Single Canonical Subject Noun] + [Habitat/Action]`).
       - TUYỆT ĐỐI KHÔNG nhét `"A vs B"` hoặc `"vs"` vào `fallback_broad_query`, CẤM cắt mất danh từ gốc của chủ thể và CẤM dùng từ vật liệu vô hồn.
    4. `search_scope` (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`) & `overall_script_query`:
       - Chọn `"SCENE_SPECIFIC"` khi câu thoại (sau khi đã khôi phục đại từ về tên chủ thể thật) mô tả hành động/chi tiết thị giác rõ ràng.
       - Chọn `"SCRIPT_OVERALL"` khi câu thoại là câu chuyển ý, ẩn dụ hoặc số liệu trừu tượng -> tìm kiếm song song `overall_script_query` (3–5 từ cốt lõi) và `fallback_broad_query` đơn chủ thể.
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
    media_preference: str = "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ ĐẶC NHIỆM CHUYÊN BIỆT CHO TÂM LÝ HỌC PHÁP Y (FORENSIC PSYCHOLOGY), THẨM VẤN FBI/TÒA ÁN, THÍ NGHIỆM NÃO BỘ 3D, NHÂN VẬT ĐIỆN ẢNH BIỂU TƯỢNG & B-ROLL DARK NOIR SẠCH CHỮ.
    GỌI NGAY CÔNG CỤ NÀY KHI:
    - Kịch bản hoặc phân cảnh nói về thẩm vấn FBI/CIA, biểu cảm nghi phạm, tòa án, mô phỏng não bộ 3D, nhân vật ngôn ngữ cơ thể (Thomas Shelby, Patrick Jane, Homelander, Joker) hoặc tâm lý con người.
    - Tham số quan trọng:
      * `human_action_query`: Cụm 3-5 từ tiếng Anh mô tả HÀNH ĐỘNG VẬT LÝ, HIỆN TRƯỜNG PHÁP Y, MÔ PHỎNG 3D NÃO BỘ hoặc NHÂN VẬT BIỂU TƯỢNG cụ thể (TUYỆT ĐỐI KHÔNG dùng từ trừu tượng như "narcissist", "cognitive dissonance", "manipulation").
        Ví dụ chuẩn: "fbi interrogation suspect sweating", "thomas shelby cold stare eye contact", "3d glowing brain neurons firing", "polygraph needle trembling paper", "detective dark interrogation shadow".
      * `media_preference`:
        - `"YOUTUBE_FORENSIC_OR_CHARACTER_SHORT"`: Ưu tiên số 1 săn đoạn Peak từ YouTube Shorts (băng thẩm vấn thật, 3D não bộ, hoặc cảnh nhân vật điện ảnh sạch chữ), nếu không có mới chuyển sang Dark Noir B-Roll.
        - `"VIDEO_BROLL"`: Tải đoạn video Dark Noir B-Roll diễn viên thật ánh sáng tối kịch tính sạch chữ 100%.
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
def render_doodle_2d_explainer_scene(
    scene_id: int,
    character_tag: str = "@MANIPULATOR",
    prop_type: str = "puppet_strings",
    scene_concept_summary: str = "",
    camera_motion: str = "dolly_in",
    target_duration_sec: float = 4.5,
) -> Dict[str, Any]:
    """
    CÔNG CỤ DỰNG VIDEO HOẠT HỌA 2D DOODLE / STICKMAN EXPLAINER (1080x1920 @ 24 FPS) CHO KHÁI NIỆM TÂM LÝ HỌC ĐEN TRỪU TƯỢNG, BẪY THAO TÚNG VÔ HÌNH, DOPAMINE & BẪY TIỀN BẠC.
    GỌI NGAY CÔNG CỤ NÀY KHI:
    - Phân cảnh giải thích một cơ chế tâm lý vô hình, bẫy thao túng tinh thần (Gaslighting, Guilt-Tripping, Mirror Trap, Silent Treatment), tụt pin Dopamine não bộ, bẫy nợ tài chính, hoặc nghịch lý cuộc sống hiện đại mà máy quay thật không thể ghi hình trực tiếp (nếu tìm stock thường sẽ bị ra cảnh nhân viên văn phòng/áo thun giả tạo).
    - Tham số bắt buộc:
      * `character_tag`: Chọn đúng 1 trong 5 thẻ nhân vật:
        - `"@MANIPULATOR"` (Áo đỏ thẫm — Kẻ thao túng / Narcissist / Mastermind)
        - `"@PLEASER"` (Áo xanh lục — Nạn nhân bị thao túng / People-Pleaser)
        - `"@MODERNYOU"` (Áo xanh xám — Con người hiện đại / Overthinker / Nghi phạm đang căng thẳng não bộ)
        - `"@FARMER"` (Áo nâu — Con nợ / Người chịu áp lực nặng nề)
        - `"@ANCESTOR"` (Áo vàng đất — Nhà hiền triết Khắc kỷ / Stoic)
      * `prop_type`: Chọn đúng 1 trong 8 đạo cụ ẩn dụ trực quan:
        - `"puppet_strings"` (Giật dây con rối thao túng tâm lý)
        - `"brain_battery"` (Pin năng lượng não bộ / Dopamine / Cognitive Load tụt đỏ)
        - `"split"` (Chia đôi màn hình đối chiếu 2 trạng thái tâm lý / thật vs giả)
        - `"money_scale"` (Cán cân tiền bạc / bẫy sòng bạc / sunk cost)
        - `"alarm"` (Đồng hồ đếm ngược / áp lực thời gian 3 giây)
        - `"block"` (Khối tạ áp lực tâm lý đè nặng)
        - `"climax"` (Bừng sáng nhận ra sự thật / đòn hóa giải)
        - `"spear_nature"` (Tự do / thoát khỏi bẫy)
    """
    return {
        "tool_name": "render_doodle_2d_explainer_scene",
        "scene_id": scene_id,
        "character_tag": character_tag,
        "prop_type": prop_type,
        "scene_concept_summary": scene_concept_summary,
        "camera_motion": camera_motion,
        "target_duration_sec": target_duration_sec,
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
    - Bước 1 (LLM Gọi API): Gửi `primary_stock_query` và `secondary_stock_query` (cụm 2–3 từ tiếng Anh gồm **Danh từ loài/chủ thể gốc + hành động/môi trường**, VD: `"porcupine quills closeup"`, `"leopard hunting savanna"`, `"hippopotamus river wildlife"`) tới API Pexels & Pixabay.
      * CẤM TUYỆT ĐỐI: Khi câu thoại mô tả bộ phận cơ thể ở góc cận/vi mô (`"these needles"`, `"microscopic barbs"`, `"skull-crushing bite"`, `"venom"`), TUYỆT ĐỐI KHÔNG viết truy vấn bề mặt/đồ vật vô hồn như `"microscopic texture nature"` hay `"sharp needle texture macro"` (sẽ làm Pexels trả về kim khâu, que diêm, lá cây!). Bắt buộc phải gắn tên con vật/chủ thể thật (`"porcupine quills closeup"`, `"porcupine wildlife"`).
    - Bước 2 (LLM Kiểm Tra Lại Link & Ảnh Preview Trả Về):
      * Ngay khi nhận danh sách link từ API, LLM Auditor (`curate_candidate_videos`) sẽ đọc từng đường link `returned_url` kết hợp NHÌN TRỰC TIẾP ảnh `thumbnail` của từng video.
      * Đối chiếu với `expected_visual_content` và `forbidden_false_positives` trước khi tải file MP4!
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
    render_doodle_2d_explainer_scene,
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
    - Khi `strip_distractor_props=True` và rút gọn thực thể (`max_words <= 3`), tự động tách phần tiêu đề trước dấu `:` / `vs`
      và giữ danh từ chính ở cuối cụm danh từ tiếng Anh (`core[-max_words:]`, VD: `'Crested Porcupine'` / `'Porcupine'` thay vì `'African Crested'`).
    """
    raw_q = str(q or "").strip()
    if strip_distractor_props and max_words <= 3 and raw_q:
        raw_q = re.split(r"[:—–\-]\s+", raw_q, maxsplit=1)[0].strip()
        if max_words <= 3:
            raw_q = re.split(r"\b(?:vs\.?|versus)\b", raw_q, maxsplit=1, flags=re.IGNORECASE)[0].strip() or raw_q

    words = re.findall(r"[a-zA-Z0-9]+", raw_q)
    extra_skip = {"vs", "versus"} if strip_distractor_props else set()
    core = [
        w for w in words
        if w.lower() not in _YT_QUERY_FILLER_WORDS
        and w.lower() not in extra_skip
        and len(w) >= 2
    ]
    if not core and subject_entity:
        core = [
            w for w in re.findall(r"[a-zA-Z0-9]+", subject_entity)
            if w.lower() not in _YT_QUERY_FILLER_WORDS
            and w.lower() not in extra_skip
            and len(w) >= 2
        ]
    if strip_distractor_props and max_words in (1, 2) and len(core) > max_words:
        return " ".join(core[-max_words:]).strip()
    return " ".join(core[:max_words]).strip()


def build_single_entity_stock_queries(
    raw_queries: List[str],
    subject_entity: str = "",
    topic: str = "",
    max_words: int = 4,
) -> List[str]:
    """
    Chuyển hóa danh sách truy vấn (có thể chứa 'Entity A vs Entity B' hoặc 'Entity A or Entity B')
    thành các câu truy vấn ĐƠN CHỦ THỂ sạch ('Entity A + hành động/môi trường', 'Entity B + ...')
    để:
      1. Chạy nhánh song song (Dual-Track) cùng với 'A vs B' trên YouTube.
      2. Tầng dự phòng Pexels / Pixabay / Wikimedia Stock tuyệt đối KHÔNG gửi cụm 'A vs B' gây lạc chủ đề.
    Không hardcode bất kỳ từ khóa chủ đề/loài vật nào.
    """
    out: List[str] = []
    seen_low: Set[str] = set()

    def _add_clean(phrase: str) -> None:
        cleaned = _clean_yt_search_phrase(
            phrase,
            subject_entity="",
            max_words=max_words,
            strip_distractor_props=False,
        )
        # Loại bỏ token 'vs' / 'versus' nếu còn sót ở rìa
        toks = [t for t in cleaned.split() if t.lower() not in ("vs", "versus")]
        final_p = " ".join(toks[:max_words]).strip()
        if final_p and final_p.lower() not in seen_low:
            seen_low.add(final_p.lower())
            out.append(final_p)

    # 1. Giữ nguyên trước tất cả các câu truy vấn vốn đã ĐƠN CHỦ THỂ (không chứa 'vs'/'versus' hoặc 'A or B')
    for rq in raw_queries:
        s = str(rq or "").strip()
        if not s:
            continue
        head = re.split(r"[:—–]\s*", s, maxsplit=1)[0].strip()
        if not has_versus_pattern(head) and not re.search(r"\bor\b", head, re.IGNORECASE):
            _add_clean(head)

    # 2. Tách các thực thể độc lập từ subject_entity ('A or B'), các câu 'A vs B' và topic
    subj_low = str(subject_entity or "").lower()
    split_entities: List[str] = []
    for src in (subject_entity, *raw_queries, topic):
        s = str(src or "").strip()
        if not s:
            continue
        head = re.split(r"[:—–]\s*", s, maxsplit=1)[0].strip()
        if has_versus_pattern(head):
            parts = _VERSUS_TOKEN_RE.split(head)
        elif re.search(r"\bor\b", head, re.IGNORECASE):
            parts = re.split(r"\bor\b", head, flags=re.IGNORECASE)
        else:
            continue
        for pt in parts:
            # Bỏ phần hashtag hoặc hậu tố số liệu đo lường (VD: '3700 PSI', '#Shorts')
            pt_clean = re.sub(r"#\S+", " ", pt)
            pt_clean = re.sub(r"\b\d[\d,.]*\s*(?:psi|lbs?|kg|km|mph|ft|meters?|m)\b", " ", pt_clean, flags=re.IGNORECASE)
            ent = _clean_yt_search_phrase(pt_clean, max_words=min(3, max_words), strip_distractor_props=True)
            if ent and ent.lower() not in {e.lower() for e in split_entities}:
                split_entities.append(ent)

    # Ưu tiên thực thể khớp với subject_entity của cảnh hiện tại lên trước (nếu subject_entity chỉ nêu 1 bên)
    if split_entities and subj_low and not re.search(r"\b(?:vs\.?|versus|or)\b", subj_low):
        split_entities.sort(
            key=lambda e: 0 if any(tok in subj_low for tok in e.lower().split() if len(tok) >= 3) else 1
        )

    for ent in split_entities:
        _add_clean(ent)

    if not out and (subject_entity or topic):
        fallback_one = _clean_yt_search_phrase(
            subject_entity or topic,
            max_words=min(3, max_words),
            strip_distractor_props=True,
        )
        if fallback_one:
            _add_clean(fallback_one)

    # Nếu phân cảnh này chỉ nói về 1 thực thể cụ thể (subject_entity không chứa 'vs'/'or'), ổn định thứ tự ưu tiên thực thể đó lên đầu!
    if len(out) > 1 and subj_low and not re.search(r"\b(?:vs\.?|versus|or)\b", subj_low):
        subj_toks = [t for t in re.findall(r"[a-zA-Z0-9]{3,}", subj_low) if t not in _YT_QUERY_FILLER_WORDS]
        if subj_toks:
            out.sort(key=lambda q: 0 if any(st in q.lower() for st in subj_toks) else 1)

    return out


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
    Chiến lược Tìm Kiếm Song Song 2 Nhánh (Dual-Track: 'A vs B' + 'Đơn Chủ Thể Quay Thật'):
    - Giữ nhánh `A vs B` (từ `overall_script_query` / `scene_query`) để YouTube vẫn săn được video chạm trán thật nếu có.
    - Đồng thời LUÔN kèm nhánh truy vấn ĐƠN CHỦ THỂ sạch (không chứa `vs`) ngay trong cùng lượt quét song song 4 nhánh,
      để nếu `A vs B` chỉ toàn video `#edit` / AI hoặc không tìm được clip sạch thì có sẵn ngay clip quay thật của từng thực thể!
    """
    _ = narration
    scope_norm = (search_scope or "SCENE_SPECIFIC").strip().upper()
    if scope_norm not in ("SCENE_SPECIFIC", "SCRIPT_OVERALL"):
        scope_norm = "SCENE_SPECIFIC"

    overall_base = re.split(r"[:—–]\s*", str(overall_script_query or topic or subject_entity or ""), maxsplit=1)[0].strip()
    q_overall = _clean_yt_search_phrase(overall_base, subject_entity=subject_entity, max_words=max_words)
    q_scene = _clean_yt_search_phrase(scene_query, subject_entity=subject_entity, max_words=max_words)

    single_entity_fallbacks = build_single_entity_stock_queries(
        raw_queries=[fallback_query, scene_query, overall_base],
        subject_entity=subject_entity,
        topic=topic,
        max_words=min(4, max_words),
    )
    q_fallback = single_entity_fallbacks[0] if single_entity_fallbacks else _clean_yt_search_phrase(
        fallback_query or subject_entity or topic,
        subject_entity=subject_entity,
        max_words=min(3, max_words),
        strip_distractor_props=True,
    )

    if scope_norm == "SCRIPT_OVERALL":
        base_list = [q_overall, q_scene, q_fallback, *single_entity_fallbacks[:2]]
    else:
        base_list = [q_scene, q_fallback, q_overall, *single_entity_fallbacks[:2]]

    ordered: List[str] = []
    seen_q: Set[str] = set()
    for q in base_list:
        qn = (q or "").strip()
        if qn and qn.lower() not in seen_q:
            seen_q.add(qn.lower())
            ordered.append(qn)

    return scope_norm, ordered[:4]


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
