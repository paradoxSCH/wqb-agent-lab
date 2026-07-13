from __future__ import annotations

import io
import json
import unittest

from scripts.json_output import write_json_line


class JsonOutputTests(unittest.TestCase):
    def test_machine_json_is_ascii_safe_and_round_trips_unicode(self) -> None:
        stdout = io.StringIO()
        payload = {"message": "invalid output \ufffd with Chinese \u4e2d\u6587"}

        write_json_line(payload, stdout)

        encoded = stdout.getvalue().encode("ascii")
        self.assertEqual(payload, json.loads(encoded))
        self.assertTrue(stdout.getvalue().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
