/**
 * Deterministic, fully offline analysis engine.
 *
 * This is the guaranteed fallback behind the AI provider abstraction: every
 * AI-powered feature must produce sensible results with zero API keys. Pure
 * functions only — no DB, no network.
 */

const POSITIVE_WORDS = new Set([
  "love", "loves", "loved", "great", "awesome", "amazing", "excellent",
  "fantastic", "perfect", "helpful", "easy", "intuitive", "fast", "beautiful",
  "wonderful", "best", "brilliant", "smooth", "delightful", "happy", "thanks",
  "thank", "appreciate", "impressive", "solid", "reliable", "clean", "nice",
  "good", "useful", "saves", "win", "wins",
]);

const NEGATIVE_WORDS = new Set([
  "hate", "hates", "terrible", "awful", "broken", "bug", "bugs", "crash",
  "crashes", "crashed", "slow", "confusing", "frustrating", "frustrated",
  "annoying", "bad", "worst", "impossible", "unusable", "missing", "fails",
  "fail", "failing", "error", "errors", "painful", "clunky", "difficult",
  "disappointed", "disappointing", "useless", "laggy", "stuck", "lost",
  "wrong", "problem", "problems", "issue", "issues", "cannot", "can't",
]);

const STOP_WORDS = new Set([
  "the", "a", "an", "and", "or", "but", "if", "then", "is", "are", "was",
  "were", "be", "been", "being", "to", "of", "in", "on", "at", "for", "with",
  "by", "from", "as", "it", "its", "this", "that", "these", "those", "i",
  "we", "you", "they", "he", "she", "my", "our", "your", "their", "me", "us",
  "them", "would", "could", "should", "can", "will", "shall", "may", "might",
  "have", "has", "had", "do", "does", "did", "not", "no", "yes", "so",
  "just", "really", "very", "please", "add", "want", "need", "like", "get",
  "make", "when", "what", "how", "why", "there", "here", "also", "more",
  "some", "any", "all", "able", "us",
]);

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s'-]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1);
}

export type SentimentResult = {
  sentiment: "POSITIVE" | "NEUTRAL" | "NEGATIVE";
  /** -1 (very negative) .. +1 (very positive) */
  score: number;
};

/** Lexicon-based sentiment with simple negation handling. */
export function analyzeSentiment(text: string): SentimentResult {
  const words = tokenize(text);
  if (words.length === 0) return { sentiment: "NEUTRAL", score: 0 };
  let score = 0;
  for (let i = 0; i < words.length; i++) {
    const word = words[i]!;
    const negated =
      i > 0 && ["not", "never", "no", "hardly", "isn't", "don't", "doesn't", "won't", "can't"].includes(words[i - 1]!);
    if (POSITIVE_WORDS.has(word)) score += negated ? -1 : 1;
    else if (NEGATIVE_WORDS.has(word)) score += negated ? 1 : -1;
  }
  const normalized = Math.max(-1, Math.min(1, score / Math.sqrt(words.length)));
  const sentiment =
    normalized > 0.15 ? "POSITIVE" : normalized < -0.15 ? "NEGATIVE" : "NEUTRAL";
  return { sentiment, score: Number(normalized.toFixed(3)) };
}

/** Top content words by frequency (stop words removed). */
export function extractKeywords(text: string, limit = 6): string[] {
  const counts = new Map<string, number>();
  for (const word of tokenize(text)) {
    if (STOP_WORDS.has(word) || word.length < 3) continue;
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([w]) => w);
}

/** One-sentence extractive summary: the highest-keyword-density sentence. */
export function summarizeText(text: string, maxLen = 180): string {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= maxLen) return clean;
  const keywords = new Set(extractKeywords(clean, 8));
  const sentences = clean.split(/(?<=[.!?])\s+/).filter((s) => s.length > 12);
  if (sentences.length === 0) return clean.slice(0, maxLen - 1) + "…";
  let best = sentences[0]!;
  let bestScore = -1;
  for (const s of sentences) {
    const words = tokenize(s);
    const hits = words.filter((w) => keywords.has(w)).length;
    const score = hits / Math.sqrt(words.length + 1);
    if (score > bestScore) {
      bestScore = score;
      best = s;
    }
  }
  return best.length > maxLen ? best.slice(0, maxLen - 1) + "…" : best;
}

export type ClusterInput = { id: string; text: string };
export type ClusterOutput = { label: string; memberIds: string[] };

/**
 * Greedy keyword-overlap clustering. Groups items whose keyword sets overlap
 * (Jaccard >= threshold) and labels each group with its dominant keywords.
 */
export function clusterTexts(
  items: ClusterInput[],
  threshold = 0.2
): ClusterOutput[] {
  const keywordSets = items.map((item) => ({
    id: item.id,
    keywords: new Set(extractKeywords(item.text, 8)),
  }));
  const assigned = new Set<string>();
  const clusters: { members: typeof keywordSets }[] = [];

  for (const item of keywordSets) {
    if (assigned.has(item.id)) continue;
    const cluster = { members: [item] };
    assigned.add(item.id);
    for (const other of keywordSets) {
      if (assigned.has(other.id)) continue;
      const inter = [...item.keywords].filter((k) => other.keywords.has(k)).length;
      const union = new Set([...item.keywords, ...other.keywords]).size;
      if (union > 0 && inter / union >= threshold) {
        cluster.members.push(other);
        assigned.add(other.id);
      }
    }
    clusters.push(cluster);
  }

  return clusters
    .filter((c) => c.members.length >= 2)
    .map((c) => {
      const freq = new Map<string, number>();
      for (const m of c.members) {
        for (const k of m.keywords) freq.set(k, (freq.get(k) ?? 0) + 1);
      }
      const label = [...freq.entries()]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .slice(0, 3)
        .map(([w]) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" · ");
      return { label: label || "Related feedback", memberIds: c.members.map((m) => m.id) };
    })
    .sort((a, b) => b.memberIds.length - a.memberIds.length);
}

/** Suggest the best-matching category name for a text, or null. */
export function suggestCategory(
  text: string,
  categories: { id: string; name: string }[]
): string | null {
  const words = new Set(tokenize(text));
  let best: string | null = null;
  let bestHits = 0;
  for (const cat of categories) {
    const catWords = tokenize(cat.name);
    const hits = catWords.filter((w) => words.has(w)).length;
    if (hits > bestHits) {
      bestHits = hits;
      best = cat.id;
    }
  }
  return best;
}
