# 🏛️ System Architecture & Engineering Decisions (`AI-YouTube-Studio`)

This document details the core architectural patterns, failure-handling mechanisms, and media-processing pipelines inside **AI YouTube Studio**.

---

## 1. Architectural Philosophy: Semantic Reasoning vs. Deterministic Gates

A common anti-pattern in LLM engineering is either:
1. Trusting raw LLM outputs without deterministic post-conditions, OR
2. Hardcoding brittle keyword `if/else` lists in Python to patch semantic edge cases.

**AI YouTube Studio** enforces a strict boundary across all 242 modules:

- **Semantic Layer (LLM & Multimodal Vision):**
  - Declarative niche profiles (`config/niches/*.yaml`) define narrative voice, visual search strategies, and audience persona without touching Python code.
  - `ContentAgent` emits strongly typed `ScriptContract` / `SceneContract` objects (including active `subject_entity` and multi-angle `search_queries`).
  - `VisualCurationAgent` inspects extracted 3-frame contact sheets via Gemini Multimodal Vision to verify subject fidelity, lighting quality, and absence of burned-in third-party captions.
- **Deterministic Layer (Python, OpenCV, SQLite/Postgres, FFmpeg):**
  - `ContentLinter` & Pydantic v2 enforce word-count budgets, metric consistency, and timestamp monotonicity.
  - `VideoMemoryRAG` computes perceptual 3-frame `dHash` Hamming distances to guarantee zero duplicate footage within a video or across historical uploads.
  - `validate_output()` verifies exact resolution (`1080x1920` or `1920x1080`), locked Constant Frame Rate (`CFR`), `yuv420p` pixel format, `bt709` color space, `48kHz` stereo AAC audio, and A/V duration drift `< 120ms` (`< 35ms` typical).

---

## 2. Fault Tolerance & Multi-Tenant Quota Isolation

### 2.1 Parallel `KeyRotator` (`app/utils/key_rotator.py`)
- Manages pools of LLM API keys with per-key cooldown state:
  - Transient `429 RESOURCE_EXHAUSTED` $\rightarrow$ 30-second soft quarantine + instant failover to next healthy key.
  - Hard daily quota exhaustion / billing block $\rightarrow$ 30-minute isolation without stalling active worker threads.

### 2.2 7-Pool Google Cloud OAuth Isolation (`app/services/quota_manager.py`)
- YouTube Data API v3 enforces a strict `10,000 units/day` ceiling per Google Cloud Project (`videos.insert` costs `1,600 units`).
- Channels are partitioned across **7 isolated OAuth credential pools** (`project_pool_1` .. `project_pool_7`), paired with `CHANNEL_STAGGER_MINUTES` in `smart_scheduler.py` so co-located channels never collide on upload I/O or audience notification windows.
- If a pool approaches its safety threshold, `PublisherAgent` automatically serializes a ready-to-publish fallback bundle instead of dropping the rendered asset.

### 2.3 Hybrid Database Failover (`app/database/models.py`)
- Probes remote PostgreSQL connectivity (including 16KB payload MTU blackhole detection within `0.8s`). If a cloud link degrades, the ORM transparently synchronizes and falls back to local **SQLite WAL** mode with zero downtime.

---

## 3. High-Precision Programmatic Video & Audio Engine

### 3.1 Eliminating Sub-Pixel Staircase Jitter (`app/utils/ffmpeg/color_and_motion.py`)
Standard FFmpeg `crop` animations on `yuv420p` snap chroma planes to 2-pixel boundaries, causing visible micro-stutter on slow pans.
- **Solution:** Still frames are pre-normalized via LANCZOS to **2.5x target resolution** (`2700x4800` for `9:16` Shorts) and animated via sub-pixel `zoompan` expressions across 8 camera motion profiles (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `hyper_zoom`, `cinematic_drift`), downsampling cleanly to `1080x1920 @ 30fps CFR`.
- **Opening Hook Multi-Cut:** Renders 2–3 distinct camera framing cuts in the opening 3 seconds using `2.2x` supersampled sub-shots to maximize early viewer retention.

### 3.2 Seamless Tail Cloning vs. Stream Loop Resets (`concat_and_transitions.py`)
When a stock clip is slightly shorter than neural TTS narration, `-stream_loop -1` causes an abrupt jump-cut back to `t=0.0s` mid-sentence.
- **Solution:** Clips within margin use `tpad=stop_mode=clone:stop_duration=...` to hold optical continuity cleanly through the xfade transition.

### 3.3 Broadcast Audio Mastering (`EBU R128`)
- Voiceover tracks drive dynamic BGM ducking via `sidechaincompress`, followed by `loudnorm=I=-14:TP=-1.5:LRA=11` at `48000 Hz` Stereo AAC `192k`.
- Multi-niche BGM & SFX assets are resolved in **$O(1)$ time** through `assets/bgm/audio_catalog.json`.

---

## 4. Living Studio v2.0 — 2D Skeletal Cinemagraph Pipeline (`app/services/pomodoro`)

Designed for long-form focus & study channels (`1–3 hours` at `1920x1080 @ 24fps CFR`):
1. **Multi-Zone Mesh Deformation (`mascot_engine.py`):** Applies smooth sinusoidal affine/mesh warping across independent anatomical zones (`body_breath`, `ear`, `tail`, `head`, `front_paw`) plus procedural `Zzz` sleep particles.
2. **State-Driven Artwork Switching (`scene_composer.py`):** Automatically transitions from sleeping companion artwork during `FOCUS` intervals to dedicated wide-awake companion artwork during `BREAK` intervals, triggered by acoustic singing-bowl chimes.
3. **Single-Pass Filtergraph Compositor (`video_renderer.py`):** Renders frosted-glass HUD cards, `trunc(t)` frame-accurate countdown timers, and timestamp-shifted (`setpts=PTS+{offset}/TB`) mid-session encouragement bubbles without decoding full-length transparent overlays across every frame.
