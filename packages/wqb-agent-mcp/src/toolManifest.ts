export interface ToolManifestEntry {
  name: string;
  description: string;
  engineOperation: string;
  inputSchema: Record<string, unknown>;
}

export const TOOL_MANIFEST: ToolManifestEntry[] = [
  {
    name: "schemas.list",
    description: "List public JSON contract schemas exposed by the Python engine.",
    engineOperation: "schemas.list",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {},
    },
  },
  {
    name: "schemas.digest",
    description: "Return the SHA-256 digest for one public JSON contract schema.",
    engineOperation: "schemas.digest",
    inputSchema: {
      type: "object",
      required: ["schema"],
      additionalProperties: false,
      properties: {
        schema: { type: "string" },
      },
    },
  },
  {
    name: "contracts.validate",
    description: "Validate a JSON payload against a public contract schema.",
    engineOperation: "contracts.validate",
    inputSchema: {
      type: "object",
      required: ["schema", "payload"],
      additionalProperties: false,
      properties: {
        schema: { type: "string" },
        payload: { type: "object" },
      },
    },
  },
  {
    name: "policy.validate",
    description: "Validate the research budget and behavioral-boundary policy without WQB calls.",
    engineOperation: "policy.validate",
    inputSchema: {
      type: "object",
      required: ["config"],
      additionalProperties: false,
      properties: {
        config: { type: "string" },
      },
    },
  },
  {
    name: "policy.show",
    description: "Read the normalized research policy and its stable digest.",
    engineOperation: "policy.show",
    inputSchema: {
      type: "object",
      required: ["config"],
      additionalProperties: false,
      properties: {
        config: { type: "string" },
      },
    },
  },
  {
    name: "submission.evaluate",
    description: "Evaluate a structured submit decision through the Python governance policy layer.",
    engineOperation: "submission.evaluate",
    inputSchema: {
      type: "object",
      required: ["decision"],
      additionalProperties: false,
      properties: {
        decision: { type: "object" },
      },
    },
  },
  {
    name: "submission.submit_intent",
    description: "Record and queue a submit intent for the independent submission executor.",
    engineOperation: "submission.submit_intent",
    inputSchema: {
      type: "object",
      required: ["run_dir", "decision"],
      additionalProperties: false,
      properties: {
        run_dir: { type: "string" },
        decision: { type: "object" },
        evaluation: { type: "object" },
      },
    },
  },
  {
    name: "submission.execute_live",
    description: "Execute a live-capable submit decision through governance and audit controls.",
    engineOperation: "submission.execute_live",
    inputSchema: {
      type: "object",
      required: ["run_dir", "decision"],
      additionalProperties: false,
      properties: {
        run_dir: { type: "string" },
        decision: { type: "object" },
        evaluation: { type: "object" },
      },
    },
  },
  {
    name: "submission.audit_tail",
    description: "Read recent submission governance audit events from a run directory.",
    engineOperation: "submission.audit_tail",
    inputSchema: {
      type: "object",
      required: ["run_dir"],
      additionalProperties: false,
      properties: {
        run_dir: { type: "string" },
        limit: { type: "integer" },
      },
    },
  },
  {
    name: "loop.dry_run_validate",
    description: "Run the local dry-run agent loop validation and write closed-loop artifacts.",
    engineOperation: "loop.dry_run_validate",
    inputSchema: {
      type: "object",
      required: ["workspace_root"],
      additionalProperties: false,
      properties: {
        workspace_root: { type: "string" },
        run_tag: { type: "string" },
      },
    },
  },
];

export const toolNames = TOOL_MANIFEST.map((tool) => tool.name);
