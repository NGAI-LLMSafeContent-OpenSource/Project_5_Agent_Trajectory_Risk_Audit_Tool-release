import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_app", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class StudioAppTest(unittest.TestCase):
    def test_core_workflows_and_download(self):
        with tempfile.TemporaryDirectory() as temp:
            app.OUTPUTS = Path(temp)
            paths = []
            for name, mode, adapter in [("openai_messages_smoke.json", "run", "openai-messages"), ("atbench_smoke.json", "batch", "atbench"), ("hintbench_smoke.json", "batch", "hintbench")]:
                result, archive = app.audit_file(ROOT / "fixtures/smoke" / name, mode, adapter)
                self.assertTrue(result["audit_results"])
                with zipfile.ZipFile(archive) as z:
                    self.assertTrue(any(n.endswith("index.html") for n in z.namelist()))
                    self.assertTrue(any(n.endswith("audit_result.json") for n in z.namelist()))
                paths.append(archive)
            self.assertEqual(len(set(paths)), 3)
    def test_rejects_invalid_mode_and_oversized_input(self):
        with self.assertRaises(ValueError):
            app.audit_file(ROOT / "fixtures/smoke/openai_messages_smoke.json", "shell")
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"large.json";p.write_bytes(b" " * (app.MAX_BYTES+1))
            with self.assertRaises(ValueError):app.audit_file(p)

if __name__ == "__main__":unittest.main()
