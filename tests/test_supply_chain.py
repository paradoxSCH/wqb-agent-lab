from __future__ import annotations

import unittest

from scripts.checks.supply_chain import canonical_license, evaluate_license_inventory, license_ids


class SupplyChainTests(unittest.TestCase):
    def test_canonical_license_prefers_spdx_expression(self) -> None:
        self.assertEqual(
            "MIT OR Apache-2.0",
            canonical_license(
                {
                    "License-Expression": "MIT OR Apache-2.0",
                    "License": "ignored",
                    "Classifier": [],
                }
            ),
        )

    def test_canonical_license_recognizes_classifier_and_long_license_text(self) -> None:
        self.assertEqual(
            "MIT",
            canonical_license({"License": "MIT License\n" + "text" * 30, "Classifier": []}),
        )
        self.assertEqual(
            "BSD-3-Clause",
            canonical_license(
                {
                    "License": "",
                    "Classifier": ["License :: OSI Approved :: BSD License"],
                }
            ),
        )
        self.assertEqual("PSF-2.0", canonical_license({"License": "PSFL", "Classifier": []}))
        self.assertEqual("Apache-2.0", canonical_license({"License": "Apache 2.0", "Classifier": []}))
        self.assertEqual(
            "Apache-2.0 OR BSD-3-Clause",
            canonical_license(
                {
                    "License": "Dual License",
                    "Classifier": [
                        "License :: OSI Approved :: BSD License",
                        "License :: OSI Approved :: Apache Software License",
                    ],
                }
            ),
        )

    def test_license_ids_parse_compound_expression(self) -> None:
        self.assertEqual(
            frozenset({"BSD-3-Clause", "MIT", "Zlib"}),
            license_ids("BSD-3-Clause AND (MIT OR Zlib)"),
        )

    def test_license_policy_separates_unknown_and_disallowed(self) -> None:
        inventory = [
            {"name": "good", "version": "1", "license": "MIT"},
            {"name": "mystery", "version": "2", "license": "UNKNOWN"},
            {"name": "blocked", "version": "3", "license": "GPL-3.0-only"},
        ]

        unknown, disallowed = evaluate_license_inventory(
            inventory,
            {"allowed_spdx_ids": ["MIT"], "exceptions": []},
        )

        self.assertEqual(("mystery==2",), unknown)
        self.assertEqual(("blocked==3: GPL-3.0-only",), disallowed)

    def test_exception_requires_exact_version_and_rationale(self) -> None:
        inventory = [{"name": "special", "version": "1", "license": "UNKNOWN"}]

        accepted = evaluate_license_inventory(
            inventory,
            {
                "allowed_spdx_ids": [],
                "exceptions": [{"name": "special", "version": "1", "rationale": "Reviewed upstream."}],
            },
        )
        rejected = evaluate_license_inventory(
            inventory,
            {"allowed_spdx_ids": [], "exceptions": [{"name": "special", "version": "1", "rationale": ""}]},
        )

        self.assertEqual(((), ()), accepted)
        self.assertEqual((("special==1",), ()), rejected)


if __name__ == "__main__":
    unittest.main()
