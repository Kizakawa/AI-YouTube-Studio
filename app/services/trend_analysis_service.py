"""
app.services.trend_analysis_service — Dịch Vụ Phân Tích Xu Hướng & Bắt Trend Tự Động (Google Trends & Breaking News Intelligence).

Tính năng:
1. Trích xuất thời gian thực xu hướng tìm kiếm từ Google Trends (trend.google.com) qua RSS Feed (VN & Global US).
2. Quét tin tức nóng mới nhất theo từng ngách (Niche Breaking News) từ Google News RSS.
3. Thuật toán "Trend-Jacking" (Bắt Trend thông minh): Dùng AI kết nối từ khóa đang thịnh hành với ngách của kênh.
4. Gợi ý 5 ý tưởng video bùng nổ có nhu cầu tìm kiếm thật (High Search Demand), kèm tỷ lệ CTR dự kiến và hook 3s đầu.
"""
import re
import json
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Tuple
import requests

from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

# Map từ khóa chính của từng niche để tìm kiếm Google News
NICHE_NEWS_KEYWORDS: Dict[str, List[str]] = {
    "wildlife_predators": ["động vật hoang dã", "săn mồi", "thế giới tự nhiên", "vườn quốc gia", "loài vật quý hiếm"],
    "animal_rescue": ["giải cứu động vật", "chó mèo", "thú cưng", "cứu hộ động vật", "tình cảm động vật"],
    "scifi_future_tech": ["khoa học vũ trụ", "trí tuệ nhân tạo AI", "công nghệ mới", "hố đen thiên hà", "nasa vũ trụ"],
    "psychology": ["tâm lý học", "thao túng tâm lý", "hành vi con người", "giao tiếp", "ngôn ngữ cơ thể"],
    "philosophy_stoicism": ["triết học khắc kỷ", "bản lĩnh sống", "tư duy tích cực", "vượt qua nghịch cảnh"],
    "finance_wealth": ["kinh tế", "đầu tư", "thị trường tài chính", "kiếm tiền", "kinh doanh"],
    "history_mystery": ["bí ẩn lịch sử", "khảo cổ học", "di tích cổ đại", "lịch sử thế giới", "lăng mộ cổ"],
    "health_biohacking": ["sức khỏe", "tuổi thọ", "chế độ ăn", "giấc ngủ", "khoa học não bộ"],
    "luxury_motivation": ["thành công", "tỷ phú", "động lực", "thói quen thành công"],
}


class TrendAnalysisService:
    """Service trích xuất và phân tích xu hướng Google Trends & Google News."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def fetch_google_trends(self, geo: str = "VN", limit: int = 15) -> List[Dict[str, Any]]:
        """
        Lấy danh sách các từ khóa tìm kiếm đang bùng nổ theo thời gian thực từ Google Trends (trend.google.com).
        Hỗ trợ geo='VN' (Việt Nam) hoặc geo='US' (Quốc tế/Toàn cầu).
        """
        url = f"https://trends.google.com/trending/rss?geo={geo.upper()}"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code != 200:
                logger.warning(f"Google Trends RSS status code {res.status_code}")
                return self._get_fallback_trends(geo=geo)

            root = ET.fromstring(res.content)
            items = root.findall(".//item")
            trends = []

            for it in items[:limit]:
                title_elem = it.find("title")
                approx_elem = it.find("{https://trends.google.com/trending/rss}approx_traffic")
                pub_elem = it.find("pubDate")
                news_items = it.findall("{https://trends.google.com/trending/rss}news_item")

                title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                traffic = approx_elem.text.strip() if approx_elem is not None and approx_elem.text else "5K+"
                pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""

                news_snippet = ""
                news_url = ""
                if news_items:
                    nt = news_items[0].find("{https://trends.google.com/trending/rss}news_item_title")
                    nu = news_items[0].find("{https://trends.google.com/trending/rss}news_item_url")
                    news_snippet = nt.text.strip() if nt is not None and nt.text else ""
                    news_url = nu.text.strip() if nu is not None and nu.text else ""

                if title:
                    trends.append({
                        "query": title,
                        "title": title,
                        "traffic": traffic,
                        "approx_traffic": traffic,
                        "snippet": news_snippet or f"Chủ đề đang được tìm kiếm nhiều nhất trên Google ({traffic} lượt tìm kiếm)",
                        "url": news_url or f"https://www.google.com/search?q={urllib.parse.quote(title)}",
                        "pub_date": pub_date,
                        "news_items": [{"title": news_snippet, "url": news_url}] if news_snippet else [],
                        "source": "google_trends",
                        "geo": geo.upper()
                    })

            return trends or self._get_fallback_trends(geo=geo)
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất Google Trends ({geo}): {e}")
            return self._get_fallback_trends(geo=geo)

    def fetch_breaking_news(self, geo: str = "VN", limit: int = 8) -> List[Dict[str, Any]]:
        """Lấy các tin tức mới nhất, sự kiện nóng từ Google News RSS."""
        return self.fetch_niche_news(niche_id="general", geo=geo, limit=limit)

    def fetch_niche_news(self, niche_id: str = "general", geo: str = "VN", limit: int = 8) -> List[Dict[str, Any]]:
        """
        Lấy các tin tức mới nhất, sự kiện nóng liên quan trực tiếp đến ngách nội dung từ Google News RSS.
        """
        kw_list = NICHE_NEWS_KEYWORDS.get(niche_id, ["tin tức nổi bật", "khám phá mới"])
        query = " OR ".join([f'"{k}"' for k in kw_list[:3]])
        hl = "vi" if geo.upper() == "VN" else "en"
        gl = geo.upper()
        ceid = f"{gl}:{hl}"

        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code != 200:
                return []

            root = ET.fromstring(res.content)
            items = root.findall(".//item")
            news = []

            for it in items[:limit]:
                title_elem = it.find("title")
                link_elem = it.find("link")
                pub_elem = it.find("pubDate")

                title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
                pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""

                if title:
                    # Cắt bỏ tên nguồn báo ở đuôi (ví dụ: - VnExpress, - Tuổi Trẻ)
                    clean_title = re.sub(r"\s+-\s+[^-]+$", "", title)
                    news.append({
                        "query": clean_title,
                        "title": clean_title,
                        "traffic": "Trending News",
                        "approx_traffic": "Trending News",
                        "snippet": clean_title,
                        "url": link,
                        "pub_date": pub_date,
                        "source": "google_news",
                        "niche": niche_id
                    })

            return news
        except Exception as e:
            logger.debug(f"Google News RSS fetch error for niche '{niche_id}': {e}")
            return []

    def bridge_trend_to_niche(
        self,
        trend_keywords: Optional[List[Dict[str, Any]]] = None,
        niche_id: str = "general",
        channel_niche: Optional[str] = None,
        channel_name: str = "",
        existing_titles: Optional[List[str]] = None,
        count: int = 5,
        geo: str = "VN",
        language: str = "vi"
    ) -> List[Dict[str, Any]]:
        """
        Thuật toán Bắt Trend (Trend-Jacking): Sử dụng LLM để kết nối các chủ đề tìm kiếm nóng nhất
        trên Google với lĩnh vực của kênh, tạo ra 5 ý tưởng có CTR cao và chạm đúng cảm xúc số đông.
        """
        target_niche = channel_niche or niche_id or "general"
        
        if not trend_keywords:
            trend_keywords = self.fetch_google_trends(geo=geo, limit=12)

        if not self.rotator:
            return self._generate_heuristic_topics(target_niche, trend_keywords, count)

        api_key = self.rotator.get_key()
        if not api_key:
            return self._generate_heuristic_topics(target_niche, trend_keywords, count)

        trends_summary = "\n".join([
            f"- '{t.get('query') or t.get('title')}': {t.get('snippet')} (Lượng tìm kiếm: {t.get('traffic') or t.get('approx_traffic', '5K+')})"
            for t in trend_keywords[:10]
        ])

        past_titles_str = "\n".join([f"- {t}" for t in (existing_titles or [])[:15]])

        prompt = f"""Bạn là Giám đốc Sáng tạo Nội dung YouTube hàng đầu thế giới, chuyên gia về YouTube Shorts và thuật toán tăng trưởng 2026.
Nhiệm vụ của bạn là: PHÂN TÍCH XU HƯỚNG TÌM KIẾM (GOOGLE TRENDS) & BẮT TREND (TREND-JACKING) ĐỈNH CAO CHO KÊNH.

THÔNG TIN KÊNH:
- Tên kênh: {channel_name or 'Kênh YouTube'}
- Ngách nội dung: {target_niche}
- Ngôn ngữ: {language}
- Các video kênh ĐÃ LÀM (TUYỆT ĐỐI KHÔNG ĐƯỢC LÀM TRÙNG LẶP):
{past_titles_str or 'Chưa có video cũ'}

CÁC TỪ KHÓA ĐANG XU HƯỚNG BÙNG NỔ TRÊN GOOGLE (GOOGLE TRENDS & NEWS MỚI NHẤT):
{trends_summary}

QUY TẮC BẮT TREND & NỘI DUNG CHẠM CẢM XÚC (BẮT BUỘC):
1. BẮT KỊP XU HƯỚNG (BẮT TREND): Khéo léo liên kết từ khóa đang hot trên Google vào ngách của kênh. Nếu từ khóa hot là công nghệ/thể thao/thời sự, hãy tìm góc nhìn tâm lý, khoa học, bài học hoặc sự thật ẩn sau để khán giả tò mò bấm vào xem ngay!
2. NỘI DUNG CHẠM CẢM XÚC & NỖI LÒNG SỐ ĐÔNG: Đánh thẳng vào nỗi đau, sự tò mò (curiosity gap), nỗi sợ bỏ lỡ (FOMO), hoặc sự bất ngờ cực lớn.
3. CON SỐ CỤ THỂ: Tiêu đề và hook phải có con số cụ thể gây sốc (ví dụ: '97% mọi người...', 'Chỉ 3 giây...', 'Ở độ tuổi 25-35...', 'Lực sát thương 4,000 PSI...').
4. HOOK 3 GIÂY ĐẦU (THỊ TRƯỜNG 1 GIÂY): Câu mở đầu đập tan thói quen lướt qua (swipe-away), giữ chân người xem ngay lập tức.
5. THUMBNAIL TEXT (3-4 TỪ TƯƠNG PHẢN CAO): Chữ to, in hoa, giật gân, khêu gợi tò mò để người xem bấm vào trong 1 giây đầu.

HÃY TRẢ VỀ DUY NHẤT 1 ĐỐI TƯỢNG JSON HỢP LỆ VỚI SCHEMA:
{{
  "trend_proposals": [
    {{
      "topic": "Tên chủ đề chi tiết để sản xuất video (tiếng Việt)",
      "viral_title": "Tiêu đề YouTube giật gân, cuốn hút có con số cụ thể (tiếng Việt)",
      "trending_source_keyword": "Từ khóa Google Trends gốc đã dùng để bắt trend",
      "search_demand": "EXPLODING | HIGH | VERY_HIGH",
      "hook_3s": "Câu hook mở đầu 3 giây chấn động cảm xúc đánh vào tâm lý số đông",
      "thumbnail_text": "3-4 TỪ IN HOA CỰC TO GỢI TÒ MÒ CHO ẢNH BÌA",
      "why_viral": "Giải thích 1 câu vì sao chủ đề này sẽ bùng nổ view"
    }}
  ]
}}"""

        for model in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            try:
                r = requests.post(
                    url,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"}
                    },
                    timeout=20
                )
                if r.status_code == 200:
                    data = r.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
                    text = parts[0].get("text", "")
                    clean_json = re.sub(r"^```json\s*", "", text.strip())
                    clean_json = re.sub(r"\s*```$", "", clean_json).strip()
                    parsed = json.loads(clean_json)
                    proposals = parsed.get("trend_proposals", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
                    if proposals:
                        for p in proposals:
                            hook_val = p.get("hook_3s") or p.get("hook") or ""
                            why_val = p.get("why_viral") or p.get("angle") or ""
                            orig_val = p.get("trending_source_keyword") or p.get("origin_trend") or ""
                            p["hook"] = hook_val
                            p["hook_3s"] = hook_val
                            p["curiosity_hook"] = hook_val
                            p["angle"] = why_val
                            p["why_viral"] = why_val
                            p["origin_trend"] = orig_val
                            p["trending_source_keyword"] = orig_val
                            p["thumbnail_text"] = p.get("thumbnail_text", "")
                        logger.info(f"✅ Bắt trend thành công qua {model}: {len(proposals)} chủ đề bùng nổ!")
                        return proposals[:count]
            except Exception as ex:
                logger.debug(f"Trend bridging error with {model}: {ex}")

        return self._generate_heuristic_topics(target_niche, trend_keywords, count)

    def _generate_heuristic_topics(
        self,
        niche_id: str,
        trend_keywords: List[Dict[str, Any]],
        count: int = 5
    ) -> List[Dict[str, Any]]:
        """Fallback tạo ý tưởng khi API tạm thời gián đoạn."""
        top_trends = [t.get("query") or t.get("title", "xu hướng mới") for t in trend_keywords[:count]]
        results = []
        for i, tr in enumerate(top_trends):
            hook_txt = f"Nếu bạn cũng đang theo dõi {tr}, thì con số này sẽ khiến bạn lạnh gáy ngay từ giây thứ 3..."
            why_txt = f"Tận dụng lượng tìm kiếm bùng nổ của từ khóa {tr} kết hợp yếu tố tâm lý tò mò số đông."
            results.append({
                "topic": f"Sự thật chấn động đằng sau hiện tượng {tr}",
                "viral_title": f"99% Mọi Người Không Biết Sự Thật Về {tr} Này!",
                "trending_source_keyword": tr,
                "origin_trend": tr,
                "search_demand": "HIGH",
                "hook_3s": hook_txt,
                "hook": hook_txt,
                "curiosity_hook": hook_txt,
                "thumbnail_text": "SỰ THẬT TÀN KHỐC",
                "why_viral": why_txt,
                "angle": why_txt
            })
        return results

    def _get_fallback_trends(self, geo: str = "VN") -> List[Dict[str, Any]]:
        """Dữ liệu dự phòng khi Google Trends RSS bị chặn hoặc timeout."""
        if geo.upper() == "VN":
            return [
                {"query": "trí tuệ nhân tạo AI 2026", "title": "Trí tuệ nhân tạo AI 2026", "traffic": "50K+", "approx_traffic": "50K+", "snippet": "Sự phát triển chóng mặt của mô hình AI thế hệ mới tác động tới công việc", "source": "google_trends_cached", "geo": "VN"},
                {"query": "khám phá vũ trụ mới", "title": "Khám phá vũ trụ mới", "traffic": "20K+", "approx_traffic": "20K+", "snippet": "Kính thiên văn James Webb phát hiện tín hiệu bất thường ngoài thiên hà", "source": "google_trends_cached", "geo": "VN"},
                {"query": "động vật săn mồi châu phi", "title": "Động vật săn mồi châu Phi", "traffic": "10K+", "approx_traffic": "10K+", "snippet": "Khoảnh khắc sinh tồn nghẹt thở giữa đàn linh cẩu và báo hoa mai", "source": "google_trends_cached", "geo": "VN"},
                {"query": "tâm lý học hành vi", "title": "Tâm lý học hành vi", "traffic": "10K+", "approx_traffic": "10K+", "snippet": "Cách nhận biết người nói dối qua 3 biểu cảm vi mô trên gương mặt", "source": "google_trends_cached", "geo": "VN"},
                {"query": "bí ẩn lăng mộ cổ", "title": "Bí ẩn lăng mộ cổ", "traffic": "5K+", "approx_traffic": "5K+", "snippet": "Các nhà khảo cổ mở niêm phong căn hầm bí mật 2000 năm tuổi", "source": "google_trends_cached", "geo": "VN"},
            ]
        else:
            return [
                {"query": "AI Breakthrough 2026", "title": "AI Breakthrough 2026", "traffic": "100K+", "approx_traffic": "100K+", "snippet": "New generative AI reasoning models show human level breakthroughs", "source": "google_trends_cached", "geo": "US"},
                {"query": "James Webb Deep Space", "title": "James Webb Deep Space", "traffic": "50K+", "approx_traffic": "50K+", "snippet": "Astronomers discover massive ancient galaxy that shouldn't exist", "source": "google_trends_cached", "geo": "US"},
                {"query": "Apex Predators Savanna", "title": "Apex Predators Savanna", "traffic": "20K+", "approx_traffic": "20K+", "snippet": "Rare high-speed hunt footage captured in Serengeti National Park", "source": "google_trends_cached", "geo": "US"},
                {"query": "Dark Psychology Tells", "title": "Dark Psychology Tells", "traffic": "20K+", "approx_traffic": "20K+", "snippet": "The subtle body language cues that reveal covert manipulation", "source": "google_trends_cached", "geo": "US"},
            ]
