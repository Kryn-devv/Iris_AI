import "server-only";
import Anthropic from "@anthropic-ai/sdk";

/**
 * AI provider abstraction.
 *
 * The platform never talks to a vendor SDK directly outside this file. Every
 * AI feature goes through `complete()` (which returns model text) and falls
 * back to the deterministic heuristic engine (`heuristic.ts`) when no provider
 * is configured or a call fails. Switching vendors = changing AI_PROVIDER.
 */

export type CompletionRequest = {
  system?: string;
  prompt: string;
  maxTokens?: number;
};

export interface AIProvider {
  readonly name: string;
  complete(req: CompletionRequest): Promise<string>;
}

class AnthropicProvider implements AIProvider {
  readonly name = "anthropic";
  private client: Anthropic;
  private model: string;

  constructor(apiKey: string) {
    this.client = new Anthropic({ apiKey });
    this.model = process.env.ANTHROPIC_MODEL || "claude-opus-5";
  }

  async complete(req: CompletionRequest): Promise<string> {
    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: req.maxTokens ?? 8192,
      system: req.system,
      messages: [{ role: "user", content: req.prompt }],
    });
    if (response.stop_reason === "refusal") {
      throw new Error("AI provider declined the request");
    }
    return response.content
      .filter((block): block is Anthropic.TextBlock => block.type === "text")
      .map((block) => block.text)
      .join("");
  }
}

/** Works for OpenAI and any OpenAI-compatible endpoint (OpenRouter, Groq…). */
class OpenAICompatibleProvider implements AIProvider {
  readonly name = "openai";
  private apiKey: string;
  private baseUrl: string;
  private model: string;

  constructor(apiKey: string) {
    this.apiKey = apiKey;
    this.baseUrl = (process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").replace(/\/$/, "");
    this.model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  }

  async complete(req: CompletionRequest): Promise<string> {
    const res = await fetch(`${this.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: req.maxTokens ?? 4096,
        messages: [
          ...(req.system ? [{ role: "system", content: req.system }] : []),
          { role: "user", content: req.prompt },
        ],
      }),
    });
    if (!res.ok) {
      throw new Error(`AI provider error: ${res.status} ${await res.text()}`);
    }
    const json = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    const text = json.choices?.[0]?.message?.content;
    if (typeof text !== "string") throw new Error("AI provider returned no text");
    return text;
  }
}

/**
 * Returns the configured LLM provider, or null when the platform should use
 * the deterministic heuristic engine (AI_PROVIDER=heuristic, or missing key).
 */
export function getProvider(): AIProvider | null {
  const which = (process.env.AI_PROVIDER || "heuristic").toLowerCase();
  if (which === "anthropic" && process.env.ANTHROPIC_API_KEY) {
    return new AnthropicProvider(process.env.ANTHROPIC_API_KEY);
  }
  if (which === "openai" && process.env.OPENAI_API_KEY) {
    return new OpenAICompatibleProvider(process.env.OPENAI_API_KEY);
  }
  return null;
}

/**
 * Ask the provider for strict JSON and parse it. Returns null when no
 * provider is configured or the call/parse fails — callers then use the
 * heuristic fallback.
 */
export async function completeJSON<T>(
  system: string,
  prompt: string,
  maxTokens = 4096
): Promise<T | null> {
  const provider = getProvider();
  if (!provider) return null;
  try {
    const raw = await provider.complete({
      system: `${system}\nRespond with a single valid JSON value and nothing else — no markdown fences, no commentary.`,
      prompt,
      maxTokens,
    });
    const match = raw.match(/[[{][\s\S]*[\]}]/);
    return JSON.parse(match ? match[0] : raw) as T;
  } catch (err) {
    console.error("[ai] provider call failed, using heuristic fallback", err);
    return null;
  }
}
