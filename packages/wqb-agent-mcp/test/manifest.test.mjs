import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const root = resolve(import.meta.dirname, "..");
const manifestSource = readFileSync(resolve(root, "src", "toolManifest.ts"), "utf8");
const engineSource = readFileSync(resolve(root, "src", "engineClient.ts"), "utf8");
const serverSource = readFileSync(resolve(root, "src", "server.ts"), "utf8");
const mainSource = readFileSync(resolve(root, "src", "main.ts"), "utf8");
const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));

assert.equal(packageJson.name, "@wqb-agent-lab/mcp");
assert.match(engineSource, /wqb-engine/);
assert.ok(packageJson.dependencies["@modelcontextprotocol/sdk"]);
assert.ok(packageJson.scripts.build);
assert.ok(packageJson.scripts.start);
assert.match(engineSource, /WQB_ENGINE_COMMAND/);
assert.match(engineSource, /\.venv/);
assert.match(engineSource, /wqb-engine\.exe/);
assert.match(serverSource, /@modelcontextprotocol\/sdk\/server\/mcp\.js/);
assert.match(mainSource, /@modelcontextprotocol\/sdk\/server\/stdio\.js/);
assert.match(serverSource, /readOnlyHint: true/);
assert.match(serverSource, /destructiveHint: false/);

for (const name of [
  "schemas.list",
  "schemas.digest",
  "contracts.validate",
  "submission.evaluate",
  "submission.submit_intent",
  "submission.execute_live",
  "submission.audit_tail",
  "loop.dry_run_validate",
  "policy.validate",
  "policy.show",
]) {
  assert.match(manifestSource, new RegExp(`name: "${name}"`));
  assert.match(manifestSource, new RegExp(`engineOperation: "${name}"`));
  assert.match(serverSource, new RegExp(`"${name}"`));
}

for (const forbidden of ["api.worldquantbrain.com", "WQB_EMAIL", "WQB_PASSWORD"]) {
  assert.equal(engineSource.includes(forbidden), false);
  assert.equal(manifestSource.includes(forbidden), false);
  assert.equal(serverSource.includes(forbidden), false);
}
