import base64
import hashlib
import io
import os
import random
import re
import time
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
FRONTEND_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
IMAGE_DIR = os.path.join(FRONTEND_DIR, "image")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(title="MEME MIND AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if os.path.isdir(IMAGE_DIR):
    app.mount("/image", StaticFiles(directory=IMAGE_DIR), name="images")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

_gallery_files_cache: list[str] | None = None
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _list_gallery_filenames() -> list[str]:
    global _gallery_files_cache
    if _gallery_files_cache is not None:
        return _gallery_files_cache
    names: list[str] = []
    if os.path.isdir(IMAGE_DIR):
        for n in os.listdir(IMAGE_DIR):
            if os.path.splitext(n)[1].lower() in _IMAGE_EXT:
                names.append(n)
    names.sort(key=lambda s: s.lower())
    _gallery_files_cache = names
    return _gallery_files_cache


class Message(BaseModel):
    message: str


def _font_path() -> str | None:
    candidates = [
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arialbd.ttf"),
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


# Prefer newest IDs first; Google renames/retires models over time.
_FIXED_CAPTION_MODEL_ORDER = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro",
    "gemini-1.5-pro-latest",
)

_CAPTION_MODELS_CACHE: list[str] | None = None

_CAPTION_SAFETY = [
    genai_types.SafetySetting(
        category=genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=genai_types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    genai_types.SafetySetting(
        category=genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=genai_types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    genai_types.SafetySetting(
        category=genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=genai_types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    genai_types.SafetySetting(
        category=genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=genai_types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
]


def _discover_caption_models() -> list[str]:
    """Ask the API which model IDs support generateContent (best effort)."""
    found: list[str] = []
    if not _gemini_client:
        return found
    try:
        for m in _gemini_client.models.list():
            raw = (getattr(m, "name", "") or "").strip()
            short = raw.rsplit("/", 1)[-1]
            low = short.lower()
            if "embed" in low or "imagen" in low or "aqa" in low:
                continue
            found.append(short)
    except Exception as e:
        print(f"list_models skipped: {e}")
    return found


def _caption_model_candidates() -> list[str]:
    """Single cached merge: curated order + API-discovered names."""
    global _CAPTION_MODELS_CACHE
    if _CAPTION_MODELS_CACHE is not None:
        return _CAPTION_MODELS_CACHE

    discovered = _discover_caption_models()

    def score(name: str) -> int:
        low = name.lower()
        s = 0
        if "embed" in low:
            return -10_000
        if "2.5" in low and "flash" in low:
            s += 500
        if "2.0" in low and "flash" in low:
            s += 400
        if "1.5" in low and "flash" in low:
            s += 300
        if "flash" in low:
            s += 120
        if "pro" in low:
            s += 80
        return s

    extras = sorted(set(discovered), key=score, reverse=True)
    merged: list[str] = []
    seen: set[str] = set()
    for n in list(_FIXED_CAPTION_MODEL_ORDER) + extras:
        if n not in seen:
            seen.add(n)
            merged.append(n)
    _CAPTION_MODELS_CACHE = merged
    return _CAPTION_MODELS_CACHE


def _template_caption(user_query: str) -> str:
    """Deterministic offline caption so meme images always render."""
    cleaned = re.sub(r"\s+", " ", (user_query or "").strip()) or "this whole situation"
    snippet = cleaned[:72] + ("…" if len(cleaned) > 72 else "")
    variants = [
        f"POV:\n{snippet}",
        f"Nobody:\nAbsolutely nobody:\n{snippet}",
        f"Brain: one more thing\nAlso brain:\n{snippet}",
        f"It be like that sometimes.\n({snippet})",
    ]
    idx = int(hashlib.sha256(cleaned.encode("utf-8")).hexdigest(), 16) % len(variants)
    return variants[idx]



def generate_meme_text(user_query: str) -> tuple[str | None, str | None]:
    """
    Returns (caption, error). On success error is None.
    """
    if not _gemini_client:
        return None, "Set GEMINI_API_KEY in Backend/.env and restart the server."

    prompt = f"""Create a short, funny meme caption for this idea: {user_query}
Rules: one or two lines max, no hashtags, no quotes around the whole thing."""

    gen_cfg = genai_types.GenerateContentConfig(
        temperature=0.9,
        top_p=1.0,
        top_k=32,
        max_output_tokens=160,
        safety_settings=_CAPTION_SAFETY,
    )

    last_error = ""
    for model_name in _caption_model_candidates():
        try:
            response = _gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gen_cfg,
            )

            text = (getattr(response, "text", None) or "").strip()
            if text:
                return text, None

            last_error = "Model returned an empty caption."
            print(f"Caption empty from {model_name}")
        except Exception as e:
            last_error = str(e)
            print(f"Caption model {model_name} failed: {e}")
            continue

    short_err = (last_error or "").strip()
    if "API key not valid" in short_err or "API_KEY_INVALID" in short_err:
        short_err = "Invalid GEMINI_API_KEY - create a new key at https://aistudio.google.com/apikey"
    elif "404" in short_err and "not found" in short_err.lower():
        short_err = "Gemini model not available for this key/region. Try another Google AI Studio key or check model access."
    friendly = short_err or "Could not generate an AI caption (check key, billing, and quota in Google AI Studio)."
    return None, friendly


def create_meme_image(width: int = 800, height: int = 600, background_color=(34, 34, 34)):
    return Image.new("RGB", (width, height), background_color)


def add_text_to_image(image: Image.Image, text: str) -> Image.Image:
    draw = ImageDraw.Draw(image)
    font_path = _font_path()
    max_width = image.size[0] - 60
    font_size = 44
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    bbox = (0, 0, 1, 1)

    while font_size >= 12:
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        if tw <= max_width:
            break
        font_size -= 2

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    w, h = image.size
    x = (w - text_width) / 2
    y = h - text_height - 40

    for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")
    return image


def _safe_filename_from_message(msg: str) -> str:
    h = hashlib.sha256(msg.encode("utf-8")).hexdigest()[:16]
    return f"meme_{h}.png"


def _demo_meme_caption(user_idea: str) -> str:
    """Short Pillow-safe caption for always-on demo PNGs (no Gemini)."""
    cleaned = re.sub(r"\s+", " ", (user_idea or "").strip()) or "Your idea goes here"
    snippet = cleaned[:220] + ("…" if len(cleaned) > 220 else "")
    return f"DEMO / OFFLINE (no AI)\n{snippet}"


@app.get("/api/demo-meme")
def api_demo_meme_png(q: str = ""):
    """
    Always returns a PNG for marketing / offline showcase.
    Does not call Gemini; uses the same Pillow path as /send-message.
    """
    try:
        caption = _demo_meme_caption(q)
        image = create_meme_image()
        final = add_text_to_image(image, caption)
        buf = io.BytesIO()
        final.save(buf, format="PNG")
        data = buf.getvalue()
        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        print(f"/api/demo-meme error: {e}")
        return Response(content=b"", status_code=500, media_type="application/octet-stream")


@app.get("/health")
def health():
    return {"status": "ok", "gemini_configured": _gemini_client is not None}


def _gallery_files_ordered(sort: str) -> list[str]:
    files = list(_list_gallery_filenames())
    sort = (sort or "all").lower()
    if sort not in ("all", "latest", "trending", "popular"):
        sort = "all"
    if sort == "latest":
        files.sort(reverse=True, key=lambda s: s.lower())
    elif sort == "trending":
        rng = random.Random(int(time.time()) // 3600)
        rng.shuffle(files)
    elif sort == "popular":
        rng = random.Random(9001 + int(time.time()) // 86400)
        rng.shuffle(files)
    else:
        files.sort(key=lambda s: s.lower())
    return files


@app.get("/api/gallery")
def api_gallery(page: int = 1, per_page: int = 12, sort: str = "all"):
    page = max(1, page)
    per_page = min(max(1, per_page), 48)
    files = _gallery_files_ordered(sort)
    total = len(files)
    start = (page - 1) * per_page
    slice_names = files[start : start + per_page]
    items = []
    for name in slice_names:
        path = f"/image/{quote(name)}"
        items.append(
            {
                "url": path,
                "title": os.path.splitext(name)[0].replace("_", " ")[:80],
                "description": "From your MEME MIND AI library",
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "sort": sort,
    }


@app.post("/send-message")
async def receive_message(msg: Message):
    try:
        prompt = msg.message.strip()
        if not prompt:
            return {"status": "error", "message": "Please enter a meme concept."}

        caption_source = "gemini"
        notice: str | None = None
        caption: str | None = None

        if GEMINI_API_KEY:
            caption, cap_err = generate_meme_text(prompt)
            if not caption:
                caption_source = "template"
                caption = _template_caption(prompt)
                notice = cap_err or "AI did not return a caption; using an offline template instead."
        else:
            caption_source = "template"
            caption = _template_caption(prompt)
            notice = "No GEMINI_API_KEY in Backend/.env - using an offline caption. Add a valid key from Google AI Studio for AI-generated text."

        image = create_meme_image()
        final_image = add_text_to_image(image, caption)

        buf = io.BytesIO()
        final_image.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        image_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

        output_filename = _safe_filename_from_message(prompt)
        output_path = os.path.join(STATIC_DIR, output_filename)
        try:
            with open(output_path, "wb") as f:
                f.write(png_bytes)
        except OSError as write_err:
            print(f"static meme write skipped: {write_err}")

        return {
            "status": "success",
            "caption": caption,
            "image_url": image_url,
            "caption_source": caption_source,
            "notice": notice,
        }
    except Exception as e:
        print(f"/send-message error: {e}")
        return {"status": "error", "message": f"Backend error: {e!s}"}


@app.get("/", response_class=HTMLResponse)
async def serve_home():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.isfile(index_path):
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return FileResponse(index_path, media_type="text/html")


@app.get("/gallery.html", response_class=HTMLResponse)
@app.get("/gallery", response_class=HTMLResponse)
async def serve_gallery():
    path = os.path.join(FRONTEND_DIR, "gallery.html")
    if not os.path.isfile(path):
        return HTMLResponse("<h1>gallery.html not found</h1>", status_code=404)
    return FileResponse(path, media_type="text/html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_post(
    email: str = Form(""),
    password: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
):
    _ = (email, password, first_name, last_name)
    return RedirectResponse(url="/", status_code=303)
