import {
  Activity,
  BarChart3,
  BrainCircuit,
  Check,
  ChevronRight,
  Database,
  GitBranch,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import "./styles.css";

type JsonObject = Record<string, unknown>;

interface RunSnapshot {
  run_tag: string;
  current_stage: string;
  daily_budget: number;
  inferred_spent_simulations: number;
  remaining_simulations_after_commitments: number;
  health: string;
  latest_activity_at?: string;
  stages: Array<{ stage: string; budget: number; effective_spend: number; status: string }>;
}

interface DashboardModel {
  summary: Record<string, number | string | null>;
  memory_layers: Array<Record<string, string | number>>;
  memory_edges: Array<Record<string, string>>;
  retrieval_trace: { query: string; steps: Array<Record<string, string>> };
  governance_queues: Record<string, string[]>;
  hypothesis_ledger: Array<Record<string, string>>;
  wqb_action_lanes: Array<Record<string, string>>;
  adversarial_review: string[];
  agent_panels: Array<Record<string, string>>;
  agent_evaluation: { summary: Record<string, unknown>; reports: Array<Record<string, unknown>> };
}

interface DashboardPayload {
  generated_at: string;
  model: DashboardModel;
  runs: RunSnapshot[];
}

interface Mechanism {
  mechanism_id: string;
  enabled: boolean;
  allowed_proxy_fields: string[];
  kill_conditions: string[];
}

interface ResearchPolicy {
  version: number;
  budget: {
    daily_simulation_limit: number;
    exploration_share_limit: number;
    exploration_stages: string[];
    stage_allocations: Record<string, number>;
  };
  behavioral_boundaries: {
    block_unclassified_candidates: boolean;
    require_kill_conditions: boolean;
    forbid_pure_price_volume: boolean;
    mechanisms: Mechanism[];
  };
}

const NAV = [
  ["boundaries", "研究边界", SlidersHorizontal],
  ["behavior", "行为逻辑", BrainCircuit],
  ["memory", "记忆", GitBranch],
  ["evaluation", "评估", BarChart3],
  ["runs", "运行", Activity],
  ["system", "自动化", Settings2],
] as const;

const BEHAVIOR_LIBRARY = [
  { name: "锚定反转", mechanism: "投资者依赖近期参照点，对偏离反应不足，随后出现反转。", proxy: "经营现金流、分析师修正、质量价值价差、近期参考点。" },
  { name: "质量价值错定价", mechanism: "市场短期忽视质量改善或价值修复，形成延迟重定价。", proxy: "盈利质量、现金流质量、估值压缩、销售或利润修复。" },
  { name: "拥挤交易松动", mechanism: "热门方向过度拥挤后，边际资金撤离带来相关性风险和反向机会。", proxy: "高自相关 winner、成交活跃度、波动放大、同质化骨架。" },
  { name: "处置效应", mechanism: "投资者过早兑现盈利并延迟确认亏损，造成收益与损失路径不对称。", proxy: "未实现盈亏、换手差异、价格相对成本基准、成交量。" },
  { name: "注意力偏差", mechanism: "显著事件吸引非对称关注，短期交易压力超过基本面信息。", proxy: "新闻热度、成交量冲击、分析师覆盖变化、异常波动。" },
];

const emptyModel: DashboardModel = {
  summary: {}, memory_layers: [], memory_edges: [], retrieval_trace: { query: "", steps: [] },
  governance_queues: {}, hypothesis_ledger: [], wqb_action_lanes: [], adversarial_review: [],
  agent_panels: [], agent_evaluation: { summary: {}, reports: [] },
};

function value(value: unknown, fallback = "--") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function Status({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status}</span>;
}

function SectionHeader({ title, detail }: { title: string; detail?: string }) {
  return <header className="section-header"><div><h2>{title}</h2>{detail && <p>{detail}</p>}</div></header>;
}

export function App() {
  const [view, setView] = useState<(typeof NAV)[number][0]>("boundaries");
  const [payload, setPayload] = useState<DashboardPayload>({ generated_at: "", model: emptyModel, runs: [] });
  const [policy, setPolicy] = useState<ResearchPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [runsResponse, policyResponse] = await Promise.all([fetch("/api/runs"), fetch("/api/policy")]);
      if (!runsResponse.ok) throw new Error(`运行 API HTTP ${runsResponse.status}`);
      setPayload(await runsResponse.json() as DashboardPayload);
      if (policyResponse.ok) {
        const policyPayload = await policyResponse.json() as { research_policy: ResearchPolicy };
        setPolicy(policyPayload.research_policy);
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取工作台数据");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const savePolicy = async () => {
    if (!policy) return;
    setSaveState("saving");
    const response = await fetch("/api/policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ research_policy: policy }),
    });
    if (response.ok) {
      const result = await response.json() as { research_policy: ResearchPolicy };
      setPolicy(result.research_policy);
      setSaveState("saved");
      window.setTimeout(() => setSaveState("idle"), 1800);
    } else {
      const result = await response.json() as { error?: string };
      setError(result.error || "研究边界保存失败");
      setSaveState("error");
    }
  };

  const latest = payload.runs[0];
  const budgetPct = latest?.daily_budget ? Math.min(100, Math.round((latest.inferred_spent_simulations / latest.daily_budget) * 100)) : 0;
  const title = NAV.find(([id]) => id === view)?.[1] ?? "研究边界";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><BrainCircuit size={20} /><div><strong>WQB Agent Lab</strong><span>因子研究工作台</span></div></div>
        <nav aria-label="主导航">
          {NAV.map(([id, label, Icon]) => (
            <button
              key={id}
              className={view === id ? "active" : ""}
              aria-label={label}
              title={label}
              onClick={() => setView(id)}
            >
              <Icon size={17} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-state"><span className={error ? "dot error" : "dot"} />{error ? "数据异常" : "本地服务已连接"}</div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div><h1>{title}</h1><p>{latest ? `${latest.run_tag} · ${latest.current_stage}` : "等待第一轮研究数据"}</p></div>
          <div className="topbar-actions">
            <span className="generated-at">{payload.generated_at ? `更新于 ${payload.generated_at.slice(11, 19)}` : "尚未更新"}</span>
            <button className="icon-button" title="刷新数据" aria-label="刷新数据" onClick={() => void refresh()} disabled={loading}><RefreshCw size={17} className={loading ? "spinning" : ""} /></button>
          </div>
        </header>

        {error && <div className="error-banner" role="alert">{error}</div>}
        {loading && !payload.generated_at ? <LoadingState /> : (
          <>
            {view === "boundaries" && <BoundariesView policy={policy} onChange={setPolicy} onSave={() => void savePolicy()} saveState={saveState} budgetPct={budgetPct} latest={latest} />}
            {view === "behavior" && <BehaviorView />}
            {view === "memory" && <MemoryView model={payload.model} />}
            {view === "evaluation" && <EvaluationView model={payload.model} />}
            {view === "runs" && <RunsView runs={payload.runs} />}
            {view === "system" && <SystemView model={payload.model} />}
          </>
        )}
      </main>
    </div>
  );
}

function BoundariesView({ policy, onChange, onSave, saveState, budgetPct, latest }: {
  policy: ResearchPolicy | null; onChange: (value: ResearchPolicy) => void; onSave: () => void;
  saveState: string; budgetPct: number; latest?: RunSnapshot;
}) {
  if (!policy) return <EmptyState title="尚未创建生产研究策略" detail="完成 runtime onboarding 后，工作台会从 production.json 读取预算和行为边界。" />;
  const updateBudget = (key: "daily_simulation_limit" | "exploration_share_limit", next: number) => onChange({ ...policy, budget: { ...policy.budget, [key]: next } });
  const updateBoundary = (key: keyof ResearchPolicy["behavioral_boundaries"], next: boolean) => onChange({ ...policy, behavioral_boundaries: { ...policy.behavioral_boundaries, [key]: next } });
  return <div className="view-stack">
    <section className="budget-band">
      <div><span className="section-label">当前预算</span><strong>{latest?.inferred_spent_simulations ?? 0} / {latest?.daily_budget ?? policy.budget.daily_simulation_limit}</strong></div>
      <div className="budget-meter" aria-label={`预算已使用 ${budgetPct}%`}><span style={{ width: `${budgetPct}%` }} /></div>
      <div className="budget-meta"><span>{budgetPct}% 已使用</span><span>剩余 {latest?.remaining_simulations_after_commitments ?? policy.budget.daily_simulation_limit}</span></div>
    </section>

    <section className="settings-section">
      <SectionHeader title="预算配置" detail="阶段分配必须与每日模拟上限完全一致。" />
      <div className="form-grid">
        <label>每日模拟上限<input type="number" min="1" value={policy.budget.daily_simulation_limit} onChange={(event) => updateBudget("daily_simulation_limit", Number(event.target.value))} /></label>
        <label>探索比例上限<input type="number" min="0" max="1" step="0.05" value={policy.budget.exploration_share_limit} onChange={(event) => updateBudget("exploration_share_limit", Number(event.target.value))} /></label>
      </div>
      <div className="allocation-table">
        {Object.entries(policy.budget.stage_allocations).map(([stage, amount]) => <div className="allocation-row" key={stage}><code>{stage}</code><input aria-label={`${stage} 预算`} type="number" min="0" value={amount} onChange={(event) => onChange({ ...policy, budget: { ...policy.budget, stage_allocations: { ...policy.budget.stage_allocations, [stage]: Number(event.target.value) } } })} /></div>)}
      </div>
    </section>

    <section className="settings-section">
      <SectionHeader title="行为经济学边界" detail="候选在模拟前必须通过这些约束。" />
      <div className="toggle-list">
        <Toggle label="阻止未分类候选" checked={policy.behavioral_boundaries.block_unclassified_candidates} onChange={(checked) => updateBoundary("block_unclassified_candidates", checked)} />
        <Toggle label="要求 Kill 条件" checked={policy.behavioral_boundaries.require_kill_conditions} onChange={(checked) => updateBoundary("require_kill_conditions", checked)} />
        <Toggle label="禁止纯价量独立逻辑" checked={policy.behavioral_boundaries.forbid_pure_price_volume} onChange={(checked) => updateBoundary("forbid_pure_price_volume", checked)} />
      </div>
      <div className="mechanism-list">
        {policy.behavioral_boundaries.mechanisms.map((mechanism, index) => <div className="mechanism-row" key={mechanism.mechanism_id}>
          <div><strong>{mechanism.mechanism_id}</strong><span>代理字段：{mechanism.allowed_proxy_fields.join(", ")}</span><span>Kill：{mechanism.kill_conditions.join(", ")}</span></div>
          <input aria-label={`${mechanism.mechanism_id} 启用状态`} type="checkbox" checked={mechanism.enabled} onChange={(event) => {
            const mechanisms = [...policy.behavioral_boundaries.mechanisms]; mechanisms[index] = { ...mechanism, enabled: event.target.checked };
            onChange({ ...policy, behavioral_boundaries: { ...policy.behavioral_boundaries, mechanisms } });
          }} />
        </div>)}
      </div>
      <div className="save-row"><button className="primary-button" onClick={onSave} disabled={saveState === "saving"}><Save size={16} />{saveState === "saving" ? "正在保存" : saveState === "saved" ? "已保存" : "保存研究边界"}</button></div>
    </section>
  </div>;
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="toggle-row"><span>{label}</span><input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>;
}

function BehaviorView() {
  return <section className="data-section"><SectionHeader title="行为经济学逻辑库" detail="逻辑用于定义研究边界，代理字段决定它能否转化为可检验候选。" />
    <div className="behavior-list">{BEHAVIOR_LIBRARY.map((item) => <article key={item.name}><div><BrainCircuit size={18} /><strong>{item.name}</strong></div><p>{item.mechanism}</p><span>{item.proxy}</span></article>)}</div>
  </section>;
}

function MemoryView({ model }: { model: DashboardModel }) {
  return <div className="view-stack">
    <section className="data-section"><SectionHeader title="分层记忆" detail="短期证据经过晋升和治理后进入长期记忆与知识图谱。" />
      <div className="memory-flow">{model.memory_layers.map((layer, index) => <div key={String(layer.id)} className="memory-step"><div className="memory-node"><Database size={18} /><div><strong>{value(layer.zh_label)}</strong><span>{value(layer.scope)}</span><small>{value(layer.items, "0")} 条</small></div></div>{index < model.memory_layers.length - 1 && <ChevronRight size={18} className="flow-arrow" />}</div>)}</div>
      <div className="relation-table">{model.memory_edges.map((edge) => <div key={`${edge.from}-${edge.to}`}><code>{edge.from}</code><span>{edge.zh_relation}</span><code>{edge.to}</code><p>{edge.rule}</p></div>)}</div>
    </section>
    <section className="data-section"><SectionHeader title="检索链路" detail={model.retrieval_trace.query} /><div className="trace-list">{model.retrieval_trace.steps.map((step, index) => <div key={step.stage}><span>{index + 1}</span><div><strong>{step.stage}</strong><p>{step.body}</p></div></div>)}</div></section>
    <section className="data-section"><SectionHeader title="治理队列" /><div className="queue-columns">{Object.entries(model.governance_queues).map(([name, items]) => <div key={name}><strong>{name}</strong>{items.map((item) => <span key={item}>{item}</span>)}</div>)}</div></section>
  </div>;
}

function EvaluationView({ model }: { model: DashboardModel }) {
  const reports = model.agent_evaluation.reports;
  return <section className="data-section"><SectionHeader title="Agent 效果评估" detail="比较记忆、策略和生成链路是否提高每单位模拟预算的产出。" />
    {reports.length === 0 ? <EmptyState title="还没有消融报告" detail="完成带 baseline 的评估运行后，这里会显示质量变化。" /> : <div className="report-list">{reports.map((report, index) => <div key={value(report.run_tag, String(index))}><strong>{value(report.run_tag)}</strong><Status status={value(report.verdict, "unknown")} /><span>{value(report.comparison_type)}</span></div>)}</div>}
  </section>;
}

function RunsView({ runs }: { runs: RunSnapshot[] }) {
  return <section className="data-section"><SectionHeader title="研究运行" detail="预算、阶段和健康状态来自本地 run ledger。" />
    {runs.length === 0 ? <EmptyState title="暂无运行" detail="启动第一轮 workflow 后会自动出现在这里。" /> : <div className="table-wrap"><table><thead><tr><th>Run</th><th>阶段</th><th>预算</th><th>已用</th><th>状态</th><th>最后活动</th></tr></thead><tbody>{runs.map((run) => <tr key={run.run_tag}><td><code>{run.run_tag}</code></td><td>{run.current_stage}</td><td>{run.daily_budget}</td><td>{run.inferred_spent_simulations}</td><td><Status status={run.health} /></td><td>{value(run.latest_activity_at)}</td></tr>)}</tbody></table></div>}
  </section>;
}

function SystemView({ model }: { model: DashboardModel }) {
  return <div className="view-stack"><section className="data-section"><SectionHeader title="自动化链路" />
    <div className="agent-list">{model.agent_panels.map((panel) => <div key={panel.title}><ShieldCheck size={18} /><div><strong>{panel.title}</strong><p>{panel.body}</p></div><Status status={panel.status} /></div>)}</div>
  </section><section className="data-section"><SectionHeader title="动作通道" /><div className="lane-list">{model.wqb_action_lanes.map((lane) => <span key={lane.id}>{lane.label}</span>)}</div></section>
  <section className="data-section"><SectionHeader title="对抗审查" /><ul className="review-list">{model.adversarial_review.map((rule) => <li key={rule}><Check size={16} />{rule}</li>)}</ul></section></div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><Database size={22} /><strong>{title}</strong><p>{detail}</p></div>;
}

function LoadingState() {
  return <div className="loading-state" aria-label="正在加载"><span /><span /><span /></div>;
}

export type { DashboardPayload, JsonObject, ResearchPolicy };
