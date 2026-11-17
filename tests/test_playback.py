import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.playback import cache_path, for_client, forget, should_convert


class PlaybackTests(unittest.TestCase):
    def test_phone_asks_for_aac_and_desktop_chrome_does_not(self):
        self.assertTrue(should_convert("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", ""))
        self.assertTrue(should_convert("Mozilla/5.0 (Macintosh) Version/17.0 Safari/605.1.15", ""))
        self.assertTrue(should_convert("Mozilla/5.0 Chrome/120.0.0.0", "m4a"))
        self.assertFalse(
            should_convert(
                "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "",
            )
        )

    def test_webm_is_copied_for_the_phone_and_reused(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "turn-01-retell.webm"
            source.write_bytes(b"webm")
            calls = []

            def runner(_source, target):
                calls.append(_source)
                target.write_bytes(b"m4a-audio-bytes-long-enough-for-a-file")

            path, mime = for_client(source, "audio/webm", "iPhone", "", runner)
            again, again_mime = for_client(source, "audio/webm", "iPhone", "", runner)
            desktop, desktop_mime = for_client(source, "audio/webm", "Chrome/120.0.0.0", "", runner)
            self.assertEqual(path, cache_path(source))
            self.assertEqual(again, path)
            self.assertEqual(mime, "audio/mp4")
            self.assertEqual(again_mime, "audio/mp4")
            self.assertEqual(path.read_bytes(), b"m4a-audio-bytes-long-enough-for-a-file")
            self.assertEqual(calls, [source])
            self.assertEqual(desktop, source)
            self.assertEqual(desktop_mime, "audio/webm")

    def test_a_newer_recording_is_converted_again(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "turn-01-retell.webm"
            source.write_bytes(b"webm")
            calls = []

            def runner(_source, target):
                calls.append(target.stat().st_size if target.exists() else 0)
                target.write_bytes(b"m4a-audio-2-bytes-long-enough-for-a-file")

            for_client(source, "audio/webm", "", "m4a", runner)
            cached = cache_path(source)
            stamp = cached.stat().st_mtime
            source.write_bytes(b"webm-new")
            os.utime(source, (stamp + 5, stamp + 5))
            for_client(source, "audio/webm", "", "m4a", runner)
            self.assertEqual(len(calls), 2)
            self.assertEqual(cached.read_bytes(), b"m4a-audio-2-bytes-long-enough-for-a-file")

    def test_wav_is_left_alone_and_a_removed_recording_drops_the_copy(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "turn-01-speech.wav"
            source.write_bytes(b"wav")
            path, mime = for_client(source, "audio/wav", "iPhone", "m4a")
            self.assertEqual((path, mime), (source, "audio/wav"))
            webm = Path(folder) / "turn-01-retell.webm"
            webm.write_bytes(b"webm")
            copy = cache_path(webm)
            copy.write_bytes(b"m4a")
            forget(webm)
            self.assertFalse(copy.exists())
