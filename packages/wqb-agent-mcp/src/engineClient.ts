import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export type EngineResponse<T = unknown> =
  | {
      ok: true;
      operation: string;
      data: T;
    }
  | {
      ok: false;
      operation: string;
      error: {
        code: string;
        message: string;
        details: unknown[];
      };
    };

export interface EngineClientOptions {
  command?: string;
  cwd?: string;
  timeoutMs?: number;
}

export function resolveEngineCommand(
  options: EngineClientOptions = {},
  platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
): string {
  if (options.command) {
    return options.command;
  }
  if (env.WQB_ENGINE_COMMAND?.trim()) {
    return env.WQB_ENGINE_COMMAND.trim();
  }
  if (platform === "win32") {
    let directory = resolve(options.cwd ?? process.cwd());
    for (let depth = 0; depth < 5; depth += 1) {
      const virtualenvCommand = resolve(directory, ".venv", "Scripts", "wqb-engine.exe");
      if (existsSync(virtualenvCommand)) {
        return virtualenvCommand;
      }
      const parent = resolve(directory, "..");
      if (parent === directory) {
        break;
      }
      directory = parent;
    }
    return "wqb-engine.exe";
  }
  return "wqb-engine";
}

export async function runEngine<T = unknown>(
  operation: string,
  args: string[] = [],
  payload?: unknown,
  options: EngineClientOptions = {},
): Promise<EngineResponse<T>> {
  const command = resolveEngineCommand(options);
  const child = spawn(command, [operation, ...args], {
    cwd: options.cwd,
    stdio: ["pipe", "pipe", "pipe"],
    shell: false,
  });

  const timeoutMs = options.timeoutMs ?? 30_000;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill();
  }, timeoutMs);
  const stdoutChunks: Buffer[] = [];
  const stderrChunks: Buffer[] = [];

  child.stdout.on("data", (chunk: Buffer) => stdoutChunks.push(chunk));
  child.stderr.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

  if (payload !== undefined) {
    child.stdin.write(JSON.stringify(payload));
  }
  child.stdin.end();

  const outcome = await new Promise<
    { exitCode: number | null; spawnError?: Error }
  >((resolveOutcome) => {
    let settled = false;
    child.once("error", (error) => {
      if (!settled) {
        settled = true;
        resolveOutcome({ exitCode: null, spawnError: error });
      }
    });
    child.once("close", (exitCode) => {
      if (!settled) {
        settled = true;
        resolveOutcome({ exitCode });
      }
    });
  });
  clearTimeout(timer);

  const stdout = Buffer.concat(stdoutChunks).toString("utf8").trim();
  const stderr = Buffer.concat(stderrChunks).toString("utf8").trim();

  if (outcome.spawnError) {
    return {
      ok: false,
      operation,
      error: {
        code: "engine_spawn_failed",
        message: `Unable to start wqb-engine: ${outcome.spawnError.message}`,
        details: [`command=${command}`, `cwd=${options.cwd ?? process.cwd()}`],
      },
    };
  }

  if (timedOut) {
    return {
      ok: false,
      operation,
      error: {
        code: "engine_timeout",
        message: `wqb-engine exceeded timeout of ${timeoutMs}ms`,
        details: [`command=${command}`],
      },
    };
  }

  if (!stdout) {
    return {
      ok: false,
      operation,
      error: {
        code: "empty_engine_response",
        message: stderr || `wqb-engine exited with code ${outcome.exitCode}`,
        details: [],
      },
    };
  }

  try {
    return JSON.parse(stdout) as EngineResponse<T>;
  } catch (error) {
    return {
      ok: false,
      operation,
      error: {
        code: "invalid_engine_response",
        message: error instanceof Error ? error.message : "wqb-engine returned invalid JSON",
        details: [stdout],
      },
    };
  }
}
