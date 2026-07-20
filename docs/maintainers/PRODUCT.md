# Product

## Register

product

## Users

This project is for a single WorldQuant BRAIN alpha researcher who wants an agent-assisted workflow without giving up control of research budget and behavioral-economics boundaries. The user reviews run ledgers and result artifacts and cares about whether each simulation budget unit moves toward independent, submit-ready alpha candidates.

## Product Purpose

The product turns WQB factor mining into a productized open-source workflow. It manages run evidence, hypotheses, governed memory, submission queues, and dashboard feedback so the user can pursue more high-quality, low-correlation alphas. Success is measured by submit-ready candidates per simulation budget, fewer duplicate or high-correlation attempts, clearer kill conditions, and a workflow that other technical users can understand, inspect, and extend.

## 当前生产边界

- `wqb_agent_lab.workflow.ResearchWorkflow` is the production orchestrator; the continuous scheduler is retained only for historical compatibility.
- The daemon, budget ledger, candidate preflight, simulation, diagnosis, triage, asynchronous submission, registry sync, memory sync, and completion evaluation are wired into unattended execution.
- Memory retrieval is available, but the production planner does not yet retrieve from the memory graph automatically.
- Structured submission governance is available through the engine/MCP path; the automatic backlog path still launches the submission worker directly.
- Evaluation and ablation reports are generated after completion, but they do not yet directly reallocate the next run's budget.

## 目标能力

Close the three open feedback links above only when measured evidence shows that retrieval, governance unification, or evaluation-driven allocation improves final submitted quality per simulation budget. Until then, the product describes itself as an unattended execution loop with partial feedback, not a fully self-evolving agent.

## Brand Personality

Calm, rigorous, research-native. The interface should feel like a disciplined quant research cockpit: dense enough for repeated use, explicit about evidence and failure modes, and confident without sounding promotional.

## Anti-references

Do not make this look like a generic SaaS landing page, decorative agent demo, or chatbot-first toy. Avoid oversized marketing heroes, vague productivity copy, decorative memory diagrams without operational meaning, and UI that asks the user to manually manage every candidate. The user should maintain budgets and behavioral boundaries; the system should expose memory, graph, and governance details when they help audit decisions.

## Design Principles

1. Budget is the unit of truth: every feature should help explain or improve how simulation budget turns into submit-ready alpha evidence.
2. Behavioral logic must stay inspectable: hypotheses, proxy mappings, kill conditions, and action lanes should be visible enough to audit.
3. Memory is operational evidence, not decoration: retrieval traces, graph relationships, promotion, decay, and forgetting need measured consequences before they control production planning.
4. Empty states must be honest: never imply evidence, candidates, or graph density that the run data has not produced.
5. Deterministic behavior beats spectacle: reproducible outputs, clear failure states, and readable Chinese defaults matter more than dramatic visuals.

## Accessibility & Inclusion

Target a practical WCAG AA baseline for contrast, keyboard access, and readable text. The dashboard should remain usable in Chinese by default, with English copy available. Motion should be restrained and nonessential, with reduced-motion users able to use all workflows without losing information.
