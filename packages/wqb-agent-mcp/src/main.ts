import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createWQBAgentMcpServer } from "./server.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const workspaceRoot = resolve(packageRoot, "..", "..");
const server = createWQBAgentMcpServer({ cwd: workspaceRoot });
const transport = new StdioServerTransport();

await server.connect(transport);
