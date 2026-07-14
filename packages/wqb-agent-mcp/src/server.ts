import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";

import { runEngine } from "./engineClient.js";

export interface WQBAgentMcpServerOptions {
  engineCommand?: string;
  cwd?: string;
  timeoutMs?: number;
}

export function createWQBAgentMcpServer(options: WQBAgentMcpServerOptions = {}): McpServer {
  const server = new McpServer({
    name: "wqb-agent-lab",
    version: "0.2.0-alpha.1",
  });

  const engineOptions = {
    command: options.engineCommand,
    cwd: options.cwd,
    timeoutMs: options.timeoutMs,
  };

  server.registerTool(
    "schemas.list",
    {
      title: "List WQB Agent Schemas",
      description: "List public JSON contract schemas exposed by the Python engine.",
      inputSchema: {},
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (): Promise<CallToolResult> => {
      const response = await runEngine("schemas.list", [], undefined, engineOptions);
      return asToolResult(response);
    },
  );

  server.registerTool(
    "schemas.digest",
    {
      title: "Digest WQB Agent Schema",
      description: "Return the SHA-256 digest for one public JSON contract schema.",
      inputSchema: {
        schema: z.string(),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const response = await runEngine(
        "schemas.digest",
        ["--schema", args.schema],
        undefined,
        engineOptions,
      );
      return asToolResult(response);
    },
  );

  server.registerTool(
    "contracts.validate",
    {
      title: "Validate WQB Agent Contract",
      description: "Validate a JSON payload against a public contract schema.",
      inputSchema: {
        schema: z.string(),
        payload: z.record(z.string(), z.unknown()),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const response = await runEngine(
        "contracts.validate",
        ["--schema", args.schema],
        args.payload,
        engineOptions,
      );
      return asToolResult(response);
    },
  );

  for (const operation of ["policy.validate", "policy.show"] as const) {
    server.registerTool(
      operation,
      {
        title: operation === "policy.validate" ? "Validate Research Policy" : "Show Research Policy",
        description: operation === "policy.validate"
          ? "Validate the research budget and behavioral-boundary policy without WQB calls."
          : "Read the normalized research policy and its stable digest.",
        inputSchema: {
          config: z.string(),
        },
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args): Promise<CallToolResult> => {
        const response = await runEngine(operation, ["--config", args.config], undefined, engineOptions);
        return asToolResult(response);
      },
    );
  }

  server.registerTool(
    "submission.evaluate",
    {
      title: "Evaluate Submission Decision",
      description: "Evaluate a structured submit decision through the Python governance policy layer.",
      inputSchema: {
        decision: z.record(z.string(), z.unknown()),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const response = await runEngine("submission.evaluate", [], args.decision, engineOptions);
      return asToolResult(response);
    },
  );

  server.registerTool(
    "submission.submit_intent",
    {
      title: "Submit Intent",
      description: "Record and queue a submit intent for the independent submission executor.",
      inputSchema: {
        run_dir: z.string(),
        decision: z.record(z.string(), z.unknown()),
        evaluation: z.record(z.string(), z.unknown()).optional(),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const response = await runEngine(
        "submission.submit_intent",
        ["--run-dir", args.run_dir],
        { decision: args.decision, evaluation: args.evaluation },
        engineOptions,
      );
      return asToolResult(response);
    },
  );

  server.registerTool(
    "submission.execute_live",
    {
      title: "Execute Live Submission",
      description: "Execute a live-capable submit decision through governance and audit controls.",
      inputSchema: {
        run_dir: z.string(),
        decision: z.record(z.string(), z.unknown()),
        evaluation: z.record(z.string(), z.unknown()).optional(),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async (args): Promise<CallToolResult> => {
      const response = await runEngine(
        "submission.execute_live",
        ["--run-dir", args.run_dir],
        { decision: args.decision, evaluation: args.evaluation },
        engineOptions,
      );
      return asToolResult(response);
    },
  );

  server.registerTool(
    "submission.audit_tail",
    {
      title: "Read Submission Audit Tail",
      description: "Read recent submission governance audit events from a run directory.",
      inputSchema: {
        run_dir: z.string(),
        limit: z.number().int().positive().optional(),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const cliArgs = ["--run-dir", args.run_dir];
      if (args.limit !== undefined) {
        cliArgs.push("--limit", String(args.limit));
      }
      const response = await runEngine("submission.audit_tail", cliArgs, undefined, engineOptions);
      return asToolResult(response);
    },
  );

  server.registerTool(
    "loop.dry_run_validate",
    {
      title: "Run Dry-Run Loop Validation",
      description: "Run the local dry-run agent loop validation and write closed-loop artifacts.",
      inputSchema: {
        workspace_root: z.string(),
        run_tag: z.string().optional(),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async (args): Promise<CallToolResult> => {
      const cliArgs = ["--workspace-root", args.workspace_root];
      if (args.run_tag !== undefined) {
        cliArgs.push("--run-tag", args.run_tag);
      }
      const response = await runEngine("loop.dry_run_validate", cliArgs, undefined, engineOptions);
      return asToolResult(response);
    },
  );

  return server;
}

export function asToolResult(response: unknown): CallToolResult {
  const ok = typeof response === "object" && response !== null && "ok" in response
    ? Boolean((response as { ok: unknown }).ok)
    : true;

  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(response, null, 2),
      },
    ],
    isError: !ok,
  };
}
