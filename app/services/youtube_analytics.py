import urllib.request
import urllib.error
import re
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def parse_view_count(view_text: str) -> int:
    """Chuyển chuỗi lượt xem (ví dụ: '471 lượt xem', '1,2 N lượt xem', '1.2K views', '100K lượt xem') thành số nguyên."""
    if not view_text:
        return 0
    text = view_text.replace("\xa0", " ").replace(",", ".").lower().strip()
    match = re.search(r"([\d\.]+)\s*(nghìn|triệu|tỷ|tr|k|m|b|\bn\b)?", text)
    if not match:
        return 0
    try:
        num = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").strip()
    if unit in ("k", "n", "nghìn"):
        return int(num * 1000)
    elif unit in ("m", "tr", "triệu"):
        return int(num * 1000000)
    elif unit in ("b", "tỷ"):
        return int(num * 1000000000)
    return int(num)

def parse_sub_count(sub_text: str) -> int:
    """Chuyển chuỗi sub thành số nguyên."""
    return parse_view_count(sub_text)

def fetch_channel_live_stats(channel_handle_or_url: str) -> Dict[str, Any]:
    """
    Tự động truy cập kênh YouTube và cào dữ liệu thời gian thực:
    - Tên kênh, handle, người đăng ký, số video
    - Danh sách video shorts và lượt xem của từng video
    """
    if not channel_handle_or_url:
        return {"success": False, "error": "Chưa cung cấp đường dẫn hoặc handle kênh"}

    if channel_handle_or_url.startswith("http"):
        url = channel_handle_or_url.rstrip("/")
    else:
        handle = channel_handle_or_url if channel_handle_or_url.startswith("@") else f"@{channel_handle_or_url}"
        url = f"https://www.youtube.com/{handle}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        html = ""
        last_err = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                break
            except (TimeoutError, urllib.error.URLError) as err:
                last_err = err
        if not html and last_err is not None:
            raise last_err

        m = re.search(r"var ytInitialData = ({.*?});</script>", html)
        if not m:
            m = re.search(r'window\["ytInitialData"\] = ({.*?});</script>', html)
        if not m:
            return {"success": False, "error": "Không thể nạp dữ liệu từ YouTube"}

        d = json.loads(m.group(1))

        # 1. Trích xuất thông tin kênh từ Header
        header = d.get("header", {}).get("pageHeaderRenderer", {})
        content_model = header.get("content", {}).get("pageHeaderViewModel", {})
        ch_title = content_model.get("title", {}).get("dynamicTextViewModel", {}).get("text", {}).get("content", "YouTube Channel")
        
        metadata = content_model.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        meta_parts = []
        for r in metadata:
            for part in r.get("metadataParts", []):
                t = part.get("text", {}).get("content", "").strip()
                if t:
                    meta_parts.append(t)

        handle_text = meta_parts[0] if len(meta_parts) > 0 else "@channel"
        subs_text = meta_parts[1] if len(meta_parts) > 1 else "0 người đăng ký"
        vids_text = meta_parts[2] if len(meta_parts) > 2 else "0 video"

        # Avatar
        avatar_sources = content_model.get("image", {}).get("decoratedAvatarViewModel", {}).get("avatar", {}).get("avatarViewModel", {}).get("image", {}).get("sources", [])
        avatar_url = avatar_sources[-1].get("url") if avatar_sources else ""

        # 2. Tìm kiếm danh sách Video Shorts qua shortsLockupViewModel
        def find_shorts_lockups(obj):
            items = []
            if isinstance(obj, dict):
                if "shortsLockupViewModel" in obj:
                    items.append(obj["shortsLockupViewModel"])
                for v in obj.values():
                    items.extend(find_shorts_lockups(v))
            elif isinstance(obj, list):
                for i in obj:
                    items.extend(find_shorts_lockups(i))
            return items

        lockups = find_shorts_lockups(d)
        videos: List[Dict[str, Any]] = []
        total_views = 0

        for s in lockups:
            om = s.get("overlayMetadata", {})
            title = om.get("primaryText", {}).get("content", "Video Shorts")
            views_text = om.get("secondaryText", {}).get("content", "0 lượt xem")
            views_num = parse_view_count(views_text)
            total_views += views_num

            # Video ID
            vid_id = ""
            tap = s.get("onTap", {})
            cmd = tap.get("innertubeCommand", {})
            reel_cmd = cmd.get("reelWatchEndpoint", {})
            if reel_cmd:
                vid_id = reel_cmd.get("videoId", "")

            # Thumbnail
            thumb_sources = s.get("thumbnailViewModel", {}).get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
            thumb_url = thumb_sources[0].get("url") if thumb_sources else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

            videos.append({
                "video_id": vid_id,
                "title": title,
                "views_text": views_text,
                "views_num": views_num,
                "thumbnail": thumb_url,
                "url": f"https://youtube.com/shorts/{vid_id}" if vid_id else url,
            })

        # Sắp xếp video theo lượt xem giảm dần
        videos.sort(key=lambda x: x["views_num"], reverse=True)
        if videos:
            videos[0]["is_top_performer"] = True

        avg_views = int(total_views / len(videos)) if videos else 0

        return {
            "success": True,
            "channel_title": ch_title,
            "channel_handle": handle_text,
            "channel_url": url,
            "avatar_url": avatar_url,
            "subscribers_text": subs_text,
            "subscribers_num": parse_sub_count(subs_text),
            "videos_count_text": vids_text,
            "videos_count_num": len(videos) or parse_view_count(vids_text),
            "total_views": total_views,
            "avg_views": avg_views,
            "videos": videos
        }

    except Exception as e:
        logger.warning(f"Tạm thời chưa lấy được stats kênh {channel_handle_or_url}: {e}")
        return {
            "success": False,
            "error": str(e)
        }

def _get_niche_recommended_topics(niche: str = "history_mystery", language: str = "en") -> List[Dict[str, Any]]:
    """
    Trả về danh sách chủ đề chiến lược có điểm tiềm năng cao nhất theo từng ngách và ngôn ngữ kênh,
    đồng thời tránh các chủ đề đã bão hòa (như Caiman/Jaguar cũ, Mariana Trench chung chung, hay Rockefeller lặp lại).
    """
    lang_low = (language or "en").lower()

    if niche == "deep_sea_earth_mysteries":
        return [
            {
                "viral_title": "Why Sound Drops to Zero at 3,600 Meters Under Antarctica #Shorts",
                "hook": "At 3,600 meters beneath the Antarctic ice sheet, sonar didn't just fade—something absorbed it completely.",
                "topic": "Hồ ngầm Lake Vostok dưới lớp băng Nam Cực 3.600m và dị thường từ trường khiến radar mất tín hiệu",
                "thumbnail_text": "3,600M ICE ANOMALY",
                "predicted_retention": "88% - 94%",
                "why_viral": "Nhân bản DNA 'Con số độ sâu + Giác quan âm thanh' từ video top 1 (1,286 views) nhưng đổi sang bối cảnh băng vĩnh cửu Nam Cực.",
                "fixes_flaw": "Tránh bão hòa từ khóa Mariana Trench/Point Nemo (đã làm 5 video liên tiếp).",
                "potential_score": 9.9,
                "target_views": "50K - 250K"
            },
            {
                "viral_title": "The 12,262-Meter Borehole Where Microphones Recorded the Unthinkable #Shorts",
                "hook": "When engineers lowered a heat-shielded microphone 12,262 meters into Earth's crust, they expected silence—not this.",
                "topic": "Hố khoan sâu nhất thế giới Kola Superdeep Borehole (12.262m) và hiện tượng vật lý đá dẻo sôi ở 180°C",
                "thumbnail_text": "12,262M DOWN",
                "predicted_retention": "86% - 92%",
                "why_viral": "Khai thác nỗi sợ độ sâu lòng đất kèm âm thanh thực địa (Audio Cue) kích thích xem lại (Rewatch).",
                "fixes_flaw": "Thay thế mở đầu liệt kê kiến thức bằng trải nghiệm thính giác ở giây thứ 2.",
                "potential_score": 9.7,
                "target_views": "40K - 180K"
            },
            {
                "viral_title": "Why Submarine Cables Keep Snapping in This 7,200m Ocean Canyon #Shorts",
                "hook": "Three titanium-armored fiber cables snapped at 7,200 meters depth—and the claw marks on the casing weren't geological.",
                "topic": "Hiện tượng đứt cáp quang biển sâu tại hẻm núi ngầm Congo Canyon và dòng chảy bùn siêu tốc dưới đáy đại dương",
                "thumbnail_text": "SNAPPED AT 7,200M",
                "predicted_retention": "85% - 91%",
                "why_viral": "Kết hợp công nghệ hiện đại (cáp ngầm internet) với bí ẩn tự nhiên cực đoan.",
                "fixes_flaw": "Loại bỏ lối kể chuyện từ điển chậm rãi của các video ~700 views.",
                "potential_score": 9.6,
                "target_views": "35K - 150K"
            },
            {
                "viral_title": "TheUnderwater Brine Lake That Kills Anything Crossing Its Shoreline #Shorts",
                "hook": "Look closely at this shoreline—it's already 2,100 meters underwater, and crossing it means instant toxic shock.",
                "topic": "Hồ nước muối siêu đậm đặc (Jacuzzi of Despair) dưới đáy vịnh Mexico và ranh giới tử thần 2.100m",
                "thumbnail_text": "LAKE UNDER THE SEA",
                "predicted_retention": "84% - 90%",
                "why_viral": "Hình ảnh nghịch lý thị giác cực mạnh: 'Đáy biển dưới đáy biển' giữ chân người xem từ giây đầu.",
                "fixes_flaw": "Tăng tương phản thị giác cho khung hình mở đầu.",
                "potential_score": 9.5,
                "target_views": "30K - 120K"
            }
        ]

    if niche == "wildlife_predators":
        if lang_low.startswith("pt"):
            return [
                {
                    "viral_title": "Por Que Até a Onça Recua Diante de 100 Queixadas Furiosos! #Shorts",
                    "hook": "Um único queixada é presa fácil, mas quando 100 batem os dentes juntos na mata fechada, o caçador vira a caça em 3 segundos.",
                    "topic": "A estratégia militar dos queixadas (porcos-do-mato) cercando uma suçuarana na floresta amazônica",
                    "thumbnail_text": "100 CONTRA 1!",
                    "predicted_retention": "88% - 95%",
                    "why_viral": "Đảo ngược vai trò Kẻ săn mồi thành Con mồi (Role-Reversal) với loài bản địa Brazil mới lạ.",
                    "fixes_flaw": "Chấm dứt chuỗi 6 video lặp đi lặp lại về Cá sấu Caiman & Báo đốm khiến kênh tụt xuống 9 views.",
                    "potential_score": 9.9,
                    "target_views": "40K - 200K"
                },
                {
                    "viral_title": "As Garras de 13 Centímetros da Águia-Harpia Que Dominam a Amazônia #Shorts",
                    "hook": "Olhe para o tamanho dessa garra—maior que a de um urso-pardo, mas silenciosa a 80 km/h entre as árvores.",
                    "topic": "A física do ataque aéreo da Águia-Harpia (Gavião-Real) capturando preguiças e macacos no topo da Amazônia",
                    "thumbnail_text": "GARRA DE 13 CM!",
                    "predicted_retention": "86% - 92%",
                    "why_viral": "Đánh vào niềm tự hào thiên nhiên Amazônia của khán giả Brazil kèm thông số cơ học gây sốc (13cm, 80km/h).",
                    "fixes_flaw": "Đưa thông số vật lý lên ngay giây thứ 2 thay vì giới thiệu rừng rậm lan man.",
                    "potential_score": 9.7,
                    "target_views": "35K - 150K"
                },
                {
                    "viral_title": "O Bando de Ariranhas Que Expulsou um Jacaré de 4 Metros do Rio! #Shorts",
                    "hook": "Elas parecem lontras brincalhonas—até cercarem um jacaré de 4 metros com táticas de matilha implacáveis.",
                    "topic": "Ariranhas (Lontras-gigantes do Pantanal) usando coordenação vocal e emboscada aquática para dominar o rio",
                    "thumbnail_text": "TERROR DO RIO!",
                    "predicted_retention": "85% - 91%",
                    "why_viral": "Hiệu ứng bầy đàn thông minh áp đảo kẻ khổng lồ tạo kịch tính liên tục từng nhịp 4 giây.",
                    "fixes_flaw": "Tăng nhịp độ cắt cảnh và âm thanh thực địa hoang dã.",
                    "potential_score": 9.6,
                    "target_views": "30K - 120K"
                },
                {
                    "viral_title": "O Golpe de 30 Milissegundos da Jararaca-Ilhoa Que Ninguém Vê Chegar #Shorts",
                    "hook": "Em apenas 30 milissegundos—mais rápido que um piscar de olhos humano—o veneno mais letal do Atlântico já agiu.",
                    "topic": "A evolução extrema da Jararaca-Ilhoa na Ilha da Queimada Grande (Ilha das Cobras no Brasil)",
                    "thumbnail_text": "30 MILISSEGUNDOS!",
                    "predicted_retention": "84% - 90%",
                    "why_viral": "Chủ đề Đảo Rắn (Ilha da Queimada Grande) luôn có CTR cực cao tại thị trường Brazil.",
                    "fixes_flaw": "Sử dụng đếm ngược mili-giây giữ chân khán giả đến giây cuối.",
                    "potential_score": 9.5,
                    "target_views": "25K - 100K"
                }
            ]
        return [
            {
                "viral_title": "Why Even Lions Back Down From a Honey Badger With Zero Fear #Shorts",
                "hook": "Six lions surrounded a 12-kilogram honey badger—and within 10 seconds, the lions were the ones backing away.",
                "topic": "Cơ chế da dày 6mm chống răng nanh và phản xạ tấn công ngược của lửng mật trước đàn sư tử",
                "thumbnail_text": "6 LIONS VS 1",
                "predicted_retention": "87% - 93%",
                "why_viral": "Tương phản kích thước cực đoan (12kg vs 6 con sư tử) tạo sự tò mò tức thì.",
                "fixes_flaw": "Vào thẳng khoảnh khắc xung đột ở giây đầu tiên.",
                "potential_score": 9.8,
                "target_views": "40K - 180K"
            },
            {
                "viral_title": "The 1,500 MPH Mantis Shrimp Punch That Boils Water on Impact #Shorts",
                "hook": "This thumb-sized crustacean punches so fast it literally boils the seawater in front of its claw.",
                "topic": "Vật lý cú đấm tạo bọt khí siêu nhiệt (Cavitation Bubble) của tôm tích hạ gục cua giáp cứng",
                "thumbnail_text": "BOILS WATER!",
                "predicted_retention": "85% - 91%",
                "why_viral": "Thông số vật lý siêu thực trong thế giới tự nhiên kích thích bình luận và chia sẻ.",
                "fixes_flaw": "Tránh mô tả chung chung, tập trung vào cơ chế sinh học độc nhất.",
                "potential_score": 9.6,
                "target_views": "30K - 140K"
            }
        ]

    if niche == "animal_rescue":
        return [
            {
                "viral_title": "Wait For the Moment This Paralyzed Street Puppy Realizes His Wheelchair Works #Shorts",
                "hook": "He dragged his back legs through alley mud for 4 months—watch his ears the exact second the wheels touch the grass.",
                "topic": "Hành trình 14 ngày phục hồi kỳ diệu của chú chó nhỏ bị liệt 2 chân sau và khoảnh khắc chạy lần đầu trên xe lăn mini",
                "thumbnail_text": "HIS FIRST RUN!",
                "predicted_retention": "89% - 96%",
                "why_viral": "Cấu trúc 'Payoff Contract' (Hẹn trước khoảnh khắc kỳ diệu ở giây thứ 45) giữ chân người xem đến hết video.",
                "fixes_flaw": "Khắc phục lỗi bi kịch kéo dài quá lâu ở đầu video khiến khán giả lướt bỏ (như video 529 views).",
                "potential_score": 9.9,
                "target_views": "50K - 220K"
            },
            {
                "viral_title": "She Hid Behind a Dumpster Protecting 4 Newborns in Freezing Rain #Shorts",
                "hook": "She refused to eat the warm food we offered—instead, she picked it up and carried it straight under the rusted container.",
                "topic": "Chó mẹ nhịn đói nhường từng miếng thức ăn cho 4 chó con dưới gầm thùng rác trong đêm mưa lạnh và cái kết ấm áp",
                "thumbnail_text": "SHE HID 4 BABIES",
                "predicted_retention": "87% - 93%",
                "why_viral": "Tình mẫu tử của loài vật kết hợp hành động dẫn đường bí ẩn tạo mạch truyện lôi cuốn không thể rời mắt.",
                "fixes_flaw": "Chuyển cảnh từ tuyệt vọng sang hy vọng ngay ở giây thứ 10.",
                "potential_score": 9.7,
                "target_views": "40K - 160K"
            },
            {
                "viral_title": "30 Days After Removing 4 Pounds of Matted Fur From This Blind Senior Dog #Shorts",
                "hook": "Underneath 4 pounds of rock-hard matted fur was a tiny 9-year-old dog who hadn't seen daylight in years.",
                "topic": "Màn lột xác (Grooming + Medical Transformation) sau 30 ngày của chú chó già mù bị bỏ rơi ngoài nghĩa trang",
                "thumbnail_text": "DAY 1 VS DAY 30",
                "predicted_retention": "86% - 92%",
                "why_viral": "Hiệu ứng thị giác Before/After cực mạnh (tương tự video top 1 đạt 1,291 views của kênh).",
                "fixes_flaw": "Đẩy nhanh tiến độ biến hình theo mốc Ngày 1 -> Ngày 7 -> Ngày 30.",
                "potential_score": 9.6,
                "target_views": "35K - 140K"
            },
            {
                "viral_title": "The Stray Dog Who Waited at the Same Hospital Door for 60 Days #Shorts",
                "hook": "Every morning at 7 AM, the automatic hospital doors opened—and this golden stray looked for the one person who wouldn't return.",
                "topic": "Chú chó trung thành đợi chủ trước cửa bệnh viện suốt 60 ngày và khoảnh khắc bác sĩ trực cấp cứu quyết định nhận nuôi",
                "thumbnail_text": "WAITED 60 DAYS",
                "predicted_retention": "85% - 91%",
                "why_viral": "Khai thác lòng trung thành tuyệt đối (Hachiko archetype) nhưng kết thúc có hậu đầy nhân văn.",
                "fixes_flaw": "Tránh kết thúc buồn làm giảm tỷ lệ Like/Share.",
                "potential_score": 9.5,
                "target_views": "30K - 120K"
            }
        ]

    if niche == "alternate_history":
        return [
            {
                "viral_title": "What If the Library of Alexandria Never Burned Down? (300-Year Leap) #Shorts",
                "hook": "Look at your smartphone—if one fire in 48 BC had been stopped, human beings would have landed on Mars by the year 1650.",
                "topic": "Hiệu ứng cánh bướm 3 tầng nếu Thư viện Alexandria và động cơ hơi nước của Hero xứ Alexandria được công nghiệp hóa từ thế kỷ 1",
                "thumbnail_text": "MARS IN 1650?!",
                "predicted_retention": "88% - 94%",
                "why_viral": "Liên hệ trực tiếp vật dụng hiện đại (Smartphone/Mars) với sự kiện cổ đại ngay trong 2 giây đầu.",
                "fixes_flaw": "Khắc phục lỗi giảng sử khô khan của video 41 views bằng chuỗi Domino 3 mốc thời gian rõ ràng.",
                "potential_score": 9.9,
                "target_views": "45K - 200K"
            },
            {
                "viral_title": "What If the Titanic Never Hit the Iceberg in 1912? #Shorts",
                "hook": "Saving the Titanic in 1912 sounds like a miracle—until you realize it would have caused an even deadlier disaster 2 years later.",
                "topic": "Nghịch lý lịch sử: Nếu Titanic không chìm năm 1912, luật an toàn hàng hải SOLAS không ra đời và thảm kịch Thế chiến 1",
                "thumbnail_text": "A WORSE DISaster?",
                "predicted_retention": "87% - 93%",
                "why_viral": "Góc nhìn phản trực giác (Counter-intuitive twist): Một điều tốt lại dẫn đến thảm họa lớn hơn.",
                "fixes_flaw": "Loại bỏ các chủ đề quân sự quá hẹp, chọn sự kiện đại chúng 100% người xem đều biết.",
                "potential_score": 9.7,
                "target_views": "35K - 150K"
            },
            {
                "viral_title": "What If Genghis Khan's Empire Invaded Western Europe in 1242? #Shorts",
                "hook": "In 1242, Mongol scouts were just miles from Vienna—and only one sudden death saved Europe from total erasure.",
                "topic": "Kịch bản giả định nếu Đại Hãn Ögedei không đột tử năm 1241 và kỵ binh Mông Cổ tiến thẳng tới Đại Tây Dương",
                "thumbnail_text": "EUROPE ERASED?",
                "predicted_retention": "85% - 91%",
                "why_viral": "Nhân bản công thức chiến thắng của video #1 kênh (1,248 views - Đế chế bành trướng toàn cầu).",
                "fixes_flaw": "Dùng bản đồ dịch chuyển biên giới trực quan trong từng nhịp 4 giây.",
                "potential_score": 9.6,
                "target_views": "30K - 130K"
            },
            {
                "viral_title": "What If Apollo 11 Was Stranded on the Moon in 1969? (The Secret Speech) #Shorts",
                "hook": "President Nixon had a secret speech already written on his desk in July 1969—in case Neil Armstrong's engine failed to ignite.",
                "topic": "Diễn văn mật 'In Event of Moon Disaster' năm 1969 và thế giới sẽ thay đổi thế nào nếu Apollo 11 kẹt lại Mặt Trăng",
                "thumbnail_text": "NIXON'S SECRET SPEECH",
                "predicted_retention": "85% - 90%",
                "why_viral": "Có bằng chứng lịch sử thật (bản thảo diễn văn mật của Nixon) làm mỏ neo chân thực cho giả thuyết.",
                "fixes_flaw": "Tăng độ gay cấn bằng đồng hồ đếm ngược lượng oxy còn lại.",
                "potential_score": 9.5,
                "target_views": "25K - 110K"
            }
        ]

    # Default: psychology / history_mystery
    return [
        {
            "viral_title": "The 4-Second Silence Trick That Forces Anyone to Respect You #Shorts",
            "hook": "The next time someone interrupts you, don't argue—just stare at their forehead and count to four in dead silence.",
            "topic": "Hiệu ứng áp lực im lặng 4 giây (The 4-Second Silence Effect) trong đàm phán và tâm lý học quyền lực",
            "thumbnail_text": "COUNT TO 4...",
            "predicted_retention": "89% - 95%",
            "why_viral": "Kỹ thuật thực chiến làm được ngay trong 4 giây (Actionable Behavioral Trigger), tỷ lệ Save video cực cao.",
            "fixes_flaw": "Không lặp lại chủ đề Rockefeller/Machiavelli/Im lặng chung chung đã làm ở 4 video trước.",
            "potential_score": 9.9,
            "target_views": "50K - 220K"
        },
        {
            "viral_title": "Watch Their Feet: The FBI Trick That Exposes Fake Friends in 3 Seconds #Shorts",
            "hook": "People spend years mastering how to fake a smile—but 98% of humans forget to control where their feet point.",
            "topic": "Tâm lý học hành vi FBI: Giải mã hướng mũi chân (Foot Torso Disconnect) để biết ai đang muốn rời bỏ hoặc ganh ghét bạn",
            "thumbnail_text": "WATCH THEIR FEET",
            "predicted_retention": "87% - 93%",
            "why_viral": "Đánh trúng tâm lý muốn đọc vị người khác (Mind Reading) bằng dấu hiệu cơ thể cụ thể.",
            "fixes_flaw": "Thay lý thuyết trừu tượng bằng dấu hiệu thị giác kiểm chứng được ngay.",
            "potential_score": 9.7,
            "target_views": "40K - 160K"
        },
        {
            "viral_title": "Why Answering a Question With a Question Destroys Manipulators #Shorts",
            "hook": "When a manipulator asks a trap question to make you defensive, these 5 quiet words flip the spotlight instantly.",
            "topic": "Kỹ thuật đảo khung câu hỏi (Frame Flipping) của chuyên gia thương thuyết con tin để vô hiệu hóa kẻ thao túng",
            "thumbnail_text": "SAY THESE 5 WORDS",
            "predicted_retention": "86% - 92%",
            "why_viral": "Cung cấp đúng mẫu câu 5 từ (Exact Script) để người xem ghi chép và xem lại nhiều lần.",
            "fixes_flaw": "Cụ thể hóa lời thoại thay vì khuyên nhủ sáo rỗng.",
            "potential_score": 9.6,
            "target_views": "35K - 140K"
        },
        {
            "viral_title": "The 'Ben Franklin Paradox' That Makes Rivals Secretly Like You #Shorts",
            "hook": "If you want someone who dislikes you to suddenly respect you, never do them a favor—ask them for a tiny one instead.",
            "topic": "Nghịch lý tâm lý học nhận thức (Cognitive Dissonance - Hiệu ứng Ben Franklin) biến đối thủ thành đồng minh",
            "thumbnail_text": "ASK FOR THIS",
            "predicted_retention": "85% - 91%",
            "why_viral": "Nghịch lý tâm lý học phản trực giác khiến người xem phải dừng lại suy ngẫm và bình luận.",
            "fixes_flaw": "Mở rộng sang tâm lý học nhận thức thực nghiệm thay vì lịch sử tài phiệt cũ.",
            "potential_score": 9.4,
            "target_views": "30K - 110K"
        }
    ]


def calculate_ai_forecast(
    total_views: int,
    total_videos: int,
    avg_views: int,
    daily_uploads: float = 1.0,
    niche: str = "history_mystery",
    language: str = "en",
    channel_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dự đoán tăng trưởng và tiềm năng tài chính cho kênh trong 30, 90, 180, 365 ngày.
    Đồng thời đề xuất các chủ đề chuẩn ngách & ngôn ngữ có tiềm năng bùng nổ view cao nhất.
    """
    base_avg_views = max(avg_views, 300)

    stages = [
        {"days": 30, "label": "30 Ngày Tới (Tạo Đà)", "authority_multiplier": 1.1, "breakout_chance": "Cao (1-2 videos bùng nổ)"},
        {"days": 90, "label": "90 Ngày (Bứt Phá)", "authority_multiplier": 2.2, "breakout_chance": "Rất cao (Đạt mốc 100K views)"},
        {"days": 180, "label": "6 Tháng (Bật Kiếm Tiền)", "authority_multiplier": 4.5, "breakout_chance": "Định hình thương hiệu"},
        {"days": 365, "label": "1 Năm (Quy Mô Lớn)", "authority_multiplier": 8.0, "breakout_chance": "Thư viện Shorts đồ sộ"},
    ]

    forecast_results = []
    accumulated_videos = total_videos
    accumulated_views = total_views

    for stg in stages:
        days = stg["days"]
        new_videos = int(days * daily_uploads)
        accumulated_videos = total_videos + new_videos

        projected_avg = int(base_avg_views * stg["authority_multiplier"])
        period_views = int(new_videos * projected_avg)
        accumulated_views += period_views

        projected_subs = max(1, int(accumulated_views * 0.0055))

        est_adsense_usd = round(accumulated_views * 0.00008, 1)
        est_affiliate_usd = round(accumulated_views * 0.00045, 1)
        est_total_revenue_vnd = int((est_adsense_usd + est_affiliate_usd) * 25400)

        forecast_results.append({
            "days": days,
            "label": stg["label"],
            "total_videos": accumulated_videos,
            "projected_views": accumulated_views,
            "projected_subs": projected_subs,
            "adsense_usd": est_adsense_usd,
            "affiliate_usd": est_affiliate_usd,
            "total_revenue_usd": round(est_adsense_usd + est_affiliate_usd, 1),
            "total_revenue_vnd_formatted": f"{est_total_revenue_vnd:,.0f} ₫",
            "breakout_chance": stg["breakout_chance"]
        })

    recommended_topics = _get_niche_recommended_topics(niche=niche, language=language)

    return {
        "daily_uploads": daily_uploads,
        "base_avg_views": base_avg_views,
        "niche": niche,
        "language": language,
        "stages": forecast_results,
        "recommended_topics": recommended_topics
    }

