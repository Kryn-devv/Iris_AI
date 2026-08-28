import "server-only";
import { completeJSON } from "@/lib/ai/provider";
import {
  analyzeSentiment,
  summarizeText,
  suggestCategory,
} from "@/lib/ai/heuristic";

export type PostAnalysis = {
  sentiment: "POSITIVE" | "NEUTRAL" | "NEGATIVE";
  sentimentScore: number;
  aiSummary: string;
  suggestedCategoryId: string | null;
};

/**
 * Analyze a single piece of feedback: sentiment, one-line summary, and a
 * category suggestion. Uses the configured LLM provider when available and
 * falls back to the deterministic heuristic engine otherwise.
 */
export async function analyzePostText(
  title: string,
  body: string,
  categories: { id: string; name: string }[]
): Promise<PostAnalysis> {
  const text = `${title}\n\n${body}`.trim();

  const llm = await completeJSON<{
    sentiment?: string;
    sentimentScore?: number;
    summary?: string;
    category?: string | null;
  }>(
    "You analyze a piece of customer product feedback.",
    [
      `Feedback:\n"""\n${text.slice(0, 4000)}\n"""`,
      `Available category names: ${categories.map((c) => c.name).join(", ") || "(none)"}`,
      `Return JSON: {"sentiment":"POSITIVE"|"NEUTRAL"|"NEGATIVE","sentimentScore":-1..1,"summary":"<one sentence>","category":"<one of the category names or null>"}`,
    ].join("\n")
  );

  if (llm && (llm.sentiment === "POSITIVE" || llm.sentiment === "NEUTRAL" || llm.sentiment === "NEGATIVE")) {
    const matched = categories.find(
      (c) => c.name.toLowerCase() === (llm.category ?? "").toLowerCase()
    );
    return {
      sentiment: llm.sentiment,
      sentimentScore: Math.max(-1, Math.min(1, Number(llm.sentimentScore) || 0)),
      aiSummary: (llm.summary || summarizeText(text)).slice(0, 300),
      suggestedCategoryId: matched?.id ?? null,
    };
  }

  const { sentiment, score } = analyzeSentiment(text);
  return {
    sentiment,
    sentimentScore: score,
    aiSummary: summarizeText(text),
    suggestedCategoryId: suggestCategory(text, categories),
  };
}
