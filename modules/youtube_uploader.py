import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _json_env(*names):
    import os
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                return json.loads(raw), name
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{name} must contain valid JSON.") from exc
    raise RuntimeError("Missing YouTube secret. Add YOUTUBE_TOKEN_JSON and YOUTUBE_CLIENT_SECRET_JSON under Settings > Secrets and variables > Actions.")


def upload_video(video_path, topic, content_type="documentary", duration_minutes=1):
    token_info, token_name = _json_env("YOUTUBE_TOKEN_JSON", "YOUTUBE_TOKEN_JSON_BOOKS")
    try:
        client_info, _ = _json_env("YOUTUBE_CLIENT_SECRET_JSON", "YOUTUBE_CLIENT_JSON")
        client_info = client_info.get("installed") or client_info.get("web") or client_info
    except RuntimeError:
        client_info = {}
    client_id = token_info.get("client_id") or client_info.get("client_id")
    client_secret = token_info.get("client_secret") or client_info.get("client_secret")
    token_uri = token_info.get("token_uri") or client_info.get("token_uri") or "https://oauth2.googleapis.com/token"
    if not client_id or not client_secret:
        raise RuntimeError("OAuth client_id/client_secret missing. Check YOUTUBE_TOKEN_JSON and YOUTUBE_CLIENT_SECRET_JSON.")
    credentials = Credentials(
        token=token_info.get("token"), refresh_token=token_info.get("refresh_token"),
        token_uri=token_uri, client_id=client_id, client_secret=client_secret, scopes=SCOPES
    )
    if not credentials.valid:
        if not credentials.refresh_token:
            raise RuntimeError(f"{token_name} has no refresh_token. Re-authorize YouTube and update the secret.")
        credentials.refresh(Request())

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    title = (topic.strip() or "New video")[:95]
    description = (f"{topic}\n\nContent type: {content_type}\nTarget length: {duration_minutes:g} minute(s)\n\nCreated with an automated video production workflow.")[:4900]
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["education", "documentary", content_type.replace("_", " ")],
            "categoryId": "27"
        },
        # Always private. Change visibility manually on YouTube after review.
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload: {int(status.progress() * 100)}%")
    video_id = response.get("id")
    print(f"Uploaded privately to YouTube. Video ID: {video_id}")
    print(f"https://www.youtube.com/watch?v={video_id}")
    return video_id
