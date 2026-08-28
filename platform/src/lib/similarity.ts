/**
 * Text similarity for duplicate detection and "similar request" suggestions.
 * Pure functions — usable server-side anywhere.
 */

function trigrams(text: string): Set<string> {
  const norm = ` ${text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()} `;
  const grams = new Set<string>();
  for (let i = 0; i < norm.length - 2; i++) {
    grams.add(norm.slice(i, i + 3));
  }
  return grams;
}

/** Trigram Jaccard similarity, 0..1. */
export function similarity(a: string, b: string): number {
  const ga = trigrams(a);
  const gb = trigrams(b);
  if (ga.size === 0 || gb.size === 0) return 0;
  let inter = 0;
  for (const g of ga) if (gb.has(g)) inter++;
  return inter / (ga.size + gb.size - inter);
}

export type SimilarCandidate = { id: string; title: string; body?: string };
export type SimilarMatch = { id: string; title: string; score: number };

/**
 * Rank candidates by similarity to the query (title weighted over body).
 * Returns matches with score >= threshold, best first.
 */
export function findSimilar(
  query: { title: string; body?: string },
  candidates: SimilarCandidate[],
  { threshold = 0.22, limit = 5 }: { threshold?: number; limit?: number } = {}
): SimilarMatch[] {
  const results: SimilarMatch[] = [];
  for (const c of candidates) {
    const titleScore = similarity(query.title, c.title);
    const bodyScore =
      query.body && c.body ? similarity(query.body, c.body) : 0;
    const score = titleScore * 0.7 + bodyScore * 0.3;
    if (score >= threshold) {
      results.push({ id: c.id, title: c.title, score: Number(score.toFixed(3)) });
    }
  }
  return results.sort((a, b) => b.score - a.score).slice(0, limit);
}
