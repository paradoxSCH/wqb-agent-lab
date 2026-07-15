import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const root = resolve(import.meta.dirname, "..");
const source = readFileSync(resolve(root, "src", "runSummaryView.ts"), "utf8");
const appSource = readFileSync(resolve(root, "src", "App.tsx"), "utf8");
const styleSource = readFileSync(resolve(root, "src", "styles.css"), "utf8");
const html = readFileSync(resolve(root, "index.html"), "utf8");
const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));

assert.equal(packageJson.name, "@wqb-agent-lab/ui");
assert.ok(packageJson.dependencies.react);
assert.ok(packageJson.devDependencies.vite);
assert.ok(packageJson.dependencies["lucide-react"]);
assert.match(source, /contract: "run_summary"/);
assert.match(source, /toRunSummaryViewModel/);
assert.match(source, /budgetRemaining/);
assert.match(source, /submitReady/);
assert.match(appSource, /研究边界/);
assert.match(appSource, /行为经济学逻辑库/);
assert.match(appSource, /分层记忆/);
assert.match(appSource, /fetch\("\/api\/policy"/);
assert.match(appSource, /method: "PUT"/);
assert.match(styleSource, /oklch/);
assert.match(html, /id="root"/);

for (const forbidden of ["api.worldquantbrain.com", "WQB_EMAIL", "WQB_PASSWORD", "src/wqb"]) {
  assert.equal(source.includes(forbidden), false);
  assert.equal(appSource.includes(forbidden), false);
}
