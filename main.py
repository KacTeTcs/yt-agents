"""AI science-facts YouTube pipeline (US audience):
Ideas -> Script -> Fact-check -> Edit -> Upload (long + Short) -> Analyze"""
import os, re, json, asyncio, subprocess, textwrap, pathlib, requests, feedparser
import anthropic, edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

MODEL = "claude-sonnet-5-5"
VOICE = "en-US-AndrewNeural"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CHANNEL = "an English-language YouTube channel for a US audience about science and amazing facts"
STATE = pathlib.Path("state.json")
W = pathlib.Path("work"); W.mkdir(exist_ok=True)
claude = anthropic.Anthropic()

def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"topics": [], "lessons": []}

def ask(system, user, tokens=4000):
    r = claude.messages.create(model=MODEL, max_tokens=tokens, system=system,
                               messages=[{"role": "user", "content": user}])
    return r.content[0].text

def ask_json(system, user, tokens=4000):
    t = ask(system + "\nReturn ONLY valid JSON, no other text.", user, tokens)
    return json.loads(re.search(r"[\[{].*[\]}]", t, re.S).group(0))

def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)

def yt_service():
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"])
    return build("youtube", "v3", credentials=creds)

# ---------- 6. ANALYST ----------
def agent_analyst(yt, state):
    try:
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=15).execute()["items"]
        ids = ",".join(i["contentDetails"]["videoId"] for i in items)
        if not ids: return
        vids = yt.videos().list(part="snippet,statistics", id=ids).execute()["items"]
        data = [{"title": v["snippet"]["title"], **v["statistics"]} for v in vids]
        res = ask_json(f"You are a YouTube analyst for {CHANNEL}. From the stats, find what works "
            "(topics, title styles, long vs Shorts). "
            'JSON: {"lessons": ["5 short, specific, actionable lessons"]}', json.dumps(data))
        state["lessons"] = res["lessons"]
        print("Lessons:", state["lessons"])
    except Exception as e:
        print("Analyst skipped:", e)

# ---------- 1. IDEA AGENT ----------
def agent_ideas(state):
    f = feedparser.parse("https://trends.google.com/trending/rss?geo=US")
    trends = [e.title for e in f.entries[:20]]
    return ask_json(
        f"You are the idea strategist for {CHANNEL}. Propose ONE video topic with a strong curiosity gap "
        "and mass appeal (mind-blowing facts, space, human body, animals, physics, nature, tech). "
        "Use the trending list only as inspiration if something fits. Avoid: medical advice, politics, "
        "tragedies, conspiracy theories, pseudoscience, anything unverifiable. Never repeat used topics. "
        "Apply the lessons. "
        'JSON: {"topic":"","angle":"","hook":"","title":"<=70 chars, curiosity-driven, honest","thumb_text":"max 4 words"}',
        json.dumps({"trends": trends, "used": state["topics"][-40:], "lessons": state["lessons"]}))

# ---------- 2. SCRIPT AGENT ----------
def agent_script(idea):
    return ask_json(
        f"You are a top scriptwriter for {CHANNEL}. Write an 8-10 minute voiceover (1100-1300 words), "
        "conversational, vivid, American English. Structure: HOOK in the first 10 seconds (bold claim or question) "
        "-> open a curiosity loop that pays off at the end -> 6-7 escalating facts, each with a vivid analogy or "
        "'here's how to picture it' (original insight, not a list of trivia) -> a re-hook around the middle -> "
        "payoff -> short CTA (subscribe). Use ONLY well-established facts. Never invent numbers, quotes or studies; "
        "if unsure say 'scientists estimate'. 24-30 scenes. Each scene: 'text' (40-55 words, spoken) and 'query' "
        "(2-3 concrete English words for stock video search, e.g. 'galaxy stars', 'human brain'). "
        'JSON: {"scenes":[{"text":"","query":""}]}',
        json.dumps(idea), tokens=8000)

# ---------- 3. FACT-CHECK AGENT ----------
def agent_factcheck(script):
    return ask_json(
        "You are a rigorous science fact-checker. Review every claim in the narration. Fix or remove anything "
        "inaccurate, overstated, outdated or unverifiable (soften with 'scientists estimate' when needed). "
        "Keep the structure, tone, length and each scene's 'query'. "
        'Return the same JSON: {"scenes":[{"text":"","query":""}]}',
        json.dumps(script), tokens=8000)

# ---------- 4. EDITOR ----------
async def tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(str(path))

def duration(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())

def stock(query, path, min_w):
    r = requests.get("https://pixabay.com/api/videos/",
        params={"key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 10, "safesearch": "true"},
        timeout=30).json()
    for hit in r.get("hits", []):
        for size in ("medium", "large", "small"):
            v = hit["videos"].get(size, {})
            if v.get("url") and v.get("width", 0) >= min_w:
                path.write_bytes(requests.get(v["url"], timeout=120).content)
                return True
    return False

def build_video(scenes, w, h, tag, min_w):
    clips = []
    wide = w > h
    fs, wrap, margin = (44, 45, int(h * 0.2)) if wide else (58, 26, int(h * 0.28))
    for i, sc in enumerate(scenes):
        a, v, sub, out = (W / f"{tag}_a{i}.mp3", W / f"{tag}_v{i}.mp4", W / f"{tag}_s{i}.txt", W / f"{tag}_c{i}.mp4")
        asyncio.run(tts(sc["text"], a))
        d = duration(a) + 0.3
        if not stock(sc["query"], v, min_w) and not stock("galaxy stars", v, min_w):
            raise RuntimeError("No stock video for: " + sc["query"])
        sub.write_text("\n".join(textwrap.wrap(sc["text"], wrap)[:3]))
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,"
              f"drawtext=fontfile={FONT}:textfile={sub}:fontcolor=white:fontsize={fs}:"
              f"box=1:boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h-{margin}:line_spacing=10")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(v), "-i", str(a), "-t", f"{d:.2f}",
             "-vf", vf, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])
        clips.append(out)
    (W / f"{tag}_list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    final = W / f"{tag}_video.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(W / f"{tag}_list.txt"), "-c", "copy", str(final)])
    return final

def make_thumb(clip, text):
    t = W / "thumb.txt"
    t.write_text("\n".join(textwrap.wrap(text.upper(), 14)[:3]))
    out = W / "thumb.jpg"
    run(["ffmpeg", "-y", "-ss", "1", "-i", str(clip), "-frames:v", "1", "-vf",
         f"scale=1280:720,drawtext=fontfile={FONT}:textfile={t}:fontcolor=yellow:fontsize=130:"
         "box=1:boxcolor=black@0.65:boxborderw=22:x=(w-text_w)/2:y=(h-text_h)/2", "-q:v", "3", str(out)])
    return out

# ---------- 5. UPLOADER ----------
def upload(yt, path, title, desc, tags):
    body = {"snippet": {"title": title[:100], "description": desc, "tags": tags,
                        "categoryId": "28", "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": os.environ.get("PRIVACY", "private"), "selfDeclaredMadeForKids": False}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), resumable=True)).execute()
    return res["id"]

def agent_uploader(yt, idea, script, video, thumb):
    meta = ask_json(
        f"You are the YouTube SEO manager for {CHANNEL}. Write an honest, curiosity-driven title (<=70 chars), "
        "a description (first 2 lines = hook with main keywords; 150-200 words; end with 3 #hashtags) and 12 tags. "
        'JSON: {"title":"","description":"","tags":[]}',
        json.dumps({"idea": idea, "script": [s["text"] for s in script["scenes"]]}), tokens=3000)
    vid = upload(yt, video, meta["title"], meta["description"], meta["tags"])
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
    except Exception as e:
        print("Thumbnail not set:", e)
    print("Long video: https://youtu.be/" + vid)
    return vid

# ---------- 7. SHORTS ----------
def agent_short(idea, script):
    return ask_json(
        f"You are a Shorts writer for {CHANNEL}. Turn the best, most surprising part of this video into a "
        "45-55 second vertical Short (110-140 words): hook in the first 2 seconds, 1-2 mind-blowing facts, "
        "end with 'Full video on my channel'. Only verified facts. 7-9 scenes with 'text' and 'query' "
        "(2-3 concrete English stock-video words). "
        'JSON: {"title":"","scenes":[{"text":"","query":""}]}',
        json.dumps({"idea": idea, "script": [s["text"] for s in script["scenes"]]}), tokens=3000)

def upload_short(yt, short, path, long_id):
    desc = f"Full video: https://youtu.be/{long_id}\n\n#Shorts #science #facts"
    vid = upload(yt, path, short["title"][:90] + " #Shorts", desc, ["shorts", "science", "facts"])
    print("Short: https://youtu.be/" + vid)

if __name__ == "__main__":
    state = load_state()
    yt = yt_service()
    agent_analyst(yt, state)
    idea = agent_ideas(state);                print("Idea:", idea)
    script = agent_factcheck(agent_script(idea))
    video = build_video(script["scenes"], 1920, 1080, "L", 1280)
    thumb = make_thumb(W / "L_c0.mp4", idea["thumb_text"])
    long_id = agent_uploader(yt, idea, script, video, thumb)
    state["topics"].append(idea["topic"])
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    try:
        short = agent_short(idea, script)
        sv = build_video(short["scenes"], 1080, 1920, "S", 720)
        upload_short(yt, short, sv, long_id)
    except Exception as e:
        print("Short skipped:", e)
