"""
Unit tests for native/downloader.py validation + progress parsing.

Run from the repo root:
    pytest -q tests/

These tests intentionally do not exercise the yt-dlp subprocess path —
they cover the parts of the host that have to defend against bad input.
"""
import os

import pytest

from downloader import (
    ALLOWED_DOWNLOAD_DIRS,
    AUDIO_ONLY_FORMATS,
    DEFAULT_DOWNLOAD_DIR,
    FORMAT_PRESETS,
    PROGRESS_PREFIX,
    ValidationError,
    parse_progress_line,
    validate_and_normalize_message,
)


# ---------- URL allow-list ----------

class TestUrlValidation:
    @pytest.mark.parametrize("url", [
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ])
    def test_allowed_youtube_hosts(self, url):
        out_url, _, _, _ = validate_and_normalize_message({
            "action": "download",
            "url": url,
            "format": "best",
        })
        assert out_url == url

    @pytest.mark.parametrize("bad_url", [
        "https://example.com/watch?v=abc",
        "https://evil.com/youtube.com/watch?v=abc",
        "https://youtube.com.evil.com/watch?v=abc",
        "ftp://youtube.com/watch?v=abc",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "",
    ])
    def test_rejects_non_youtube_hosts(self, bad_url):
        with pytest.raises(ValidationError):
            validate_and_normalize_message({
                "action": "download",
                "url": bad_url,
                "format": "best",
            })

    def test_rejects_overly_long_url(self):
        long_url = "https://www.youtube.com/watch?v=" + ("a" * 4096)
        with pytest.raises(ValidationError, match="too long"):
            validate_and_normalize_message({
                "action": "download",
                "url": long_url,
                "format": "best",
            })

    def test_rejects_non_string_url(self):
        with pytest.raises(ValidationError):
            validate_and_normalize_message({
                "action": "download",
                "url": 12345,
                "format": "best",
            })


# ---------- Action allow-list ----------

class TestActionValidation:
    def test_rejects_non_dict_message(self):
        with pytest.raises(ValidationError):
            validate_and_normalize_message("download")

    def test_rejects_unknown_action(self):
        with pytest.raises(ValidationError, match="Unsupported action"):
            validate_and_normalize_message({
                "action": "rm",
                "url": "https://youtube.com/watch?v=x",
                "format": "best",
            })


# ---------- Format enum ----------

class TestFormatValidation:
    @pytest.mark.parametrize("key", list(FORMAT_PRESETS))
    def test_every_preset_resolves(self, key):
        _, fmt, _, returned_key = validate_and_normalize_message({
            "action": "download",
            "url": "https://youtube.com/watch?v=x",
            "format": key,
        })
        assert fmt == FORMAT_PRESETS[key]
        assert returned_key == key

    def test_2k_and_4k_present(self):
        # Anti-regression: the explicit reason this PR exists.
        assert "2k" in FORMAT_PRESETS
        assert "4k" in FORMAT_PRESETS
        assert "1440" in FORMAT_PRESETS["2k"]
        assert "2160" in FORMAT_PRESETS["4k"]

    def test_video_presets_dont_lock_container(self):
        # 4K on YouTube is VP9/AV1 in webm — locking [ext=mp4] would silently
        # downgrade to 1080p. Make sure no video preset re-introduces that
        # constraint.
        for key in ("4k", "2k", "1080p", "720p", "480p", "best"):
            assert "[ext=mp4]" not in FORMAT_PRESETS[key], (
                f"{key} preset must not lock the container to mp4"
            )

    def test_audio_preset_present(self):
        assert "audio" in FORMAT_PRESETS
        assert "audio" in AUDIO_ONLY_FORMATS

    def test_format_is_case_insensitive(self):
        _, fmt, _, _ = validate_and_normalize_message({
            "action": "download",
            "url": "https://youtube.com/watch?v=x",
            "format": "  4K  ",
        })
        assert fmt == FORMAT_PRESETS["4k"]

    @pytest.mark.parametrize("bad_format", [
        "",
        "1440p",                         # not a key (use "2k")
        "best[height<=1080]",            # raw yt-dlp DSL — must be rejected
        "; rm -rf /",                    # injection probe
        "best && curl evil.com",         # injection probe
        None,
        12345,
    ])
    def test_rejects_unknown_format(self, bad_format):
        with pytest.raises(ValidationError):
            validate_and_normalize_message({
                "action": "download",
                "url": "https://youtube.com/watch?v=x",
                "format": bad_format,
            })


# ---------- Download path whitelist ----------

class TestDownloadPath:
    def test_default_path_when_omitted(self):
        _, _, path, _ = validate_and_normalize_message({
            "action": "download",
            "url": "https://youtube.com/watch?v=x",
            "format": "best",
        })
        assert path == DEFAULT_DOWNLOAD_DIR

    @pytest.mark.parametrize("bad_path", [
        "/etc/passwd",
        "/tmp",
        "/",
        "../../etc",
        "/var/log/somewhere",
    ])
    def test_rejects_non_whitelisted_paths(self, bad_path):
        with pytest.raises(ValidationError):
            validate_and_normalize_message({
                "action": "download",
                "url": "https://youtube.com/watch?v=x",
                "format": "best",
                "downloadPath": bad_path,
            })

    def test_accepts_whitelisted_path_when_directory_exists(self, tmp_path, monkeypatch):
        # Pretend the tmp dir is one of the allowed roots so the test is
        # hermetic and doesn't depend on the user's real ~/Downloads.
        fake_root = str(tmp_path)
        monkeypatch.setattr(
            "downloader.ALLOWED_DOWNLOAD_DIRS",
            (os.path.realpath(fake_root),),
        )
        _, _, path, _ = validate_and_normalize_message({
            "action": "download",
            "url": "https://youtube.com/watch?v=x",
            "format": "best",
            "downloadPath": fake_root,
        })
        assert path == os.path.realpath(fake_root)


# ---------- Progress parsing ----------

class TestProgressParsing:
    def test_progress_template_line(self):
        line = f"{PROGRESS_PREFIX}1048576|10485760|524288.0|20"
        evt = parse_progress_line(line, "video.mp4")
        assert evt is not None
        assert evt["type"] == "progress"
        assert evt["percent"] == 10.0
        assert evt["filename"] == "video.mp4"
        assert "KiB/s" in evt["speed"] or "MiB/s" in evt["speed"]
        assert evt["eta"] == "00:20"

    def test_progress_handles_missing_total(self):
        line = f"{PROGRESS_PREFIX}1024|NA|NA|NA"
        evt = parse_progress_line(line, "")
        assert evt is not None
        assert evt["percent"] == 0.0
        assert evt["speed"] == ""
        assert evt["eta"] == ""

    def test_legacy_progress_line(self):
        line = "[download]  42.3% of 123.45MiB at 1.23MiB/s ETA 00:42"
        evt = parse_progress_line(line, "")
        assert evt is not None
        assert evt["percent"] == 42.3
        assert evt["speed"] == "1.23MiB/s"
        assert evt["eta"] == "00:42"

    def test_non_progress_line_returns_none(self):
        assert parse_progress_line("[info] Loading metadata", "") is None
        assert parse_progress_line("ERROR: video unavailable", "") is None
