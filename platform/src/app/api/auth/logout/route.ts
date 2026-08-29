import { api, ok } from "@/lib/api";
import { destroySession } from "@/lib/auth/session";

/** POST /api/auth/logout — destroy the current session. */
export async function POST() {
  return api(async () => {
    await destroySession();
    return ok({ loggedOut: true });
  });
}
