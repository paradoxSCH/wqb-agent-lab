from __future__ import annotations


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WQ Alpha Agent | 因子挖掘研究边界</title>
  <style>
    :root {
      color-scheme: light;
      --bg: oklch(0.97 0.008 220);
      --shell: oklch(0.99 0.004 225);
      --side: oklch(0.94 0.012 220);
      --panel: oklch(0.998 0.002 230);
      --soft: oklch(0.955 0.012 220);
      --line: oklch(0.875 0.018 225);
      --ink: oklch(0.23 0.035 235);
      --muted: oklch(0.46 0.035 230);
      --accent: oklch(0.48 0.105 185);
      --good: oklch(0.52 0.12 150);
      --risk: oklch(0.57 0.15 25);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    button, input, textarea { font: inherit; }
    button:focus-visible, input:focus-visible, textarea:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .app {
      display: grid;
      grid-template-columns: 224px minmax(0, 1fr) 328px;
      min-height: 100vh;
    }
    .sidebar {
      background: var(--side);
      border-right: 1px solid var(--line);
      padding: 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .brand { padding: 8px 10px; }
    .brand h1 { margin: 0; font-size: 16px; line-height: 1.25; }
    .brand p { margin: 4px 0 0; color: var(--muted); font-size: 12px; line-height: 1.4; }
    .nav { display: grid; gap: 4px; }
    .nav-item {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border: 0;
      border-radius: 8px;
      background: transparent;
      color: var(--muted);
      padding: 9px 10px;
      cursor: pointer;
      text-align: left;
    }
    .nav-item:hover, .nav-item[aria-current="page"] {
      background: var(--panel);
      color: var(--ink);
    }
    .nav-count {
      min-width: 22px;
      border-radius: 999px;
      padding: 2px 6px;
      background: var(--soft);
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }
    .sidebar-footer {
      margin-top: auto;
      border-top: 1px solid var(--line);
      padding: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .workspace {
      min-width: 0;
      background: var(--shell);
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .topbar {
      min-height: 60px;
      border-bottom: 1px solid var(--line);
      padding: 10px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .topbar-left, .top-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .run-name {
      max-width: 44vw;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 650;
    }
    .content {
      min-width: 0;
      padding: 20px;
      overflow: auto;
    }
    .view { display: none; gap: 16px; }
    .view.active { display: grid; }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 4px; font-size: 24px; line-height: 1.2; }
    h2 { margin-bottom: 0; font-size: 18px; line-height: 1.25; }
    h3 { margin-bottom: 8px; font-size: 14px; line-height: 1.25; }
    .muted { color: var(--muted); }
    .mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 12px; }
    .panel {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    .panel.pad { padding: 14px; }
    .stack { display: grid; gap: 12px; }
    .grid-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .grid-3 {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .memory-layout {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
      gap: 12px;
      align-items: start;
    }
    .memory-layer-board {
      display: grid;
      gap: 10px;
    }
    .memory-layer-card {
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
      cursor: pointer;
      text-align: left;
    }
    .memory-layer-card[aria-selected="true"] {
      border-color: oklch(0.72 0.08 185);
      background: oklch(0.978 0.018 185);
    }
    .memory-graph {
      min-height: 340px;
      display: grid;
      gap: 10px;
      padding: 12px;
    }
    .memory-node {
      display: grid;
      gap: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
    }
    .memory-node.active {
      border-color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .memory-edge {
      display: grid;
      grid-template-columns: minmax(88px, 0.8fr) 92px minmax(88px, 0.8fr);
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }
    .memory-edge span {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .memory-edge .relation {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 7px;
      text-align: center;
      background: var(--soft);
      color: var(--ink);
    }
    .field label {
      display: block;
      margin-bottom: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .input, .textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      padding: 9px 10px;
    }
    .textarea { min-height: 132px; resize: vertical; }
    .button {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      padding: 8px 10px;
      cursor: pointer;
    }
    .button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }
    .language-button {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel);
      color: var(--muted);
      padding: 5px 8px;
      cursor: pointer;
    }
    .language-button[aria-pressed="true"] {
      border-color: oklch(0.78 0.06 185);
      background: oklch(0.965 0.025 185);
      color: var(--accent);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      width: fit-content;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel);
      color: var(--muted);
      padding: 3px 8px;
      font-size: 12px;
      white-space: nowrap;
    }
    .badge.ready, .badge.active {
      color: var(--accent);
      border-color: oklch(0.78 0.06 185);
      background: oklch(0.965 0.025 185);
    }
    .badge.complete {
      color: var(--good);
      border-color: oklch(0.78 0.07 150);
      background: oklch(0.965 0.025 150);
    }
    .badge.stalled, .badge.blocked {
      color: var(--risk);
      border-color: oklch(0.78 0.07 25);
      background: oklch(0.965 0.022 25);
    }
    .section-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
    }
    .metric {
      min-height: 82px;
      padding: 13px;
      display: grid;
      align-content: space-between;
      gap: 8px;
    }
    .metric-value { font-size: 26px; line-height: 1; font-weight: 720; }
    .metric-label { color: var(--muted); font-size: 12px; }
    .boundary-card, .run-row, .plan-row, .thesis-row {
      display: grid;
      gap: 8px;
      padding: 12px 14px;
      border-top: 1px solid var(--line);
    }
    .boundary-card:first-child, .run-row:first-child, .plan-row:first-child, .thesis-row:first-child { border-top: 0; }
    .run-row {
      grid-template-columns: minmax(220px, 1.4fr) 90px 110px minmax(120px, 1fr);
      align-items: center;
    }
    .plan-row {
      grid-template-columns: minmax(180px, 1fr) 90px minmax(240px, 1.5fr);
      align-items: center;
    }
    .bar {
      height: 8px;
      border-radius: 999px;
      overflow: hidden;
      background: var(--soft);
    }
    .bar-fill {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), oklch(0.55 0.12 250));
    }
    .inspector {
      min-width: 0;
      border-left: 1px solid var(--line);
      background: oklch(0.956 0.01 225);
      padding: 16px;
      overflow: auto;
    }
    .inspector .panel + .panel { margin-top: 12px; }
    .evidence-list {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .evidence-list li {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
    }
    .empty {
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: oklch(0.986 0.004 225);
      color: var(--muted);
    }
    @media (max-width: 1180px) {
      .app { grid-template-columns: 212px minmax(0, 1fr); }
      .inspector { display: none; }
    }
    @media (max-width: 820px) {
      .app { grid-template-columns: 1fr; }
      .sidebar {
        position: sticky;
        top: 0;
        z-index: 10;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .nav-count, .brand p, .sidebar-footer { display: none; }
      .topbar { align-items: start; flex-direction: column; }
      .content { padding: 14px; }
      .grid-2, .grid-3, .memory-layout, .run-row, .plan-row { grid-template-columns: 1fr; }
      .memory-edge { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>WQ Alpha Agent</h1>
        <p data-i18n="brandSubtitle">只维护预算和行为经济学边界，其余交给 agent</p>
      </div>
      <nav class="nav" id="nav"></nav>
      <div class="sidebar-footer" data-i18n-html="sidebarFooter">
        研究运行操作台，读取 <span class="mono">.local/data/runs/continuous-alpha</span> 下的 ledger 和结果文件。
      </div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <span class="badge active" data-i18n="currentRun">当前 run</span>
          <span class="run-name" id="current-run">加载中...</span>
        </div>
        <div class="top-actions">
          <span class="muted" data-i18n="language">语言</span>
          <button class="language-button" type="button" data-lang="zh" aria-pressed="true">中文</button>
          <button class="language-button" type="button" data-lang="en" aria-pressed="false">EN</button>
          <button class="button" type="button" id="refresh-button" data-i18n="refreshData">刷新数据</button>
          <button class="button primary" type="button" data-jump="boundaries" data-i18n="openBoundaries">维护边界</button>
        </div>
      </header>

      <div class="content">
        <section class="view active" id="view-boundaries">
          <div class="section-head">
            <div>
              <h1 data-i18n="boundariesTitle">研究边界</h1>
              <p class="muted" data-i18n="boundariesSubtitle">用户只需维护预算和行为经济学边界，agent 自动完成检索、规划、生成、筛选和反思。</p>
            </div>
            <button class="button primary" type="button" id="generate-plan-button" data-i18n="generatePlan">生成研究计划</button>
          </div>

          <div class="grid-2">
            <div class="panel pad stack">
              <h2 data-i18n="budgetTitle">因子挖掘预算</h2>
              <div class="field">
                <label for="daily-budget" data-i18n="dailyBudget">每日模拟预算</label>
                <input class="input" id="daily-budget" value="1000" inputmode="numeric">
              </div>
              <div class="field">
                <label for="risk-budget" data-i18n="riskBudget">探索比例上限</label>
                <input class="input" id="risk-budget" value="20%">
              </div>
              <div class="field">
                <label for="objective" data-i18n="objective">目标</label>
                <input class="input" id="objective" value="最大化 submit-ready 独立候选">
              </div>
            </div>

            <div class="panel pad stack" id="behavior-boundaries">
              <h2 data-i18n="behaviorBoundaryTitle">行为经济学边界</h2>
              <div class="field">
                <label for="allowed-thesis" data-i18n="allowedThesis">允许的行为逻辑</label>
                <textarea class="textarea" id="allowed-thesis">锚定反转
质量价值错定价
拥挤交易松动</textarea>
              </div>
              <div class="field">
                <label for="blocked-thesis" data-i18n="blockedThesis">禁止或降权的边界</label>
                <textarea class="textarea" id="blocked-thesis">纯价格/成交量 standalone
纯 decay 参数扫描
高 self-corr 跟随者</textarea>
              </div>
            </div>
          </div>

          <div class="panel pad stack">
            <h2 data-i18n="planTitle">Agent 计划预览</h2>
            <div class="empty" id="plan-output" aria-live="polite" data-i18n="planOutputEmpty">点击“生成研究计划”后，这里会展示预算拆分和行为边界摘要。</div>
          </div>
        </section>

        <section class="view" id="view-behavior">
          <div class="section-head">
            <div>
              <h1 data-i18n="behaviorTitle">行为经济学中文版</h1>
              <p class="muted" data-i18n="behaviorSubtitle">这些是 agent 可使用的行为经济学底层逻辑，用户只维护边界，不需要管理候选和记忆细节。</p>
            </div>
            <button class="button" type="button" id="add-thesis-button" data-i18n="addBoundaryCard">新增边界草案</button>
          </div>
          <div class="panel" id="behavior-library"></div>
        </section>

        <section class="view" id="view-memory">
          <div class="section-head">
            <div>
              <h1 data-i18n="memoryTitle">记忆层管理</h1>
              <p class="muted" data-i18n="memorySubtitle">管理短期记忆、长期记忆、知识图谱，以及记忆之间的依赖关系图。</p>
            </div>
            <div class="top-actions">
              <button class="button" type="button" id="promote-memory-button" data-i18n="promoteMemory">晋升</button>
              <button class="button" type="button" id="decay-memory-button" data-i18n="decayMemory">衰减</button>
              <button class="button" type="button" id="forget-memory-button" data-i18n="forgetMemory">遗忘</button>
            </div>
          </div>

          <div class="memory-layout">
            <div class="panel pad stack">
              <div class="section-head">
                <div>
                  <h2 data-i18n="memoryLayersTitle">分层存储架构</h2>
                  <p class="muted" data-i18n="memoryLayersSubtitle">短期记忆负责 run 内证据，长期记忆沉淀可复用经验，知识图谱维护行为逻辑与候选依赖。</p>
                </div>
                <span class="badge ready" data-i18n="memoryPolicyBadge">晋升、衰减与遗忘策略</span>
              </div>
              <div class="memory-layer-board" id="memory-layer-board"></div>
            </div>

            <div class="panel pad stack">
              <div class="section-head">
                <div>
                  <h2 data-i18n="memoryGraphTitle">依赖关系图</h2>
                  <p class="muted" data-i18n="memoryGraphSubtitle">展示记忆层之间如何晋升、沉淀、检索增强，并服务 RAG 链路。</p>
                </div>
                <span class="badge" data-i18n="ragChainBadge">Query 改写 → 向量检索 → Rerank → 多粒度融合</span>
              </div>
              <div class="memory-graph" id="memory-graph"></div>
            </div>
          </div>

          <div class="panel pad stack">
            <h2 data-i18n="memoryRagTitle">RAG 检索链路</h2>
            <div class="grid-3">
              <div class="memory-node"><strong data-i18n="queryRewriteTitle">Query 理解与改写</strong><span class="muted" data-i18n="queryRewriteBody">把预算、行为边界、失败信号转换成结构化检索意图。</span></div>
              <div class="memory-node"><strong data-i18n="retrievalTitle">向量检索与 Rerank</strong><span class="muted" data-i18n="retrievalBody">先召回相似 run、候选、行为逻辑，再按新颖性和可执行性重排。</span></div>
              <div class="memory-node"><strong data-i18n="fusionTitle">多粒度结果融合</strong><span class="muted" data-i18n="fusionBody">融合 run 级、family 级、表达式骨架级和行为逻辑级证据。</span></div>
            </div>
          </div>

          <div class="grid-2">
            <div class="panel pad stack">
              <div class="section-head">
                <h2 data-i18n="hypothesisLedgerTitle">假设账本</h2>
                <span class="badge ready" data-i18n="proxyMappingLabel">代理映射</span>
              </div>
              <div class="stack" id="hypothesis-ledger"></div>
            </div>

            <div class="panel pad stack">
              <div class="section-head">
                <h2 data-i18n="adversarialReviewTitle">对抗审查</h2>
                <span class="badge" data-i18n="killConditionLabel">Kill 条件</span>
              </div>
              <div class="stack" id="adversarial-review"></div>
            </div>
          </div>

          <div class="grid-2">
            <div class="panel pad stack">
              <h2 data-i18n="retrievalTraceTitle">RAG Trace</h2>
              <div class="stack" id="retrieval-trace"></div>
            </div>

            <div class="panel pad stack">
              <h2 data-i18n="governanceQueuesTitle">治理队列</h2>
              <div class="stack" id="governance-queues"></div>
            </div>
          </div>

          <div class="panel pad stack">
            <h2 data-i18n="wqbActionLanesTitle">WQB 动作队列</h2>
            <div class="grid-3" id="wqb-action-lanes"></div>
          </div>
        </section>

        <section class="view" id="view-evaluation">
          <div class="section-head">
            <div>
              <h1 data-i18n="evaluationTitle">Agent 评估</h1>
              <p class="muted" data-i18n="evaluationSubtitle">用消融对照判断系统是更有用，还是只是更臃肿。</p>
            </div>
            <span class="badge ready" data-i18n="ablationBadge">消融对照</span>
          </div>

          <div class="grid-3" id="agent-evaluation-summary"></div>

          <div class="panel pad stack">
            <div class="section-head">
              <h2 data-i18n="evaluationReportsTitle">评估报告</h2>
              <span class="badge" data-i18n="fairnessBadge">公平性标记</span>
            </div>
            <div class="stack" id="agent-evaluation-reports"></div>
          </div>
        </section>

        <section class="view" id="view-runs">
          <div class="section-head">
            <div>
              <h1 data-i18n="runsTitle">运行记录</h1>
              <p class="muted" data-i18n="runsSubtitle">系统自动记录 run 状态，用户通常只需要审计。</p>
            </div>
            <span class="badge" id="refresh-note">尚未加载</span>
          </div>
          <div class="grid-3" id="summary-metrics"></div>
          <div class="panel">
            <div class="run-row">
              <strong>Run</strong><strong data-i18n="healthColumn">健康状态</strong><strong data-i18n="budgetColumn">预算</strong><strong data-i18n="stageColumn">阶段</strong>
            </div>
            <div id="run-list"></div>
          </div>
        </section>

        <section class="view" id="view-system">
          <div class="section-head">
            <div>
              <h1 data-i18n="systemTitle">系统自动层</h1>
              <p class="muted" data-i18n="systemSubtitle">记忆检索、候选治理、图谱关系和提交策略由 agent 维护。</p>
            </div>
            <span class="badge ready" data-i18n="autoManaged">自动维护</span>
          </div>
          <div class="grid-3" id="agent-panels"></div>
        </section>
      </div>
    </main>

    <aside class="inspector">
      <div class="panel pad">
        <h2 data-i18n="inspectorTitle">检查器</h2>
        <p class="muted" id="inspector-copy">维护预算和行为经济学边界即可。</p>
      </div>
      <div class="panel pad">
        <h3 data-i18n="boundaryPolicyTitle">边界策略</h3>
        <ul class="evidence-list">
          <li><strong data-i18n="policyBudget">预算优先</strong><br><span class="muted" data-i18n="policyBudgetBody">每日只暴露总预算和探索比例，阶段预算由 agent 自动拆分。</span></li>
          <li><strong data-i18n="policyBehavior">行为边界优先</strong><br><span class="muted" data-i18n="policyBehaviorBody">生成必须落在已允许的行为经济学逻辑内。</span></li>
          <li><strong data-i18n="policyMemory">记忆后台化</strong><br><span class="muted" data-i18n="policyMemoryBody">RAG、图谱、候选队列和反思写回默认隐藏。</span></li>
        </ul>
      </div>
    </aside>
  </div>

  <script>
    const COPY = {
      zh: {
        brandSubtitle: '只维护预算和行为经济学边界，其余交给 agent',
        sidebarFooter: '研究运行操作台，读取 <span class="mono">.local/data/runs/continuous-alpha</span> 下的 ledger 和结果文件。',
        currentRun: '当前 run',
        language: '语言',
        refreshData: '刷新数据',
        openBoundaries: '维护边界',
        boundariesTitle: '研究边界',
        boundariesSubtitle: '用户只需维护预算和行为经济学边界，agent 自动完成检索、规划、生成、筛选和反思。',
        budgetTitle: '因子挖掘预算',
        dailyBudget: '每日模拟预算',
        riskBudget: '探索比例上限',
        objective: '目标',
        behaviorBoundaryTitle: '行为经济学边界',
        allowedThesis: '允许的行为逻辑',
        blockedThesis: '禁止或降权的边界',
        generatePlan: '生成研究计划',
        planTitle: 'Agent 计划预览',
        planOutputEmpty: '点击“生成研究计划”后，这里会展示预算拆分和行为边界摘要。',
        generatedPlanTitle: '已生成研究计划',
        approvePlan: '批准计划',
        editBudget: '调整预算',
        planDraft: '计划草案',
        planApproved: '计划已批准',
        planApprovedBody: '计划已批准。下一步可接入 workflow config 写入和执行。',
        editBudgetBody: '已回到预算输入框，可以调整后重新生成计划。',
        evidenceSummary: '计划由预算、探索比例、允许逻辑和禁止边界生成；候选、记忆和图谱由 agent 后台维护。',
        behaviorTitle: '行为经济学中文版',
        behaviorSubtitle: '这些是 agent 可使用的行为经济学底层逻辑，用户只维护边界，不需要管理候选和记忆细节。',
        addBoundaryCard: '新增边界草案',
        boundaryDraftName: '新行为边界草案',
        boundaryDraftBody: '补充行为机制、代理字段、适用条件和失效信号后，再允许 agent 使用。',
        boundaryDraftAdded: '已新增行为边界草案',
        memoryTitle: '记忆层管理',
        memorySubtitle: '管理短期记忆、长期记忆、知识图谱，以及记忆之间的依赖关系图。',
        memoryLayersTitle: '分层存储架构',
        memoryLayersSubtitle: '短期记忆负责 run 内证据，长期记忆沉淀可复用经验，知识图谱维护行为逻辑与候选依赖。',
        memoryPolicyBadge: '晋升、衰减与遗忘策略',
        memoryGraphTitle: '依赖关系图',
        memoryGraphSubtitle: '展示记忆层之间如何晋升、沉淀、检索增强，并服务 RAG 链路。',
        ragChainBadge: 'Query 改写 → 向量检索 → Rerank → 多粒度融合',
        memoryRagTitle: 'RAG 检索链路',
        queryRewriteTitle: 'Query 理解与改写',
        queryRewriteBody: '把预算、行为边界、失败信号转换成结构化检索意图。',
        retrievalTitle: '向量检索与 Rerank',
        retrievalBody: '先召回相似 run、候选、行为逻辑，再按新颖性和可执行性重排。',
        fusionTitle: '多粒度结果融合',
        fusionBody: '融合 run 级、family 级、表达式骨架级和行为逻辑级证据。',
        hypothesisLedgerTitle: '假设账本',
        adversarialReviewTitle: '对抗审查',
        retrievalTraceTitle: 'RAG Trace',
        governanceQueuesTitle: '治理队列',
        wqbActionLanesTitle: 'WQB 动作队列',
        proxyMappingLabel: '代理映射',
        killConditionLabel: 'Kill 条件',
        promoteMemory: '晋升',
        decayMemory: '衰减',
        forgetMemory: '遗忘',
        memoryPromoted: '已标记晋升：该记忆会进入长期记忆候选队列。',
        memoryDecayed: '已标记衰减：该记忆会降低检索权重。',
        memoryForgotten: '已标记遗忘：该记忆会从主动召回链路移除。',
        evaluationTitle: 'Agent 评估',
        evaluationSubtitle: '用消融对照判断系统是更有用，还是只是更臃肿。',
        ablationBadge: '消融对照',
        evaluationReportsTitle: '评估报告',
        fairnessBadge: '公平性标记',
        evaluationReportCount: '评估报告',
        latestVerdict: '最新结论',
        comparisonType: '对照类型',
        missingVariants: '缺失分组',
        noEvaluationReports: '暂无评估报告。运行 ablation suite 后会显示结论。',
        noBaselineDelta: '没有 baseline delta，当前结论不能视为严格提升证明。',
        runsTitle: '运行记录',
        runsSubtitle: '系统自动记录 run 状态，用户通常只需要审计。',
        systemTitle: '系统自动层',
        systemSubtitle: '记忆检索、候选治理、图谱关系和提交策略由 agent 维护。',
        autoManaged: '自动维护',
        inspectorTitle: '检查器',
        boundaryPolicyTitle: '边界策略',
        policyBudget: '预算优先',
        policyBudgetBody: '每日只暴露总预算和探索比例，阶段预算由 agent 自动拆分。',
        policyBehavior: '行为边界优先',
        policyBehaviorBody: '生成必须落在已允许的行为经济学逻辑内。',
        policyMemory: '记忆后台化',
        policyMemoryBody: 'RAG、图谱、候选队列和反思写回默认隐藏。',
        runsIndexed: '已索引 runs',
        activeRuns: '进行中 runs',
        completeRuns: '已完成 runs',
        healthColumn: '健康状态',
        budgetColumn: '预算',
        stageColumn: '阶段',
        updated: '已更新',
        noRunsFound: '没有发现 run',
        simulationsUsed: '次模拟已消耗',
        minutesAgo: '分钟前',
        status: { active: '进行中', 'inferred-active': '推断进行中', stalled: '停滞', complete: '完成', pending: '待处理', ready: '就绪', idle: '空闲' },
        nav: { boundaries: '研究边界', behavior: '行为经济学', memory: '记忆层', evaluation: '评估', runs: '运行记录', system: '系统自动层' },
        panels: {
          'Memory briefing': ['记忆简报', '后台读取 ledger、结果文件和反思产物，为计划提供证据。'],
          'Budget planner': ['预算规划', '根据总预算和边界自动拆分 probe、scale、repair、rescue、holdout。'],
          'Submission governance': ['提交治理', '自动处理 champion、follower、repair、blocked 和 archive 队列。']
        }
      },
      en: {
        brandSubtitle: 'Maintain budget and behavioral boundaries, let the agent handle the rest',
        sidebarFooter: 'Research run dashboard. Reads ledgers and result artifacts from <span class="mono">.local/data/runs/continuous-alpha</span>.',
        currentRun: 'Current run',
        language: 'Language',
        refreshData: 'Refresh data',
        openBoundaries: 'Edit boundaries',
        boundariesTitle: 'Research Boundaries',
        boundariesSubtitle: 'Users maintain only alpha mining budget and behavioral economics boundaries. The agent handles retrieval, planning, generation, triage, and reflection.',
        budgetTitle: 'Alpha mining budget',
        dailyBudget: 'Daily simulation budget',
        riskBudget: 'Exploration cap',
        objective: 'Objective',
        behaviorBoundaryTitle: 'Behavioral economics boundaries',
        allowedThesis: 'Allowed behavioral logic',
        blockedThesis: 'Blocked or downweighted boundaries',
        generatePlan: 'Generate research plan',
        planTitle: 'Agent plan preview',
        planOutputEmpty: 'After generating a plan, budget allocation and boundary summary appear here.',
        generatedPlanTitle: 'Generated research plan',
        approvePlan: 'Approve plan',
        editBudget: 'Edit budget',
        planDraft: 'Plan draft',
        planApproved: 'Plan approved',
        planApprovedBody: 'Plan approved. The next layer can write workflow config and execute it.',
        editBudgetBody: 'Budget input is focused. Adjust it and generate the plan again.',
        evidenceSummary: 'Plan generated from budget, exploration cap, allowed logic, and blocked boundaries. Candidates, memory, and graph are maintained by the agent.',
        behaviorTitle: 'Behavioral Economics Library',
        behaviorSubtitle: 'These are the behavioral economics primitives the agent can use. Users maintain boundaries, not candidates or memory details.',
        addBoundaryCard: 'Add boundary draft',
        boundaryDraftName: 'New behavioral boundary draft',
        boundaryDraftBody: 'Add mechanism, proxy fields, conditions, and failure signals before allowing the agent to use it.',
        boundaryDraftAdded: 'Boundary draft added',
        memoryTitle: 'Memory Layer Management',
        memorySubtitle: 'Manage short-term memory, long-term memory, the knowledge graph, and dependency relationships between memories.',
        memoryLayersTitle: 'Layered storage architecture',
        memoryLayersSubtitle: 'Short-term memory keeps run evidence, long-term memory stores reusable lessons, and the knowledge graph maintains behavioral and candidate dependencies.',
        memoryPolicyBadge: 'Promotion, decay, and forgetting policy',
        memoryGraphTitle: 'Dependency graph',
        memoryGraphSubtitle: 'Shows how memory layers promote, ground, retrieve, and support the RAG chain.',
        ragChainBadge: 'Query rewrite → vector retrieval → rerank → multi-granularity fusion',
        memoryRagTitle: 'RAG retrieval chain',
        queryRewriteTitle: 'Query understanding and rewrite',
        queryRewriteBody: 'Convert budgets, behavioral boundaries, and failure signals into structured retrieval intent.',
        retrievalTitle: 'Vector retrieval and rerank',
        retrievalBody: 'Recall similar runs, candidates, and behavioral logic, then rerank by novelty and actionability.',
        fusionTitle: 'Multi-granularity fusion',
        fusionBody: 'Fuse evidence across run, family, expression skeleton, and behavioral logic levels.',
        hypothesisLedgerTitle: 'Hypothesis Ledger',
        adversarialReviewTitle: 'Adversarial Review',
        retrievalTraceTitle: 'RAG Trace',
        governanceQueuesTitle: 'Governance Queues',
        wqbActionLanesTitle: 'WQB Action Lanes',
        proxyMappingLabel: 'Proxy Mapping',
        killConditionLabel: 'Kill Condition',
        promoteMemory: 'Promote',
        decayMemory: 'Decay',
        forgetMemory: 'Forget',
        memoryPromoted: 'Promotion marked: this memory enters the long-term candidate queue.',
        memoryDecayed: 'Decay marked: this memory receives a lower retrieval weight.',
        memoryForgotten: 'Forget marked: this memory is removed from active recall.',
        evaluationTitle: 'Agent Evaluation',
        evaluationSubtitle: 'Use ablation evidence to decide whether the system is useful or bloated.',
        ablationBadge: 'Ablation',
        evaluationReportsTitle: 'Evaluation reports',
        fairnessBadge: 'Fairness flag',
        evaluationReportCount: 'Evaluation reports',
        latestVerdict: 'Latest verdict',
        comparisonType: 'Comparison type',
        missingVariants: 'Missing variants',
        noEvaluationReports: 'No evaluation reports yet. Run the ablation suite to see evidence.',
        noBaselineDelta: 'No baseline delta is available, so this is not proof of strict lift.',
        runsTitle: 'Run History',
        runsSubtitle: 'The system records run state automatically. Users usually only audit this view.',
        systemTitle: 'System Automation',
        systemSubtitle: 'Memory retrieval, candidate governance, graph relations, and submission policy are maintained by the agent.',
        autoManaged: 'Auto-managed',
        inspectorTitle: 'Inspector',
        boundaryPolicyTitle: 'Boundary policy',
        policyBudget: 'Budget first',
        policyBudgetBody: 'Expose only total budget and exploration cap. Stage budgets are split by the agent.',
        policyBehavior: 'Behavior first',
        policyBehaviorBody: 'Generation must stay inside allowed behavioral economics logic.',
        policyMemory: 'Memory behind the scenes',
        policyMemoryBody: 'RAG, graph, candidate queues, and reflection write-back are hidden by default.',
        runsIndexed: 'Runs indexed',
        activeRuns: 'Active runs',
        completeRuns: 'Complete runs',
        healthColumn: 'Health',
        budgetColumn: 'Budget',
        stageColumn: 'Stage',
        updated: 'Updated',
        noRunsFound: 'No runs found',
        simulationsUsed: 'simulations used',
        minutesAgo: 'min ago',
        status: { active: 'active', 'inferred-active': 'inferred-active', stalled: 'stalled', complete: 'complete', pending: 'pending', ready: 'ready', idle: 'idle' },
        nav: { boundaries: 'Boundaries', behavior: 'Behavior', memory: 'Memory', evaluation: 'Evaluation', runs: 'Runs', system: 'Automation' },
        panels: {
          'Memory briefing': ['Memory briefing', 'Reads ledgers, result files, and reflections in the background.'],
          'Budget planner': ['Budget planner', 'Splits total budget into probe, scale, repair, rescue, and holdout.'],
          'Submission governance': ['Submission governance', 'Maintains champion, follower, repair, blocked, and archive lanes.']
        }
      }
    };

    const BEHAVIOR_LIBRARY = [
      {
        id: 'anchoring_reversal',
        zhName: '锚定反转',
        zhMechanism: '投资者过度依赖近期参照点，对基本面或价格参照点的偏离反应不足，随后出现反转。',
        zhProxy: '可观察代理：经营现金流、分析师修正、质量价值价差、近期 winner/loser 参考点。',
        enName: 'Anchoring reversal',
        enMechanism: 'Investors over-anchor on recent reference points and underreact to deviations, creating later reversal.',
        enProxy: 'Proxies: operating cashflow, analyst revisions, quality-value spread, recent winner/loser anchors.'
      },
      {
        id: 'quality_value_mispricing',
        zhName: '质量价值错定价',
        zhMechanism: '市场短期忽视质量改善或价值修复，导致高质量低估资产在后续窗口重新定价。',
        zhProxy: '可观察代理：盈利质量、现金流质量、估值压缩、销售或利润修复。',
        enName: 'Quality-value mispricing',
        enMechanism: 'Markets underweight quality improvement or value repair, causing delayed repricing.',
        enProxy: 'Proxies: earnings quality, cashflow quality, valuation compression, sales or margin repair.'
      },
      {
        id: 'crowding_unwind',
        zhName: '拥挤交易松动',
        zhMechanism: '热门方向过度拥挤后，边际资金撤离会让相似暴露的 alpha 出现相关性风险和反向机会。',
        zhProxy: '可观察代理：高 self-corr winner、成交活跃度、波动放大、同质化表达式骨架。',
        enName: 'Crowding unwind',
        enMechanism: 'After crowding in popular directions, marginal exits create correlation risk and reversal opportunities.',
        enProxy: 'Proxies: high self-corr winners, activity, volatility expansion, homogeneous skeletons.'
      }
    ];

    function defaultModel() {
      return {
        summary: {},
        navigation: [],
        agent_panels: [],
        memory_layers: [],
        memory_edges: [],
        retrieval_trace: { query: '', steps: [] },
        governance_queues: {},
        hypothesis_ledger: [],
        wqb_action_lanes: [],
        adversarial_review: [],
        agent_evaluation: { summary: {}, reports: [] }
      };
    }

    let state = {
      runs: [],
      model: defaultModel(),
      view: 'boundaries',
      lang: 'zh',
      generatedAt: null,
      boundaryDraftAdded: false,
      selectedMemoryId: 'short_term',
      inspector: { type: 'empty' }
    };

    const fmt = (value) => value === null || value === undefined || value === '' ? '--' : value;
    const clamp = (value) => Math.max(0, Math.min(100, Number(value || 0)));
    const t = (key) => COPY[state.lang][key] || COPY.zh[key] || key;
    const statusText = (value) => COPY[state.lang].status[value] || fmt(value);

    function escapeHtml(value) {
      return String(fmt(value))
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function objectOrEmpty(value) {
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    }

    function normalizeModel(model) {
      const source = objectOrEmpty(model);
      const trace = objectOrEmpty(source.retrieval_trace);
      const evaluation = objectOrEmpty(source.agent_evaluation);
      return {
        summary: source.summary && typeof source.summary === 'object' && !Array.isArray(source.summary) ? source.summary : {},
        navigation: Array.isArray(source.navigation) ? source.navigation : [],
        agent_panels: Array.isArray(source.agent_panels) ? source.agent_panels : [],
        memory_layers: Array.isArray(source.memory_layers) ? source.memory_layers : [],
        memory_edges: Array.isArray(source.memory_edges) ? source.memory_edges : [],
        retrieval_trace: {
          query: trace.query || '',
          steps: Array.isArray(trace.steps) ? trace.steps : []
        },
        governance_queues: source.governance_queues && typeof source.governance_queues === 'object' && !Array.isArray(source.governance_queues) ? source.governance_queues : {},
        hypothesis_ledger: Array.isArray(source.hypothesis_ledger) ? source.hypothesis_ledger : [],
        wqb_action_lanes: Array.isArray(source.wqb_action_lanes) ? source.wqb_action_lanes : [],
        adversarial_review: Array.isArray(source.adversarial_review) ? source.adversarial_review : [],
        agent_evaluation: {
          summary: evaluation.summary && typeof evaluation.summary === 'object' && !Array.isArray(evaluation.summary) ? evaluation.summary : {},
          reports: Array.isArray(evaluation.reports) ? evaluation.reports : []
        }
      };
    }

    function setView(view) {
      state.view = view;
      document.querySelectorAll('.view').forEach((node) => node.classList.toggle('active', node.id === `view-${view}`));
      document.querySelectorAll('.nav-item').forEach((node) => {
        node.setAttribute('aria-current', node.dataset.view === view ? 'page' : 'false');
      });
    }

    function applyLanguage(lang) {
      state.lang = lang;
      document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
      document.title = lang === 'zh' ? 'WQ Alpha Agent | 因子挖掘研究边界' : 'WQ Alpha Agent | Research Boundaries';
      document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.dataset.i18n); });
      document.querySelectorAll('[data-i18n-html]').forEach((node) => { node.innerHTML = t(node.dataset.i18nHtml); });
      document.querySelectorAll('[data-lang]').forEach((button) => button.setAttribute('aria-pressed', button.dataset.lang === lang ? 'true' : 'false'));
      renderAll();
      renderInspector();
      renderRefreshNote();
    }

    function renderNav() {
      const nav = document.getElementById('nav');
      nav.innerHTML = state.model.navigation.map((item) => {
        const navItem = objectOrEmpty(item);
        const itemId = fmt(navItem.id);
        const count = itemId === 'runs' ? state.model.summary.run_count || 0 : '';
        const label = COPY[state.lang].nav[itemId] || navItem.label;
        return `<button class="nav-item" type="button" data-view="${escapeHtml(itemId)}" aria-current="${itemId === state.view ? 'page' : 'false'}">
          <span>${escapeHtml(label)}</span><span class="nav-count">${escapeHtml(count)}</span>
        </button>`;
      }).join('');
      nav.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
    }

    function renderSummary() {
      const summary = state.model.summary || {};
      document.getElementById('current-run').textContent = summary.latest_run_tag || t('noRunsFound');
      document.getElementById('summary-metrics').innerHTML = [
        metric(t('runsIndexed'), summary.run_count),
        metric(t('activeRuns'), summary.active_count),
        metric(t('completeRuns'), summary.complete_count)
      ].join('');
    }

    function metric(label, value) {
      return `<div class="panel metric"><div class="metric-value">${escapeHtml(value)}</div><div class="metric-label">${escapeHtml(label)}</div></div>`;
    }

    function renderRuns() {
      const list = document.getElementById('run-list');
      const runs = Array.isArray(state.runs) ? state.runs : [];
      if (!runs.length) {
        list.innerHTML = `<div class="empty">${t('noRunsFound')}</div>`;
        return;
      }
      list.innerHTML = runs.map((run) => {
        const item = objectOrEmpty(run);
        const percent = item.daily_budget ? clamp((item.inferred_spent_simulations / item.daily_budget) * 100) : 0;
        return `<div class="run-row">
          <span><strong>${escapeHtml(item.run_tag)}</strong><br><span class="muted mono">${escapeHtml(item.date)}</span></span>
          <span class="badge ${escapeHtml(item.health)}">${escapeHtml(statusText(item.health))}</span>
          <span><span class="mono">${escapeHtml(item.inferred_spent_simulations)}/${escapeHtml(item.daily_budget)}</span><div class="bar"><span class="bar-fill" style="width:${escapeHtml(percent)}%"></span></div></span>
          <span class="mono">${escapeHtml(item.current_stage)}</span>
        </div>`;
      }).join('');
    }

    function renderBehaviorLibrary() {
      const rows = [...BEHAVIOR_LIBRARY];
      if (state.boundaryDraftAdded) {
        rows.unshift({
          id: 'draft',
          zhName: t('boundaryDraftName'),
          zhMechanism: t('boundaryDraftBody'),
          zhProxy: '状态：待补充代理字段和失效信号。',
          enName: t('boundaryDraftName'),
          enMechanism: t('boundaryDraftBody'),
          enProxy: 'Status: proxy fields and failure signals required.'
        });
      }
      document.getElementById('behavior-library').innerHTML = rows.map((item) => {
        const entry = objectOrEmpty(item);
        const name = state.lang === 'zh' ? entry.zhName : entry.enName;
        const mechanism = state.lang === 'zh' ? entry.zhMechanism : entry.enMechanism;
        const proxy = state.lang === 'zh' ? entry.zhProxy : entry.enProxy;
        return `<article class="thesis-row">
          <span class="badge ready">${escapeHtml(entry.id)}</span>
          <h2>${escapeHtml(name)}</h2>
          <p>${escapeHtml(mechanism)}</p>
          <p class="muted">${escapeHtml(proxy)}</p>
        </article>`;
      }).join('');
    }

    function memoryLayerLabel(layer) {
      return state.lang === 'zh' ? layer.zh_label || layer.label : layer.label;
    }

    function renderMemoryLayers() {
      const board = document.getElementById('memory-layer-board');
      if (!board) return;
      const layers = state.model.memory_layers || [];
      if (!layers.length) {
        board.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无记忆层数据' : 'No memory layer data'}</div>`;
        return;
      }
      board.innerHTML = layers.map((layer) => {
        const item = objectOrEmpty(layer);
        const selected = item.id === state.selectedMemoryId;
        const policyLabel = state.lang === 'zh' ? '策略' : 'Policy';
        const scopeLabel = state.lang === 'zh' ? '范围' : 'Scope';
        const retentionLabel = state.lang === 'zh' ? '保留期' : 'Retention';
        const itemLabel = state.lang === 'zh' ? '条记忆' : 'memories';
        return `<button class="memory-layer-card" type="button" data-memory-id="${escapeHtml(item.id)}" aria-selected="${selected ? 'true' : 'false'}">
          <span class="badge ${selected ? 'ready' : ''}">${escapeHtml(item.id)}</span>
          <h2>${escapeHtml(memoryLayerLabel(item))}</h2>
          <p class="muted"><strong>${escapeHtml(scopeLabel)}</strong>: ${escapeHtml(item.scope)}</p>
          <p class="muted"><strong>${escapeHtml(retentionLabel)}</strong>: ${escapeHtml(item.retention)}</p>
          <p>${escapeHtml(policyLabel)}: ${escapeHtml(item.policy)}</p>
          <span class="mono">${escapeHtml(item.items)} ${escapeHtml(itemLabel)}</span>
        </button>`;
      }).join('');
      board.querySelectorAll('[data-memory-id]').forEach((button) => {
        button.addEventListener('click', () => selectMemoryNode(button.dataset.memoryId));
      });
    }

    function renderMemoryGraph() {
      const graph = document.getElementById('memory-graph');
      if (!graph) return;
      const layers = state.model.memory_layers || [];
      const edges = state.model.memory_edges || [];
      const layerById = Object.fromEntries(layers.map((layer) => {
        const item = objectOrEmpty(layer);
        return [item.id, item];
      }));
      const nodes = layers.map((layer) => {
        const item = objectOrEmpty(layer);
        return `<button class="memory-node ${item.id === state.selectedMemoryId ? 'active' : ''}" type="button" data-memory-id="${escapeHtml(item.id)}">
        <span class="badge">${escapeHtml(item.id)}</span>
        <strong>${escapeHtml(memoryLayerLabel(item))}</strong>
        <span class="muted">${escapeHtml(item.scope)}</span>
      </button>`;
      }).join('');
      const edgeRows = edges.map((edge) => {
        const item = objectOrEmpty(edge);
        const from = layerById[item.from] ? memoryLayerLabel(layerById[item.from]) : item.from;
        const to = layerById[item.to] ? memoryLayerLabel(layerById[item.to]) : item.to;
        const relation = state.lang === 'zh' ? item.zh_relation || item.relation : item.relation;
        return `<div class="memory-edge" data-from="${escapeHtml(item.from)}" data-to="${escapeHtml(item.to)}" data-depends-on="${escapeHtml(item.from)}-&gt;${escapeHtml(item.to)}">
          <span>${escapeHtml(from)}</span><span class="relation">${escapeHtml(relation)}</span><span>${escapeHtml(to)}</span>
          <span class="muted" style="grid-column: 1 / -1;">${escapeHtml(item.rule)}</span>
        </div>`;
      }).join('');
      graph.innerHTML = `${nodes}${edgeRows}`;
      graph.querySelectorAll('[data-memory-id]').forEach((button) => {
        button.addEventListener('click', () => selectMemoryNode(button.dataset.memoryId));
      });
    }

    function selectMemoryNode(memoryId) {
      state.selectedMemoryId = memoryId;
      renderMemoryLayers();
      renderMemoryGraph();
      const layer = (state.model.memory_layers || []).map((item) => objectOrEmpty(item)).find((item) => item.id === memoryId);
      state.inspector = { type: 'memorySelected', layer };
      renderInspector();
    }

    function promoteMemoryNode() {
      state.inspector = { type: 'memoryAction', action: 'promote' };
      renderInspector();
    }

    function decayMemoryNode() {
      state.inspector = { type: 'memoryAction', action: 'decay' };
      renderInspector();
    }

    function forgetMemoryNode() {
      state.inspector = { type: 'memoryAction', action: 'forget' };
      renderInspector();
    }

    function renderAgentPanels() {
      const panels = state.model.agent_panels || [];
      document.getElementById('agent-panels').innerHTML = panels.map((panel) => {
        const item = objectOrEmpty(panel);
        const copy = COPY[state.lang].panels[item.title] || [item.title, item.body];
        return `<div class="panel metric"><span class="badge ${escapeHtml(item.status)}">${escapeHtml(statusText(item.status))}</span><strong>${escapeHtml(copy[0])}</strong><span class="muted">${escapeHtml(copy[1])}</span></div>`;
      }).join('');
    }

    function renderRetrievalTrace() {
      const target = document.getElementById('retrieval-trace');
      if (!target) return;
      const trace = state.model.retrieval_trace || {};
      const steps = trace.steps || [];
      if (!steps.length && !trace.query) {
        target.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无 RAG trace，可刷新数据后查看检索链路。' : 'No RAG trace yet. Refresh data to inspect the retrieval chain.'}</div>`;
        return;
      }
      const query = trace.query || (state.lang === 'zh' ? '未提供 query' : 'No query provided');
      const rows = steps.map((step) => `<div class="memory-node">
        <span class="badge">${escapeHtml(objectOrEmpty(step).stage)}</span>
        <span class="muted">${escapeHtml(objectOrEmpty(step).body)}</span>
      </div>`).join('');
      target.innerHTML = `<div class="memory-node active">
        <span class="badge ready">query</span>
        <strong>${escapeHtml(query)}</strong>
      </div>${rows}`;
    }

    function renderGovernanceQueues() {
      const target = document.getElementById('governance-queues');
      if (!target) return;
      const queues = state.model.governance_queues || {};
      const entries = Object.entries(queues);
      if (!entries.length) {
        target.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无治理队列。' : 'No governance queues yet.'}</div>`;
        return;
      }
      target.innerHTML = entries.map(([queue, items]) => {
        const rows = (Array.isArray(items) ? items : [items]).map((item) => `<span class="muted">${escapeHtml(item)}</span>`).join('');
        return `<div class="memory-node">
          <span class="badge">${escapeHtml(queue)}</span>
          ${rows}
        </div>`;
      }).join('');
    }

    function renderHypothesisLedger() {
      const target = document.getElementById('hypothesis-ledger');
      if (!target) return;
      const entries = state.model.hypothesis_ledger || [];
      if (!entries.length) {
        target.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无假设账本条目。' : 'No hypothesis ledger entries yet.'}</div>`;
        return;
      }
      target.innerHTML = entries.map((entry) => {
        const item = objectOrEmpty(entry);
        return `<div class="memory-node">
        <strong>${escapeHtml(item.thesis)}</strong>
        <span class="muted"><strong>${escapeHtml(t('proxyMappingLabel'))}</strong>: ${escapeHtml(item.proxy)}</span>
        <span class="muted"><strong>${escapeHtml(t('killConditionLabel'))}</strong>: ${escapeHtml(item.kill_condition)}</span>
        <span class="badge ready">${escapeHtml(item.success)}</span>
      </div>`;
      }).join('');
    }

    function renderWqbActionLanes() {
      const target = document.getElementById('wqb-action-lanes');
      if (!target) return;
      const lanes = state.model.wqb_action_lanes || [];
      if (!lanes.length) {
        target.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无 WQB 动作队列。' : 'No WQB action lanes yet.'}</div>`;
        return;
      }
      target.innerHTML = lanes.map((lane) => {
        const item = objectOrEmpty(lane);
        return `<div class="memory-node">
        <span class="badge ready">${escapeHtml(item.id)}</span>
        <strong>${escapeHtml(item.label)}</strong>
      </div>`;
      }).join('');
    }

    function renderAdversarialReview() {
      const target = document.getElementById('adversarial-review');
      if (!target) return;
      const entries = state.model.adversarial_review || [];
      if (!entries.length) {
        target.innerHTML = `<div class="empty">${state.lang === 'zh' ? '暂无对抗审查规则。' : 'No adversarial review rules yet.'}</div>`;
        return;
      }
      target.innerHTML = entries.map((entry) => {
        const item = objectOrEmpty(entry);
        const body = typeof entry === 'string' ? entry : item.body || item.rule || item.title;
        return `<div class="memory-node">
          <span class="badge">${escapeHtml(t('adversarialReviewTitle'))}</span>
          <span>${escapeHtml(body)}</span>
        </div>`;
      }).join('');
    }

    function renderAgentEvaluation() {
      const summaryTarget = document.getElementById('agent-evaluation-summary');
      const reportsTarget = document.getElementById('agent-evaluation-reports');
      if (!summaryTarget || !reportsTarget) return;
      const evaluation = objectOrEmpty(state.model.agent_evaluation);
      const summary = objectOrEmpty(evaluation.summary);
      const reports = Array.isArray(evaluation.reports) ? evaluation.reports : [];
      summaryTarget.innerHTML = [
        metric(t('evaluationReportCount'), summary.report_count || 0),
        metric(t('latestVerdict'), summary.latest_verdict || '--'),
        metric(t('comparisonType'), summary.latest_comparison_type || '--')
      ].join('');
      if (!reports.length) {
        reportsTarget.innerHTML = `<div class="empty">${escapeHtml(t('noEvaluationReports'))}</div>`;
        return;
      }
      reportsTarget.innerHTML = reports.map((report) => {
        const item = objectOrEmpty(report);
        const variants = objectOrEmpty(item.variants);
        const deltas = objectOrEmpty(item.delta_vs_baseline);
        const missing = Array.isArray(item.missing_variants) && item.missing_variants.length
          ? item.missing_variants.join(', ')
          : '--';
        const variantRows = Object.entries(variants).map(([name, metrics]) => {
          const values = objectOrEmpty(metrics);
          return `<div class="memory-node">
            <span class="badge">${escapeHtml(name)}</span>
            <span class="muted">submit_ready_per_1000 ${escapeHtml(values.submit_ready_per_1000)}</span>
            <span class="muted">final_submitted_per_1000 ${escapeHtml(values.final_submitted_per_1000)}</span>
            <span class="muted">wasted_budget_rate ${escapeHtml(values.wasted_budget_rate)}</span>
            <span class="muted">complexity_cost_rate ${escapeHtml(values.complexity_cost_rate)}</span>
            <strong>${escapeHtml(values.net_usefulness_score)}</strong>
          </div>`;
        }).join('');
        const deltaRows = Object.entries(deltas).map(([name, metrics]) => {
          const values = objectOrEmpty(metrics);
          return `<div class="memory-node">
            <span class="badge ready">${escapeHtml(name)}</span>
            <span class="muted">delta submit_ready_per_1000 ${escapeHtml(values.submit_ready_per_1000)}</span>
            <span class="muted">delta wasted_budget_rate ${escapeHtml(values.wasted_budget_rate)}</span>
            <span class="muted">delta net_usefulness_score ${escapeHtml(values.net_usefulness_score)}</span>
          </div>`;
        }).join('') || `<div class="empty">${escapeHtml(t('noBaselineDelta'))}</div>`;
        return `<article class="panel pad stack">
          <div class="section-head">
            <div>
              <span class="badge ${escapeHtml(item.verdict)}">${escapeHtml(item.verdict)}</span>
              <h2>${escapeHtml(item.run_tag)}</h2>
              <p class="muted mono">${escapeHtml(item.report_path)}</p>
            </div>
            <div class="top-actions">
              <span class="badge">${escapeHtml(item.comparison_type)}</span>
              <span class="badge">${escapeHtml(t('missingVariants'))}: ${escapeHtml(missing)}</span>
            </div>
          </div>
          <div class="grid-3">${variantRows}</div>
          <div class="stack">${deltaRows}</div>
        </article>`;
      }).join('');
    }

    function renderAll() {
      state.model = normalizeModel(state.model);
      state.runs = Array.isArray(state.runs) ? state.runs : [];
      renderNav();
      renderSummary();
      renderRuns();
      renderBehaviorLibrary();
      renderMemoryLayers();
      renderMemoryGraph();
      renderAgentPanels();
      renderRetrievalTrace();
      renderGovernanceQueues();
      renderHypothesisLedger();
      renderWqbActionLanes();
      renderAdversarialReview();
      renderAgentEvaluation();
    }

    function renderRefreshNote() {
      const note = document.getElementById('refresh-note');
      if (note) note.textContent = state.generatedAt ? `${t('updated')} ${state.generatedAt}` : '--';
    }

    function renderInspector() {
      const target = document.getElementById('inspector-copy');
      if (state.inspector.type === 'plan') {
        target.textContent = state.lang === 'zh'
          ? `研究计划已生成：${state.inspector.budget} 次模拟，边界数量 ${state.inspector.boundaryCount}。`
          : `Research plan generated: ${state.inspector.budget} simulations, ${state.inspector.boundaryCount} boundaries.`;
        return;
      }
      if (state.inspector.type === 'approved') {
        target.textContent = t('planApprovedBody');
        return;
      }
      if (state.inspector.type === 'editBudget') {
        target.textContent = t('editBudgetBody');
        return;
      }
      if (state.inspector.type === 'boundaryDraft') {
        target.textContent = t('boundaryDraftAdded');
        return;
      }
      if (state.inspector.type === 'memorySelected') {
        const layer = state.inspector.layer;
        target.textContent = layer
          ? `${memoryLayerLabel(layer)}: ${layer.policy}`
          : (state.lang === 'zh' ? '请选择一个记忆层。' : 'Select a memory layer.');
        return;
      }
      if (state.inspector.type === 'memoryAction') {
        const actionCopy = {
          promote: t('memoryPromoted'),
          decay: t('memoryDecayed'),
          forget: t('memoryForgotten')
        };
        target.textContent = actionCopy[state.inspector.action];
        return;
      }
      target.textContent = state.lang === 'zh' ? '维护预算和行为经济学边界即可。' : 'Maintain budget and behavioral boundaries only.';
    }

    function generatePlan() {
      const budgetInput = document.getElementById('daily-budget');
      const budget = Math.max(1, Number.parseInt(budgetInput.value, 10) || 1000);
      budgetInput.value = String(budget);
      const allowed = document.getElementById('allowed-thesis').value.split('\\n').map((x) => x.trim()).filter(Boolean);
      const blocked = document.getElementById('blocked-thesis').value.split('\\n').map((x) => x.trim()).filter(Boolean);
      const stages = [
        ['direction_probe', 0.12, state.lang === 'zh' ? '只在允许边界内小规模试探新方向。' : 'Probe only inside allowed boundaries.'],
        ['scale_winners', 0.36, state.lang === 'zh' ? '扩大近期有效且不违反边界的 family。' : 'Scale recent winners that stay inside boundaries.'],
        ['pass_corr_repair_optimization', 0.34, state.lang === 'zh' ? '修复 high self-corr、LOW_FITNESS 和重复 skeleton。' : 'Repair high self-corr, LOW_FITNESS, and duplicate skeletons.'],
        ['late_rescue_or_exploration', 0.12, state.lang === 'zh' ? '用于 near-pass rescue 和受控探索。' : 'Use for near-pass rescue and bounded exploration.'],
        ['end_of_day_holdout', 0.06, state.lang === 'zh' ? '保留最终验证预算。' : 'Hold budget for final validation.']
      ];
      let allocated = 0;
      const rows = stages.map(([stage, ratio, reason], index) => {
        const amount = index === stages.length - 1 ? budget - allocated : Math.round(budget * ratio);
        allocated += amount;
        return `<div class="plan-row"><strong>${escapeHtml(stage)}</strong><span class="badge ready">${escapeHtml(amount)}</span><span class="muted">${escapeHtml(reason)}</span></div>`;
      }).join('');
      const allowedSummary = allowed.map((value) => escapeHtml(value)).join(' / ');
      const blockedSummary = blocked.map((value) => escapeHtml(value)).join(' / ');
      document.getElementById('plan-output').innerHTML = `
        <div class="section-head">
          <div>
            <h2>${escapeHtml(t('generatedPlanTitle'))}</h2>
            <p class="muted">${escapeHtml(state.lang === 'zh' ? '允许边界' : 'Allowed boundaries')}: ${allowedSummary}</p>
            <p class="muted">${escapeHtml(state.lang === 'zh' ? '禁止边界' : 'Blocked boundaries')}: ${blockedSummary}</p>
          </div>
          <div class="top-actions">
            <span class="badge" id="plan-status">${escapeHtml(t('planDraft'))}</span>
            <button class="button" type="button" id="edit-budget-button">${escapeHtml(t('editBudget'))}</button>
            <button class="button primary" type="button" id="approve-plan-button">${escapeHtml(t('approvePlan'))}</button>
          </div>
        </div>
        <div>${rows}</div>
        <div class="empty"><strong>${escapeHtml(state.lang === 'zh' ? '边界摘要' : 'Boundary summary')}</strong><br>${escapeHtml(t('evidenceSummary'))}</div>`;
      state.inspector = { type: 'plan', budget, boundaryCount: allowed.length + blocked.length };
      renderInspector();
    }

    function approvePlan() {
      const status = document.getElementById('plan-status');
      if (status) {
        status.textContent = t('planApproved');
        status.className = 'badge ready';
      }
      state.inspector = { type: 'approved' };
      renderInspector();
    }

    function editBudget() {
      const budgetInput = document.getElementById('daily-budget');
      budgetInput.focus();
      budgetInput.select();
      state.inspector = { type: 'editBudget' };
      renderInspector();
    }

    function addThesisCard() {
      state.boundaryDraftAdded = true;
      renderBehaviorLibrary();
      state.inspector = { type: 'boundaryDraft' };
      renderInspector();
    }

    async function refresh() {
      const response = await fetch('/api/runs');
      const responsePayload = await response.json();
      const payload = responsePayload && typeof responsePayload === 'object' && !Array.isArray(responsePayload) ? responsePayload : {};
      state.runs = Array.isArray(payload.runs) ? payload.runs : [];
      state.model = normalizeModel(payload.model);
      state.generatedAt = payload.generated_at;
      renderAll();
      renderRefreshNote();
    }

    document.getElementById('refresh-button').addEventListener('click', refresh);
    document.getElementById('generate-plan-button').addEventListener('click', generatePlan);
    document.getElementById('add-thesis-button').addEventListener('click', addThesisCard);
    document.getElementById('promote-memory-button').addEventListener('click', promoteMemoryNode);
    document.getElementById('decay-memory-button').addEventListener('click', decayMemoryNode);
    document.getElementById('forget-memory-button').addEventListener('click', forgetMemoryNode);
    document.getElementById('plan-output').addEventListener('click', (event) => {
      if (event.target.id === 'approve-plan-button') approvePlan();
      if (event.target.id === 'edit-budget-button') editBudget();
    });
    document.querySelectorAll('[data-lang]').forEach((button) => button.addEventListener('click', () => applyLanguage(button.dataset.lang)));
    document.querySelectorAll('[data-jump]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.jump)));

    applyLanguage('zh');
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""
