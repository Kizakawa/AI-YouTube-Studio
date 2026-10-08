"""
SeedanceDirector — Hệ thống chỉ đạo nghệ thuật & phân cảnh hình học AI (Seedance 2.0 & LocalMiniDrama Integration).

Chức năng:
1. Physical Anchor Converter: Chuyển đổi từ ngữ cảm xúc trừu tượng / ẩn dụ thành hành động thể chất thực tế (Feeling + Physical Anchor).
2. Idle Business Injector: Bổ sung hành động phụ & chuyển động môi trường chống đơ cứng ma-nơ-canh (Anti-Mannequin Freeze).
3. The Lock Line Rule: Khóa nguồn sáng, trang phục, diện mạo và góc máy cố định giữa các phân cảnh để giữ tính nhất quán 100%.
4. Cast & Setting Registry (LocalMiniDrama Gem): Bóc tách và neo giữ diện mạo nhân vật & bối cảnh xuyên suốt mọi phân cảnh, chống lỗi "mỗi cảnh một khuôn mặt khác nhau".
5. Dialogue vs Narration Parser: Phân định rạch ròi thoại nhân vật ("...") và lời dẫn chuyện.
6. Shot Table Builder: Dựng bảng hình học phân cảnh chuẩn mực điện ảnh (Floor Plan, Camera, Eyeline, Action, Light, Last Frame, Cast Anchor).
"""

import re
import logging
from typing import Dict, List, Any, Optional, Union

logger = logging.getLogger(__name__)


# ── BẢNG ÁNH XẠ CẢM XÚC TRỪU TƯỢNG SANG ĐIỂM NEO THỂ CHẤT (FEELING + PHYSICAL ANCHOR) ──
_ANCHOR_PATTERNS = [
    # Sốc, sững sờ, chết lặng
    (
        r"\b(s[ốo]c|s[ữư]ng s[ờo]|ch[ếe]t l[ặa]ng|ng[ỡo] ng[àa]ng|shocked|stunned|speechless|paralyzed|flabbergasted)\b",
        "freezes completely, eyes wide with sudden realization, Adam's apple moving once in a hard swallow, motionless chest"
    ),
    # Thất vọng, mặt sa sầm
    (
        r"\b(th[ấa]t v[ọo]ng|sa s[ầa]m|h[ụu]t h[ẫa]ng|face fell|disappointed|crestfallen|disheartened)\b",
        "eyes slowly casting downward, the polite smile fading, shoulders dropping slightly, a slow visible exhale"
    ),
    # Giận dữ, bốc hỏa, tức giận
    (
        r"\b(t[ứu]c gi[ậa]n|gi[ậa]n d[ữu]|b[ốo]c h[ỏo]a|ph[ẫa]n n[ộo]|furious|enraged|angry|fuming|wrathful)\b",
        "jaw clenching hard, veins subtly prominent at temple, fists tightening firmly until knuckles pale, sharp chest breathing"
    ),
    # Đau lòng, xót xa, nghẹn ngào
    (
        r"\b([đd]au l[òo]ng|x[óo]t xa|ngh[ẹe]n ng[àa]o|n[ướu]c m[ắa]t|heartbroken|grief|weeping|tearful|sorrowful)\b",
        "eyes glistening with unshed tears, lower lip pressed firmly to suppress tremor, head turning slightly away into shadow"
    ),
    # Nhẹ nhõm, thở phào
    (
        r"\b(nh[ẹe] nh[õo]m|th[ởo] ph[àa]o|y[êe]n t[âa]m|relieved|sigh of relief|reassurance)\b",
        "tight shoulders instantly dropping, a deep audible breath released, eyes softening gently into a quiet release"
    ),
    # Khinh bỉ, mỉa mai, cười khẩy
    (
        r"\b(khinh b[ỉi]|m[ỉi]a mai|c[ườu]i kh[ẩa]y|smirk|sneer|scoff|mocking|condescending)\b",
        "one subtle corner of the lips lifting slightly, eyes narrowing calmly in cold amusement, unhurried posture"
    ),
    # Hoảng loạn, sợ hãi
    (
        r"\b(ho[ảa]ng lo[ạa]n|s[ợo] h[ãa]i|kinh ho[àa]ng|terrified|panic|frightened|dread)\b",
        "taking a rapid half-step back, trembling fingers catching the doorway or clothing, rapid shallow breath, darting gaze"
    ),
    # Kiệt sức, mệt mỏi
    (
        r"\b(ki[ệe]t s[ứu]c|m[ệe]t m[ỏo]i|h[ụu]t h[ơo]i|exhausted|fatigued|drained|weary)\b",
        "leaning slightly forward, palms resting on knees, sweat dripping past brow, chest heaving with heavy slow respiration"
    ),
    # Quyết tâm, kiên định
    (
        r"\b(quy[ếe]t t[âa]m|ki[êe]n [đd][ịi]nh|b[ấa]t khu[ấa]t|determined|resolute|unyielding|firm)\b",
        "stance widening firmly, spine straightening, eyes locked unblinking onto the focal target, breath steady and deliberate"
    )
]

# ── HÀNH ĐỘNG NỀN & MÔI TRƯỜNG CHỐNG ĐƠ CỨNG (IDLE BUSINESS PATTERNS) ──
_IDLE_BUSINESS_MAP = {
    "storm": "ambient environment: wind driving fine water mist sideways across surfaces, fabric fluttering vigorously in gusts, rain streaks rippling across floor",
    "indoor": "ambient indoor life: subtle dust particles drifting through warm light beams, soft natural breathing, nearby background figures exchanging subtle glances or continuing quiet tasks",
    "street": "ambient city depth: distant pedestrians walking naturally past in soft background bokeh, ambient vehicle headlights casting gentle diffuse reflections on asphalt",
    "nature": "ambient wild nature: tall blades of grass swaying in unison with the breeze, background tree leaves trembling, insects darting through beams of sunlight",
    "scifi": "ambient futuristic atmosphere: subtle cyclic pulsing of console indicator lights, fine atmospheric condensation vapor drifting past, holographic noise particles floating"
}

# ── HỒ SƠ NHÂN VẬT & TƯỢNG ĐÀI ĐIỆN ẢNH (CAST ARCHETYPES — LocalMiniDrama Inspired) ──
_KNOWN_CAST_ARCHETYPES = [
    (
        r"\b(nikola tesla|tesla)\b",
        "Nikola Tesla",
        "tall lean 40-year-old inventor, slicked dark parted hair, neat mustache, sharp intense deep-set eyes, dark three-piece Victorian suit with stiff high collar"
    ),
    (
        r"\b(albert einstein|einstein)\b",
        "Albert Einstein",
        "50-year-old theoretical physicist, wild iconic gray hair, thick bushy mustache, warm furrowed brow, tweed blazer with knitted vest"
    ),
    (
        r"\b(julius caesar|caesar)\b",
        "Julius Caesar",
        "commanding 50-year-old Roman general, gaunt weathered features, close-cropped graying hair, crimson and gold trimmed toga, laurel wreath"
    ),
    (
        r"\b(marcus aurelius|stoic|khắc kỷ)\b",
        "Marcus Aurelius",
        "bearded 55-year-old philosopher emperor, curly graying beard and hair, contemplative stoic gaze, dark draped woolen philosopher robe"
    ),
    (
        r"\b(steve jobs|jobs)\b",
        "Steve Jobs",
        "40-year-old tech visionary, round wire-rimmed spectacles, trimmed stubble beard, iconic black mock turtleneck, intense focused presence"
    ),
    (
        r"\b(cleopatra)\b",
        "Cleopatra",
        "regal 28-year-old Egyptian queen, sharp geometric dark kohl eyeliner, sleek black braided hair with gold beads, elegant pleated royal linen gown"
    ),
    (
        r"\b(thám tử|detective|investigator)\b",
        "The Detective",
        "veteran detective, 45-year-old, tired sharp observant eyes, stubble jaw, dark charcoal trench coat over rumpled collared shirt"
    ),
    (
        r"\b(hacker|cyberpunk|netrunner)\b",
        "The Netrunner",
        "cyberpunk hacker, 28-year-old, glowing neon ocular cybernetic implant over left eye, dark techwear hooded jacket with subtle luminescent wiring"
    ),
    (
        r"\b(bác sĩ|doctor|surgeon|psychologist|nhà tâm lý)\b",
        "The Specialist",
        "clinical specialist, 42-year-old, calm piercing analytical eyes, subtle tortoiseshell spectacles, tailored dark blazer"
    ),
    (
        r"\b(chiến binh|samurai|warrior|knight)\b",
        "The Warrior",
        "ancient warrior, 38-year-old, battle-hardened jaw, hair tied back in a topknot, weathered traditional dark lacquered armor"
    ),
    (
        r"\b(thợ lặn|biologist|deep sea researcher|nhà sinh vật biển)\b",
        "The Deep Sea Researcher",
        "deep sea researcher, 35-year-old, focused analytical gaze, waterproof tactical expedition jumpsuit with neon safety patches"
    ),
]

# ── HỒ SƠ BỐI CẢNH CỐ ĐỊNH (SETTING ARCHETYPES — LocalMiniDrama Inspired) ──
_KNOWN_SETTING_ARCHETYPES = [
    (
        r"\b(vũ trụ|space|galaxy|hố đen|black hole|astronomy)\b",
        "Deep Space Cockpit",
        "pressurized observation deck, curved thick viewport gazing into dark cosmic void, dim instrument backlighting, blue-green status LEDs"
    ),
    (
        r"\b(đại dương|ocean|deep sea|trench|biển sâu|vực thẳm)\b",
        "Submerged Research Pod",
        "cramped reinforced titanium submersible cabin, circular quartz viewport facing dark murky water, warm red emergency auxiliary lighting"
    ),
    (
        r"\b(lab|laboratory|thí nghiệm|phòng thí nghiệm|khoa học)\b",
        "Subterranean Laboratory",
        "industrial research lab, exposed brass and copper pipes, slate chalkboard with complex diagrams, warm amber pendant lamps, cold atmospheric vapor"
    ),
    (
        r"\b(cổ đại|ancient|la mã|rome|temple|đền đài|hoàng cung)\b",
        "Imperial Senate Chamber",
        "grand classical hall, fluted monolithic marble columns, heavy crimson velvet draping, high arched windows casting sharp afternoon sunbeams"
    ),
    (
        r"\b(cyberpunk|tech|ai|future|công nghệ|thành phố tương lai)\b",
        "Neo-Metropolis Alleyway",
        "rain-soaked futuristic street, towering vertical skyscrapers with holographic billboards, reflections of neon cyan and magenta on wet asphalt"
    ),
    (
        r"\b(tâm lý|psychology|noir|tội phạm|crime|mystery)\b",
        "Noir Consultation Study",
        "dimly lit private archive room, floor-to-ceiling mahogany bookshelves, green glass banker's lamp casting deep shadows, heavy oak desk"
    ),
    (
        r"\b(rừng|forest|hoang dã|wildlife|jungle)\b",
        "Misty Primeval Canopy",
        "dense ancient rainforest, giant moss-covered trees, thick morning fog drifting between fern undergrowth, shafts of dawn sunlight"
    ),
]


def extract_dialogue_quotes(text: str) -> Dict[str, Any]:
    """
    Tách biệt Lời thoại nhân vật (Dialogue trong ngoặc kép) và Lời bình người dẫn chuyện (Narration).
    Hỗ trợ cả ngoặc kép chuẩn (\"...\"), ngoặc kép cong (“...”, ”...”), và câu trích dẫn thoại.
    """
    if not text:
        return {"is_dialogue": False, "dialogue": "", "narration": ""}

    quote_pattern = r'["“]([^"”]+)["”]'
    quotes = re.findall(quote_pattern, text)

    if quotes:
        dialogue = " ".join(quotes).strip()
        narration_only = re.sub(quote_pattern, '', text)
        narration_only = re.sub(r'\s+', ' ', narration_only).strip(" ,-:")
        return {
            "is_dialogue": True,
            "dialogue": dialogue,
            "narration": narration_only or text.strip()
        }

    return {
        "is_dialogue": False,
        "dialogue": "",
        "narration": text.strip()
    }


class SeedanceDirector:
    """
    SeedanceDirector — Điều phối viên hình học phân cảnh và chất lượng chỉ đạo nghệ thuật.
    Triển khai các nguyên lý đột phá từ Seedance 2.0 Skill OS & LocalMiniDrama Nano-Pipeline.
    """

    def __init__(self):
        pass

    def enrich_with_physical_anchor(self, prompt: str, narration: str = "") -> str:
        """
        Quét và chuyển đổi các từ khóa cảm xúc trừu tượng / ẩn dụ văn học thành
        hành động thể chất cụ thể (Feeling + One Physical Anchor).
        """
        if not prompt:
            return ""

        combined_text = f"{prompt} {narration}".lower()
        anchors_found: List[str] = []

        for pattern, anchor in _ANCHOR_PATTERNS:
            if re.search(pattern, combined_text, flags=re.IGNORECASE):
                if anchor not in anchors_found:
                    anchors_found.append(anchor)
                if len(anchors_found) >= 2:
                    break

        if not anchors_found:
            return prompt.strip()

        anchor_str = ", ".join(anchors_found)
        if anchor_str.lower() in prompt.lower():
            return prompt.strip()

        return f"{prompt.strip()}, physical anchor: {anchor_str}"

    def inject_idle_business(self, prompt: str, scene_type: str = "auto") -> str:
        """
        Bổ sung hành động phụ của môi trường hoặc nhân vật nền để chống hiện tượng
        ma-nơ-canh bất động (Anti-Mannequin Freeze) theo chuẩn Seedance 2.0.
        """
        if not prompt:
            return ""

        p_lower = prompt.lower()
        selected_key = "indoor"

        if scene_type in _IDLE_BUSINESS_MAP:
            selected_key = scene_type
        else:
            if any(w in p_lower for w in ["storm", "rain", "ocean", "wave", "bão", "mưa", "biển"]):
                selected_key = "storm"
            elif any(w in p_lower for w in ["space", "cyberpunk", "robot", "alien", "vũ trụ", "hố đen", "laser"]):
                selected_key = "scifi"
            elif any(w in p_lower for w in ["street", "city", "market", "traffic", "đường phố", "thành phố"]):
                selected_key = "street"
            elif any(w in p_lower for w in ["wildlife", "animal", "forest", "jungle", "safari", "động vật", "rừng"]):
                selected_key = "nature"
            else:
                selected_key = "indoor"

        idle_str = _IDLE_BUSINESS_MAP[selected_key]
        if idle_str.lower() in p_lower:
            return prompt.strip()

        return f"{prompt.strip()}, {idle_str}"

    def extract_cast_and_scene_anchors(
        self,
        topic: str,
        script_or_scenes: Any,
        niche_id: str = "general"
    ) -> Dict[str, Any]:
        """
        Bóc tách Bảng Neo Nhân Vật (Character Cast Anchor) và Bối Cảnh (Scene Setting Anchor)
        xuyên suốt toàn bộ video theo nguyên lý Nano-Pipeline từ LocalMiniDrama:
        - Giữ nguyên 1 diện mạo nhân vật và 1 không gian cố định giữa mọi cảnh quay.
        - Tránh triệt để hiện tượng 'mỗi cảnh một khuôn mặt khác nhau' của AI.
        """
        combined_text = str(topic or "")
        if isinstance(script_or_scenes, str):
            combined_text += " " + script_or_scenes
        elif isinstance(script_or_scenes, list):
            for sc in script_or_scenes:
                if isinstance(sc, dict):
                    combined_text += " " + sc.get("narration", "") + " " + sc.get("visual_keyword", "")
                elif hasattr(sc, "narration"):
                    combined_text += " " + getattr(sc, "narration", "") + " " + getattr(sc, "visual_keyword", "")

        combined_lower = combined_text.lower()

        # 1. Trích xuất hoặc đối chiếu Nhân Vật Chính (Character Cast Anchor)
        char_name = None
        char_traits = None

        for pattern, name, traits in _KNOWN_CAST_ARCHETYPES:
            if re.search(pattern, combined_lower, flags=re.IGNORECASE):
                char_name = name
                char_traits = traits
                break

        if not char_name:
            generic_matches = [
                (r"\b(cô gái|thiếu nữ|người phụ nữ|young woman|girl|lady)\b", "Young Woman", "young woman in her twenties, natural expressive eyes, neat hair, contemporary clean styling"),
                (r"\b(chàng trai|thanh niên|người đàn ông|young man|guy)\b", "Young Man", "young man in his late twenties, sharp focused gaze, casual neat attire"),
                (r"\b(ông lão|bà lão|người già|old man|elder|elderly)\b", "The Elder", "weathered elderly person, gentle wise furrowed brow, silver-gray hair, understated comfortable wool clothing"),
                (r"\b(chủ tịch|ceo|sếp|giám đốc|boss|executive)\b", "The Executive", "authoritative 45-year-old executive, immaculate dark tailored suit, sharp confident presence"),
                (r"\b(nhà vua|vua|king|emperor|hoàng đế)\b", "The Monarch", "commanding ruler, royal regal robes with embroidered gold trim, dignified stature"),
                (r"\b(chiến sĩ|người lính|soldier|commander)\b", "The Commander", "disciplined 35-year-old military officer, utilitarian tactical dark uniform, steady composed posture"),
            ]
            for pat, g_name, g_traits in generic_matches:
                if re.search(pat, combined_lower, flags=re.IGNORECASE):
                    char_name = g_name
                    char_traits = g_traits
                    break

        if not char_name:
            clean_subject = (topic.split(":")[0].split("-")[0].strip()) if topic else "Subject"
            char_name = clean_subject[:40] or "Subject"
            char_traits = f"authentic consistent form of {char_name}, realistic physical textures"

        # 2. Trích xuất Bối Cảnh Cố Định (Scene Setting Anchor)
        setting_name = None
        setting_traits = None

        for pattern, s_name, s_traits in _KNOWN_SETTING_ARCHETYPES:
            if re.search(pattern, combined_lower, flags=re.IGNORECASE):
                setting_name = s_name
                setting_traits = s_traits
                break

        if not setting_name:
            raw_niche = (niche_id or "").lower()
            if "deep_sea" in raw_niche or "ocean" in raw_niche:
                setting_name = "Abyssal Trench Realm"
                setting_traits = "deep ocean floor, bioluminescent vents, dark cyan water with drifting marine snow"
            elif "wildlife" in raw_niche or "animal" in raw_niche:
                setting_name = "Natural Habitat Sanctuary"
                setting_traits = "authentic outdoor wild habitat, natural golden hour sunlight, lush organic flora"
            elif "scifi" in raw_niche or "tech" in raw_niche:
                setting_name = "High-Tech Command Facility"
                setting_traits = "futuristic modular architecture, dark matte composite walls, subtle neon telemetry arrays"
            elif "psychology" in raw_niche or "stoic" in raw_niche:
                setting_name = "Minimalist Architectural Space"
                setting_traits = "stark concrete and dark timber walls, raking natural side window illumination, atmospheric stillness"
            else:
                setting_name = f"{topic[:30]} Master Set" if topic else "Master Studio Set"
                setting_traits = "clean cinematic environment, balanced 3-point studio illumination, soft depth of field"

        character_anchor_str = f"{char_name}: {char_traits}"
        setting_anchor_str = f"{setting_name}: {setting_traits}"

        return {
            "character_name": char_name,
            "character_traits": char_traits,
            "character_anchor": character_anchor_str,
            "setting_name": setting_name,
            "setting_traits": setting_traits,
            "setting_anchor": setting_anchor_str,
        }

    def bind_cast_to_prompt(
        self,
        prompt: str,
        character_anchor: Optional[str] = None,
        setting_anchor: Optional[str] = None
    ) -> str:
        """
        Khóa chặt diện mạo nhân vật và bối cảnh vào prompt của AI Image Generator.
        Đảm bảo tính bất biến (idempotent): không lặp lại nếu đã chứa neo.
        """
        if not prompt:
            return ""

        result = prompt.strip()

        # 1. Neo nhân vật (Character Cast Anchor)
        if character_anchor:
            clean_char = character_anchor.strip()
            if clean_char.lower() not in result.lower() and "cast:" not in result.lower():
                result = f"{result}, cast: {clean_char}"

        # 2. Neo bối cảnh (Setting Anchor)
        if setting_anchor:
            clean_set = setting_anchor.strip()
            if clean_set.lower() not in result.lower() and "setting:" not in result.lower():
                result = f"{result}, setting: {clean_set}"

        return result

    def build_lock_line(
        self,
        entity_name: str = "",
        visual_traits: str = "",
        lighting: str = "warm tungsten 3200k overhead, soft subtle fill",
        camera_style: str = "35mm cinematic lens, stable vertical framing",
        setting_name: str = "",
    ) -> str:
        """
        Tạo 'The Lock Line' — chuỗi khóa danh tính, bối cảnh và ánh sáng
        lặp lại đồng nhất giữa các phân cảnh để mô hình AI không bao giờ bị mất trí nhớ.
        """
        traits = visual_traits.strip() or "consistent recognizable features, natural wardrobe"
        name = entity_name.strip() or "primary subject"
        light = lighting.strip() or "consistent directional cinematic lighting"
        cam = camera_style.strip() or "vertical composition"
        setting_part = f" Setting ({setting_name})." if setting_name.strip() else ""

        return f"Lock Line: Same light ({light}). Subject {name} ({traits}).{setting_part} Camera ({cam})."

    def apply_lock_line_to_scene(self, prompt: str, lock_line: str) -> str:
        """Gắn Lock Line vào cuối phân cảnh nếu chưa có."""
        if not lock_line:
            return prompt
        if "lock line:" in prompt.lower() or lock_line.strip().rstrip(".").lower() in prompt.lower():
            return prompt.strip()
        clean_p = prompt.strip().rstrip(".")
        clean_l = lock_line.strip()
        return f"{clean_p}. {clean_l}"

    def create_shot_table(
        self,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        topic: str = "",
        channel_style: str = "cinematic",
        character_anchor: Optional[str] = None,
        setting_anchor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Biến đổi danh sách phân cảnh thô thành 'Bảng Phân Cảnh Hình Học' (Shot Table)
        kèm Sơ đồ mặt sàn (Floor Plan) theo chuẩn đạo diễn Seedance 2.0 & LocalMiniDrama Nano-Pipeline.
        """
        total = len(scenes)

        # 1. Bóc tách hoặc sử dụng Character & Setting Anchors
        if not character_anchor or not setting_anchor or not primary_entity:
            extracted = self.extract_cast_and_scene_anchors(
                topic=topic,
                script_or_scenes=scenes,
            )
            entity = primary_entity or extracted["character_name"]
            char_anchor = character_anchor or extracted["character_anchor"]
            char_traits = extracted["character_traits"]
            set_anchor = setting_anchor or extracted["setting_anchor"]
            set_name = extracted["setting_name"]
        else:
            entity = primary_entity
            char_anchor = character_anchor
            char_traits = character_anchor
            set_anchor = setting_anchor
            set_name = setting_anchor.split(":")[0] if ":" in setting_anchor else setting_anchor

        # 2. Thiết lập Floor Plan mẫu dựa trên topic
        floor_plan_header = (
            f"Floor Plan & Spatial Orientation: Master set for '{set_name}' in '{topic or 'Scene Production'}'. "
            f"Central axis aligned towards {entity}. Key light: warm directional tungsten (3200K) "
            f"with cold ambient blue fill from side aperture. Fixed landmarks established for camera alignment."
        )

        shot_rows: List[Dict[str, Any]] = []
        lock_line_base = self.build_lock_line(
            entity_name=entity,
            visual_traits=f"consistent look for {entity}, realistic textures",
            lighting="warm 3200k key with soft ambient side fill",
            setting_name=set_name
        )

        for idx, sc in enumerate(scenes):
            sc_id = sc.get("scene_id", idx + 1)
            narr = sc.get("narration", "")

            # Phân tách lời thoại trực tiếp (Dialogue) vs Lời dẫn chuyện (Narration)
            dialogue_info = extract_dialogue_quotes(narr)

            # Xác định Camera Shot Size
            if sc_id == 1:
                cam = "Wide establishing shot from rear entrance facing focal landmark"
                eyeline = "Eyeline: looking directly towards central entrance"
            elif sc_id == total:
                cam = "Tight low-angle insert holding on the concluding element"
                eyeline = "Eyeline: held steady on the final outcome"
            elif sc_id % 2 == 0:
                cam = "Medium profile shot at chest height from counter side"
                eyeline = "Eyeline: looking across lens towards conversational partner"
            else:
                cam = "Close-up cinematic shot framing subject from side-angle"
                eyeline = "Eyeline: fixed intently on the immediate physical object"

            # 1 Action duy nhất + Physical Anchor
            enriched_action = self.enrich_with_physical_anchor(
                prompt=sc.get("visual_keyword") or narr[:60],
                narration=narr
            )

            # Idle business chống đơ
            idle_biz = "Background figures exchange subtle glances and breathe naturally; ambient dust motes drift in light"

            # Last frame
            last_frame = f"Hold final frame on {entity} completing movement; camera remains stable"

            shot_rows.append({
                "shot_id": sc_id,
                "camera": cam,
                "in_frame": f"{entity} positioned at primary landmark; secondary elements in soft background",
                "eyeline": eyeline,
                "action": enriched_action,
                "others_idle_business": idle_biz,
                "light": "Consistent 3200K key light with subtle cold rim",
                "last_frame": last_frame,
                "lock_line": lock_line_base,
                "duration_seconds": sc.get("estimated_seconds", sc.get("actual_duration_seconds", 3.5)),
                "narration_excerpt": (narr[:65] + "...") if len(narr) > 65 else narr,
                "is_dialogue": dialogue_info["is_dialogue"],
                "dialogue_text": dialogue_info["dialogue"],
                "character_anchor": char_anchor,
                "setting_anchor": set_anchor,
            })

        return {
            "title": f"Seedance 2.0 Shot Table — {topic or 'Video Project'}",
            "topic": topic,
            "floor_plan": floor_plan_header,
            "primary_entity": entity,
            "character_anchor": char_anchor,
            "setting_anchor": set_anchor,
            "shot_count": len(shot_rows),
            "shots": shot_rows
        }

