from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.dashboard_assets import HTML_PAGE
from src.daily_workflow_dashboard import build_dashboard_model, build_run_snapshot, collect_evaluation_reports, collect_run_snapshots


class DailyWorkflowDashboardTests(unittest.TestCase):
    def test_build_run_snapshot_infers_partial_progress_from_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260511"
            run_dir.mkdir(parents=True)
            self._write_json(run_dir / "daily_budget_ledger.json", {
                "daily_run_tag": "deepseek-v4-pro-daily-budget-20260511",
                "date": "2026-05-11",
                "daily_budget": 1000,
                "spent_simulations": 0,
                "remaining_simulations_after_commitments": 1000,
                "stage_order": ["direction_probe", "scale_winners"],
                "stage_budgets": {"direction_probe": 120, "scale_winners": 360},
                "stage_spend": {},
                "current_stage": "initialized",
            })
            self._write_json(run_dir / "direction_probe_behavioral-efficient-500-20260504_results.json", [
                {"alpha_id": "A1"},
                {"alpha_id": "A2"},
            ])

            snapshot = build_run_snapshot(run_dir, now=datetime(2026, 5, 11, 14, 0))

            self.assertEqual(snapshot["health"], "inferred-active")
            self.assertEqual(snapshot["inferred_spent_simulations"], 2)
            self.assertEqual(snapshot["stages"][0]["status"], "partial")
            self.assertEqual(snapshot["stages"][0]["effective_spend"], 2)

    def test_collect_run_snapshots_sorts_latest_run_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260508"
            newer = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260511"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            self._write_json(older / "daily_budget_ledger.json", {
                "daily_run_tag": "deepseek-v4-pro-daily-budget-20260508",
                "date": "2026-05-08",
                "daily_budget": 1000,
                "spent_simulations": 1000,
                "remaining_simulations_after_commitments": 0,
                "stage_order": [],
                "stage_budgets": {},
                "stage_spend": {},
                "current_stage": "budget_complete_report_written",
            })
            self._write_json(newer / "daily_budget_ledger.json", {
                "daily_run_tag": "deepseek-v4-pro-daily-budget-20260511",
                "date": "2026-05-11",
                "daily_budget": 1000,
                "spent_simulations": 0,
                "remaining_simulations_after_commitments": 1000,
                "stage_order": [],
                "stage_budgets": {},
                "stage_spend": {},
                "current_stage": "initialized",
            })

            snapshots = collect_run_snapshots(root / ".local" / "data" / "runs" / "continuous-alpha", now=datetime(2026, 5, 11, 14, 0))

            self.assertEqual(snapshots[0]["run_tag"], "deepseek-v4-pro-daily-budget-20260511")
            self.assertEqual(snapshots[1]["run_tag"], "deepseek-v4-pro-daily-budget-20260508")

    def test_build_dashboard_model_summarizes_runs_for_agent_workbench(self) -> None:
        runs = [
            {
                "run_tag": "deepseek-v4-pro-daily-budget-20260511",
                "date": "2026-05-11",
                "health": "active",
                "daily_budget": 1000,
                "inferred_spent_simulations": 420,
                "current_stage": "scale_winners_partial",
                "stages": [
                    {"stage": "direction_probe", "status": "complete"},
                    {"stage": "scale_winners", "status": "partial"},
                ],
                "result_files": [{"name": "scale_winners_results.json"}],
            },
            {
                "run_tag": "deepseek-v4-pro-daily-budget-20260510",
                "date": "2026-05-10",
                "health": "complete",
                "daily_budget": 1000,
                "inferred_spent_simulations": 1000,
                "current_stage": "budget_complete_report_written",
                "stages": [],
                "result_files": [],
            },
        ]

        model = build_dashboard_model(runs)

        self.assertEqual(model["summary"]["run_count"], 2)
        self.assertEqual(model["summary"]["active_count"], 1)
        self.assertEqual(model["summary"]["complete_count"], 1)
        self.assertEqual(model["summary"]["latest_run_tag"], "deepseek-v4-pro-daily-budget-20260511")
        self.assertEqual(model["summary"]["budget_percent"], 71.0)
        self.assertEqual(model["navigation"][0]["id"], "boundaries")
        self.assertIn("memory", [item["id"] for item in model["navigation"]])
        self.assertIn("Memory briefing", [item["title"] for item in model["agent_panels"]])
        self.assertEqual(model["memory_layers"][0]["id"], "short_term")
        self.assertEqual(model["memory_layers"][1]["id"], "long_term")
        self.assertEqual(model["memory_layers"][2]["id"], "knowledge_graph")
        self.assertIn("short_term", model["memory_edges"][0]["from"])

    def test_build_dashboard_model_includes_memory_workbench_sections(self) -> None:
        model = build_dashboard_model([])

        json.dumps(model, ensure_ascii=False)

        navigation_ids = [item["id"] for item in model["navigation"]]
        self.assertEqual(navigation_ids, ["boundaries", "behavior", "memory", "evaluation", "runs", "system"])
        self.assertIn("retrieval_trace", model)
        self.assertIn("governance_queues", model)
        self.assertIn("hypothesis_ledger", model)
        self.assertIn("wqb_action_lanes", model)
        self.assertIn("adversarial_review", model)

        layer_ids = {layer["id"] for layer in model["memory_layers"]}
        self.assertEqual(layer_ids, {"short_term", "long_term", "knowledge_graph"})
        for layer in model["memory_layers"]:
            self.assertTrue({"id", "label", "zh_label", "scope", "retention", "policy", "items"} <= layer.keys())
            self.assertEqual(layer["items"], 0)

        for edge in model["memory_edges"]:
            self.assertIn(edge["from"], layer_ids)
            self.assertIn(edge["to"], layer_ids)

        self.assertEqual(model["retrieval_trace"]["query"], "budget + behavioral boundary")
        self.assertEqual(
            [step["stage"] for step in model["retrieval_trace"]["steps"]],
            ["query_rewrite", "fts_recall", "graph_expand", "rerank"],
        )

        self.assertEqual(
            model["governance_queues"],
            {
                "promotion": ["low-corr near-pass evidence", "submit-ready proxy mapping"],
                "decay": ["budget sink without near-pass", "high self-corr family"],
                "forgetting": ["non-actionable retrieved text", "decorative thesis without proxy"],
                "merge": ["duplicate operator skeleton", "same behavior thesis"],
            },
        )
        self.assertEqual(
            [lane["id"] for lane in model["wqb_action_lanes"]],
            ["probe", "scale", "repair", "block", "submit", "holdout"],
        )
        for entry in model["hypothesis_ledger"]:
            self.assertTrue({"thesis", "proxy", "kill_condition", "success"} <= entry.keys())

    def test_collect_evaluation_reports_reads_latest_ablation_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / ".local" / "data" / "evaluations" / "daily-run"
            report_dir.mkdir(parents=True)
            self._write_json(
                report_dir / "ablation_report.json",
                {
                    "verdict": "useful",
                    "metrics": ["submit_ready_per_1000"],
                    "variants": {"full_agent": {"submit_ready_per_1000": 6.0}},
                    "delta_vs_baseline": {"full_agent": {"submit_ready_per_1000": 3.0}},
                    "fairness": {"comparison_type": "controlled", "missing_variants": []},
                },
            )
            (report_dir / "summary.md").write_text("# Summary\n", encoding="utf-8")

            reports = collect_evaluation_reports(root / ".local" / "data" / "evaluations")

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["run_tag"], "daily-run")
            self.assertEqual(reports[0]["verdict"], "useful")
            self.assertEqual(reports[0]["comparison_type"], "controlled")
            self.assertEqual(reports[0]["report_path"], (report_dir / "ablation_report.json").as_posix())

    def test_build_dashboard_model_includes_agent_evaluation(self) -> None:
        evaluation_reports = [
            {
                "run_tag": "daily-run",
                "verdict": "inconclusive",
                "comparison_type": "observational",
                "missing_variants": ["memory_only"],
                "metrics": ["submit_ready_per_1000"],
                "variants": {"full_agent": {"submit_ready_per_1000": 6.0}},
                "delta_vs_baseline": {},
                "report_path": ".local/data/evaluations/daily-run/ablation_report.json",
            }
        ]

        model = build_dashboard_model([], evaluation_reports=evaluation_reports)

        self.assertEqual(model["agent_evaluation"]["summary"]["report_count"], 1)
        self.assertEqual(model["agent_evaluation"]["summary"]["latest_verdict"], "inconclusive")
        self.assertEqual(model["agent_evaluation"]["reports"][0]["comparison_type"], "observational")
        self.assertIn("evaluation", [item["id"] for item in model["navigation"]])

    def test_dashboard_html_defaults_to_chinese(self) -> None:
        self.assertIn('<html lang="zh-CN">', HTML_PAGE)
        self.assertIn("\u7814\u7a76\u8fb9\u754c", HTML_PAGE)
        self.assertIn("\u884c\u4e3a\u7ecf\u6d4e\u5b66\u8fb9\u754c", HTML_PAGE)
        self.assertIn("语言", HTML_PAGE)

    def test_dashboard_html_centers_user_work_on_budget_and_behavioral_boundaries(self) -> None:
        self.assertIn('id="view-boundaries"', HTML_PAGE)
        self.assertIn('id="daily-budget"', HTML_PAGE)
        self.assertIn('id="behavior-boundaries"', HTML_PAGE)
        self.assertIn("\u7528\u6237\u53ea\u9700\u7ef4\u62a4\u9884\u7b97\u548c\u884c\u4e3a\u7ecf\u6d4e\u5b66\u8fb9\u754c", HTML_PAGE)

    def test_dashboard_html_includes_chinese_behavioral_economics_library(self) -> None:
        self.assertIn("\u951a\u5b9a\u53cd\u8f6c", HTML_PAGE)
        self.assertIn("\u6295\u8d44\u8005\u8fc7\u5ea6\u4f9d\u8d56\u8fd1\u671f\u53c2\u7167\u70b9", HTML_PAGE)
        self.assertIn("\u8d28\u91cf\u4ef7\u503c\u9519\u5b9a\u4ef7", HTML_PAGE)

    def test_dashboard_html_exposes_layered_memory_management(self) -> None:
        self.assertIn('id="view-memory"', HTML_PAGE)
        self.assertIn('id="memory-layer-board"', HTML_PAGE)
        self.assertIn('id="memory-graph"', HTML_PAGE)
        self.assertIn("\u77ed\u671f\u8bb0\u5fc6", HTML_PAGE)
        self.assertIn("\u957f\u671f\u8bb0\u5fc6", HTML_PAGE)
        self.assertIn("\u77e5\u8bc6\u56fe\u8c31", HTML_PAGE)
        self.assertIn("\u664b\u5347\u3001\u8870\u51cf\u4e0e\u9057\u5fd8\u7b56\u7565", HTML_PAGE)
        self.assertIn("\u4f9d\u8d56\u5173\u7cfb\u56fe", HTML_PAGE)

    def test_dashboard_html_includes_memory_workbench_containers_and_copy(self) -> None:
        self.assertIn('id="retrieval-trace"', HTML_PAGE)
        self.assertIn('id="governance-queues"', HTML_PAGE)
        self.assertIn('id="hypothesis-ledger"', HTML_PAGE)
        self.assertIn('id="wqb-action-lanes"', HTML_PAGE)
        self.assertIn('id="adversarial-review"', HTML_PAGE)

        self.assertIn("\u5047\u8bbe\u8d26\u672c", HTML_PAGE)
        self.assertIn("\u4ee3\u7406\u6620\u5c04", HTML_PAGE)
        self.assertIn("Kill \u6761\u4ef6", HTML_PAGE)
        self.assertIn("WQB \u52a8\u4f5c\u961f\u5217", HTML_PAGE)
        self.assertIn("\u5bf9\u6297\u5ba1\u67e5", HTML_PAGE)
        self.assertIn("\u6cbb\u7406\u961f\u5217", HTML_PAGE)

    def test_dashboard_html_includes_agent_evaluation_view(self) -> None:
        self.assertIn('id="view-evaluation"', HTML_PAGE)
        self.assertIn('id="agent-evaluation-summary"', HTML_PAGE)
        self.assertIn('id="agent-evaluation-reports"', HTML_PAGE)
        self.assertIn("\u8bc4\u4f30", HTML_PAGE)
        self.assertIn("\u6d88\u878d\u5bf9\u7167", HTML_PAGE)
        self.assertIn("function renderAgentEvaluation()", HTML_PAGE)
        self.assertIn("renderAgentEvaluation();", HTML_PAGE)

    def test_dashboard_html_wires_memory_workbench_renderers(self) -> None:
        self.assertIn("function renderRetrievalTrace()", HTML_PAGE)
        self.assertIn("function renderGovernanceQueues()", HTML_PAGE)
        self.assertIn("function renderHypothesisLedger()", HTML_PAGE)
        self.assertIn("function renderWqbActionLanes()", HTML_PAGE)
        self.assertIn("function renderAdversarialReview()", HTML_PAGE)
        self.assertIn("renderRetrievalTrace();", HTML_PAGE)
        self.assertIn("renderGovernanceQueues();", HTML_PAGE)
        self.assertIn("renderHypothesisLedger();", HTML_PAGE)
        self.assertIn("renderWqbActionLanes();", HTML_PAGE)
        self.assertIn("renderAdversarialReview();", HTML_PAGE)

    def test_dashboard_html_defines_rendering_safety_helpers(self) -> None:
        self.assertIn("function escapeHtml(value)", HTML_PAGE)
        self.assertIn("function normalizeModel(model)", HTML_PAGE)
        self.assertIn("state.model = normalizeModel(payload.model);", self._js_function_body("refresh"))

        normalize_body = self._js_function_body("normalizeModel")
        for key in [
            "summary",
            "navigation",
            "agent_panels",
            "memory_layers",
            "memory_edges",
            "retrieval_trace",
            "governance_queues",
            "hypothesis_ledger",
            "wqb_action_lanes",
            "adversarial_review",
            "agent_evaluation",
        ]:
            self.assertIn(key, normalize_body)

    def test_dashboard_html_escapes_dynamic_inner_html_renderers(self) -> None:
        for name in [
            "metric",
            "renderNav",
            "renderRuns",
            "renderBehaviorLibrary",
            "renderMemoryLayers",
            "renderMemoryGraph",
            "renderAgentPanels",
            "renderRetrievalTrace",
            "renderGovernanceQueues",
            "renderHypothesisLedger",
            "renderWqbActionLanes",
            "renderAdversarialReview",
            "renderAgentEvaluation",
            "generatePlan",
        ]:
            self.assertIn("escapeHtml(", self._js_function_body(name), name)

    def test_dashboard_html_memory_workbench_copy_is_not_mojibake(self) -> None:
        for text in [
            "假设账本",
            "代理映射",
            "Kill 条件",
            "WQB 动作队列",
            "对抗审查",
            "治理队列",
        ]:
            self.assertIn(text, HTML_PAGE)
            for match in re.finditer(re.escape(text), HTML_PAGE):
                window = HTML_PAGE[max(0, match.start() - 80):match.end() + 80]
                self.assertNotIn("�", window)

    def test_dashboard_renderers_escape_malformed_memory_payload_in_node(self) -> None:
        script = self._inline_dashboard_script()
        harness = f"""
class FakeElement {{
  constructor(id) {{
    this.id = id;
    this.dataset = {{}};
    this.attributes = {{}};
    this._innerHTML = '';
    this.textContent = '';
    this.value = '';
    this.className = '';
    this.classList = {{ toggle() {{}} }};
  }}
  get innerHTML() {{ return this._innerHTML; }}
  set innerHTML(value) {{ this._innerHTML = String(value); }}
  addEventListener() {{}}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  querySelectorAll() {{ return []; }}
  focus() {{}}
  select() {{}}
}}

const elements = new Map();
const document = {{
  title: '',
  documentElement: new FakeElement('documentElement'),
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }},
  querySelectorAll() {{ return []; }}
}};
globalThis.document = document;
globalThis.fetch = async () => ({{ json: async () => ({{ runs: [], model: {{}} }}) }});
globalThis.setInterval = () => 0;

{script}

(async () => {{
  await Promise.resolve();
  await Promise.resolve();

  state.runs = [{{
    run_tag: '<img src=x onerror=alert(1)>',
    date: '<script>date</script>',
    health: '<b>active</b>',
    inferred_spent_simulations: '<img>',
    daily_budget: '<svg>',
    current_stage: '<script>stage</script>'
  }}];
  state.model = normalizeModel({{
    summary: null,
    navigation: 'bad',
    retrieval_trace: {{
      query: '<script>query()</script>',
      steps: [{{ stage: '<img src=x onerror=1>', body: '<script>body()</script>' }}]
    }},
    hypothesis_ledger: [{{
      thesis: '<img src=x onerror=alert(1)>',
      proxy: '<b>x</b>',
      kill_condition: '<script>x</script>',
      success: '<svg onload=1>'
    }}],
    governance_queues: {{ promotion: ['<img onerror=1>'] }},
    wqb_action_lanes: [{{ id: '<b>probe</b>', label: '<img>' }}],
    adversarial_review: ['<script>alert(1)</script>']
  }});

  renderAll();

  const rendered = [
    'run-list',
    'hypothesis-ledger',
    'governance-queues',
    'wqb-action-lanes',
    'adversarial-review',
    'retrieval-trace',
    'memory-layer-board',
    'memory-graph',
    'agent-panels'
  ].map((id) => document.getElementById(id).innerHTML).join('\\n');

  const requiredEscaped = [
    '&lt;img',
    '&lt;script',
    '&lt;b&gt;x&lt;/b&gt;',
    '&lt;svg',
    '&lt;script&gt;query()&lt;/script&gt;',
    '&lt;script&gt;body()&lt;/script&gt;',
    '&lt;img src=x onerror=1&gt;'
  ];
  for (const value of requiredEscaped) {{
    if (!rendered.includes(value)) throw new Error(`missing escaped value ${{value}}\\n${{rendered}}`);
  }}
  const forbiddenRaw = [
    '<img src=x',
    '<img onerror=1>',
    '<script>x</script>',
    '<script>alert(1)</script>',
    '<script>query()</script>',
    '<script>body()</script>',
    '<img src=x onerror=1>',
    '<svg onload=1>',
    '<b>x</b>',
    '<b>probe</b>'
  ];
  for (const value of forbiddenRaw) {{
    if (rendered.includes(value)) throw new Error(`raw dangerous HTML leaked ${{value}}\\n${{rendered}}`);
  }}
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
        with tempfile.TemporaryDirectory() as tmp:
            js_path = Path(tmp) / "dashboard_renderer_safety_test.js"
            js_path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(js_path)], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_dashboard_html_wires_memory_management_interaction(self) -> None:
        self.assertIn("function renderMemoryLayers()", HTML_PAGE)
        self.assertIn("function renderMemoryGraph()", HTML_PAGE)
        self.assertIn("function selectMemoryNode", HTML_PAGE)
        self.assertIn("function promoteMemoryNode()", HTML_PAGE)
        self.assertIn("function decayMemoryNode()", HTML_PAGE)
        self.assertIn("function forgetMemoryNode()", HTML_PAGE)
        self.assertIn('id="promote-memory-button"', HTML_PAGE)
        self.assertIn('id="decay-memory-button"', HTML_PAGE)
        self.assertIn('id="forget-memory-button"', HTML_PAGE)

    def test_dashboard_html_wires_generate_plan_interaction(self) -> None:
        self.assertIn('id="generate-plan-button"', HTML_PAGE)
        self.assertIn('id="plan-output"', HTML_PAGE)
        self.assertIn("function generatePlan()", HTML_PAGE)
        self.assertIn("addEventListener('click', generatePlan)", HTML_PAGE)
        self.assertIn('id="approve-plan-button"', HTML_PAGE)
        self.assertIn('id="edit-budget-button"', HTML_PAGE)
        self.assertIn("function approvePlan()", HTML_PAGE)
        self.assertIn("function editBudget()", HTML_PAGE)
        self.assertIn("function renderInspector()", HTML_PAGE)

    def test_dashboard_html_wires_thesis_creation_interaction(self) -> None:
        self.assertIn('id="add-thesis-button"', HTML_PAGE)
        self.assertIn("function addThesisCard()", HTML_PAGE)
        self.assertIn("addEventListener('click', addThesisCard)", HTML_PAGE)

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _js_function_body(self, name: str) -> str:
        match = re.search(rf"\n    (?:async )?function {re.escape(name)}\([^)]*\) {{", HTML_PAGE)
        self.assertIsNotNone(match, f"missing JavaScript function {name}")
        start = match.start()
        next_match = re.search(r"\n    (?:async )?function \w+\([^)]*\) {", HTML_PAGE[match.end():])
        end = match.end() + next_match.start() if next_match else HTML_PAGE.index("\n    document.getElementById", match.end())
        return HTML_PAGE[start:end]

    def _inline_dashboard_script(self) -> str:
        start_marker = "  <script>"
        end_marker = "  </script>"
        start = HTML_PAGE.index(start_marker) + len(start_marker)
        end = HTML_PAGE.index(end_marker, start)
        return HTML_PAGE[start:end]


if __name__ == "__main__":
    unittest.main()
