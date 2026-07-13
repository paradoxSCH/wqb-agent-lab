import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveEngineCommand, runEngine } from "../dist/engineClient.js";

test("engine command prefers explicit environment override", () => {
  const command = resolveEngineCommand({ cwd: "C:/workspace" }, "win32", {
    WQB_ENGINE_COMMAND: "C:/tools/custom-engine.exe",
  });
  assert.equal(command, "C:/tools/custom-engine.exe");
});

test("Windows engine command uses workspace virtualenv executable", () => {
  const root = mkdtempSync(join(tmpdir(), "wqb-mcp-"));
  const executable = join(root, ".venv", "Scripts", "wqb-engine.exe");
  mkdirSync(join(root, ".venv", "Scripts"), { recursive: true });
  writeFileSync(executable, "");

  assert.equal(resolveEngineCommand({ cwd: root }, "win32", {}), executable);
});

test("Windows engine command finds repository virtualenv from package cwd", () => {
  const root = mkdtempSync(join(tmpdir(), "wqb-mcp-package-"));
  const packageRoot = join(root, "packages", "wqb-agent-mcp");
  const executable = join(root, ".venv", "Scripts", "wqb-engine.exe");
  mkdirSync(packageRoot, { recursive: true });
  mkdirSync(join(root, ".venv", "Scripts"), { recursive: true });
  writeFileSync(executable, "");

  assert.equal(resolveEngineCommand({ cwd: packageRoot }, "win32", {}), executable);
});

test("spawn failures become structured engine errors", async () => {
  const response = await runEngine("schemas.list", [], undefined, {
    command: join(tmpdir(), "definitely-missing-wqb-engine.exe"),
    timeoutMs: 1000,
  });

  assert.equal(response.ok, false);
  assert.equal(response.error.code, "engine_spawn_failed");
  assert.ok(response.error.details.some((detail) => detail.includes("command=")));
});
