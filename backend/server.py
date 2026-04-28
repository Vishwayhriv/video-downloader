from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
import yt_dlp



ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.getenv("MONGO_URL")

if not mongo_url:
    raise Exception("MONGO_URL not found in environment variables")

client = AsyncIOMotorClient(mongo_url)
db = client["videodb"]

app = FastAPI()
api_router = APIRouter(prefix="/api")


# ---------- Models ----------
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


class ValidateRequest(BaseModel):
    url: str


class ValidateResponse(BaseModel):
    success: bool
    is_public: bool
    video_url: Optional[str] = None
    thumbnail: Optional[str] = None
    title: Optional[str] = None
    size_mb: Optional[float] = None
    error: Optional[str] = None


class DownloadItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    title: str
    thumbnail: str
    file_uri: Optional[str] = None
    size_mb: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------- Helpers ----------
VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.m4v', '.mkv', '.avi', '.ogg'}
HLS_HINT = ('mpegurl', 'm3u8')

GENERIC_THUMB = "https://images.unsplash.com/photo-1574717024653-61fd2cf4d44d?w=600&q=80"


def is_valid_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https"):
            return False
        if not p.netloc or "." not in p.netloc:
            return False
        return True
    except Exception:
        return False


def _is_video_response(content_type: str, final_url: str) -> bool:
    ct = (content_type or "").lower()
    path = urlparse(final_url).path.lower()
    has_ext = any(path.endswith(ext) for ext in VIDEO_EXTS)
    is_video_ct = ct.startswith("video/") or any(h in ct for h in HLS_HINT) or ct in ("application/octet-stream",) and has_ext
    return has_ext or is_video_ct


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit('/', 1)[-1]
    return name or "Video"


# ---------- Routes ----------
@api_router.get("/")
async def root():
    return {"message": "Downloader API"}

@api_router.post("/download/validate", response_model=ValidateResponse)
async def validate_link(req: ValidateRequest):

    if not is_valid_url(req.url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    target = req.url.strip()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(12.0),
            headers={"User-Agent": "SocialDownloader/1.0"},
        ) as client_http:

            resp = None

            # Try HEAD first
            try:
                r = await client_http.head(target)
                if r.status_code < 400 or r.status_code in (401, 403, 404):
                    resp = r
            except Exception:
                resp = None

            # Fallback GET
            if resp is None or resp.status_code in (405, 501):
                async with client_http.stream("GET", target) as gr:
                    status = gr.status_code
                    headers = dict(gr.headers)
                    final_url = str(gr.url)
            else:
                status = resp.status_code
                headers = dict(resp.headers)
                final_url = str(resp.url)

            # Basic error checks
            if status in (401, 403):
                return ValidateResponse(
                    success=False,
                    is_public=False,
                    error="Private or restricted content",
                )

            if status == 404:
                return ValidateResponse(
                    success=False,
                    is_public=False,
                    error="Link not found",
                )

            if status >= 400:
                return ValidateResponse(
                    success=False,
                    is_public=False,
                    error="Could not access link",
                )

            content_type = headers.get("content-type", "")

            # ✅ DIRECT VIDEO → return
            if _is_video_response(content_type, final_url):
                return ValidateResponse(
                    success=True,
                    is_public=True,
                    video_url=final_url,
                    thumbnail=GENERIC_THUMB,
                    title=_filename_from_url(final_url),
                    size_mb=0.0,
                )

        # 🔥 NOT DIRECT → use yt-dlp
        try:
            ydl_opts = {
                "quiet": True,
                "format": "best",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target, download=False)

                return ValidateResponse(
                    success=True,
                    is_public=True,
                    video_url=info.get("url"),
                    thumbnail=info.get("thumbnail"),
                    title=info.get("title"),
                    size_mb=0.0,
                )

        except Exception as e:
            print("yt-dlp error:", e)

            return ValidateResponse(
                success=False,
                is_public=False,
                error="Could not extract video (private/restricted)",
            )

    except Exception as e:
        print("Server error:", e)
        return ValidateResponse(
            success=False,
            is_public=False,
            error="Something went wrong",
        )

@api_router.post("/download/save", response_model=DownloadItem)
async def save_download(item: DownloadItem):
    """Persist a completed download record."""
    await db.downloads.insert_one(item.dict())
    return item


@api_router.get("/download/list", response_model=List[DownloadItem])
async def list_downloads():
    docs = await db.downloads.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [DownloadItem(**d) for d in docs]


@api_router.delete("/download/{download_id}")
async def delete_download(download_id: str):
    res = await db.downloads.delete_one({"id": download_id})
    return {"deleted": res.deleted_count}


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
