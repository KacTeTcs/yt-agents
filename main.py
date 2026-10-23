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

# ---------- 2. SSENARI
