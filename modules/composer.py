import os
import random
import re
import ffmpeg


class Composer:
    def __init__(self):
        self.temp_dir = os.path.join(os.getcwd(), "assets", "temp")
        self.final_dir = os.path.join(os.getcwd(), "assets", "final")
        self.avatar_path = os.path.join(os.getcwd(), "assets", "avatar", "avatars.mp4")
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.final_dir, exist_ok=True)
        self.transitions = ["fade", "wipeleft", "wiperight", "smoothleft"]
        self.width, self.height, self.fps = 1920, 1080, 30
        self.visual_change_seconds = 4.5
        self.transition_seconds = 0.25

    def get_duration(self, filepath):
        try:
            return float(ffmpeg.probe(filepath)["format"]["duration"])
        except Exception:
            return 0.0

    def _fit_landscape(self, stream):
        return (stream.filter("scale", self.width, self.height, force_original_aspect_ratio="increase")
                     .filter("crop", self.width, self.height)
                     .filter("setsar", "1")
                     .filter("fps", fps=self.fps, round="up"))

    def process_scene(self, scene, video_pair, is_avatar=False):
        scene_id = scene["id"]
        audio_path = scene.get("audio_path")
        total_duration = float(scene.get("duration", 0) or 0)
        if not audio_path or total_duration <= 0:
            print(f"Scene {scene_id} has no valid narration; skipping.")
            return None
        output_path = os.path.join(self.temp_dir, f"scene_{scene_id}.mp4")
        try:
            if is_avatar:
                source = ffmpeg.input(video_pair[0], stream_loop=-1).video
                video_stream = self._fit_landscape(source.filter("trim", duration=total_duration).setpts("PTS-STARTPTS"))
                video_stream = video_stream.filter("trim", duration=total_duration).setpts("PTS-STARTPTS")
            else:
                path_a, path_b = video_pair
                remaining, index, pieces = total_duration, 0, []
                while remaining > 0.02:
                    segment_duration = min(self.visual_change_seconds, remaining)
                    source_path = path_a if index % 2 == 0 else path_b
                    source = ffmpeg.input(source_path, stream_loop=-1).video
                    segment = source.filter("trim", duration=segment_duration).setpts("PTS-STARTPTS")
                    segment = self._fit_landscape(segment).filter("trim", duration=segment_duration).setpts("PTS-STARTPTS")
                    pieces.append(segment)
                    remaining -= segment_duration
                    index += 1
                video_stream = pieces[0] if len(pieces) == 1 else ffmpeg.concat(*pieces, v=1, a=0)
            audio_stream = ffmpeg.input(audio_path).audio
            (ffmpeg.output(video_stream, audio_stream, output_path, vcodec="libx264", acodec="aac",
                           pix_fmt="yuv420p", r=self.fps, movflags="faststart",
                           preset=os.getenv("FFMPEG_PRESET", "veryfast"))
             .run(overwrite_output=True, quiet=True))
            return output_path
        except ffmpeg.Error as exc:
            stderr = (exc.stderr or b"").decode("utf8", errors="replace")
            print(f"Render failed for scene {scene_id}: {stderr[-2500:]}")
            return None
        except Exception as exc:
            print(f"Render failed for scene {scene_id}: {exc}")
            return None

    def render_all_scenes(self, script_data, video_pairs):
        rendered = []
        self.rendered_scenes = []
        avatar_indices = []
        if len(script_data) >= 4 and os.path.exists(self.avatar_path) and os.getenv("USE_AVATAR", "false").lower() == "true":
            candidates = list(range(1, len(script_data) - 1))
            avatar_indices = sorted(random.sample(candidates, min(2, len(candidates))))
        for i, scene in enumerate(script_data):
            pair = video_pairs[i] if i < len(video_pairs) else None
            is_avatar = i in avatar_indices
            if is_avatar:
                pair = (self.avatar_path, None)
            if not pair:
                print(f"Skipping scene {scene.get('id', i + 1)}: no footage.")
                continue
            output = self.process_scene(scene, pair, is_avatar)
            if output:
                rendered.append(output)
                self.rendered_scenes.append(scene)
        return rendered

    @staticmethod
    def _ass_time(seconds):
        cs = int(round(max(0.0, float(seconds)) * 100))
        hours, cs = divmod(cs, 360000)
        minutes, cs = divmod(cs, 6000)
        secs, cs = divmod(cs, 100)
        return f"{hours}:{minutes:02}:{secs:02}.{cs:02}"

    @staticmethod
    def _clean_text(text):
        text = str(text).replace("{", "(").replace("}", ")")
        return re.sub(r"\s+", " ", text).strip()

    def _write_karaoke_ass(self, script_data, output_path):
        # Each timed event shows a short phrase; the spoken word changes to yellow.
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,100,100,78,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        cursor = 0.0
        for scene_index, scene in enumerate(script_data):
            duration = float(scene.get("duration", 0) or 0)
            words = self._clean_text(scene.get("text", "")).split()
            if duration <= 0 or not words:
                cursor += max(0.0, duration - self.transition_seconds)
                continue
            chunks = [words[i:i + 5] for i in range(0, len(words), 5)]
            weights = [sum(max(1, len(w.strip(".,!?;:()\"'"))) for w in chunk) for chunk in chunks]
            total_weight = max(1, sum(weights))
            local_time = 0.0
            for chunk, chunk_weight in zip(chunks, weights):
                chunk_duration = duration * chunk_weight / total_weight
                word_weights = [max(1, len(w.strip(".,!?;:()\"'"))) for w in chunk]
                word_total = max(1, sum(word_weights))
                word_time = local_time
                for active_index, (word, weight) in enumerate(zip(chunk, word_weights)):
                    word_duration = chunk_duration * weight / word_total
                    visible = []
                    for j, item in enumerate(chunk):
                        visible.append((r"{\c&H0000FFFF&}" + item + r"{\c&H00FFFFFF&}") if j == active_index else item)
                    events.append(
                        f"Dialogue: 0,{self._ass_time(cursor + word_time)},{self._ass_time(cursor + word_time + max(0.04, word_duration))},Karaoke,,0,0,0,,{' '.join(visible)}"
                    )
                    word_time += word_duration
                local_time += chunk_duration
            cursor += max(0.0, duration - self.transition_seconds) if scene_index < len(script_data) - 1 else duration
        with open(output_path, "w", encoding="utf-8-sig") as handle:
            handle.write(header + "\n".join(events) + "\n")
        return output_path

    def concatenate_with_transitions(self, video_paths, output_filename="final_video.mp4", script_data=None):
        print("Stitching 16:9 video and permanently burning word-highlight karaoke captions...")
        output_path = os.path.join(self.final_dir, output_filename)
        if not video_paths:
            print("No rendered scenes to stitch.")
            return None
        try:
            inputs = [ffmpeg.input(path) for path in video_paths]
            v_stream, a_stream = inputs[0].video, inputs[0].audio
            current_duration = self.get_duration(video_paths[0])
            for i in range(1, len(inputs)):
                next_duration = self.get_duration(video_paths[i])
                trans_duration = min(self.transition_seconds, max(0.08, next_duration / 5))
                offset = max(0.0, current_duration - trans_duration)
                v_stream = ffmpeg.filter([v_stream, inputs[i].video], "xfade",
                                          transition=random.choice(self.transitions), duration=trans_duration, offset=offset)
                a_stream = ffmpeg.filter([a_stream, inputs[i].audio], "acrossfade", d=trans_duration)
                current_duration += next_duration - trans_duration
            if script_data:
                ass_path = os.path.abspath(os.path.join(self.temp_dir, "karaoke_captions.ass"))
                self._write_karaoke_ass(script_data, ass_path)
                v_stream = v_stream.filter("subtitles", filename=ass_path)
            (ffmpeg.output(v_stream, a_stream, output_path, vcodec="libx264", acodec="aac",
                           pix_fmt="yuv420p", r=self.fps, movflags="faststart",
                           preset=os.getenv("FFMPEG_PRESET", "veryfast"))
             .run(overwrite_output=True, quiet=False))
            print(f"FINAL VIDEO SAVED: {output_path}")
            return output_path
        except ffmpeg.Error as exc:
            stderr = (exc.stderr or b"").decode("utf8", errors="replace")
            print(f"Stitching error: {stderr[-5000:]}")
            return None
