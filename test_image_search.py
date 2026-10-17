"""
test_image_search.py — Công cụ kiểm thử trực tiếp Module Tìm Ảnh & Tuyển Chọn Thị Giác AI (Image Search & Vision Curation Lab).

Cách dùng trên Terminal:
  1. Test nhanh 1 prompt (tự động mở báo cáo ảnh trên trình duyệt):
     python test_image_search.py "quái vật biển sâu khổng lồ phát sáng dưới đáy vực Mariana"

  2. Chế độ tương tác liên tục (nhập nhiều prompt liên tiếp):
     python test_image_search.py
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

# Đảm bảo import được package app/ khi chạy trực tiếp từ bất kỳ thư mục nào
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_image_search_test(
    prompt: str,
    niche: str = "auto",
    scene_id: int = 1,
    mode: str = "search_and_vision",
    download_winner: bool = True,
) -> Dict[str, Any]:
    """
    Thực thi trọn vẹn pipeline tìm kiếm & chấm chọn hình ảnh của hệ thống cho 1 câu prompt/lời thoại:
      Bước 1: VisualCurationAgent.analyze_scene_context() -> Phân tích ngữ cảnh, dịch sang bộ truy vấn Google/Bing/ArtStation/Wallhaven.
      Bước 2: WebImageFetcher.search_all() + StockFetcher -> Quét song song đa nguồn trên Internet & lọc prefilter_candidates.
      Bước 3: VisualCurationAgent.curate_candidate_photos() -> Gemini Vision chấm điểm từng ảnh và chọn #1 Winner.
      Bước 4: Tải ảnh Winner (hoặc sinh ảnh AI 8K nếu chọn mode='ai_generate') và đo chỉ số độ nét / tương phản (assess_image_wow_factor).
    """
    t0 = time.perf_counter()
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Prompt không được để trống.")

    from app.agents.visual_curation_agent import VisualCurationAgent
    from app.services.media.web_image_fetcher import WebImageFetcher
    from app.services.media.image_quality import (
        prefilter_candidates,
        assess_image_wow_factor,
        is_creature_subject,
        creature_queries,
    )
    from app.agents.media_agent import MediaAgent, register_download_fallbacks

    curator = VisualCurationAgent()
    media_agent = MediaAgent()

    out_dir = BASE_DIR / "output" / "image_lab"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── BƯỚC 1: AI Phân tích ngữ nghĩa & lên chiến lược truy vấn ──
    t_step1 = time.perf_counter()
    resolved_niche = "general" if niche in ("", "auto", "general") else niche
    visual_analysis = curator.analyze_scene_context(
        narration=clean_prompt,
        topic=clean_prompt,
        scene_id=scene_id,
        total_scenes=1,
        channel_niche=resolved_niche,
    )
    visual_analysis["scene_id"] = scene_id
    visual_analysis["is_hook_scene"] = (scene_id == 1)

    if is_creature_subject(clean_prompt, narration=clean_prompt, topic=clean_prompt) and not visual_analysis.get("creature_mode"):
        c_qs = creature_queries(clean_prompt, topic=clean_prompt, narration=clean_prompt)
        visual_analysis["creature_mode"] = True
        visual_analysis["creature_queries"] = c_qs
        if not visual_analysis.get("google_image_queries"):
            visual_analysis["google_image_queries"] = c_qs[:3]

    step1_ms = int((time.perf_counter() - t_step1) * 1000)

    # Nếu người dùng muốn test riêng chế độ Sinh ảnh AI 8K (ImageAgent)
    if mode == "ai_generate":
        from app.agents.image_agent import ImageAgent
        t_ai = time.perf_counter()
        img_agent = ImageAgent()
        ai_prompt = visual_analysis.get("cinematic_ai_prompt") or clean_prompt
        ts_tag = int(time.time() * 1000) % 1000000
        ai_out_path = str(out_dir / f"ai_generated_{ts_tag}.jpg")
        saved_path = img_agent.generate_scene_image(
            prompt=ai_prompt,
            output_path=ai_out_path,
            aspect_ratio="9:16",
            niche=resolved_niche or "general",
            scene_id=scene_id,
            visual_keyword=clean_prompt,
        )
        wow_metrics = assess_image_wow_factor(saved_path) if saved_path and Path(saved_path).exists() else {}
        rel_url = f"/media/image_lab/{Path(saved_path).name}" if saved_path and Path(saved_path).exists() else ""
        total_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "prompt": clean_prompt,
            "mode": mode,
            "niche": niche,
            "elapsed_ms": total_ms,
            "step1_analysis_ms": step1_ms,
            "visual_analysis": visual_analysis,
            "winner": {
                "url": rel_url,
                "local_path": saved_path,
                "local_web_url": rel_url,
                "source": "ai_image_8k",
                "title": f"AI Generated 8K: {ai_prompt[:90]}",
                "vision_score": 9.5,
                "rationale": "Sinh trực tiếp bằng mô hình AI 8K theo cinematic_ai_prompt của VisualCurationAgent.",
                "wow_metrics": wow_metrics,
            },
            "candidates": [],
        }

    # ── BƯỚC 2: Quét ảnh đa nguồn trên Internet (Bing HD, ArtStation 4K, Wallhaven 4K, DDGS, Google, Stock) ──
    t_step2 = time.perf_counter()
    fetcher = WebImageFetcher()
    creature_mode = bool(visual_analysis.get("creature_mode"))
    creature_qs = [q for q in (visual_analysis.get("creature_queries") or []) if q]
    google_qs = [q for q in (visual_analysis.get("google_image_queries") or []) if q]
    search_qs = [q for q in (visual_analysis.get("search_queries") or []) if q]
    pref_engines = visual_analysis.get("preferred_engines")
    subject = visual_analysis.get("subject") or (search_qs[0] if search_qs else clean_prompt)

    if creature_mode and creature_qs:
        primary_q = creature_qs[0]
        extra_qs = creature_qs[1:] + google_qs[:2]
    elif google_qs:
        primary_q = google_qs[0]
        extra_qs = google_qs[1:] + search_qs[:2]
    elif search_qs:
        primary_q = search_qs[0]
        extra_qs = search_qs[1:3]
    else:
        primary_q = clean_prompt
        extra_qs = []

    if len(clean_prompt.split()) <= 4 and all(ord(ch) < 128 for ch in clean_prompt):
        if clean_prompt.lower() != primary_q.lower():
            extra_qs = [primary_q] + [q for q in extra_qs if q.lower() != clean_prompt.lower()]
            primary_q = clean_prompt

    raw_candidates = fetcher.search_all(
        query=primary_q,
        per_source=12,
        subject=subject,
        extra_queries=extra_qs,
        creature_mode=creature_mode,
        preferred_engines=pref_engines or ["google"],
    )

    # Bổ sung thêm Pexels / Pixabay nếu số lượng ứng viên web < 6
    if len(raw_candidates) < 6:
        try:
            px_items = media_agent.search_photos(primary_q, per_page=4, orientation="portrait", topic=clean_prompt, narration=clean_prompt)
            raw_candidates.extend(px_items)
        except Exception:
            pass

    candidates = prefilter_candidates(
        raw_candidates,
        subject=subject or primary_q,
        limit=12,
        creature_mode=creature_mode,
        max_per_source=12,
    )
    step2_ms = int((time.perf_counter() - t_step2) * 1000)

    # ── BƯỚC 3: Gemini Vision Multimodal chấm điểm & chọn bức ảnh xuất sắc nhất (#1 Winner) ──
    t_step3 = time.perf_counter()
    winner: Optional[Dict[str, Any]] = None
    vision_score: float = 0.0
    rationale: str = "Không tìm thấy ứng viên phù hợp."
    should_ai: bool = False

    if candidates:
        winner, vision_score, rationale, should_ai = curator.curate_candidate_photos(
            candidates=candidates,
            visual_analysis=visual_analysis,
            narration=clean_prompt,
            topic=clean_prompt,
            visual_keyword=primary_q,
            used_urls=set(),
        )
        if not winner and candidates:
            winner = dict(candidates[0])
            rationale = f"[Fallback Top Heuristic — AI đề xuất sinh ảnh riêng: {should_ai}] {rationale}"
        elif winner:
            winner = dict(winner)
    step3_ms = int((time.perf_counter() - t_step3) * 1000)

    # ── BƯỚC 4: Tải bức ảnh thắng cuộc về máy & kiểm định chất lượng thực tế (WOW Factor) ──
    local_path = ""
    local_web_url = ""
    wow_metrics: Dict[str, Any] = {}
    if winner and winner.get("url") and download_winner:
        try:
            register_download_fallbacks(winner, candidates)
            ts_tag = int(time.time() * 1000) % 1000000
            dest_file = out_dir / f"winner_{ts_tag}.jpg"
            media_agent.download_file(winner["url"], str(dest_file))
            if dest_file.exists() and dest_file.stat().st_size > 2000:
                import shutil
                latest_img = out_dir / "latest_winner.jpg"
                try:
                    shutil.copy2(dest_file, latest_img)
                except Exception:
                    pass
                local_path = str(dest_file)
                local_web_url = f"/media/image_lab/{dest_file.name}"
                wow_metrics = assess_image_wow_factor(local_path)
        except Exception as e_dl:
            wow_metrics = {"error": str(e_dl)}

    if winner:
        winner["vision_score"] = round(float(vision_score or 0.0), 2)
        winner["rationale"] = rationale
        winner["should_fallback_to_ai"] = bool(should_ai)
        winner["local_path"] = local_path
        winner["local_web_url"] = local_web_url
        winner["wow_metrics"] = wow_metrics

    formatted_candidates = []
    winner_url = (winner or {}).get("url", "")
    for idx, c in enumerate(candidates, start=1):
        formatted_candidates.append({
            "rank": idx,
            "is_winner": bool(winner_url and c.get("url") == winner_url),
            "url": c.get("url", ""),
            "fallback_url": c.get("fallback_url") or c.get("thumbnail") or c.get("url", ""),
            "page_url": c.get("page_url", ""),
            "source": c.get("source", "web"),
            "creator": c.get("creator", "web"),
            "google_rank": int(c.get("_google_rank") or 999),
            "title": c.get("title") or c.get("alt") or f"Candidate #{idx}",
            "width": c.get("width", 0),
            "height": c.get("height", 0),
            "quality_score": round(float(c.get("_hscore", c.get("_quality_score", 0.0))), 2),
        })

    total_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "prompt": clean_prompt,
        "mode": mode,
        "niche": niche,
        "primary_query": primary_q,
        "extra_queries": extra_qs,
        "raw_found_count": len(raw_candidates),
        "elapsed_ms": total_ms,
        "step1_analysis_ms": step1_ms,
        "step2_search_ms": step2_ms,
        "step3_vision_ms": step3_ms,
        "visual_analysis": visual_analysis,
        "winner": winner,
        "candidates": formatted_candidates,
    }


def generate_html_report(result: Dict[str, Any], output_html_path: Path) -> Path:
    """Sinh file báo cáo HTML trực quan để mở ngay trên trình duyệt khi chạy từ CLI."""
    va = result.get("visual_analysis") or {}
    winner = result.get("winner") or {}
    wow = winner.get("wow_metrics") or {}
    candidates = result.get("candidates") or []

    winner_img_src = winner.get("url") or winner.get("fallback_url") or ""
    if winner.get("local_path") and Path(winner["local_path"]).exists():
        winner_img_src = Path(winner["local_path"]).resolve().as_uri()

    cards_html = []
    for c in candidates:
        g_rk = int(c.get("google_rank") or 999)
        g_lbl = f"Top #{g_rk} Google" if g_rk < 900 else str(c.get("source") or "web")
        badge_cls = "winner-badge" if c.get("is_winner") else "rank-badge"
        badge_txt = f"🏆 #1 WINNER ({g_lbl})" if c.get("is_winner") else f"#{c['rank']} • {g_lbl} • Điểm: {c['quality_score']}"
        img_u = html.escape(c.get("fallback_url") or c.get("url") or "")
        full_u = html.escape(c.get("url") or "")
        title_s = html.escape(str(c.get("title") or "")[:95])
        src_s = html.escape(str(c.get("creator") or c.get("source") or "web"))
        dim_s = f"{c.get('width', 0)}×{c.get('height', 0)}"
        cards_html.append(f"""
        <div class="card {'card-winner' if c.get('is_winner') else ''}">
          <div class="card-img-wrap">
            <span class="{badge_cls}">{badge_txt}</span>
            <span class="src-badge">{src_s} • {dim_s}</span>
            <a href="{full_u}" target="_blank" rel="noopener">
              <img src="{img_u}" alt="{title_s}" loading="lazy" onerror="this.src='{full_u}'" />
            </a>
          </div>
          <div class="card-body">
            <div class="card-title">{title_s}</div>
          </div>
        </div>
        """)

    html_doc = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <title>Image Search Lab — {html.escape(result.get('prompt', ''))}</title>
  <style>
    body {{ background:#0b0f19; color:#f1f5f9; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:28px; }}
    .container {{ max-width:1320px; margin:0 auto; }}
    h1 {{ font-size:24px; margin:0 0 8px; color:#38bdf8; }}
    .meta-bar {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:20px; }}
    .pill {{ background:#1e293b; border:1px solid #334155; padding:6px 12px; border-radius:999px; font-size:13px; }}
    .grid-top {{ display:grid; grid-template-columns: 360px 1fr; gap:24px; margin-bottom:28px; }}
    .panel {{ background:#111827; border:1px solid #1f2937; border-radius:16px; padding:20px; }}
    .winner-img {{ width:100%; aspect-ratio:9/16; object-fit:cover; border-radius:12px; border:2px solid #22c55e; background:#020617; }}
    .queries-list code {{ display:block; background:#020617; color:#7dd3fc; padding:8px 12px; border-radius:8px; margin-bottom:6px; font-size:13px; }}
    .cand-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }}
    .card {{ background:#111827; border:1px solid #1f2937; border-radius:12px; overflow:hidden; }}
    .card-winner {{ border:2px solid #22c55e; box-shadow:0 0 20px rgba(34,197,94,0.25); }}
    .card-img-wrap {{ position:relative; aspect-ratio:9/14; background:#020617; overflow:hidden; }}
    .card-img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
    .winner-badge {{ position:absolute; top:8px; left:8px; background:#16a34a; color:#fff; font-weight:700; font-size:11px; padding:4px 8px; border-radius:6px; }}
    .rank-badge {{ position:absolute; top:8px; left:8px; background:rgba(15,23,42,0.85); color:#e2e8f0; font-size:11px; padding:4px 8px; border-radius:6px; }}
    .src-badge {{ position:absolute; bottom:8px; right:8px; background:rgba(2,6,23,0.85); color:#38bdf8; font-size:11px; padding:3px 7px; border-radius:6px; }}
    .card-body {{ padding:10px 12px; }}
    .card-title {{ font-size:12px; color:#cbd5e1; line-height:1.4; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🔬 Kết Quả Test Tìm Ảnh Hệ Thống (Image Search & Vision Curation)</h1>
    <div class="meta-bar">
      <span class="pill">💬 Prompt: <b>{html.escape(result.get('prompt', ''))}</b></span>
      <span class="pill">🎨 Paradigm: <b>{html.escape(str(va.get('visual_paradigm', 'auto')))}</b></span>
      <span class="pill">⚡ Tổng thời gian: <b>{result.get('elapsed_ms', 0)} ms</b></span>
      <span class="pill">🔎 Quét được: <b>{result.get('raw_found_count', 0)} ảnh web</b> (Lọc Top {len(candidates)})</span>
    </div>
    <div class="grid-top">
      <div class="panel">
        <h3 style="margin-top:0;color:#4ade80;">🏆 Ảnh Chiến Thắng (#1 Winner)</h3>
        <img class="winner-img" src="{html.escape(winner_img_src)}" alt="Winner" />
        <p style="font-size:13px;color:#94a3b8;margin:10px 0 0;">
          <b>Nguồn:</b> {html.escape(str(winner.get('source', '-')))} |
          <b>Điểm AI Vision:</b> <span style="color:#4ade80;font-weight:700;">{winner.get('vision_score', 0)}/10</span> |
          <b>WOW Score:</b> {wow.get('wow_score', '-')}
        </p>
      </div>
      <div class="panel">
        <h3 style="margin-top:0;color:#38bdf8;">🧠 Tư Duy Của AI Visual Director & Gemini Vision</h3>
        <p><b>Lý do chọn ảnh (Vision Rationale):</b><br/>
        <span style="color:#fef08a;">{html.escape(str(winner.get('rationale', '')))}</span></p>
        <p><b>Nguồn tìm kiếm ưu tiên (Preferred Engines):</b> {html.escape(', '.join(va.get('preferred_engines') or ['bing', 'artstation', 'ddgs', 'wallhaven']))}</p>
        <p><b>Bộ truy vấn Google / Bing / ArtStation mà AI tự sinh ra từ Prompt của bạn:</b></p>
        <div class="queries-list">
          {''.join(f"<code>🔎 {html.escape(q)}</code>" for q in (va.get('google_image_queries') or [result.get('primary_query', '')]))}
        </div>
        <p><b>Prompt AI 8K dự phòng (Cinematic AI Prompt):</b></p>
        <div class="queries-list"><code>🎨 {html.escape(str(va.get('cinematic_ai_prompt', '')))}</code></div>
        <p><b>Chỉ số kỹ thuật ảnh tải về (WOW Factor):</b> Độ sáng={wow.get('luminance', '-')}, Tương phản={wow.get('contrast', '-')}, Độ nét={wow.get('sharpness', '-')}, Đạt chuẩn={wow.get('passed', '-')}</p>
      </div>
    </div>
    <h2>📸 Toàn Bộ {len(candidates)} Ảnh Ứng Viên Tìm Được Trên Internet (Bấm vào ảnh để xem ảnh gốc)</h2>
    <div class="cand-grid">
      {''.join(cards_html)}
    </div>
  </div>
</body>
</html>"""
    output_html_path.write_text(html_doc, encoding="utf-8")
    return output_html_path


def _print_cli_summary(res: Dict[str, Any], report_path: Path) -> None:
    va = res.get("visual_analysis") or {}
    w = res.get("winner") or {}
    wow = w.get("wow_metrics") or {}
    print("\n" + "=" * 78)
    print(f"🎯 PROMPT TEST     : {res.get('prompt')}")
    print(f"🎨 VISUAL PARADIGM : {va.get('visual_paradigm')} | Creature Mode: {va.get('creature_mode')}")
    print(f"🌐 NGUỒN ƯU TIÊN   : {', '.join(va.get('preferred_engines') or [])}")
    print(f"🔎 QUERIES AI SINH : {va.get('google_image_queries') or [res.get('primary_query')]}")
    print(f"⏱️ THỜI GIAN XỬ LÝ : {res.get('elapsed_ms')} ms ( Tìm ảnh: {res.get('step2_search_ms')}ms | Vision: {res.get('step3_vision_ms')}ms )")
    print("-" * 78)
    print(f"🏆 #1 ẢNH ĐƯỢC CHỌN: [{w.get('source')}] {w.get('title')}")
    print(f"🔗 URL ẢNH GỐC     : {w.get('url')}")
    print(f"💾 FILE ĐÃ TẢI VỀ  : {w.get('local_path') or 'Chưa tải'}")
    print(f"⭐ ĐIỂM AI VISION  : {w.get('vision_score')}/10 | WOW Score: {wow.get('wow_score', '-')}")
    print(f"💡 LÝ DO AI CHỌN   : {w.get('rationale')}")
    print(f"📊 BÁO CÁO HÌNH ẢNH: {report_path}")
    print("=" * 78 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test trực tiếp Module Tìm Ảnh & Gemini Vision của hệ thống.")
    parser.add_argument("prompt", nargs="*", help="Câu prompt / lời thoại / chủ đề muốn tìm ảnh")
    parser.add_argument("--niche", default="auto", help="Ngách kênh (auto, deep_sea_earth_mysteries, wildlife_predators, psychology, animal_rescue)")
    parser.add_argument("--mode", default="search_and_vision", choices=["search_and_vision", "ai_generate"], help="Chế độ: search_and_vision hoặc ai_generate")
    parser.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    args = parser.parse_args()

    out_report = BASE_DIR / "output" / "image_lab" / "latest_report.html"
    out_report.parent.mkdir(parents=True, exist_ok=True)

    joined_prompt = " ".join(args.prompt).strip()
    if joined_prompt:
        print(f"⏳ Đang phân tích ngữ nghĩa & săn ảnh trên Internet cho: '{joined_prompt}'...")
        res = run_image_search_test(joined_prompt, niche=args.niche, mode=args.mode)
        generate_html_report(res, out_report)
        _print_cli_summary(res, out_report)
        if not args.no_browser:
            webbrowser.open(out_report.resolve().as_uri())
        return

    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║  🔬 HỆ THỐNG TEST TÌM ẢNH TRỰC TIẾP (IMAGE SEARCH & VISION LAB)         ║")
    print("║  Nhập bất kỳ câu mô tả / lời thoại nào để xem hệ thống trả ra ảnh gì!   ║")
    print("║  Gõ 'exit' hoặc 'q' để thoát.                                           ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")
    while True:
        try:
            user_in = input("\n👉 Nhập Prompt / Chủ đề cần tìm ảnh: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_in or user_in.lower() in ("exit", "quit", "q"):
            break
        print(f"⏳ Đang tìm kiếm & chấm điểm bằng Gemini Vision cho: '{user_in}'...")
        try:
            res = run_image_search_test(user_in, niche=args.niche, mode=args.mode)
            generate_html_report(res, out_report)
            _print_cli_summary(res, out_report)
            if not args.no_browser:
                webbrowser.open(out_report.resolve().as_uri())
        except Exception as e:
            print(f"❌ Lỗi khi test tìm ảnh: {e}")


if __name__ == "__main__":
    main()
