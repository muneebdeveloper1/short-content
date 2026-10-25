import asyncio
import json
import os
import re
import shutil

from modules.brain import ContentBrain
from modules.asset_manager import AssetManager
from modules.audio import AudioEngine
from modules.composer import Composer


def clean_cache():
    for relative in ("assets/audio_clips", "assets/video_clips", "assets/temp"):
        folder = os.path.join(os.getcwd(), relative)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
            except OSError as exc:
                print(f"Could not remove {path}: {exc}")


def get_settings():
    mode = os.getenv("VIDEO_MODE", "automatic").strip().lower()
    content_type = os.getenv("CONTENT_TYPE", "documentary").strip().lower() or "documentary"
    topic = os.getenv("VIDEO_TOPIC", "").strip()
    try:
        duration = float(os.getenv("VIDEO_LENGTH_MINUTES", "1") or "1")
    except ValueError:
        duration = 1.0
    duration = min(60.0, max(1.0, duration))
    upload = os.getenv("UPLOAD_TO_YOUTUBE", "true").strip().lower() in ("1", "true", "yes", "on")
    categories = ("documentary", "educational", "history", "science", "mystery", "motivation", "finance", "islamic_reminder", "news_explainer", "facts", "story", "custom")
    if content_type not in categories:
        content_type = "custom"
    if mode not in ("automatic", "manual"):
        mode = "automatic"
    print(f"Mode={mode} | category={content_type} | target={duration:g} minute(s) | upload={upload} (PRIVATE)")
    return mode, content_type, topic, duration, upload


async def run_pipeline():
    mode, content_type, topic, duration, upload = get_settings()
    brain = ContentBrain()
    if mode == "automatic":
        topic = brain.get_trending_topic(content_type)
    elif not topic:
        raise RuntimeError("Manual mode requires a topic. Enter one in the workflow's Topic field.")
    print(f"Topic: {topic}")

    script = brain.generate_script(topic, content_type, duration)
    if not script:
        raise RuntimeError("Script generation returned no scenes.")

    audio_engine = AudioEngine()
    script = await audio_engine.process_script(script)
    script = [scene for scene in script if scene.get("audio_path") and float(scene.get("duration", 0) or 0) > 0]
    if not script:
        raise RuntimeError("No narration scenes were generated successfully.")

    asset_manager = AssetManager()
    assets_map = asset_manager.get_videos(script)
    composer = Composer()
    scene_paths = composer.render_all_scenes(script, assets_map)
    if not scene_paths:
        raise RuntimeError("No scenes rendered successfully. Check Pexels API access and FFmpeg logs.")

    safe_topic = re.sub(r"[^a-zA-Z0-9_-]+", "_", topic).strip("_")[:65] or "video"
    output_name = f"{safe_topic}_{int(duration * 60)}s_16x9.mp4"
    final_path = composer.concatenate_with_transitions(scene_paths, output_name, composer.rendered_scenes)
    if not final_path or not os.path.isfile(final_path):
        raise RuntimeError("Final video rendering failed.")

    try:
        import ffmpeg
        probe = ffmpeg.probe(final_path)
        video_stream = next(s for s in probe["streams"] if s.get("codec_type") == "video")
        metadata = {
            "topic": topic, "content_type": content_type, "mode": mode,
            "target_minutes": duration,
            "actual_seconds": float(probe["format"].get("duration", 0)),
            "width": video_stream.get("width"), "height": video_stream.get("height"),
            "captions": "permanently burned-in word-highlight karaoke captions",
            "youtube_privacy": "private", "file": os.path.basename(final_path)
        }
        with open(os.path.join(composer.final_dir, "video_metadata.json"), "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)
        print("Output metadata:", json.dumps(metadata, ensure_ascii=False))
    except Exception as exc:
        print(f"Could not write video metadata: {exc}")

    if upload:
        from modules.youtube_uploader import upload_video
        upload_video(final_path, topic=topic, content_type=content_type, duration_minutes=duration)
    else:
        print("Upload disabled for this run.")
    clean_cache()


if __name__ == "__main__":
    asyncio.run(run_pipeline())
