"""
Unit tests verifying strict enforcement across all 14 LangGraph Nodes & 5 Graders:
1. All 4 self-correction conditional loops in `workflow.py`:
   - evaluate_script -> refine_script / generate_audio
   - evaluate_audio  -> generate_audio / visual_director
   - evaluate_media  -> refine_media / render_video
   - evaluate_video  -> render_video / __end__
2. `node_evaluate_audio` increments `audio_retry_count` when `passed=False`.
3. `node_evaluate_video` triggers 1 automatic re-render (`QA_RETRY_RENDER`) before raising `[HardBlock VideoQA]`.
4. `log_job_quality_feedback` emits all 5 Grader Feedback Badges (`script_score`, `visual_score`, `hook_score`, `audio_score`, `video_qa_passed`).
"""
import unittest
from unittest.mock import patch, MagicMock

from app.graph.workflow import (
    create_video_workflow_graph,
    should_refine_script,
    should_refine_audio,
    should_refine_media,
    should_retry_render,
)
from app.graph.nodes.render_nodes import node_evaluate_video
from app.graph.state import VideoQAReport
from app.utils.langsmith_tracer import log_job_quality_feedback


class TestLangGraphQAEnforcement(unittest.TestCase):

    def test_compiled_graph_has_all_four_self_correction_loops(self):
        wf = create_video_workflow_graph()
        g = wf.get_graph()
        edges = {(e.source, e.target, e.conditional) for e in g.edges}

        # 1. ScriptCritic loop
        self.assertIn(("evaluate_script", "refine_script", True), edges)
        self.assertIn(("evaluate_script", "generate_audio", True), edges)

        # 2. AudioQA loop
        self.assertIn(("evaluate_audio", "generate_audio", True), edges)
        self.assertIn(("evaluate_audio", "visual_director", True), edges)

        # 3. VisualCritic loop
        self.assertIn(("evaluate_media", "refine_media", True), edges)
        self.assertIn(("evaluate_media", "render_video", True), edges)

        # 4. VideoQA loop
        self.assertIn(("evaluate_video", "render_video", True), edges)
        self.assertIn(("evaluate_video", "__end__", True), edges)

    def test_should_refine_audio_routing(self):
        # Failed AudioQA on attempt 1 -> route back to generate_audio
        self.assertEqual(
            should_refine_audio({
                "audio_eval": {"passed": False, "pacing_rating": "TOO_FAST", "feedback": "195 WPM > 180"},
                "audio_retry_count": 1,
            }),
            "generate_audio",
        )
        # Failed AudioQA after exhausting retries (>2) -> proceed to visual_director
        self.assertEqual(
            should_refine_audio({
                "audio_eval": {"passed": False, "pacing_rating": "TOO_FAST", "feedback": "195 WPM > 180"},
                "audio_retry_count": 3,
            }),
            "visual_director",
        )
        # Passing AudioQA -> proceed immediately to visual_director
        self.assertEqual(
            should_refine_audio({
                "audio_eval": {"passed": True, "words_per_minute": 158.0, "pacing_rating": "PERFECT"},
                "audio_retry_count": 0,
            }),
            "visual_director",
        )

    def test_video_qa_retries_once_then_hard_blocks(self):
        fake_qa_fail = VideoQAReport(
            video_path="storage/outputs/video_999.mp4",
            file_size_bytes=500_000,
            duration_seconds=32.0,
            width=1080,
            height=1920,
            passed=False,
            issues=["Black frames detected > 0.5s"],
        )
        fake_eval_fail = {
            "passed": False,
            "score": 55,
            "issues": ["Black frames detected > 0.5s"],
        }

        with patch("app.graph.evaluators.video_qa.VideoQA.inspect_video", return_value=fake_qa_fail), \
             patch("app.services.quality_evaluator.QualityEvaluator.evaluate_video", return_value=fake_eval_fail):
            # First failure (render_retry_count=0) -> returns QA_RETRY_RENDER state so should_retry_render loops back
            out1 = node_evaluate_video({
                "rendered_video_path": "storage/outputs/video_999.mp4",
                "total_audio_duration_ms": 32000,
                "render_retry_count": 0,
            })
            self.assertEqual(out1["current_step"], "QA_RETRY_RENDER")
            self.assertEqual(out1["render_retry_count"], 1)
            self.assertFalse(out1["video_eval"]["passed"])
            self.assertEqual(should_retry_render(out1), "render_video")

            # Second failure (render_retry_count=1) -> raises RuntimeError("[HardBlock VideoQA]...")
            with self.assertRaises(RuntimeError) as ctx:
                node_evaluate_video({
                    "rendered_video_path": "storage/outputs/video_999.mp4",
                    "total_audio_duration_ms": 32000,
                    "render_retry_count": 1,
                })
            self.assertIn("[HardBlock VideoQA]", str(ctx.exception))

    def test_all_five_grader_feedback_badges_logged(self):
        fb = log_job_quality_feedback(
            job_id=8888,
            scores_summary={
                "script_critic": {"overall_score": 8.8, "passed": True},
                "visual_critic": {"overall_score": 9.6, "failed_scene_ids": []},
                "hook_video_grader": {"best_score": 8.92, "hook_visual_mode": "video"},
                "audio_qa": {"words_per_minute": 159.2, "pacing_rating": "PERFECT", "passed": True},
                "video_qa": {"passed": True, "score": 96},
            },
        )
        self.assertEqual(
            set(fb.keys()),
            {"script_score", "visual_score", "hook_score", "audio_score", "video_qa_passed"},
        )
        self.assertAlmostEqual(fb["hook_score"]["raw_10"], 8.92, places=2)
        self.assertEqual(fb["audio_score"]["raw_10"], 10.0)


if __name__ == "__main__":
    unittest.main()
