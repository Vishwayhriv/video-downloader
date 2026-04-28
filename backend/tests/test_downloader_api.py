"""Backend tests for Social Downloader — validate/save/list/delete endpoints."""
import os
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://downloader-refine.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --------- /api/download/validate ---------
class TestValidate:
    def test_public_mp4_returns_is_public_true(self, client):
        r = client.post(f"{API}/download/validate",
                        json={"url": "https://www.w3schools.com/html/mov_bbb.mp4"}, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["success"] is True
        assert data["is_public"] is True
        assert data["video_url"] and data["video_url"].startswith("http")
        assert data["title"]
        assert "size_mb" in data

    def test_403_returns_private(self, client):
        r = client.post(f"{API}/download/validate",
                        json={"url": "https://httpbin.org/status/403"}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is False
        assert data["is_public"] is False
        assert data["error"] == "Private or restricted content"

    def test_html_page_returns_private(self, client):
        r = client.post(f"{API}/download/validate",
                        json={"url": "https://example.com"}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is False
        assert data["is_public"] is False
        assert data["error"] == "Private or restricted content"

    def test_invalid_url_string_returns_400(self, client):
        r = client.post(f"{API}/download/validate",
                        json={"url": "not-a-url"}, timeout=15)
        assert r.status_code == 400

    def test_ftp_scheme_returns_400(self, client):
        r = client.post(f"{API}/download/validate",
                        json={"url": "ftp://example.com/file.mp4"}, timeout=15)
        assert r.status_code == 400


# --------- /api/download/save + list + delete ---------
class TestDownloadCRUD:
    created_id = None

    def test_save_persists_item(self, client):
        payload = {
            "url": "https://www.w3schools.com/html/mov_bbb.mp4",
            "title": "TEST_mov_bbb.mp4",
            "thumbnail": "https://example.com/thumb.jpg",
            "file_uri": None,
            "size_mb": 1.05,
        }
        r = client.post(f"{API}/download/save", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"]
        assert data["title"] == "TEST_mov_bbb.mp4"
        assert data["size_mb"] == 1.05
        assert "_id" not in data
        TestDownloadCRUD.created_id = data["id"]

    def test_list_excludes_mongo_id(self, client):
        r = client.get(f"{API}/download/list", timeout=15)
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        for item in arr:
            assert "_id" not in item
            assert "id" in item
            # new-shape fields
            assert "platform" not in item
            assert "duration" not in item
            assert "quality" not in item
        if TestDownloadCRUD.created_id:
            assert any(i["id"] == TestDownloadCRUD.created_id for i in arr)

    def test_delete_removes_item(self, client):
        assert TestDownloadCRUD.created_id, "Save test must run first"
        r = client.delete(f"{API}/download/{TestDownloadCRUD.created_id}", timeout=15)
        assert r.status_code == 200
        assert r.json().get("deleted") == 1

        # verify removed
        r2 = client.get(f"{API}/download/list", timeout=15)
        assert r2.status_code == 200
        assert not any(i["id"] == TestDownloadCRUD.created_id for i in r2.json())
