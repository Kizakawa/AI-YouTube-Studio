import unittest
from app.agents.content_agent import ContentAgent
from app.bot.handlers import detect_best_channel_for_topic

class TestEntitySanitizer(unittest.TestCase):
    def setUp(self):
        self.agent = ContentAgent()

    def test_non_destructive_script_parsing(self):
        """Kiểm tra kịch bản giữ nguyên 100% quyết định thị giác của Agent, không bị ghi đè bởi từ khóa."""
        vaccine_script_json = """
        {
            "title": "Bí Ẩn Vắc-xin Đậu Bò Của Edward Jenner",
            "topic": "Edward Jenner phát minh vắc xin đậu mùa từ đậu bò và dã thú",
            "full_narration": "Bác sĩ Edward Jenner phát hiện những người vắt sữa bò không mắc bệnh đậu mùa.",
            "scenes": [
                {
                    "scene_id": 1,
                    "narration": "Bác sĩ Edward Jenner dùng kim tiêm dịch đậu bò vào cánh tay cậu bé.",
                    "stock_query": "doctor syringe injection child",
                    "visual_keyword": "doctor syringe injection",
                    "visual_prompt": "18th century doctor carefully injecting a child with syringe, realistic lighting",
                    "media_type": "video",
                    "estimated_seconds": 3.5
                },
                {
                    "scene_id": 2,
                    "narration": "Người dân lo sợ thuốc sẽ biến con người thành quái thú hay dã thú trong rừng.",
                    "stock_query": "angry mob with torches vintage",
                    "visual_keyword": "angry mob vintage",
                    "visual_prompt": "Colonial angry crowd with torches protesting outside clinic",
                    "media_type": "video",
                    "estimated_seconds": 3.5
                }
            ]
        }
        """
        script = self.agent._parse_json_script(vaccine_script_json)

        # Kiểm tra scene 1: giữ nguyên ngữ cảnh tiêm thuốc của bác sĩ, tuyệt đối KHÔNG bị biến thành thú rừng
        sc1 = script.scenes[0]
        self.assertEqual(sc1.stock_query, "doctor syringe injection child")
        self.assertIn("doctor", sc1.visual_prompt.lower())
        self.assertNotIn("wild animals in nature", sc1.stock_query.lower())
        self.assertNotIn("wildlife habitat", sc1.visual_keyword.lower())

        # Kiểm tra scene 2: giữ nguyên đám đông phẫn nộ, không bị ghi đè thành thú rừng chạy
        sc2 = script.scenes[1]
        self.assertEqual(sc2.stock_query, "angry mob with torches vintage")
        self.assertIn("angry crowd", sc2.visual_prompt.lower())
        self.assertNotIn("wild animals in nature", sc2.stock_query.lower())

    def test_channel_routing(self):
        """Kiểm tra điều hướng kênh thông minh theo chủ đề"""
        # Chủ đề chim muông -> ch_wild_odyssey
        ch, reason = detect_best_channel_for_topic("Cảnh chú chim đi kiếm ăn nuôi các con", "channel_maya")
        self.assertEqual(ch, "ch_wild_odyssey")
        self.assertIsNotNone(reason)

        # Chủ đề cứu hộ cún cưng -> ch_paws_hearts_rescue
        ch, _ = detect_best_channel_for_topic("Giải cứu chú chó con bị bỏ rơi trong mưa", "channel_maya")
        self.assertEqual(ch, "ch_paws_hearts_rescue")

        # Chủ đề rãnh Mariana -> ucsrjiil67qxidtxt70z-iwa
        ch, _ = detect_best_channel_for_topic("Bí ẩn đáy vực Mariana sâu 11000m", "channel_maya")
        self.assertIn(ch, ("ucsrjiil67qxidtxt70z-iwa", "ch_the_mysteries_of_the_world"))

        # Chủ đề What If Thế chiến -> @alternatehistorywhatif-e2x
        ch, _ = detect_best_channel_for_topic("What if Napoleon chiến thắng trận Waterloo?", "channel_maya")
        self.assertEqual(ch, "@alternatehistorywhatif-e2x")

if __name__ == "__main__":
    unittest.main()
