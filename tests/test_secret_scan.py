from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.secret_scan import GITLEAKS_GO_MODULE, gitleaks_command


class SecretScanTests(unittest.TestCase):
    def test_prefers_installed_gitleaks(self) -> None:
        with patch("scripts.checks.secret_scan.shutil.which", side_effect=lambda name: "gitleaks-bin" if name == "gitleaks" else None):
            command = gitleaks_command(Path("snapshot"), Path("report.json"))

        self.assertEqual("gitleaks-bin", command[0])
        self.assertIn("dir", command)
        self.assertIn("--redact", command)

    def test_falls_back_to_pinned_go_module(self) -> None:
        with patch(
            "scripts.checks.secret_scan.shutil.which",
            side_effect=lambda name: "go-bin" if name == "go" else None,
        ):
            command = gitleaks_command(Path("snapshot"), Path("report.json"))

        self.assertEqual(("go-bin", "run", GITLEAKS_GO_MODULE), command[:3])


if __name__ == "__main__":
    unittest.main()
