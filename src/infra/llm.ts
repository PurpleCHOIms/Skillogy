/**
 * LLM client provider with multi-backend auth.
 *
 * Backends (auto-detected):
 *   1. Google Gemini   — when GOOGLE_API_KEY is set OR SKILLOGY_LLM=gemini
 *   2. Claude Agent SDK — inherits Claude Code OAuth (no API key needed)
 *   3. Anthropic API   — when ANTHROPIC_API_KEY is set
 *
 * Override via SKILLOGY_LLM env var: "gemini" | "sdk" | "api".
 */

const MODEL_ALIASES: Record<string, string> = {
  "claude-haiku-4-5": "claude-haiku-4-5-20251001",
};

const DEFAULT_GEMINI_MODEL =
  process.env.SKILLOGY_GEMINI_MODEL ?? "gemini-pro-latest";

function normalizeModel(model: string): string {
  return MODEL_ALIASES[model] ?? model;
}

export interface CompleteOptions {
  prompt: string;
  system?: string;
  maxTokens?: number;
  temperature?: number;
}

export interface LLMClient {
  complete(opts: CompleteOptions): Promise<string>;
}

class SDKClient implements LLMClient {
  // No model field — Claude Code's session controls which model the SDK uses.
  async complete(opts: CompleteOptions): Promise<string> {
    // Dynamic import keeps the dependency optional at module-load time.
    const { query } = (await import("@anthropic-ai/claude-agent-sdk")) as {
      query: (args: {
        prompt: string;
        options?: {
          systemPrompt?: string;
          maxTurns?: number;
          allowedTools?: string[];
        };
      }) => AsyncIterable<unknown>;
    };

    const queryOptions: { systemPrompt?: string; maxTurns: number; allowedTools: string[] } = {
      maxTurns: 1,
      allowedTools: [],
    };
    if (opts.system !== undefined) queryOptions.systemPrompt = opts.system;

    const chunks: string[] = [];
    for await (const message of query({ prompt: opts.prompt, options: queryOptions })) {
      const m = message as {
        type?: string;
        subtype?: string;
        result?: string;
        message?: { content?: Array<{ type?: string; text?: string }> };
      };

      if (m.type === "result" && m.subtype === "success" && typeof m.result === "string") {
        chunks.push(m.result);
        continue;
      }
      if (m.type === "assistant" && m.message?.content) {
        for (const block of m.message.content) {
          if (block.type === "text" && typeof block.text === "string") {
            chunks.push(block.text);
          }
        }
      }
    }
    return chunks.join("");
  }
}

class APIClient implements LLMClient {
  private _client: unknown;

  constructor(private readonly _model: string) {}

  async complete(opts: CompleteOptions): Promise<string> {
    if (!this._client) {
      const mod = (await import("@anthropic-ai/sdk")) as { default: new () => unknown };
      const Anthropic = mod.default;
      this._client = new Anthropic();
    }
    const client = this._client as {
      messages: {
        create: (args: {
          model: string;
          max_tokens: number;
          temperature: number;
          system?: string;
          messages: Array<{ role: "user"; content: string }>;
        }) => Promise<{ content: Array<{ type: string; text?: string }> }>;
      };
    };

    const args: Parameters<typeof client.messages.create>[0] = {
      model: this._model,
      max_tokens: opts.maxTokens ?? 1024,
      temperature: opts.temperature ?? 0.0,
      messages: [{ role: "user", content: opts.prompt }],
    };
    if (opts.system !== undefined) args.system = opts.system;

    const response = await client.messages.create(args);
    const first = response.content[0];
    if (first && first.type === "text" && typeof first.text === "string") {
      return first.text;
    }
    return "";
  }
}

class GeminiClient implements LLMClient {
  private _client: unknown;

  constructor(private readonly _model: string) {}

  async complete(opts: CompleteOptions): Promise<string> {
    if (!this._client) {
      const mod = (await import("@google/genai")) as {
        GoogleGenAI: new (args?: { apiKey?: string }) => unknown;
      };
      const GoogleGenAI = mod.GoogleGenAI;
      const apiKey = process.env.GOOGLE_API_KEY;
      this._client = apiKey ? new GoogleGenAI({ apiKey }) : new GoogleGenAI();
    }
    const client = this._client as {
      models: {
        generateContent: (args: {
          model: string;
          contents: string;
          config?: {
            temperature?: number;
            maxOutputTokens?: number;
            systemInstruction?: string;
          };
        }) => Promise<{ text?: string }>;
      };
    };

    const config: {
      temperature: number;
      maxOutputTokens: number;
      systemInstruction?: string;
    } = {
      temperature: opts.temperature ?? 0.0,
      maxOutputTokens: opts.maxTokens ?? 1024,
    };
    if (opts.system !== undefined) config.systemInstruction = opts.system;

    const response = await client.models.generateContent({
      model: this._model,
      contents: opts.prompt,
      config,
    });
    return response.text ?? "";
  }
}

interface ImportProbe {
  ok: boolean;
  // "missing" if the package itself isn't installed, "load-error" if it tried
  // to load but threw (most often: native binary mismatch on the SDK).
  reason?: "missing" | "load-error";
  message?: string;
}

async function probeImport(name: string): Promise<ImportProbe> {
  try {
    await import(name);
    return { ok: true };
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    const message = (err as Error).message ?? String(err);
    if (code === "ERR_MODULE_NOT_FOUND" || /Cannot find package/.test(message)) {
      return { ok: false, reason: "missing", message };
    }
    return { ok: false, reason: "load-error", message };
  }
}

async function isImportable(name: string): Promise<boolean> {
  return (await probeImport(name)).ok;
}

function resolveModelForProvider(provider: string, model: string): string {
  if (provider === "gemini") {
    return model.startsWith("gemini") ? model : DEFAULT_GEMINI_MODEL;
  }
  return normalizeModel(model);
}

export async function getLlmClient(model = "claude-haiku-4-5"): Promise<LLMClient> {
  const forced = (process.env.SKILLOGY_LLM ?? "").toLowerCase();
  const forceApiKey = process.env.SKILLOGY_FORCE_API_KEY;

  // 1. Explicit override
  if (forced === "gemini") {
    if (!(await isImportable("@google/genai"))) {
      throw new Error("SKILLOGY_LLM=gemini set but @google/genai is not installed.");
    }
    return new GeminiClient(resolveModelForProvider("gemini", model));
  }
  if (forced === "sdk") {
    if (!(await isImportable("@anthropic-ai/claude-agent-sdk"))) {
      throw new Error(
        "SKILLOGY_LLM=sdk set but @anthropic-ai/claude-agent-sdk is not installed.",
      );
    }
    return new SDKClient();
  }
  if (forced === "api") {
    if (!process.env.ANTHROPIC_API_KEY) {
      throw new Error("SKILLOGY_LLM=api set but ANTHROPIC_API_KEY is not set.");
    }
    return new APIClient(normalizeModel(model));
  }

  // 2. Auto-detect — Gemini first when key + lib present
  if (process.env.GOOGLE_API_KEY && (await isImportable("@google/genai"))) {
    return new GeminiClient(resolveModelForProvider("gemini", model));
  }

  // 3. Claude Agent SDK — probe explicitly so we can surface load errors
  //    (e.g. missing platform-specific native binary) rather than silently
  //    falling through to "no auth".
  let sdkProbe: ImportProbe | undefined;
  if (!forceApiKey) {
    sdkProbe = await probeImport("@anthropic-ai/claude-agent-sdk");
    if (sdkProbe.ok) {
      return new SDKClient();
    }
    if (sdkProbe.reason === "load-error") {
      // The package is present but failed to load — usually the platform
      // native binary is missing or wrong arch. Print the cause; don't
      // silently fall back to "no auth".
      process.stderr.write(
        `[skillogy llm] @anthropic-ai/claude-agent-sdk failed to load: ${sdkProbe.message ?? "(no message)"}\n`,
      );
    }
  }

  // 4. Anthropic API
  if (process.env.ANTHROPIC_API_KEY) {
    return new APIClient(normalizeModel(model));
  }

  const sdkDetail =
    sdkProbe?.reason === "load-error"
      ? ` (claude-agent-sdk present but failed to load: ${sdkProbe.message ?? "?"})`
      : "";
  throw new Error(
    "No LLM auth available. Set GOOGLE_API_KEY (Gemini), install @anthropic-ai/claude-agent-sdk" +
      ` (provided by Claude Code), or set ANTHROPIC_API_KEY.${sdkDetail}`,
  );
}
