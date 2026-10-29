"""Tests for INSTALL-PROMPT.md: up to date, and its embedded files extract intact."""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROMPT = REPO / "INSTALL-PROMPT.md"


class InstallPromptTest(unittest.TestCase):
    def test_up_to_date(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts/build_install_prompt.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_embedded_extractor_matches_manifest(self):
        text = PROMPT.read_text(encoding="utf-8")
        script = re.search(r"<<'PY'\n(.*?)\n   PY\n", text, re.S).group(1)
        script = "\n".join(line[3:] for line in script.splitlines())
        manifest = re.findall(r"^([0-9a-f]{64})  (\S+)$", text, re.M)
        self.assertGreater(len(manifest), 10)
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-c", script, str(PROMPT), tmp], check=True)
            for digest, path in manifest:
                data = (Path(tmp) / path).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, path)
                self.assertEqual(data, (REPO / path).read_bytes(), path)
            self.assertTrue(os.access(Path(tmp) / "install.sh", os.X_OK))
            self.assertTrue(os.access(Path(tmp) / "bin/kb", os.X_OK))


if __name__ == "__main__":
    unittest.main()
