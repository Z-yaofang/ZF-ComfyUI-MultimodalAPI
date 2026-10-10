import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("zf_video_cleanup", Path(__file__).resolve().parents[1] / "nodes.py")
nodes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nodes)


class VideoCleanupTests(unittest.TestCase):
    def check_failure(self, fail_call):
        with tempfile.TemporaryDirectory(prefix="zf_cleanup_test_") as directory:
            root = Path(directory)
            source, first, second = (root / name for name in ("source.mp4", "first.mp4", "second.mp4"))
            source.write_bytes(b"original" * 10)
            calls = []

            def fail_encoder(command, operation):
                calls.append(operation)
                Path(command[-1]).write_bytes(b"partially-encoded" * 10)
                if len(calls) == fail_call:
                    raise RuntimeError("synthetic encoding failure")

            with patch.object(nodes, "_video_duration", return_value=100), \
                 patch.object(nodes.shutil, "which", return_value="fake-ffmpeg"), \
                 patch.object(nodes, "_new_temp_path", side_effect=[first, second]), \
                 patch.object(nodes, "_run_ffmpeg", side_effect=fail_encoder):
                with self.assertRaisesRegex(RuntimeError, "synthetic encoding failure"):
                    nodes._fit_video(source, 15, 1)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(source.read_bytes(), b"original" * 10)

    def test_first_compression_failure_cleans_partial_output(self):
        self.check_failure(1)

    def test_second_compression_failure_cleans_both_outputs(self):
        self.check_failure(2)


if __name__ == "__main__":
    unittest.main()
