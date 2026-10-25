import json
import os
import re
from typing import Any, Dict, List

from dotenv import load_dotenv
from google import genai

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
    "custom": "Follow the user's topic and use an engaging, accurate structure appropriate to it.",
}

# Requested model priority. If Gemini 3.8 is not available to this API key,
# the code falls back in the exact order below.
MODEL_FALLBACK_ORDER = [
    "gemini-3.8",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


def _get_client():
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add it under GitHub Settings > Secrets and variables > Actions."
        )
    return genai.Client(api_key=api_key)


def _normalise_model_name(name: str) -> str:
    name = (name or "").strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name


def _get_model_cascade(client=None) -> List[str]:
    """Return the requested model sequence in strict priority order.

    A non-empty GEMINI_MODEL secret/variable can be used to override the first
    model, but blank values are ignored. The requested fallback sequence always
    follows, without auto-discovery reordering it.
    """
    configured = _normalise_model_name(os.getenv("GEMINI_MODEL", ""))
    candidates: List[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(MODEL_FALLBACK_ORDER)
    return list(dict.fromkeys(name for name in candidates if name))


def _response_text(response) -> str:
    value = getattr(response, "text", None)
    if not value:
        raise ValueError("Gemini returned an empty response.")
    return value.strip()


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        # Recover a JSON array if the model included a short preamble or trailing text.
        start, end = clean.find("["), clean.rfind("]")
        if start < 0 or end <= start:
            raise
        data = json.loads(clean[start:end + 1])
    if not isinstance(data, list) or not data:
        raise ValueError("Expected a non-empty JSON array of scenes.")
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Every scene must be a JSON object.")
        item.setdefault("text", "")
        item.setdefault("visual_1", "")
        item.setdefault("visual_2", item.get("visual_1", ""))
        item.setdefault("mood", "intriguing")
    return data


class ContentBrain:
    def get_trending_topic(self, content_type="documentary"):
        prompt = (
            f"Suggest one specific, compelling YouTube topic for a {content_type} video. "
            "Return only the topic, with no quotation marks or explanation."
        )
        client = _get_client()
        errors = []
        for model in _get_model_cascade(client):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                topic = _response_text(response)
                if topic:
                    print(f"✅ Topic selected using {model}.")
                    return topic
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                print(f"⚠️ Topic model {model} failed: {exc}")
        raise RuntimeError("All available Gemini models failed to select a topic. " + " | ".join(errors[-3:]))

    def generate_script(self, topic, content_type="documentary", target_minutes=1):
        target_minutes = min(60.0, max(0.25, float(target_minutes)))
        content_type = (content_type or "documentary").strip().lower()
        guidance = CONTENT_GUIDANCE.get(content_type, CONTENT_GUIDANCE["custom"])

        parts = []
        remaining = target_minutes
        while remaining > 0.001:
            part_minutes = min(3.0, remaining)
            parts.append(part_minutes)
            remaining -= part_minutes

        client = _get_client()
        models = _get_model_cascade(client)
        all_scenes = []

        for part_index, part_minutes in enumerate(parts, 1):
            target_words = max(45, int(part_minutes * 130))
            scene_count = max(6, min(45, round(part_minutes * 60 / 4.5)))
            if len(parts) == 1:
                narrative_instruction = "Make this a complete video with a strong hook, clear progression, and satisfying ending."
            elif part_index == 1:
                narrative_instruction = "Opening section: establish the hook and premise; do not force the full ending yet."
            elif part_index == len(parts):
                narrative_instruction = "Final section: continue naturally and provide the satisfying conclusion."
            else:
                narrative_instruction = "Middle section: continue naturally without restarting the hook or concluding the whole video."

            prompt = f"""
You are an experienced YouTube scriptwriter and visual story editor.
CONTENT TYPE: {content_type}
STYLE GUIDANCE: {guidance}
TOPIC: {topic}
TOTAL VIDEO TARGET: about {target_minutes:g} minute(s).
THIS SECTION: {part_index} of {len(parts)}, about {part_minutes:g} minute(s).
TARGET NARRATION: about {target_words} words in this section, approximately 130 words per minute.
Create approximately {scene_count} short scenes so visuals can change every 4–5 seconds.
{narrative_instruction}
Use natural spoken narration and a compelling opening. Keep claims accurate and distinguish facts from estimates.
For every scene include two different literal stock-footage search queries: visual_1 and visual_2.
Do not include markdown or commentary. Return ONLY a valid JSON array of objects with these fields:
id (integer), text (narration string), visual_1 (stock footage query), visual_2 (different query), mood (short label).
Aim for approximately {target_words} total narration words across this section.
""".strip()

            section = None
            errors = []
            for model in models:
                try:
                    response = client.models.generate_content(model=model, contents=prompt)
                    section = _parse_json_array(_response_text(response))
                    print(f"✅ Generated section {part_index}/{len(parts)} using {model}: {len(section)} scenes.")
                    break
                except Exception as exc:
                    errors.append(f"{model}: {exc}")
                    print(f"⚠️ Script model {model} failed on section {part_index}/{len(parts)}: {exc}")

            if section is None:
                details = " | ".join(errors[-4:])
                raise RuntimeError(
                    f"All configured Gemini models failed on script section {part_index}/{len(parts)}. "
                    f"Check that GEMINI_API_KEY is valid and that the Gemini API is enabled. Recent errors: {details}"
                )

            for scene in section:
                scene["id"] = len(all_scenes) + 1
                scene["text"] = str(scene.get("text", "")).strip()
                scene["visual_1"] = str(scene.get("visual_1") or topic).strip()
                scene["visual_2"] = str(scene.get("visual_2") or scene["visual_1"]).strip()
                scene["mood"] = str(scene.get("mood") or "intriguing").strip()
                all_scenes.append(scene)

        print(f"✨ Script ready: {len(all_scenes)} scenes for target {target_minutes:g} minute(s).")
        return all_scenes


if __name__ == "__main__":
    brain = ContentBrain()
    topic = brain.get_trending_topic("documentary")
    script = brain.generate_script(topic, "documentary", 1)
    with open("script.json", "w", encoding="utf-8") as handle:
        json.dump(script, handle, indent=2, ensure_ascii=False)
    print("✅ Saved script.json")
