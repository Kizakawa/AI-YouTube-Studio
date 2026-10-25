import unittest
from app.schemas.contracts import RenderJobContract, SceneContract, ScriptContract
from app.agents.ai_video_agent import AIVideoAgent
from app.services.media.cinematic_dictionary import (
    TOP_20_EYECANDY_CAMERA_DIRECTIVES,
    VEO_FLOW_MODEL_OPTIONS,
    build_character_reference_sheet_prompt,
    build_seven_layer_cinematic_prompt,
    resolve_smart_ref_tags,
)
from app.services.media.seedance_director import SeedanceDirector
from app.services.intelligence.comment_intelligence import (
    aggregate_comments_into_intelligence,
    build_comment_intelligence_prompt_block,
    classify_comment_zero_token,
)
from app.services.content_linter import ScriptValidationLinter
from app.services.media.timeline_manifest import (
    TimelineConcurrencyError,
    TimelineValidationError,
    apply_ops,
    build_initial_edit_manifest,
    compile_capcut_draft,
    diff_since,
    undo_last_op,
    verify_voice_clip_alignment,
)


class TestGohaCinematicAndIntelUpgrades(unittest.TestCase):

    def test_top20_eyecandy_camera_and_7layer_formula(self):
        # Đảm bảo đủ 5 model chuẩn Flow (bao gồm Veo 3.1 - Lite [Lower Priority])
        self.assertIn("Veo 3.1 - Lite [Lower Priority]", AIVideoAgent.AVAILABLE_FLOW_MODELS)
        self.assertEqual(len(VEO_FLOW_MODEL_OPTIONS), 5)

        # Kiểm tra 4 miền (Wildlife, Deep Sea, Psychology 2D, Alternate History)
        p_wildlife = build_seven_layer_cinematic_prompt(
            "Black Caiman lunging from murky Amazon river toward Jaguar",
            niche_id="wildlife_predators",
            scene_id=1,
            camera_motion="worms_eye",
        )
        self.assertIn("85mm", p_wildlife)
        self.assertIn("worm's-eye", p_wildlife.lower())

        p_motion = AIVideoAgent.build_cinematic_motion_prompt(
            prompt="@MODERNYOU lying flat on his back as a heavy grey block drops onto his chest",
            niche="satirical_psychology_animation",
            camera_motion="arc_orbit",
            scene_id=4,
        )
        self.assertIn("CHARACTER LOCK", p_motion)
        self.assertIn("circular arc camera movement", p_motion)
        self.assertIn("STRICT QUALITY & PHYSICS LOCK", p_motion)
        self.assertGreaterEqual(len(TOP_20_EYECANDY_CAMERA_DIRECTIVES), 20)

    def test_smart_ref_binding_and_character_sheet(self):
        director = SeedanceDirector()
        bound = director.bind_cast_to_prompt(
            "002. Hand-drawn 2D doodle cartoon, @FARMER hunched in wheat field with @ANCESTOR watching",
            setting_anchor="Neolithic Valley",
        )
        self.assertIn("@FARMER CHARACTER LOCK", bound)
        self.assertIn("@ANCESTOR CHARACTER LOCK", bound)
        self.assertIn("#8B5E3C", bound)
        self.assertIn("setting: Neolithic Valley", bound)

        sheet = director.build_character_sheet_prompt(
            tag_name="@FARMER",
            character_description="An early Neolithic farmer with gaunt posture and rough tunic",
            signature_prop="a simple wooden hoe and a single stalk of wheat",
            signature_color_hex="#8B5E3C",
        )
        self.assertIn("CHARACTER REFERENCE SHEET — @FARMER", sheet)
        self.assertIn("1. full-body front  2. full-body side  3. full-body back", sheet)

    def test_zero_token_comment_classifier_and_intelligence_miner(self):
        # 1. Bình luận khen xã giao / emoji -> 0 token (needs_llm=False)
        c1 = classify_comment_zero_token("Amazing!! 🔥🔥👏")
        self.assertFalse(c1["needs_llm"])
        self.assertEqual(c1["category"], "praise_or_emoji")

        # 2. Bình luận yêu cầu chủ đề -> topicRequests
        c2 = classify_comment_zero_token("Please make a video comparing Colossal Squid vs Megalodon at 3,000m!")
        self.assertTrue(c2["needs_llm"])
        self.assertEqual(c2["signal_bucket"], "topicRequests")

        # 3. Bình luận phản biện số liệu -> hotspots
        c3 = classify_comment_zero_token("Actually a saltwater crocodile bite force is 3,700 PSI, not 1,500 PSI!")
        self.assertTrue(c3["needs_llm"])
        self.assertEqual(c3["signal_bucket"], "hotspots")

        # 4. Bình luận câu hỏi mở -> openQuestions
        c4 = classify_comment_zero_token("Why doesn't the giant squid's eye implode under that extreme pressure?")
        self.assertTrue(c4["needs_llm"])
        self.assertEqual(c4["signal_bucket"], "openQuestions")

        # Tổng hợp và kiểm tra tỷ lệ tiết kiệm token
        agg = aggregate_comments_into_intelligence([
            {"id": "1", "text": "Great!", "likes": 2},
            {"id": "2", "text": "🔥🔥🔥", "likes": 1},
            {"id": "3", "text": "Please make a video comparing Colossal Squid vs Megalodon next!", "likes": 45},
            {"id": "4", "text": "Why doesn't the giant squid's eye implode under that extreme pressure?", "likes": 30},
            {"id": "5", "text": "Actually a saltwater crocodile bite force is 3,700 PSI in lab tests!", "likes": 19},
        ])
        self.assertEqual(agg["total_comments_scanned"], 5)
        self.assertEqual(agg["zero_token_filtered_count"], 2)
        self.assertEqual(len(agg["topicRequests"]), 1)
        self.assertEqual(len(agg["openQuestions"]), 1)
        self.assertEqual(len(agg["hotspots"]), 1)

        block = build_comment_intelligence_prompt_block("ch_test", precomputed_intel=agg)
        self.assertIn("AUDIENCE COMMENT INTELLIGENCE", block)
        self.assertIn("Colossal Squid vs Megalodon", block)

    def test_fact_gate_metric_consistency(self):
        scenes_no_num = [
            SceneContract(scene_id=i, narration="The predator waits silently in the dark jungle river.", visual_keyword="jaguar")
            for i in range(1, 9)
        ]
        script_mismatch = ScriptContract(
            title="Jaguar vs Black Caiman: 1500 PSI Bite #Shorts",
            hook="The predator waits silently in the dark river.",
            full_narration=" ".join(s.narration for s in scenes_no_num),
            scenes=scenes_no_num,
            tags=["shorts", "wildlife"],
        )
        issues = ScriptValidationLinter.check_metric_and_fact_consistency(script_mismatch)
        self.assertTrue(any("Fact-Gate Title-to-Script Metric Mismatch" in i for i in issues))

        scenes_impossible = [
            SceneContract(scene_id=1, narration="This simple trick unlocks 250% of the human brain instantly.", visual_keyword="brain")
        ] + scenes_no_num[1:]
        script_hallucinated = ScriptContract(
            title="Unlock Your Hidden Mind #Shorts",
            hook="This simple trick unlocks 250% of the human brain instantly.",
            full_narration=" ".join(s.narration for s in scenes_impossible),
            scenes=scenes_impossible,
            tags=["shorts", "psychology"],
        )
        issues_pct = ScriptValidationLinter.check_metric_and_fact_consistency(script_hallucinated)
        self.assertTrue(any("Hallucinated Percentage" in i for i in issues_pct))

    def test_timeline_manifest_reversible_ops_and_capcut_compiler(self):
        job = RenderJobContract(
            job_id=999,
            title="Colossal Squid vs Sperm Whale: 2,000m #Shorts",
            scenes=[
                SceneContract(
                    scene_id=1,
                    narration="At two thousand meters deep, zero sunlight survives.",
                    visual_keyword="deep ocean",
                    media_path="storage/temp/scene_001.mp4",
                    media_type="video",
                    audio_path="storage/temp/scene_001.mp3",
                    audio_duration_ms=4200,
                    camera_motion="dolly_in",
                ),
                SceneContract(
                    scene_id=2,
                    narration="A sperm whale dives into the crushing black trench.",
                    visual_keyword="sperm whale diving",
                    media_path="storage/temp/scene_002.mp4",
                    media_type="video",
                    audio_path="storage/temp/scene_002.mp3",
                    audio_duration_ms=4800,
                    camera_motion="arc_orbit",
                ),
            ],
            full_audio_path="storage/temp/full.mp3",
            total_audio_duration_ms=9000,
            subtitle_path="storage/temp/sub.srt",
            output_path="storage/outputs/video_999.mp4",
        )
        manifest = build_initial_edit_manifest(job)
        self.assertEqual(manifest["rev"], 1)

        # Áp dụng 2 thao tác ngữ nghĩa từ từ vựng đóng
        updated = apply_ops(
            manifest,
            [
                {"op": "trim_scene", "scene_id": 1, "duration_ms": 4000},
                {"op": "set_camera_motion", "scene_id": 2, "camera_motion": "worms_eye"},
            ],
            base_rev=1,
            actor="human",
        )
        self.assertEqual(updated["rev"], 3)
        self.assertEqual(updated["scenes"][0]["duration_ms"], 4000)
        self.assertEqual(updated["scenes"][1]["start_ms"], 4000)
        self.assertEqual(updated["scenes"][1]["camera_motion"], "worms_eye")

        # Kiểm tra diff_since(1)
        diffs = diff_since(updated, 1)
        self.assertEqual(len(diffs), 2)
        self.assertIn("trimmed scene 1", diffs[0]["summary"])

        # Kiểm tra undo_last_op
        undone = undo_last_op(updated, actor="agent")
        self.assertEqual(undone["rev"], 4)
        self.assertEqual(undone["scenes"][1]["camera_motion"], "arc_orbit")

        # Chặn thao tác ngoài từ vựng đóng (cấm patch_json tùy ý)
        with self.assertRaises(TimelineValidationError):
            apply_ops(undone, [{"op": "patch_json", "path": "/scenes/0"}], base_rev=4)

        # Chặn xung đột phiên bản (stale base_rev)
        with self.assertRaises(TimelineConcurrencyError):
            apply_ops(undone, [{"op": "trim_scene", "scene_id": 1, "duration_ms": 3500}], base_rev=1)

        # Kiểm chứng Fail-Closed và biên dịch sang CapCut draft_content.json
        ok, errs = verify_voice_clip_alignment(undone)
        self.assertTrue(ok, f"Unexpected alignment errors: {errs}")
        draft = compile_capcut_draft(undone)
        self.assertEqual(draft["canvas_config"]["ratio"], "9:16")
        self.assertEqual(len(draft["tracks"]), 3)
        self.assertEqual(len(draft["materials"]["videos"]), 2)


if __name__ == "__main__":
    unittest.main()
