import os, random, json, requests, asyncio
from dataclasses import dataclass
from typing import List, Dict, Any
from langgraph.graph import StateGraph, END
from playwright.async_api import async_playwright
from moviepy import VideoFileClip, TextClip, CompositeVideoClip
from time import sleep
from instagrapi import Client
from dotenv import load_dotenv
from langchain_ollama import OllamaLLM

PROGRESS_FILE = "progress.json"

@dataclass
class State:
    creators: List[str]
    chosen_creator: str | None
    reel_list: List[Dict[str, str]]
    chosen_reel: Dict[str, str] | None
    downloaded_file: str | None
    edited_file: str | None
    uploaded_id: str | None
    progress: Dict[str, List[str]]
    errors: List[str]
    caption:str
COOKIE_FILE = "session.json"
UPLOAD_SESSION_FILE="upload_session.json"

async def login_and_save():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # show UI for first login
        context = await browser.new_context()
        page = await context.new_page()

        # Go to login page
        await page.goto("https://www.instagram.com/accounts/login/")

        print("👉 Please log in manually, then press Enter here...")
        input()

        # Save cookies & localStorage
        cookies = await context.storage_state(path=COOKIE_FILE)
        print("✅ Session saved to", COOKIE_FILE)

        await browser.close()
async def use_existing_session():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=COOKIE_FILE)
        page = await context.new_page()

        await page.goto("https://www.instagram.com/s8ul_reacts/reels/")
        print("✅ Loaded with saved session, should not redirect to login!")

        await browser.close()

# ----------------- UTILS -----------------
def load_progress() -> Dict[str, List[str]]:
    return json.load(open(PROGRESS_FILE)) if os.path.exists(PROGRESS_FILE) else {}

def save_progress(progress: Dict[str, List[str]]):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)

async def scrape_reels(username: str) -> List[Dict[str, str]]:
    """Scrape reel URLs + shortcodes for a public creator account"""
    reels = []
    url = f"https://www.instagram.com/{username}/reels/"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=COOKIE_FILE)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_selector("a[href*='/reel/']", timeout=20000)
        anchors = await page.query_selector_all("a[href*='/reel/']")
        print(anchors)
        for a in anchors:
            href = await a.get_attribute("href")
            if href and "/reel/" in href:
                shortcode = href.split("/")[-2]
                reels.append({"shortcode": shortcode, "url": "https://www.instagram.com" + href})

        await browser.close()
    # oldest first
    return list(reels)

async def download_reel_video(reel_url: str, out_file: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(reel_url, wait_until="networkidle")
        video = await page.query_selector("video")
        src = await video.get_attribute("src")
        r = requests.get(src, stream=True)
        with open(out_file, "wb") as f:
            for chunk in r.iter_content(1024*1024):
                f.write(chunk)
        await browser.close()
    return out_file

def simple_edit(in_file: str, out_file: str):
    clip = VideoFileClip(in_file)
    txt = TextClip(text="BGMI_MASTERS", font_size=48, color="white")
    txt = txt.with_duration(clip.duration).with_position(("center", clip.h - 100))
    CompositeVideoClip([clip, txt]).write_videofile(out_file, codec="libx264", audio_codec="aac")
    return out_file
def generate_caption():
    llm = OllamaLLM(model="llama3")  # Ensure Ollama server running
    prompt = (
        f"Create an engaging gaming shorts funny Instagram hashtags."
        "Include exactly 5 trending bgmi gaming funny hashtags. "
        "Output only hashtags, nothing else."
    )
    hashtags = llm.invoke(prompt)
    return hashtags

def upload_to_ig(video_url: str, caption: str) -> str:
    """Placeholder for IG Content Publishing API call"""
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD") 
    # You would upload to S3 → get presigned URL → call IG /media → /media_publish
    hashtags= generate_caption()
    cl = Client()
    if os.path.exists(UPLOAD_SESSION_FILE):
        cl.load_settings(UPLOAD_SESSION_FILE)
    else:
        cl.login(username, password)
        cl.dump_settings(UPLOAD_SESSION_FILE)    
    cl.login(username, password)
    cl.clip_upload(path=video_url, caption=caption+hashtags)
    print(f"Reel Uploaded Successfully")
    sleep(10)
    return "uploaded"

# ----------------- NODES -----------------
def node_choose_creator(state: State) -> State:
    state.chosen_creator = random.choice(state.creators)
    return state

async def node_fetch_reels(state: State) -> State:
    state.reel_list = await scrape_reels(state.chosen_creator)
    return state

def node_pick_next_reel(state: State) -> State:
    uploaded = state.progress.get(state.chosen_creator, [])
    print(state.reel_list)
    for reel in state.reel_list:
        if reel["shortcode"] not in uploaded:
            state.chosen_reel = reel
            return state
    state.errors.append("No new reels left")
    return state

async def node_download_reel(state: State) -> State:
    print("state",state)
    path = f"./tmp/{state.chosen_creator}_{state.chosen_reel['shortcode']}.mp4"
    state.downloaded_file = await download_reel_video(state.chosen_reel["url"], path)
    return state

def node_edit_reel(state: State) -> State:
    out_path = state.downloaded_file.replace(".mp4", "_edited.mp4")
    state.edited_file = simple_edit(state.downloaded_file, out_path)
    return state

def node_upload_reel(state: State) -> State:
    # simulate upload (replace with IG API call)
    state.uploaded_id = upload_to_ig(state.edited_file, f"Repost from {state.chosen_creator}")
    return state

def node_save_progress(state: State) -> State:
    if state.chosen_creator and state.chosen_reel:
        state.progress.setdefault(state.chosen_creator, []).append(state.chosen_reel["shortcode"])
        save_progress(state.progress)
    return state

# ----------------- BUILD GRAPH -----------------
builder = StateGraph(State)
builder.add_node("choose_creator", node_choose_creator)
builder.add_node("fetch_reels", node_fetch_reels)
builder.add_node("pick_next", node_pick_next_reel)
builder.add_node("download", node_download_reel)
builder.add_node("edit", node_edit_reel)
builder.add_node("upload", node_upload_reel)
builder.add_node("save_progress", node_save_progress)

builder.set_entry_point("choose_creator")
builder.add_edge("choose_creator", "fetch_reels")
builder.add_edge("fetch_reels", "pick_next")
builder.add_edge("pick_next", "download")
builder.add_edge("download", "edit")
builder.add_edge("edit", "upload")
builder.add_edge("upload", "save_progress")
builder.add_edge("save_progress", END)

graph = builder.compile()



# Run once
if __name__=="__main__":
    load_dotenv()
    async def ensure_session():
        if not os.path.exists(COOKIE_FILE):
            print("⚠️ No session found, logging in manually...")
            await login_and_save()
        else:
            print("✅ Using existing session")

    asyncio.run(ensure_session())
    # ----------------- RUN -----------------
    init_state = State(
        creators=["s8ul_reacts","ig_hunter.op.gaming","gamezy.meme","disaster_b.t","aarav.reacts","madeehdard","casetoomoments"],  # Example public creators
        chosen_creator=None,
        reel_list=[],
        chosen_reel=None,
        downloaded_file=None,
        edited_file=None,
        uploaded_id=None,
        progress=load_progress(),
        errors=[],
        caption=''
    )
    result = asyncio.run(graph.ainvoke(init_state))
    print(result)
