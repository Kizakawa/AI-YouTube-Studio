import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.agents.video_strategist_agent import VideoStrategistAgent
from app.agents.content_agent import ContentAgent

def main():
    strategist = VideoStrategistAgent()
    topic = "The Dark Psychology of Gaslighting: How Narcissists Twist Your Reality"
    
    # Test sample scenes
    scenes = [
        {
            "scene_id": 1,
            "narration": "If someone makes you doubt your sanity, beware.",
            "visual_keyword": "intense cold stare in shadow",
            "visual_prompt": "Extreme macro close-up of cold calculating unblinking eye in dark chiaroscuro room, 8k cinematic",
            "stock_query": "cold stare eye shadow",
            "media_type": "ai_image",
            "estimated_seconds": 3.0
        },
        {
            "scene_id": 2,
            "narration": "They rewrite history so smoothly you question yourself.",
            "visual_keyword": "man whispering behind shadow",
            "visual_prompt": "Shadowy figure whispering sinister lies from the darkness, chiaroscuro noir, 8k cinematic",
            "stock_query": "shadow silhouette whispering",
            "media_type": "ai_image",
            "estimated_seconds": 3.5
        },
        {
            "scene_id": 3,
            "narration": "A ticking clock in an empty room, time slipping away.",
            "visual_keyword": "vintage clock ticking fast",
            "visual_prompt": "Vintage clock hands spinning rapidly, dust particles floating in sunlight, 8k cinematic",
            "stock_query": "clock ticking vintage",
            "media_type": "video",
            "estimated_seconds": 3.0
        },
        {
            "scene_id": 4,
            "narration": "Save this video before your next debate.",
            "visual_keyword": "stoic marble statue unmoving",
            "visual_prompt": "Ancient Stoic philosopher marble bust in dramatic museum rim lighting, 8k vertical",
            "stock_query": "stoic statue marble",
            "media_type": "ai_image",
            "estimated_seconds": 4.0
        }
    ]

    proposal = strategist.propose_production_strategy(
        topic=topic,
        scenes=scenes,
        primary_entity=None,
        entity_type="general",
        channel_niche="psychology"
    )

    print("\n=== TOPIC CLASSIFICATION ===")
    print(proposal.get("topic_classification"))
    print("\n=== TESTING CONTENT AGENT SCRIPT GENERATION ===")
    content_agent = ContentAgent()
    script = content_agent.generate_script(
        topic="5 Signs Someone is Secretly Manipulating You",
        channel_niche="psychology",
        language="en"
    )
    print(f"Title: {script.title}")
    print(f"Total scenes: {len(script.scenes)}")
    for sc in script.scenes:
        print(f"Scene {sc.scene_id}: media_type='{sc.media_type}' | query='{sc.stock_query}' | narr='{sc.narration}'")

if __name__ == "__main__":
    main()
