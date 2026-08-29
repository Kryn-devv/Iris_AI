/** Shared serializable option types passed from server pages to client islands. */

export type CategoryOption = { id: string; name: string; color: string };
export type TagOption = { id: string; name: string; color: string };
export type BoardOption = { id: string; name: string };

export type SimilarPost = {
  id: string;
  title: string;
  score: number;
  status: string;
  voteCount: number;
};

export type ApiEnvelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: { message: string } };

/** Small fetch wrapper for the JSON API envelope. */
export async function apiFetch<T>(
  url: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const json = (await res.json().catch(() => null)) as ApiEnvelope<T> | null;
  if (!json || !("ok" in json)) throw new Error("Request failed");
  if (!json.ok) throw new Error(json.error?.message || "Request failed");
  return json.data;
}
