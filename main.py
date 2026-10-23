"""5 agentli YouTube tizimi: G'oya -> Ssenariy -> Montaj -> Yuklash -> Tahlil"""
import os, re, json, asyncio, subprocess, textwrap, pathlib, requests, feedparser
import anthropic, edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

MODEL = "claude-sonnet-5-5"
VOICE = "uz-UZ-MadinaNeural"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STATE = pathlib.Path("state.json")
W = pathlib.Path("work"); W.mkdir(exist_ok=True)
claude = anthropic.Anthropic()

def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"topics": [], "lessons": []}

def ask(system, user, tokens=4000):
    r = claude.messages.create(model=MODEL, max_tokens=tokens, system=system,
                               messages=[{"role": "user", "content": user}])
    return r.content[0].text

def ask_json(system, user):
    t = ask(system + "\nFaqat JSON qaytar, boshqa matn yo'q.", user)
    return json.loads(re.search(r"[\[{].*[\]}]", t, re.S).group(0))

def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)

def yt_service():
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"])
    return build("youtube", "v3", credentials=creds)

# ---------- 5. TAHLIL AGENTI ----------
def agent_analyst(yt, state):
    try:
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=10).execute()["items"]
        ids = ",".join(i["contentDetails"]["videoId"] for i in items)
        if not ids: return
        vids = yt.videos().list(part="snippet,statistics", id=ids).execute()["items"]
        data = [{"title": v["snippet"]["title"], **v["statistics"]} for v in vids]
        lessons = ask_json(
            "Sen YouTube analitikasan. Kanal: o'zbekcha, trenddagi mavzular, 3-4 daqiqalik videolar. "
            "Statistikadan nima ishlaganini aniqla. JSON: {\"lessons\": [5 ta qisqa aniq dars]}",
            json.dumps(data, ensure_ascii=False))
        state["lessons"] = lessons["lessons"]
        print("Darslar:", state["lessons"])
    except Exception as e:
        print("Tahlil o'tkazib yuborildi:", e)

# ---------- 1. G'OYA AGENTI ----------
def agent_ideas(state):
    trends = []
    for geo in ("UZ", "US"):
        f = feedparser.parse(f"https://trends.google.com/trending/rss?geo={geo}")
        trends += [e.title for e in f.entries[:15]]
    return ask_json(
        "Sen YouTube trend tahlilchisisan. Kanal: o'zbek tilida trenddagi mavzular, 3-4 daqiqalik videolar. "
        "Berilgan trendlardan eng qiziqarli va xavfsiz (zo'ravonlik, siyosiy targ'ibot, shaxsiy hayot yo'q) 1 tasini tanla. "
        "Avval ishlatilgan mavzularni takrorlama. Oldingi darslarni hisobga ol. "
        'JSON: {"topic":"","angle":"","hook":"","title":""}',
        json.dumps({"trends": trends, "used": state["topics"][-30:], "lessons": state["lessons"]}, ensure_ascii=False))

# ---------- 2. SSENARIY AGENTI ----------
def agent_script(idea):
    return ask_json(
        "Sen YouTube ssenaristisan. 3-4 daqiqalik (500-600 so'z) sodda jonli o'zbekcha ssenariy yoz. "
        "Tuzilma: hook (15s) -> kontekst -> 3 asosiy fakt -> xulosa -> obuna CTA. Aniq bilmagan narsangni 'ehtimol' deb ayt, "
        "raqam/sanalarni o'ylab topma. 12-16 ta sahna. Har sahnada 'text' (ovozga, 35-50 so'z) va 'query' "
        "(stok video qidirish uchun 2-3 inglizcha so'z). JSON: {\"scenes\":[{\"text\":\"\",\"query\":\"\"}]}",
        json.dumps(idea, ensure_ascii=False))

# ---------- 3. MONTAJ AGENTI ----------
async def tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(str(path))

def duration(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())

def stock(query, path):
    r = requests.get("https://pixabay.com/api/videos/",
        params={"key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 10, "safesearch": "true"},
        timeout=30).json()
    for hit in r.get("hits", []):
        for size in ("medium", "large", "small"):
            v = hit["videos"].get(size, {})
            if v.get("url") and v.get("width", 0) >= 1280:
                path.write_bytes(requests.get(v["url"], timeout=120).content)
                return True
    return False

def agent_editor(script, title):
    clips = []
    for i, sc in enumerate(script["scenes"]):
        a, v, sub, out = W / f"a{i}.mp3", W / f"v{i}.mp4", W / f"s{i}.txt", W / f"c{i}.mp4"
        asyncio.run(tts(sc["text"], a))
        d = duration(a) + 0.3
        if not stock(sc["query"], v) and not stock("city skyline", v):
            raise RuntimeError("Video topilmadi: " + sc["query"])
        sub.write_text("\n".join(textwrap.wrap(sc["text"], 45)[:3]))
        vf = ("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,"
              f"drawtext=fontfile={FONT}:textfile={sub}:fontcolor=white:fontsize=44:"
              "box=1:boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h-220:line_spacing=10")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(v), "-i", str(a), "-t", f"{d:.2f}",
             "-vf", vf, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])
        clips.append(out)
    (W / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(W / "list.txt"), "-c", "copy", str(W / "video.mp4")])
    (W / "t.txt").write_text("\n".join(textwrap.wrap(title.upper(), 22)[:3]))
    run(["ffmpeg", "-y", "-i", str(W / "c0.mp4"), "-frames:v", "1", "-vf",
         f"drawtext=fontfile={FONT}:textfile={W/'t.txt'}:fontcolor=yellow:fontsize=110:"
         "box=1:boxcolor=black@0.6:boxborderw=20:x=(w-text_w)/2:y=(h-text_h)/2", str(W / "thumb.jpg")])
    return W / "video.mp4", W / "thumb.jpg"

# ---------- 4. YUKLOVCHI AGENT ----------
def agent_uploader(yt, idea, script, video, thumb):
    meta = ask_json(
        "Sen YouTube SEO menejerisan. O'zbekcha: sarlavha (<=70 belgi, clickbaitsiz), tavsif (150 so'z, oxirida #hashtaglar), "
        'teglar (10 ta). JSON: {"title":"","description":"","tags":[]}',
        json.dumps({"idea": idea, "script": [s["text"] for s in script["scenes"]]}, ensure_ascii=False))
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"],
                        "tags": meta["tags"], "categoryId": "25", "defaultLanguage": "uz"},
            "status": {"privacyStatus": os.environ.get("PRIVACY", "private"), "selfDeclaredMadeForKids": False}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), resumable=True)).execute()
    try:
        yt.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload(str(thumb))).execute()
    except Exception as e:
        print("Thumbnail qo'yilmadi:", e)
    print("Yuklandi: https://youtu.be/" + res["id"])

if __name__ == "__main__":
    state = load_state()
    yt = yt_service()
    agent_analyst(yt, state)
    idea = agent_ideas(state);          print("G'oya:", idea)
    script = agent_script(idea)
    video, thumb = agent_editor(script, idea["title"])
    agent_uploader(yt, idea, script, video, thumb)
    state["topics"].append(idea["topic"])
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
