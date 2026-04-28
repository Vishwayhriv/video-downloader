# Social Downloader — PRD (v2)

## Overview
Premium, minimal Expo React Native downloader for **public, direct video URLs** (http/https). The backend validates publicness; the frontend performs **real** downloads via `expo-file-system` and saves to the device gallery via `expo-media-library`.

## Tabs (Bottom Navigation)
1. **Download (Home)** — paste link → validate → download → save.
2. **Gallery** — local-first FlatList grid of downloaded items.
3. **Profile** — stats, preferences, "Social Pro (Coming Soon)".

## Backend (FastAPI)
- `POST /api/download/validate` — validates a URL by issuing a HEAD (with GET stream fallback) via `httpx`. Returns:
  - `400 Invalid URL` for non-http(s).
  - `{success:true, is_public:true, video_url, thumbnail, title, size_mb}` for direct video URLs (Content-Type `video/*` or extension `.mp4/.webm/.mov/.m4v/.mkv/.avi/.ogg`).
  - `{success:false, is_public:false, error:"Private or restricted content"}` for 401/403, HTML pages, unsupported types.
  - `{success:false, is_public:false, error:"Link not found or no longer available"}` for 404.
  - `{success:false, is_public:false, error:"Could not reach the link"}` for transport errors.
  - `{success:false, is_public:false, error:"Request timed out — please try again"}` for httpx timeouts.
- `POST /api/download/save` — persists `DownloadItem` (no platform/duration/quality fields).
- `GET /api/download/list` — list (excludes `_id`).
- `DELETE /api/download/{id}`.

## Frontend Real Download Flow
1. User submits URL or smart-clipboard auto-detects.
2. `parsing` stage → call `/api/download/validate`.
3. If `success && is_public` → real download via `FileSystem.createDownloadResumable` to `documentDirectory`. Progress callback updates UI bar from 0–99% based on `bytesWritten/totalBytes`.
4. Permission request via `MediaLibrary.requestPermissionsAsync()` → `createAssetAsync(uri)` saves to device gallery. Subtitle toggles to "Saved to gallery" when granted.
5. Persist record (file_uri included) in AsyncStorage; mirror metadata to backend.

### Error UX
- `errorKind === 'private'` → modal title "Cannot Download", subtitle "This content is private or not supported", **no Retry**, button reads "Close".
- `errorKind === 'network' | 'invalid' | 'download'` → title "Download failed" + Retry + Cancel.
- Distinguished by regex match on backend `error` field (`/private|restricted|not found|no longer/i`).

## UI Constraints (Play Store safe)
- No platform names anywhere (YouTube/Instagram/TikTok/etc. all removed).
- Generic copy: "Download from link", "Paste a public video link", "Public links only".
- Pro screen displays "Coming Soon" features only — no pricing, no purchase button.

## Permissions (declared in app.json)
- iOS: `NSPhotoLibraryUsageDescription`, `NSPhotoLibraryAddUsageDescription`, `NSUserNotificationsUsageDescription`.
- Android: `VIBRATE`, `POST_NOTIFICATIONS`, `READ_MEDIA_VIDEO`, `READ_MEDIA_IMAGES`, `WRITE_EXTERNAL_STORAGE`.
- Plugins: `expo-notifications`, `expo-media-library`.

## Expo Compatibility
- Pure Expo Go: `expo-file-system/legacy`, `expo-media-library`, `expo-clipboard`, `expo-haptics`, `expo-linear-gradient`, `expo-notifications`.
- No native modules, no background services.
- Web preview falls through to a friendly "Real download requires the Expo Go mobile app" error (real downloads need native context).

## Persistence
- AsyncStorage `@downloader/items` — DownloadItem `{ id, url, title, thumbnail, file_uri, size_mb, created_at }`.
- MongoDB `db.downloads` for cross-device history (file_uri stored as null on server).

## Removed in v2
- All YouTube logic and references.
- Platform detection / chips / filters / "Platforms" stat.
- Old `/api/download/process` endpoint (replaced by `/api/download/validate`).
