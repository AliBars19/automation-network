"""
Unit tests for src/collectors/video_clipper.py.

All subprocess calls, filesystem checks, and Path operations are mocked.
No real processes are spawned and no real files are accessed.
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_FAKE_MEDIA_DIR = Path("/fake/media")
_FAKE_VIDEO_URL = "https://www.youtube.com/watch?v=abc123"
_FAKE_VIDEO_ID = "abc123"
_FAKE_REDDIT_URL = "https://v.redd.it/xyz789/DASH_720.mp4"
_FAKE_POST_ID = "xyz789"
_FAKE_COOKIES = Path("/fake/repo/data/cookies.txt")
_FAKE_REDDIT_COOKIES = Path("/fake/repo/data/reddit_cookies.txt")


def _make_completed_process(returncode: int = 0, stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# clip_youtube_video
# ---------------------------------------------------------------------------

class TestClipYoutubeVideoCookiesMissing:
    """When cookies.txt does not exist the function must short-circuit to None."""

    def test_returns_none_when_cookies_absent(self):
        from src.collectors.video_clipper import clip_youtube_video

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
        ):
            mock_cookies.exists.return_value = False
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None

    def test_subprocess_never_called_when_cookies_absent(self):
        from src.collectors.video_clipper import clip_youtube_video

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("subprocess.run") as mock_run,
        ):
            mock_cookies.exists.return_value = False
            clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        mock_run.assert_not_called()


class TestClipYoutubeVideoCacheHit:
    """When the output file already exists the function returns it immediately."""

    def test_returns_existing_path_on_cache_hit(self):
        from src.collectors.video_clipper import clip_youtube_video

        expected_path = str(_FAKE_MEDIA_DIR / f"yt_clip_{_FAKE_VIDEO_ID}.mp4")

        def _exists(path):
            # .skip sentinel → False; .mp4 cache → True
            return str(path).endswith(".mp4")

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("subprocess.run") as mock_run,
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result == expected_path
        mock_run.assert_not_called()


class TestClipYoutubeVideoSuccess:
    """Happy-path: cookies exist, yt-dlp succeeds, file is within size limit."""

    def test_returns_output_path_on_success(self):
        from src.collectors.video_clipper import clip_youtube_video

        expected_path = str(_FAKE_MEDIA_DIR / f"yt_clip_{_FAKE_VIDEO_ID}.mp4")
        proc = _make_completed_process(returncode=0)

        def _exists_side_effect(path):
            # calls: skip-check → False, cache-check → False, post-download → True
            if not hasattr(_exists_side_effect, "_count"):
                _exists_side_effect._count = 0
            _exists_side_effect._count += 1
            return _exists_side_effect._count > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists_side_effect),
            patch("os.path.getsize", return_value=10 * 1024 * 1024),  # 10 MB
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result == expected_path

    def test_subprocess_receives_correct_url_and_output(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            # skip-check(1)→F, cache-check(2)→F, post-download(3)→T
            return call_counter["n"] > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=1024),
            patch("subprocess.run", return_value=proc) as mock_run,
        ):
            mock_cookies.exists.return_value = True
            clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        # First subprocess call is yt-dlp; second (if any) is ffprobe from _ensure_h264
        yt_dlp_cmd = mock_run.call_args_list[0][0][0]
        assert _FAKE_VIDEO_URL in yt_dlp_cmd
        expected_output = str(_FAKE_MEDIA_DIR / f"yt_clip_{_FAKE_VIDEO_ID}.mp4")
        assert expected_output in yt_dlp_cmd

    def test_cookies_path_passed_to_yt_dlp(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=1024),
            patch("subprocess.run", return_value=proc) as mock_run,
        ):
            mock_cookies.exists.return_value = True
            mock_cookies.__str__ = MagicMock(return_value=str(_FAKE_COOKIES))
            clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        yt_dlp_cmd = mock_run.call_args_list[0][0][0]
        assert "--cookies" in yt_dlp_cmd


class TestClipYoutubeVideoFailure:
    """Non-zero returncode from yt-dlp must produce None."""

    def test_returns_none_on_nonzero_returncode(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=1, stderr="ERROR: Some yt-dlp error")

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None

    def test_returns_none_on_nonzero_returncode_empty_stderr(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=2, stderr="")

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None

    def test_returns_none_on_nonzero_returncode_none_stderr(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=1)
        proc.stderr = None

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None


class TestClipYoutubeVideoTimeout:
    """subprocess.TimeoutExpired must be caught and return None."""

    def test_returns_none_on_timeout(self):
        from src.collectors.video_clipper import clip_youtube_video

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120),
            ),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None


class TestClipYoutubeVideoUnexpectedException:
    """Any unexpected exception must be caught and return None."""

    def test_returns_none_on_os_error(self):
        from src.collectors.video_clipper import clip_youtube_video

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", side_effect=OSError("yt-dlp not found")),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None

    def test_returns_none_on_runtime_error(self):
        from src.collectors.video_clipper import clip_youtube_video

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", side_effect=RuntimeError("unexpected")),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None


class TestClipYoutubeVideoFileTooLarge:
    """When the downloaded file exceeds _MAX_FILE_MB (50 MB) it should be
    removed and None returned."""

    def test_returns_none_when_file_exceeds_50mb(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            # skip(1)→F, cache(2)→F, post-download(3)→T
            return call_counter["n"] > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=51 * 1024 * 1024),  # 51 MB
            patch("os.remove") as mock_remove,
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None
        mock_remove.assert_called_once()

    def test_file_is_deleted_when_too_large(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)
        expected_path = str(_FAKE_MEDIA_DIR / f"yt_clip_{_FAKE_VIDEO_ID}.mp4")
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=60 * 1024 * 1024),  # 60 MB
            patch("os.remove") as mock_remove,
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        mock_remove.assert_called_once_with(expected_path)

    def test_exactly_50mb_is_accepted(self):
        """50 MB exactly should not be rejected (boundary: > 50, not >= 50)."""
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)
        expected_path = str(_FAKE_MEDIA_DIR / f"yt_clip_{_FAKE_VIDEO_ID}.mp4")
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 2

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=50 * 1024 * 1024),  # exactly 50 MB
            patch("os.remove") as mock_remove,
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result == expected_path
        mock_remove.assert_not_called()


class TestClipYoutubeVideoFileNotCreated:
    """yt-dlp exits 0 but the output file does not exist (e.g. format mismatch)."""

    def test_returns_none_when_file_missing_after_success(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=0)

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),  # never exists
            patch("subprocess.run", return_value=proc),
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None


class TestClipYoutubeVideoLiveStream:
    """Live-stream videos must create a .skip sentinel and return None."""

    def test_live_stream_creates_skip_sentinel(self):
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(
            returncode=1,
            stderr="ERROR: [youtube] abc123: This live event will begin in a few moments.",
        )

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.touch") as mock_touch,
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None
        mock_touch.assert_called_once()

    def test_skip_sentinel_prevents_retry(self):
        """Once .skip exists, yt-dlp must not be called again."""
        from src.collectors.video_clipper import clip_youtube_video

        def _exists(path):
            return str(path).endswith(".skip")  # sentinel present, mp4 absent

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("subprocess.run") as mock_run,
        ):
            mock_cookies.exists.return_value = True
            result = clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        assert result is None
        mock_run.assert_not_called()

    def test_non_live_stream_failure_does_not_create_sentinel(self):
        """Only 'live event' errors should create a .skip sentinel."""
        from src.collectors.video_clipper import clip_youtube_video

        proc = _make_completed_process(returncode=1, stderr="ERROR: network error")

        with (
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies,
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.touch") as mock_touch,
        ):
            mock_cookies.exists.return_value = True
            clip_youtube_video(_FAKE_VIDEO_URL, _FAKE_VIDEO_ID)

        mock_touch.assert_not_called()


class TestEnsureH264:
    """_ensure_h264 re-encodes non-H.264 videos in-place."""

    def test_no_action_for_h264(self):
        """If codec is already h264, no ffmpeg call is made."""
        from src.collectors.video_clipper import _ensure_h264

        ffprobe_result = _make_completed_process(returncode=0)
        ffprobe_result.stdout = "h264"

        with patch("subprocess.run", return_value=ffprobe_result) as mock_run:
            _ensure_h264("/some/path.mp4", "vid1")

        # Only ffprobe called, no ffmpeg
        assert mock_run.call_count == 1

    def test_reencodes_av1_to_h264(self):
        """AV1-encoded video triggers ffmpeg re-encode."""
        from src.collectors.video_clipper import _ensure_h264

        ffprobe_result = _make_completed_process(returncode=0)
        ffprobe_result.stdout = "av1"
        ffmpeg_result = _make_completed_process(returncode=0)

        call_count = {"n": 0}

        def _run(cmd, **kwargs):
            call_count["n"] += 1
            return ffprobe_result if call_count["n"] == 1 else ffmpeg_result

        with (
            patch("subprocess.run", side_effect=_run),
            patch("os.replace") as mock_replace,
            patch("os.path.exists", return_value=True),
        ):
            _ensure_h264("/some/path.mp4", "vid1")

        assert call_count["n"] == 2  # ffprobe + ffmpeg
        mock_replace.assert_called_once()

    def test_empty_codec_skips_reencode(self):
        """Empty ffprobe output (codec unknown) doesn't attempt re-encode."""
        from src.collectors.video_clipper import _ensure_h264

        ffprobe_result = _make_completed_process(returncode=0)
        ffprobe_result.stdout = ""

        with patch("subprocess.run", return_value=ffprobe_result) as mock_run:
            _ensure_h264("/some/path.mp4", "vid1")

        assert mock_run.call_count == 1  # only ffprobe, no ffmpeg


# ---------------------------------------------------------------------------
# clip_reddit_video
# ---------------------------------------------------------------------------

class TestClipRedditVideoCacheHit:
    """File already on disk — return immediately without calling yt-dlp."""

    def test_returns_existing_path_on_cache_hit(self):
        from src.collectors.video_clipper import clip_reddit_video

        expected_path = str(_FAKE_MEDIA_DIR / f"reddit_{_FAKE_POST_ID}.mp4")

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result == expected_path
        mock_run.assert_not_called()


class TestClipRedditVideoSuccess:
    """Happy-path: yt-dlp succeeds and file is within size limit."""

    def test_returns_output_path_on_success(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        expected_path = str(_FAKE_MEDIA_DIR / f"reddit_{_FAKE_POST_ID}.mp4")
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("src.collectors.video_clipper._COOKIES_PATH") as mock_yt_cookies,
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=5 * 1024 * 1024),  # 5 MB
            patch("subprocess.run", return_value=proc),
        ):
            mock_yt_cookies.exists.return_value = False
            # Reddit cookies path is computed inline inside the function
            with patch("pathlib.Path.exists", return_value=False):
                result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result == expected_path

    def test_subprocess_receives_correct_url(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=1024),
            patch("subprocess.run", return_value=proc) as mock_run,
            patch("pathlib.Path.exists", return_value=False),
        ):
            clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        cmd_used = mock_run.call_args[0][0]
        assert _FAKE_REDDIT_URL in cmd_used


class TestClipRedditVideoWithCookies:
    """When reddit_cookies.txt exists, --cookies flag must be added to the command."""

    def test_cookies_flag_added_when_reddit_cookies_exist(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _os_exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_os_exists),
            patch("os.path.getsize", return_value=1024),
            patch("subprocess.run", return_value=proc) as mock_run,
            patch("pathlib.Path.exists", return_value=True),  # reddit_cookies.txt present
        ):
            clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        cmd_used = mock_run.call_args[0][0]
        assert "--cookies" in cmd_used

    def test_cookies_flag_absent_when_reddit_cookies_missing(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _os_exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_os_exists),
            patch("os.path.getsize", return_value=1024),
            patch("subprocess.run", return_value=proc) as mock_run,
            patch("pathlib.Path.exists", return_value=False),  # reddit_cookies.txt absent
        ):
            clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        cmd_used = mock_run.call_args[0][0]
        assert "--cookies" not in cmd_used


class TestClipRedditVideoFailure:
    """Non-zero returncode must produce None."""

    def test_returns_none_on_nonzero_returncode(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=1, stderr="download failed")

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result is None


class TestClipRedditVideoFileTooLarge:
    """Files over 50 MB must be removed and None returned."""

    def test_returns_none_when_file_exceeds_50mb(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=55 * 1024 * 1024),  # 55 MB
            patch("os.remove") as mock_remove,
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result is None
        mock_remove.assert_called_once()

    def test_oversized_file_is_deleted(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)
        expected_path = str(_FAKE_MEDIA_DIR / f"reddit_{_FAKE_POST_ID}.mp4")
        call_counter = {"n": 0}

        def _exists(path):
            call_counter["n"] += 1
            return call_counter["n"] > 1

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", side_effect=_exists),
            patch("os.path.getsize", return_value=99 * 1024 * 1024),
            patch("os.remove") as mock_remove,
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.exists", return_value=False),
        ):
            clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        mock_remove.assert_called_once_with(expected_path)


class TestClipRedditVideoTimeout:
    """TimeoutExpired must be caught and return None."""

    def test_returns_none_on_timeout(self):
        from src.collectors.video_clipper import clip_reddit_video

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=60),
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result is None


class TestClipRedditVideoException:
    """Any generic exception must be caught and return None."""

    def test_returns_none_on_generic_exception(self):
        from src.collectors.video_clipper import clip_reddit_video

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", side_effect=OSError("yt-dlp not installed")),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result is None


class TestClipRedditVideoFileNotCreated:
    """yt-dlp exits 0 but output file does not exist."""

    def test_returns_none_when_file_missing_after_success(self):
        from src.collectors.video_clipper import clip_reddit_video

        proc = _make_completed_process(returncode=0)

        with (
            patch("src.collectors.video_clipper.MEDIA_DIR", _FAKE_MEDIA_DIR),
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=proc),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = clip_reddit_video(_FAKE_REDDIT_URL, _FAKE_POST_ID)

        assert result is None


# ---------------------------------------------------------------------------
# cookies_available
# ---------------------------------------------------------------------------

class TestCookiesAvailable:
    """Test the cookies_available() helper."""

    def test_returns_true_when_file_exists_and_large_enough(self):
        from src.collectors.video_clipper import cookies_available

        stat_mock = MagicMock()
        stat_mock.st_size = 500  # > 100 bytes

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = True
            mock_cookies.stat.return_value = stat_mock
            result = cookies_available()

        assert result is True

    def test_returns_false_when_file_missing(self):
        from src.collectors.video_clipper import cookies_available

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = False
            result = cookies_available()

        assert result is False

    def test_returns_false_when_file_too_small(self):
        """File exists but contains fewer than 100 bytes (likely empty/placeholder)."""
        from src.collectors.video_clipper import cookies_available

        stat_mock = MagicMock()
        stat_mock.st_size = 50  # <= 100 bytes

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = True
            mock_cookies.stat.return_value = stat_mock
            result = cookies_available()

        assert result is False

    def test_returns_false_when_file_exactly_100_bytes(self):
        """Boundary: exactly 100 bytes should still return False (condition is > 100)."""
        from src.collectors.video_clipper import cookies_available

        stat_mock = MagicMock()
        stat_mock.st_size = 100  # not > 100

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = True
            mock_cookies.stat.return_value = stat_mock
            result = cookies_available()

        assert result is False

    def test_returns_true_when_file_is_101_bytes(self):
        """One byte over the threshold — should pass."""
        from src.collectors.video_clipper import cookies_available

        stat_mock = MagicMock()
        stat_mock.st_size = 101

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = True
            mock_cookies.stat.return_value = stat_mock
            result = cookies_available()

        assert result is True

    def test_stat_not_called_when_file_absent(self):
        """stat() must not be called if exists() returns False (short-circuit)."""
        from src.collectors.video_clipper import cookies_available

        with patch("src.collectors.video_clipper._COOKIES_PATH") as mock_cookies:
            mock_cookies.exists.return_value = False
            cookies_available()

        mock_cookies.stat.assert_not_called()
