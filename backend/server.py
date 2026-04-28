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


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(data: StatusCheckCreate):
    obj = StatusCheck(**data.dict())
    await db.status_checks.insert_one(obj.dict())
    return obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    docs = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    return [StatusCheck(**d) for d in docs]


@api_router.post("/download/validate", response_model=ValidateResponse)
async def validate_link(req: ValidateRequest):
    """Validate that a URL is a publicly accessible direct video file.

    - Rejects non http(s) inputs with 400
    - Returns is_public=False for 401/403 (auth-required), HTML pages, or unsupported
    - Returns success=True, is_public=True with video_url + metadata for direct video URLs
    """
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
            # Try HEAD first (cheap)
            try:
                r = await client_http.head(target)
                if r.status_code < 400 or r.status_code in (401, 403, 404):
                    resp = r
            except Exception:
                resp = None

            # Fallback to a GET (stream just headers)
            if resp is None or resp.status_code in (405, 501):
                async with client_http.stream("GET", target) as gr:
                    status = gr.status_code
                    headers = dict(gr.headers)
                    final_url = str(gr.url)
            else:
                status = resp.status_code
                headers = dict(resp.headers)
                final_url = str(resp.url)

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
                    error="Link not found or no longer available",
                )
            if status >= 400:
                return ValidateResponse(
                    success=False,
                    is_public=False,
                    error="Could not access this link",
                )

            content_type = headers.get("content-type") or headers.get("Content-Type") or ""
            content_length_raw = headers.get("content-length") or headers.get("Content-Length") or "0"
            try:
                content_length = int(content_length_raw)
            except ValueError:
                content_length = 0

            if not _is_video_response(content_type, final_url):
                return ValidateResponse(
                    success=False,
                    is_public=False,
                    error="Private or restricted content",
                )

            return ValidateResponse(
                success=True,
                is_public=True,
                video_url=final_url,
                thumbnail=GENERIC_THUMB,
                title=_filename_from_url(final_url),
                size_mb=round(content_length / (1024 * 1024), 2) if content_length else 0.0,
            )

    except httpx.TimeoutException:
        return ValidateResponse(
            success=False,
            is_public=False,
            error="Request timed out — please try again",
        )
    except httpx.RequestError:
        return ValidateResponse(
            success=False,
            is_public=False,
            error="Could not reach the link",
        )
    except Exception:
        logger.exception("validate_link unexpected error")
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
