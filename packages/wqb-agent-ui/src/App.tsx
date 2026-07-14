import { sampleRunSummary } from "./sampleRunSummary";
import { toRunSummaryViewModel } from "./runSummaryView";
import "./styles.css";

const view = toRunSummaryViewModel(sampleRunSummary);

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function App() {
  const budgetUsedPct = view.budgetPlanned > 0
    ? Math.round((view.budgetUsed / view.budgetPlanned) * 100)
    : 0;

  return (
    <main className="app-shell" data-contract="run_summary">
      <aside className="sidebar" aria-label="WQB Agent Lab">
        <div className="brand">
          <h1>WQB Agent Lab</h1>
          <p>因子研究 cockpit</p>
        </div>
        <nav className="nav" aria-label="主导航">
          <a aria-current="page" href="#run">运行概览</a>
          <a href="#artifacts">产物</a>
          <a href="#contract">合约边界</a>
        </nav>
        <div className="read-only">只读模式，未连接 WQB live 操作</div>
      </aside>

      <section className="workspace" id="run">
        <header className="topbar">
          <div>
            <div className="label">当前运行</div>
            <h2>{view.runId}</h2>
          </div>
          <span className="mode-pill">{view.modeLabel}</span>
        </header>

        <section className="budget-panel" aria-label="预算">
          <div className="budget-heading">
            <div>
              <div className="label">模拟预算</div>
              <h3>预算是本地 loop 的主状态</h3>
            </div>
            <strong>{budgetUsedPct}% 已使用</strong>
          </div>
          <div className="budget-track">
            <span style={{ width: `${budgetUsedPct}%` }} />
          </div>
          <div className="metrics-grid">
            <Metric label="计划预算" value={view.budgetPlanned} />
            <Metric label="已使用" value={view.budgetUsed} />
            <Metric label="剩余" value={view.budgetRemaining} />
          </div>
        </section>

        <section className="metrics-grid" aria-label="关键计数">
          <Metric label="候选" value={view.candidates} />
          <Metric label="模拟" value={view.simulations} />
          <Metric label="提交就绪" value={view.submitReady} />
        </section>

        <section className="panel" id="artifacts" aria-label="运行产物">
          <div className="panel-header">
            <h3>运行产物</h3>
            <span>{view.artifactLinks.length} items</span>
          </div>
          <ul className="artifact-list">
            {view.artifactLinks.map((artifact) => (
              <li key={artifact}>
                <code>{artifact}</code>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel contract-panel" id="contract" aria-label="合约边界">
          <h3>Python / TypeScript 合约边界</h3>
          <p>
            本页面只消费 validated <code>run_summary</code> payload。TS UI 不读取 Python 内部模块，
            不触发 simulation，不触发 submit。
          </p>
        </section>
      </section>
    </main>
  );
}
