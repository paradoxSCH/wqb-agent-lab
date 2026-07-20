from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wqb_agent_lab.llm.provider import LLMProviderError, LLMRequest
from wqb_agent_lab.llm.provider import cli_process
from wqb_agent_lab.llm.provider.providers.cli import CLIProvider


class CLIProviderTests(unittest.TestCase):
    def _script(self, root: Path, source: str) -> Path:
        path = root / "provider_fixture.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    def test_argument_transport_substitutes_only_supported_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(
                root,
                """
                import sys
                print("|".join(sys.argv[1:]))
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(
                    sys.executable,
                    str(script),
                    "{system_prompt}",
                    "{prompt}",
                    "{model}",
                    "{workspace_root}",
                ),
                prompt_transport="argument",
                workspace_root=root,
            )
            result = provider.complete(LLMRequest("sys prompt", "user prompt"))
            self.assertEqual(
                f"sys prompt|user prompt|cli-test|{root.resolve()}", result.content
            )
            self.assertEqual("cli", result.provider)
            self.assertEqual("cli-test", result.model)

    def test_argument_values_remain_single_process_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(
                root,
                """
                import json
                import sys
                print(json.dumps({"content": json.dumps(sys.argv[1:])}))
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script), "prefix={prompt}"),
                prompt_transport="argument",
                workspace_root=root,
            )
            result = provider.complete(LLMRequest("sys", "a; echo unsafe value"))
            self.assertEqual(["prefix=a; echo unsafe value"], json.loads(result.content))

    def test_process_invocation_explicitly_disables_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(root, "print('answer')")
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script), "{prompt}"),
                prompt_transport="argument",
                workspace_root=root,
            )
            with patch(
                "wqb_agent_lab.llm.provider.cli_process.subprocess.Popen",
                wraps=subprocess.Popen,
            ) as popen:
                provider.complete(LLMRequest("sys", "user"))
        self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_rejects_windows_batch_and_missing_executable_before_spawn(self):
        cases = (
            ("unsafe.cmd", "invalid_configuration"),
            ("UNSAFE.BAT", "invalid_configuration"),
            ("definitely-missing-wqb-cli-executable", "invalid_configuration"),
        )
        for executable, code in cases:
            with self.subTest(executable=executable):
                with self.assertRaises(LLMProviderError) as raised:
                    CLIProvider(
                        model="cli-test",
                        command=(executable, "{prompt}"),
                        prompt_transport="argument",
                        workspace_root=Path.cwd(),
                    )
                self.assertEqual(code, raised.exception.code)

    def test_resolves_native_executable_from_path_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(root, "print('native')")
            executable = Path(sys.executable)
            with patch.dict(os.environ, {"PATH": str(executable.parent)}):
                provider = CLIProvider(
                    model="cli-test",
                    command=(executable.name, str(script)),
                    prompt_transport="stdin",
                    workspace_root=root,
                )
                result = provider.complete(LLMRequest("sys", "user"))
            self.assertEqual("native", result.content)

    def test_stdin_transport_sends_exact_json_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(
                root,
                """
                import json
                import sys
                payload = json.load(sys.stdin)
                print(json.dumps({"content": json.dumps(payload, sort_keys=True)}))
                """,
            )
            provider = CLIProvider(
                model="stdin-model",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
            )
            result = provider.complete(
                LLMRequest("system", "user", response_format="json")
            )
            self.assertEqual(
                {
                    "model": "stdin-model",
                    "response_format": "json",
                    "system_prompt": "system",
                    "user_prompt": "user",
                },
                json.loads(result.content),
            )

    def test_child_receives_allowlisted_environment_and_only_provider_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(
                root,
                """
                import json
                import os
                print(json.dumps({"content": json.dumps({
                    "credential_present": "CLI_PROVIDER_KEY" in os.environ,
                    "credential": os.environ.get("CLI_PROVIDER_KEY"),
                    "wqb_password": os.environ.get("WQB_PASSWORD"),
                    "unrelated_key": os.environ.get("UNRELATED_API_KEY"),
                })}))
                """,
            )
            secret = "test-provider-secret-value"
            with patch.dict(
                os.environ,
                {
                    "WQB_PASSWORD": "must-not-leak",
                    "UNRELATED_API_KEY": "must-not-leak-either",
                },
            ):
                provider = CLIProvider(
                    model="cli-test",
                    command=(sys.executable, str(script)),
                    prompt_transport="stdin",
                    workspace_root=root,
                    credential_env_name="CLI_PROVIDER_KEY",
                    credential_value=secret,
                )
                result = provider.complete(
                    LLMRequest("sys", "user", response_format="json")
                )

            environment = json.loads(result.content)
            self.assertTrue(environment["credential_present"])
            self.assertEqual("<redacted>", environment["credential"])
            self.assertIsNone(environment["wqb_password"])
            self.assertIsNone(environment["unrelated_key"])
            self.assertNotIn(secret, json.dumps(dict(result.raw_response)))

    def test_json_stdout_object_maps_content_and_validates_json_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(
                root,
                """
                import json
                print(json.dumps({"content": "{\\\"ok\\\": true}", "trace": "local"}))
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
            )
            result = provider.complete(
                LLMRequest("sys", "user", response_format="json")
            )
            self.assertEqual({"ok": True}, json.loads(result.content))
            self.assertEqual("local", result.raw_response["trace"])

    def test_json_mode_accepts_raw_business_object_and_array(self):
        cases = (
            (
                '{"nested": {"b": 2, "a": 1}, "alpha": 1}',
                '{"alpha":1,"nested":{"a":1,"b":2}}',
            ),
            ('[{"b": 2, "a": 1}, 3]', '[{"a":1,"b":2},3]'),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stdout, expected in cases:
                script = self._script(
                    root, f"import sys\nsys.stdout.write({stdout!r})\n"
                )
                provider = CLIProvider(
                    model="cli-test",
                    command=(sys.executable, str(script)),
                    prompt_transport="stdin",
                    workspace_root=root,
                )

                result = provider.complete(
                    LLMRequest("sys", "user", response_format="json")
                )

                self.assertEqual(expected, result.content)

    def test_text_mode_preserves_raw_json_that_is_not_a_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout = '{"other": true}'
            script = self._script(
                root, f"import sys\nsys.stdout.write({stdout!r})\n"
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
            )

            result = provider.complete(LLMRequest("sys", "user"))

            self.assertEqual(stdout, result.content)

    def test_json_stdout_redacts_known_secret_before_serialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = 'quote"secret'
            script = self._script(
                root,
                """
                import json
                import sys
                value = sys.argv[1]
                print(json.dumps({"content": json.dumps({"token": value}), "echo": value}))
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script), secret),
                prompt_transport="stdin",
                workspace_root=root,
                secrets=(secret,),
            )

            result = provider.complete(
                LLMRequest("sys", "user", response_format="json")
            )

            serialized = json.dumps(
                {"content": result.content, "raw": dict(result.raw_response)}
            )
            self.assertNotIn(secret, serialized)
            self.assertEqual("<redacted>", json.loads(result.content)["token"])

    def test_plain_stdout_is_returned_as_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(root, "print('plain answer')")
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
            )
            result = provider.complete(LLMRequest("sys", "user"))
            self.assertEqual("plain answer", result.content)

    def test_relative_working_directory_resolves_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "nested"
            child.mkdir()
            script = self._script(
                root,
                """
                from pathlib import Path
                print(Path.cwd())
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
                working_directory="nested",
            )
            result = provider.complete(LLMRequest("sys", "user"))
            self.assertEqual(str(child.resolve()), result.content)

    def test_rejects_unknown_placeholder_and_workspace_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            for kwargs in (
                {"command": ("fake", "{unknown}")},
                {"command": ("fake", "{prompt}"), "working_directory": ".."},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(
                    LLMProviderError
                ) as raised:
                    CLIProvider(
                        model="cli-test",
                        prompt_transport="argument",
                        workspace_root=root,
                        **kwargs,
                    )
                self.assertEqual("invalid_configuration", raised.exception.code)

    def test_rejects_invalid_direct_credential_values(self):
        cases = (
            {"credential_env_name": None},
            {"credential_env_name": 123},
            {"credential_env_name": []},
            {"credential_env_name": "   "},
            {"credential_env_name": "VALID", "credential_value": 123},
            {"credential_env_name": "VALID", "credential_value": []},
            {"credential_env_name": "VALID", "credential_value": ""},
            {"credential_env_name": "VALID", "credential_value": "   "},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(LLMProviderError) as raised:
                    CLIProvider(
                        model="cli-test",
                        command=(sys.executable,),
                        prompt_transport="stdin",
                        workspace_root=Path.cwd(),
                        **values,
                    )
                self.assertEqual("invalid_configuration", raised.exception.code)

    def test_argument_transport_requires_prompt_placeholder(self):
        with self.assertRaises(LLMProviderError) as raised:
            CLIProvider(
                model="cli-test",
                command=("fake", "{model}"),
                prompt_transport="argument",
                workspace_root=Path.cwd(),
            )
        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_timeout_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(root, "import time; time.sleep(5)")
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script)),
                prompt_transport="stdin",
                workspace_root=root,
                timeout_seconds=1,
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("sys", "user"))
            self.assertEqual("timeout", raised.exception.code)
            self.assertTrue(raised.exception.retryable)

    def test_timeout_terminates_spawned_child_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "delayed-marker.txt"
            child = root / "child.py"
            child.write_text(
                "import pathlib, sys, time\n"
                "time.sleep(2)\n"
                "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')\n",
                encoding="utf-8",
            )
            parent = self._script(
                root,
                """
                import subprocess
                import sys
                import time
                subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
                time.sleep(10)
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(parent), str(child), str(marker)),
                prompt_transport="stdin",
                workspace_root=root,
                timeout_seconds=1,
            )

            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("sys", "user"))
            time.sleep(2.25)

            self.assertEqual("timeout", raised.exception.code)
            self.assertFalse(marker.exists())

    def test_windows_job_termination_does_not_race_taskkill(self):
        process = Mock(pid=12345)
        process.poll.return_value = 0
        windows_job = Mock()

        with patch.object(cli_process.os, "name", "nt"), patch.object(
            cli_process.subprocess, "Popen"
        ) as popen:
            cli_process._terminate_process_tree(process, {}, windows_job)

        windows_job.terminate.assert_called_once_with()
        popen.assert_not_called()
        process.kill.assert_not_called()

    def test_post_spawn_initialization_failures_cleanup_process_tree(self):
        targets = (
            "wqb_agent_lab.llm.provider.cli_process._create_windows_job",
            "wqb_agent_lab.llm.provider.cli_process._reader_thread",
            "wqb_agent_lab.llm.provider.cli_process._writer_thread",
        )
        real_popen = subprocess.Popen
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "failure-marker.txt"
                child = root / "failure-child.py"
                child.write_text(
                    "import pathlib, sys, time\n"
                    "time.sleep(2)\n"
                    "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')\n",
                    encoding="utf-8",
                )
                parent = self._script(
                    root,
                    """
                    import subprocess
                    import sys
                    import time
                    subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
                    time.sleep(10)
                    """,
                )
                provider = CLIProvider(
                    model="cli-test",
                    command=(sys.executable, str(parent), str(child), str(marker)),
                    prompt_transport="stdin",
                    workspace_root=root,
                )
                spawned = []

                def record_popen(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    spawned.append(process)
                    return process

                def fail_initialization(*args, **kwargs):
                    del args, kwargs
                    time.sleep(0.3)
                    raise RuntimeError("injected lifecycle failure")

                with patch(
                    "wqb_agent_lab.llm.provider.cli_process.subprocess.Popen",
                    side_effect=record_popen,
                ), patch(target, side_effect=fail_initialization):
                    with self.assertRaises(RuntimeError):
                        provider.complete(LLMRequest("sys", "user"))

                self.assertTrue(spawned)
                self.assertIsNotNone(spawned[0].poll())
                time.sleep(2.25)
                self.assertFalse(marker.exists())

    def test_missing_executable_is_rejected_before_spawn(self):
        with self.assertRaises(LLMProviderError) as raised:
            CLIProvider(
                model="cli-test",
                command=("definitely-missing-wqb-llm-executable",),
                prompt_transport="stdin",
                workspace_root=Path.cwd(),
            )
        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_embedded_nul_value_error_is_normalized_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._script(root, "print('must not run')")
            secret = "test-sensitive\0value"
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script), "{prompt}"),
                prompt_transport="argument",
                workspace_root=root,
                secrets=(secret,),
            )

            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("sys", secret))

            payload = json.dumps(raised.exception.to_dict())
            self.assertEqual("process_error", raised.exception.code)
            self.assertNotIn("sensitive", payload)
            self.assertIn("reason", raised.exception.details)

    def test_nonzero_exit_truncates_and_redacts_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = 'quote"secret'
            script = self._script(
                root,
                """
                import sys
                sys.stderr.write(sys.argv[1] + ("x" * 5000))
                raise SystemExit(7)
                """,
            )
            provider = CLIProvider(
                model="cli-test",
                command=(sys.executable, str(script), secret),
                prompt_transport="stdin",
                workspace_root=root,
                secrets=(secret,),
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("sys", "user"))
            payload = json.dumps(raised.exception.to_dict())
            self.assertEqual("process_error", raised.exception.code)
            self.assertEqual(7, raised.exception.details["exit_code"])
            self.assertNotIn(secret, payload)
            self.assertLessEqual(len(raised.exception.details["stderr"]), 2048)

    def test_stdout_and_stderr_are_bounded_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stream in ("stdout", "stderr"):
                script = self._script(
                    root,
                    f"""
                    import sys
                    import time
                    sys.{stream}.write("x" * 100000)
                    sys.{stream}.flush()
                    time.sleep(10)
                    """,
                )
                provider = CLIProvider(
                    model="cli-test",
                    command=(sys.executable, str(script)),
                    prompt_transport="stdin",
                    workspace_root=root,
                    stdout_limit_bytes=1024,
                    stderr_limit_bytes=1024,
                )

                with self.subTest(stream=stream), self.assertRaises(
                    LLMProviderError
                ) as raised:
                    provider.complete(LLMRequest("sys", "user"))

                self.assertEqual("process_error", raised.exception.code)
                self.assertEqual(stream, raised.exception.details["stream"])
                self.assertEqual(1024, raised.exception.details["limit_bytes"])
                self.assertGreater(
                    raised.exception.details["observed_bytes"], 1024
                )
                self.assertLessEqual(
                    len(raised.exception.details[f"{stream}_excerpt"]), 1024
                )

    def test_empty_or_invalid_structured_output_is_rejected(self):
        cases = (
            ("", "text", "invalid_response"),
            ("not-json", "json", "invalid_structured_output"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (stdout, response_format, code) in enumerate(cases):
                script = self._script(
                    root, f"import sys\nsys.stdout.write({stdout!r})\n"
                )
                provider = CLIProvider(
                    model=f"cli-test-{index}",
                    command=(sys.executable, str(script)),
                    prompt_transport="stdin",
                    workspace_root=root,
                )
                with self.subTest(stdout=stdout):
                    with self.assertRaises(LLMProviderError) as raised:
                        provider.complete(
                            LLMRequest(
                                "sys", "user", response_format=response_format
                            )
                        )
                    self.assertEqual(code, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
