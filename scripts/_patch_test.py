
with open('tests/test_system_wide_upgrade.py', 'rb') as f:
    content = f.read()

start_marker = b'    def test_linter_approves_perfect_script(self):'
end_marker = b'\r\n\r\n    def test_publisher_agent'
start = content.find(start_marker)
end = content.find(end_marker, start)

new_block = (
    b'    def test_linter_approves_perfect_script(self):\r\n'
    b'        niche = get_niche("deep_sea_earth_mysteries")\r\n'
    b'        # Script concise ~70 words: fits the 50-85 word linter window\r\n'
    b'        scenes_good = [\r\n'
    b'            SceneContract(scene_id=1, narration="What if the ocean hides something truly impossible?", visual_keyword="ocean", estimated_seconds=3.0),\r\n'
    b'            SceneContract(scene_id=2, narration="Navy sonar detected a massive acoustic signal in 1997.", visual_keyword="sonar", estimated_seconds=3.5),\r\n'
    b'            SceneContract(scene_id=3, narration="It was louder than any known creature on Earth.", visual_keyword="whale", estimated_seconds=3.5),\r\n'
    b'            SceneContract(scene_id=4, narration="Scientists rushed to decode the frequency, then recordings vanished.", visual_keyword="scientists", estimated_seconds=3.5),\r\n'
    b'            SceneContract(scene_id=5, narration="Naval vessels were ordered back with no official explanation.", visual_keyword="sub", estimated_seconds=3.5),\r\n'
    b'            SceneContract(scene_id=6, narration="What do you think is hiding in that trench? The mystery remains.", visual_keyword="abyss", estimated_seconds=3.5),\r\n'
    b'        ]\r\n'
    b'        script_good = ScriptContract(\r\n'
    b'            title="Classified Deep Sea Sonar",\r\n'
    b'            hook="What if the ocean hides something truly impossible?",\r\n'
    b'            full_narration=" ".join(s.narration for s in scenes_good),\r\n'
    b'            scenes=scenes_good,\r\n'
    b'            tags=["shorts", "mystery"]\r\n'
    b'        )\r\n'
    b'        res = ScriptValidationLinter.lint(script_good, niche)\r\n'
    b'        self.assertTrue(res.is_valid)\r\n'
    b'        self.assertEqual(len(res.issues), 0)'
)

new_content = content[:start] + new_block + content[end:]
with open('tests/test_system_wide_upgrade.py', 'wb') as f:
    f.write(new_content)
print("PATCH OK")
