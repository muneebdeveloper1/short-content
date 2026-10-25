import os
import json
from google import genai
from dotenv import load_dotenv

load_dotenv()

CONTENT_GUIDANCE = {
    "documentary": "Cinematic mini-documentary: clear facts, narrative progression, credible tone.",
    "educational": "Educational explainer: explain the idea simply, use examples, build understanding.",
    "history": "History story: establish time and place, conflict, surprising detail, consequence.",
    "science": "Science and nature: accurate explanations, vivid real-world examples, avoid unsupported claims.",
    "mystery": "Mystery and strange events: intriguing hook, evidence, competing explanations, satisfying reveal.",
    "motivation": "Motivational story: specific struggle, turning point, practical takeaway; avoid empty clichés.",
    "finance": "Personal finance education: explain concepts, risks, and practical examples; do not promise returns.",
    "islamic_reminder": "Respectful Islamic reminder grounded in well-known Quranic themes and authentic teachings; do not invent quotations or references.",
    "news_explainer": "Evergreen news-style explainer; distinguish established facts from uncertainty and do not fabricate current events.",
    "facts": "Surprising but verifiable facts, with context and a memorable payoff.",
    "story": "Original short narrative with a strong hook, rising tension, and a clear ending.",
    "custom": "Follow the user's topic and use an engaging, accurate structure appropriate to it."
}

def _get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment or GitHub Actions secrets.")
    return genai.Client(api_key=api_key)

def _get_model_cascade():
    chosen = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    return list(dict.fromkeys([chosen, "gemini-2.0-flash-lite", "gemini-1.5-flash"]))

class ContentBrain:
    def get_trending_topic(self, content_type="documentary"):
        prompt = (
            f"Suggest one specific, compelling YouTube topic for a {content_type} video. "
            "Return only the topic, no quotation marks or explanation."
        )
        client = _get_client()
        for model in _get_model_cascade():
            try:
                result = client.models.generate_content(model=model, contents=prompt)
                if result.text and result.text.strip():
                    return result.text.strip()
            except Exception as exc:
                print(f"⚠️ Topic model {model} failed: {exc}")
        raise RuntimeError("All configured Gemini models failed to select a topic.")

    def generate_script(self, topic, content_type="documentary", target_minutes=1):
        target_minutes = min(60.0, max(0.25, float(target_minutes)))
        guidance = CONTENT_GUIDANCE.get(content_type, CONTENT_GUIDANCE["custom"])
        # Long scripts are generated in <=3-minute sections to avoid a single huge,
        # unreliable model response. Scenes are renumbered globally afterward.
        parts = []
        remaining = target_minutes
        while remaining > 0.001:
            part_minutes = min(3.0, remaining)
            parts.append(part_minutes)
            remaining -= part_minutes
        all_scenes = []
        client = _get_client()
        for part_index, part_minutes in enumerate(parts, 1):
            target_words = max(45, int(part_minutes * 130))
            scene_count = max(8, min(45, round(part_minutes * 60 / 4.5)))
            if len(parts) == 1:
                narrative_instruction = "Make this a complete video with a hook, clear progression, and satisfying ending."
            elif part_index == 1:
                narrative_instruction = "This is the opening section: establish the hook and premise, but do not force the full ending yet."
            elif part_index == len(parts):
                narrative_instruction = "This is the final section: continue naturally and provide the satisfying conclusion."
            else:
                narrative_instruction = "This is a middle section: continue the narrative naturally without restarting the hook or concluding the whole video."
            prompt = f"""
You are an experienced YouTube writer and visual story editor.
CONTENT TYPE: {content_type}
STYLE GUIDANCE: {guidance}
TOPIC: {topic}
VIDEO TARGET: about {target_minutes:g} minute(s) total.
THIS SECTION: {part_index} of {len(parts)}, about {part_minutes:g} minute(s).
TARGET NARRATION FOR THIS SECTION: about {target_words} words, roughly 130 words/minute.
Create approximately {scene_count} short scenes for this section so visuals can change about every 4–5 seconds.
{narrative_instruction}
Write narration that sounds natural aloud. Avoid repetitive filler. Use accurate claims; do not invent quotes, sources, statistics, or breaking news.
For each scene include two distinct literal stock-footage search queries (visual_1 and visual_2).
Aim for approximately {target_words} total narration words across this section.
Do not include markdown or commentary. Return ONLY a valid JSON array of objects with:
id (integer), text (narration string), visual_1 (stock footage query), visual_2 (different query), mood (short label).
"""
            section = None
            for model in _get_model_cascade():
                try:
                    response = client.models.generate_content(model=model, contents=prompt)
                    clean = (response.text or "").replace("```json", "").replace("```", "").strip()
                    data = json.loads(clean)
                    if not isinstance(data, list) or not data:
                        raise ValueError("Expected a non-empty JSON array.")
                    section = data
                    print(f"✅ Generated section {part_index}/{len(parts)} with {model}: {len(data)} scenes.")
                    break
                except Exception as exc:
                    print(f"⚠️ Script model {model} failed on section {part_index}/{len(parts)}: {exc}")
            if section is None:
                raise RuntimeError(f"All configured Gemini models failed on script section {part_index}/{len(parts)}.")
            for scene in section:
                scene["id"] = len(all_scenes) + 1
                scene.setdefault("visual_1", scene.get("keywords", topic))
                scene.setdefault("visual_2", scene["visual_1"])
                scene.setdefault("text", "")
                all_scenes.append(scene)
        print(f"✨ Script ready: {len(all_scenes)} scenes for target {target_minutes:g} min.")
        return all_scenes
