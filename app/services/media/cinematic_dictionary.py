"""
app.services.media.cinematic_dictionary — Từ Điển Ngôn Ngữ Điện Ảnh, Công Thức Prompt 7 Tầng & Smart Reference Binding.

Tích hợp từ chuẩn điện ảnh EyeCandy (136 kỹ thuật góc quay & Top 20 kỹ thuật AI Video hiểu chuẩn nhất),
Giáo trình Từ Điển Ngôn Ngữ Điện Ảnh (Frame, Shot, Lens, Light, Color, Movement) và cơ chế Smart Ref Binding (@TAG):
1. Phân biệt chính xác chuyển động vật lý không gian vs quang học (Dolly vs Zoom, Trucking vs Pan, Pedestal vs Tilt, Arc vs Roll).
2. Cấu trúc Prompt AI Video/Image 7 tầng:
   [Shot Size] + [Camera Angle / POV] + [Lens & Optics] + [Physical Camera Movement] + [Composition] + [Lighting] + [Optical/Film Effect]
3. Cơ chế Smart Ref Binding (@CHARACTER_TAG) & 6-Panel Character Reference Sheet giúp khóa chặt 100% nhân vật xuyên suốt hàng trăm phân cảnh.
"""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── DANH SÁCH MODEL VIDEO TRÊN GOOGLE FLOW / VEO 3.1 (HỖ TRỢ CHẾ ĐỘ TIẾT KIỆM CREDIT) ──
VEO_FLOW_MODEL_OPTIONS: List[str] = [
    "Omni Flash",
    "Veo 3.1 - Fast",
    "Veo 3.1 - Lite",
    "Veo 3.1 - Quality",
    "Veo 3.1 - Lite [Lower Priority]",
]


# ── TOP 20 KỸ THUẬT GÓC MÁY & CHUYỂN ĐỘNG ĐIỆN ẢNH TỐI ƯU NHẤT CHO AI VIDEO (EYECANDY STANDARD) ──
TOP_20_EYECANDY_CAMERA_DIRECTIVES: Dict[str, str] = {
    # Nhóm 1: Chuyển động không gian vật lý (Dolly / Trucking / Pedestal / Arc)
    "dolly_in": "slow motorized physical dolly-in moving through 3D space toward the primary subject with natural parallax",
    "dolly_out": "smooth physical dolly pull-back revealing the surrounding spatial environment and scale",
    "double_dolly": "double-dolly tracking shot where subject and camera move together at identical speed while background flows past",
    "dolly_zoom": "dramatic Hitchcock vertigo dolly-zoom where camera dollies forward while lens focal length zooms out, stretching background depth",
    "arc_orbit": "smooth 180-degree circular arc camera movement orbiting around the central subject at peak tension",
    "trucking_left": "fluid lateral trucking shot moving the entire camera body horizontally left with foreground parallax separation",
    "trucking_right": "fluid lateral trucking shot moving the entire camera body horizontally right with foreground parallax separation",
    "pedestal_up": "vertical pedestal crane lift raising the entire camera body straight upward to reveal vertical scale",
    "pedestal_down": "vertical pedestal descent lowering the entire camera body smoothly toward ground level",
    "tracking_follow": "dynamic tracking shot following directly behind or alongside the moving subject at constant distance",
    # Nhóm 2: Chuyển động trục máy & Quang học (Pan / Tilt / Whip Pan / Rack Focus)
    "pan_left": "steady tripod horizontal pan left rotating the lens axis smoothly across the scene",
    "pan_right": "steady tripod horizontal pan right rotating the lens axis smoothly across the scene",
    "whip_pan": "high-speed kinetic whip-pan with directional motion blur snapping focus between two points",
    "tilt_up": "smooth vertical camera tilt upward from base to apex emphasizing towering height",
    "tilt_down": "smooth vertical camera tilt downward from sky to subject establishing vulnerability",
    "rack_focus": "cinematic shallow depth-of-field rack focus shifting sharpness smoothly from foreground clue to background subject",
    # Nhóm 3: Điểm nhìn & Góc đặt máy đặc biệt (POV / Worm's-Eye / Overhead / Voyeur / FPV)
    "first_person_pov": "immersive first-person POV shot from the subject's exact eye line looking down at immediate action",
    "over_the_shoulder": "over-the-shoulder framing looking past the foreground shoulder toward the opposing subject in confrontation",
    "worms_eye": "extreme worm's-eye view placed flat on the ground looking straight upward to magnify colossal scale and dominance",
    "ground_level": "ground-level camera positioned inches above the surface capturing tactile impact and movement",
    "overhead_90": "perfect 90-degree overhead bird's-eye God's-eye shot looking straight down for geometric spatial symmetry",
    "high_angle": "dramatic high-angle shot looking down obliquely at the subject to emphasize isolation or vulnerability",
    "low_angle": "commanding low-angle hero shot looking upward at the subject towering against the background",
    "dutch_angle": "tilted Dutch-angle framing canted 15 degrees off-axis to evoke psychological unease and disorientation",
    "voyeur_shot": "voyeuristic framing peering through a partially open doorway or foreground obstruction for suspenseful tension",
    "fpv_drone": "kinetic FPV drone shot gliding rapidly through narrow environmental corridors with banking momentum",
    "locked_on": "locked-on stabilization keeping the central subject pinned at exact frame center while the environment shifts",
    "handheld_doc": "organic handheld documentary camera with subtle realistic breathing micro-motion and immediacy",
}


# ── BẢNG PHÂN BIỆT CÁC THUẬT NGỮ DỄ NHẦM LẪN (CHỐNG AI HIỂU SAI QUANG HỌC VS VẬT LÝ) ──
CAMERA_DISAMBIGUATION_RULES: Dict[str, Tuple[str, str]] = {
    "zoom_vs_dolly": (
        "Zoom only changes focal length and flattens space",
        "Dolly physically moves the camera through 3D space creating true foreground-background parallax",
    ),
    "pan_vs_trucking": (
        "Pan rotates the camera head left/right from a fixed tripod position",
        "Trucking moves the entire camera body laterally alongside the action",
    ),
    "tilt_vs_pedestal": (
        "Tilt angles the camera lens up/down from a fixed height",
        "Pedestal raises or lowers the entire camera body vertically",
    ),
    "arc_vs_roll": (
        "Arc orbits the camera around the subject in a curved path",
        "Camera Roll rotates the horizon around the lens axis",
    ),
    "overhead_vs_high_angle": (
        "Overhead looks straight down at a perpendicular 90-degree angle",
        "High Angle looks downward at an oblique diagonal angle",
    ),
}


# ── CẤU HÌNH 7 TẦNG NGÔN NGỮ ĐIỆN ẢNH THEO NGÁCH & VAI TRÒ CẢNH (7-LAYER FORMULA) ──
_NICHE_7LAYER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "wildlife_predators": {
        "lens": "85mm f/1.8 telephoto wildlife cinema lens",
        "composition": "rule-of-thirds dynamic confrontation framing with foreground habitat depth",
        "lighting": "natural golden-hour directional rim lighting cutting through jungle mist",
        "effect": "crisp 60fps shutter clarity, tactile water droplets and fur micro-detail",
    },
    "deep_sea_earth_mysteries": {
        "lens": "24mm f/1.4 wide-angle anamorphic submersible viewport lens",
        "composition": "scale-contrast framing juxtaposing tiny submersible light against colossal abyssal shadow",
        "lighting": "cold abyssal bioluminescent cyan glow paired with high-contrast tungsten spotlight beam",
        "effect": "volumetric underwater caustic light rays and drifting marine snow particles",
    },
    "satirical_psychology_animation": {
        "lens": "flat 2D orthographic animation plane, zero perspective distortion",
        "composition": "centered minimalist 2D doodle storyboard framing with clean negative space",
        "lighting": "flat solid-color cel fill with zero gradients and zero 3D shadows",
        "effect": "hand-drawn frame-by-frame sketchy black marker outlines on muted cobalt/slate background",
    },
    "psychology": {
        "lens": "50mm anamorphic prime lens with shallow depth of field",
        "composition": "tight psychological portrait framing locking on micro-expressions and eye line",
        "lighting": "high-contrast chiaroscuro key light with Moody cold slate fill",
        "effect": "subtle 35mm film halation and crisp ocular catchlights",
    },
    "alternate_history": {
        "lens": "35mm Panavision anamorphic cinema lens",
        "composition": "epic monumental symmetry with layered foreground-to-background depth",
        "lighting": "dramatic volumetric shafts of light piercing atmospheric smoke",
        "effect": "rich archival IMAX color grading with subtle optical flares",
    },
    "general": {
        "lens": "50mm prime cinema lens, shallow depth of field",
        "composition": "balanced vertical 9:16 central subject framing with clean foreground separation",
        "lighting": "dramatic three-point cinematic lighting with crisp rim light",
        "effect": "subtle film halation, razor-sharp focal plane, zero motion artifacts",
    },
}


def resolve_seven_layer_components(
    raw_prompt: str,
    niche_id: str = "general",
    scene_id: int = 1,
    total_scenes: int = 8,
    camera_motion: str = "dolly_in",
) -> Dict[str, str]:
    """
    Phân tách và điền đầy đủ 7 tầng ngôn ngữ điện ảnh cho một phân cảnh:
    1. shot_size (Cỡ cảnh)
    2. camera_angle (Góc máy / Điểm nhìn)
    3. lens (Ống kính)
    4. camera_movement (Chuyển động camera vật lý)
    5. composition (Bố cục)
    6. lighting (Ánh sáng)
    7. optical_effect (Hiệu ứng quang học / chất liệu phim)
    """
    nid = str(niche_id or "general").strip().lower()
    preset = _NICHE_7LAYER_DEFAULTS.get(nid)
    if not preset:
        for key, val in _NICHE_7LAYER_DEFAULTS.items():
            if key in nid or nid in key:
                preset = val
                break
    if not preset:
        preset = _NICHE_7LAYER_DEFAULTS["general"]

    p_low = str(raw_prompt or "").lower()

    # 1. Cỡ cảnh (Shot Size) & 2. Góc máy (Camera Angle) theo nhịp 5-Act
    if scene_id == 1:
        shot_size = "Medium close-up thumb-stopping hook shot"
        camera_angle = "commanding low-angle perspective" if "vs" in p_low or "predator" in nid else "eye-level direct confrontation angle"
    elif scene_id in (4, 5):
        # Mid-video Dual-Peak Climax Twist
        shot_size = "Extreme close-up macro detail shot"
        camera_angle = "dramatic Dutch-angle tension tilt" if "psychology" in nid else "worm's-eye upward power angle"
    elif scene_id >= total_scenes:
        shot_size = "Wide environmental reveal shot"
        camera_angle = "high-angle spatial overview"
    elif scene_id % 2 == 0:
        shot_size = "Medium full-action shot"
        camera_angle = "over-the-shoulder tactical perspective"
    else:
        shot_size = "Close-up expressive reaction shot"
        camera_angle = "intimate eye-level profile angle"

    # Ánh xạ chuyển động camera sang từ vựng vật lý chuẩn EyeCandy
    cam_key = str(camera_motion or "dolly_in").strip().lower()
    legacy_to_eyecandy = {
        "zoom_in": "dolly_in",
        "zoom_in_fast": "whip_pan",
        "hyper_zoom": "fpv_drone",
        "ken_burns_zoom_in": "dolly_in",
        "zoom_out": "dolly_out",
        "ken_burns_zoom_out": "dolly_out",
    }
    resolved_cam_key = legacy_to_eyecandy.get(cam_key, cam_key)
    movement_desc = TOP_20_EYECANDY_CAMERA_DIRECTIVES.get(
        resolved_cam_key,
        TOP_20_EYECANDY_CAMERA_DIRECTIVES["dolly_in"],
    )

    return {
        "shot_size": shot_size,
        "camera_angle": camera_angle,
        "lens": preset["lens"],
        "camera_movement": movement_desc,
        "composition": preset["composition"],
        "lighting": preset["lighting"],
        "optical_effect": preset["effect"],
    }


def build_seven_layer_cinematic_prompt(
    raw_prompt: str,
    niche_id: str = "general",
    scene_id: int = 1,
    total_scenes: int = 8,
    camera_motion: str = "dolly_in",
) -> str:
    """
    Ghép prompt theo đúng công thức 7 tầng của DINO EyeCandy:
    [Cỡ cảnh] + [Góc máy / điểm nhìn] + [Ống kính] + [Chuyển động camera] + [Bố cục] + [Ánh sáng] + [Hiệu ứng]
    kèm mô tả hành động cốt lõi của phân cảnh.
    """
    clean_subj = str(raw_prompt or "").strip().rstrip(".")
    layers = resolve_seven_layer_components(
        raw_prompt=clean_subj,
        niche_id=niche_id,
        scene_id=scene_id,
        total_scenes=total_scenes,
        camera_motion=camera_motion,
    )
    return (
        f"{layers['shot_size']}, {layers['camera_angle']}, {layers['lens']}, "
        f"{layers['camera_movement']}, {layers['composition']}, {layers['lighting']}, "
        f"{layers['optical_effect']}. Subject & Action: {clean_subj}."
    )


# ══════════════════════════════════════════════════════════════════════════════
# SMART REFERENCE BINDING (@CHARACTER_TAG) & 6-PANEL CHARACTER SHEET BUILDER
# ══════════════════════════════════════════════════════════════════════════════

# Kho hồ sơ nhân vật tham chiếu mặc định cho phép gắn thẻ @TAG trực tiếp trong kịch bản/prompt
DEFAULT_SMART_REF_REGISTRY: Dict[str, Dict[str, str]] = {
    "MODERNYOU": {
        "tag": "@MODERNYOU",
        "name": "Modern Exhausted Worker",
        "style": "hand-drawn 2D doodle cartoon character",
        "traits": (
            "simple stick-figure-style 2D doodle: large round head, dot eyes, thick expressive black marker brows, "
            "minimal body, bold black outlines, flat single-color fills, slightly hunched posture, dark under-eye circles, "
            "plain rumpled slate-blue shirt (#4A6FA5)"
        ),
        "negative": "no photorealism, no 3D render, no gradients, no shadows, no extra limbs or fingers, no redesign between panels",
    },
    "FARMER": {
        "tag": "@FARMER",
        "name": "Early Neolithic Farmer",
        "style": "hand-drawn 2D doodle cartoon character",
        "traits": (
            "simple stick-figure-style 2D doodle: large round head, dot eyes, thick expressive marker brows, "
            "gaunt stooped posture, plain rough earth-brown tunic (#8B5E3C), bare feet, holding a simple wooden hoe"
        ),
        "negative": "no photorealism, no 3D render, no gradients, no shadows, no extra limbs or fingers, no redesign between panels",
    },
    "ANCESTOR": {
        "tag": "@ANCESTOR",
        "name": "Paleolithic Hunter-Gatherer",
        "style": "hand-drawn 2D doodle cartoon character",
        "traits": (
            "simple stick-figure-style 2D doodle: large round head, alert dot eyes, upright athletic posture, "
            "warm ochre hide wrap (#C87D32), bold black marker outlines, flat colors"
        ),
        "negative": "no photorealism, no 3D render, no gradients, no shadows, no extra limbs or fingers, no redesign between panels",
    },
    "PLEASER": {
        "tag": "@PLEASER",
        "name": "The People Pleaser",
        "style": "minimalist 2D dark satirical webtoon character",
        "traits": (
            "minimalist 2D line-art character, round pale face, strained polite smile with exhausted eyes, "
            "neat oversized grey office hoodie (#6C757D), clean bold black outlines"
        ),
        "negative": "no photorealism, no 3D hyperrealism, no glossy plastic, no watermark, no text",
    },
    "MANIPULATOR": {
        "tag": "@MANIPULATOR",
        "name": "The Covert Manipulator",
        "style": "minimalist 2D dark satirical webtoon character",
        "traits": (
            "minimalist 2D line-art character, sharp calm half-smirk, dark crimson collar accent (#B22222), "
            "composed upright posture, clean bold black outlines"
        ),
        "negative": "no photorealism, no 3D hyperrealism, no glossy plastic, no watermark, no text",
    },
}

_TAG_REGEX = re.compile(r"@([A-Za-z0-9_]{2,32})\b")


def extract_smart_ref_tags(text: str) -> List[str]:
    """Trích xuất tất cả các thẻ nhân vật tham chiếu dạng @TAG (VD: @MODERNYOU, @FARMER) trong câu lệnh."""
    if not text:
        return []
    found = _TAG_REGEX.findall(text)
    seen: List[str] = []
    for t in found:
        up = t.upper()
        if up not in seen:
            seen.append(up)
    return seen


def resolve_smart_ref_tags(
    prompt: str,
    custom_registry: Optional[Dict[str, Dict[str, str]]] = None,
    fallback_character_anchor: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Tự động phát hiện các thẻ `@TAG` trong Image/Video Prompt và tiêm (bind) hồ sơ nhận dạng
    cố định của nhân vật đó vào prompt để đảm bảo đồng nhất 100% qua hàng trăm phân cảnh.
    Trả về `(enriched_prompt, matched_tags)`.
    """
    if not prompt:
        return "", []

    registry = dict(DEFAULT_SMART_REF_REGISTRY)
    if custom_registry:
        for k, v in custom_registry.items():
            registry[k.upper().lstrip("@")] = v

    tags = extract_smart_ref_tags(prompt)
    if not tags:
        return prompt, []

    injected_traits: List[str] = []
    injected_negatives: List[str] = []

    for tag_key in tags:
        prof = registry.get(tag_key)
        if prof:
            trait_desc = f"[@{tag_key} CHARACTER LOCK: {prof.get('traits', '')}]"
            if trait_desc.lower() not in prompt.lower():
                injected_traits.append(trait_desc)
            neg = prof.get("negative", "")
            if neg and neg.lower() not in prompt.lower() and neg not in injected_negatives:
                injected_negatives.append(neg)
        elif fallback_character_anchor:
            trait_desc = f"[@{tag_key} CHARACTER LOCK: {fallback_character_anchor}]"
            if trait_desc.lower() not in prompt.lower():
                injected_traits.append(trait_desc)

    enriched = prompt.strip()
    if injected_traits:
        enriched = f"{enriched} {' '.join(injected_traits)}"
    if injected_negatives:
        enriched = f"{enriched}. Negative: {', '.join(injected_negatives)}"

    return enriched, [f"@{t}" for t in tags]


def build_character_reference_sheet_prompt(
    tag_name: str,
    character_description: str,
    signature_prop: str = "a signature everyday object",
    signature_color_hex: str = "#4A6FA5",
    art_style: str = "hand-drawn 2D doodle cartoon",
    expressions: Optional[List[str]] = None,
) -> str:
    """
    Tạo Prompt chuẩn 6-Panel Character Turnaround Reference Sheet (theo mẫu Tab 8 GOHA)
    để sinh ảnh gốc tham chiếu trước khi chạy hàng loạt cảnh video.
    """
    clean_tag = tag_name.strip().upper().lstrip("@")
    expr_list = expressions or [
        "tired-hunched (slumped)",
        "strained (gritted, lifting)",
        "anxious (worried brows)",
        "blank-resigned",
    ]
    expr_str = ", ".join(expr_list)

    return (
        f"CHARACTER REFERENCE SHEET — @{clean_tag}\n"
        f"A clean character reference sheet for ONE simple {art_style} character, "
        f"on a pure white background with a thin light-grey grid. NOT realistic, NOT 3D.\n"
        f"LAYOUT:\n"
        f"1. full-body front  2. full-body side  3. full-body back\n"
        f"4. head turnaround: front / three-quarter / side\n"
        f"5. 4 EXPRESSION panels: {expr_str}\n"
        f"6. close-up of the signature prop: {signature_prop}\n"
        f"DESIGN:\n"
        f"{character_description}. Signature color: ({signature_color_hex}). Keep extremely simple and readable.\n"
        f"CONSISTENCY:\n"
        f"All panels show the EXACT same character — identical head shape, face, outline weight, colors, proportions. "
        f"Do not redesign between panels.\n"
        f"STYLE:\n"
        f"{art_style}, flat colors, bold black marker outlines, slightly imperfect sketchy lines, "
        f"no gradients, no shadows, no textures, no 3D, no realistic anatomy.\n"
        f"NEGATIVE:\n"
        f"No photorealism, no 3D render, no realistic face, no anime, no extra limbs or fingers, "
        f"no text/labels/numbers/watermark, no busy background, no redesign between panels."
    )
