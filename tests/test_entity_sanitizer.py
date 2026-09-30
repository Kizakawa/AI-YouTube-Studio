import unittest
from app.agents.content_agent import ContentAgent
from app.bot.handlers import detect_best_channel_for_topic

class TestEntitySanitizer(unittest.TestCase):
    def setUp(self):
        self.agent = ContentAgent()

    def test_wildlife_bird_entity_sanitizer(self):
        """Kiểm tra kịch bản có chứa các ảo giác (human eye, dna helix) bị khử sạch khi làm về chim"""
        fake_hallucinated_json = """
        {
            "title": "Cảnh Chú Chim Đi Kiếm Ăn Nuôi Các Con",
            "topic": "cảnh chú chim đi kiếm ăn nuôi các con",
            "full_narration": "Chú chim mẹ bay đi tìm mồi trong rừng sâu để nuôi nấng bầy con thơ.",
            "scenes": [
                {
                    "scene_id": 1,
                    "narration": "Chim mẹ sải cánh bay vào rừng già.",
                    "stock_query": "bird flying forest",
                    "visual_keyword": "bird flight",
                    "visual_prompt": "Mother bird flying over forest canopy, 8k",
                    "media_type": "video",
                    "estimated_seconds": 3.0
                },
                {
                    "scene_id": 2,
                    "narration": "Ánh mắt tập trung quan sát từng ngọn cỏ.",
                    "stock_query": "human eye",
                    "visual_keyword": "human pupil",
                    "visual_prompt": "Extreme close-up of a human iris dilating with intense fear",
                    "media_type": "video",
                    "estimated_seconds": 3.0
                },
                {
                    "scene_id": 3,
                    "narration": "Bản năng sinh tồn được khắc sâu trong bộ gen.",
                    "stock_query": "dna helix",
                    "visual_keyword": "dna double helix",
                    "visual_prompt": "Glowing 3D DNA double helix structure rotating in void",
                    "media_type": "video",
                    "estimated_seconds": 3.0
                }
            ]
        }
        """
        script = self.agent._parse_json_script(fake_hallucinated_json)
        
        # Kiểm tra scene 2: human eye đã được thay thế bằng mắt chim
        sc2 = script.scenes[1]
        self.assertNotIn("human eye", sc2.stock_query.lower())
        self.assertNotIn("human iris", sc2.visual_prompt.lower())
        self.assertIn("bird", sc2.stock_query.lower())

        # Kiểm tra scene 3: dna helix đã được thay thế bằng chim con trong tổ
        sc3 = script.scenes[2]
        self.assertNotIn("dna helix", sc3.stock_query.lower())
        self.assertNotIn("dna double helix", sc3.visual_prompt.lower())
        self.assertIn("bird", sc3.stock_query.lower())

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
        self.assertEqual(ch, "ucsrjiil67qxidtxt70z-iwa")

        # Chủ đề What If Thế chiến -> @alternatehistorywhatif-e2x
        ch, _ = detect_best_channel_for_topic("What if Napoleon chiến thắng trận Waterloo?", "channel_maya")
        self.assertEqual(ch, "@alternatehistorywhatif-e2x")

if __name__ == "__main__":
    unittest.main()
