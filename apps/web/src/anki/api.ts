import { type DecksOut, deleteImportedAnkiDeck, getAnkiDecks, type ImportReport } from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed, useMe } from "../auth/api";
import { ApiError, UNREACHABLE } from "../auth/apiError";
import { accessToken, refreshAccessToken, useAuth } from "../auth/session";

export const decksKey = (userId: string | null) => ["anki-decks", userId] as const;

const KNOWN = new Set([
  "anki_not_a_package",
  "anki_no_collection",
  "anki_corrupt",
  "anki_unsafe_path",
  "anki_too_many_files",
  "anki_too_large",
  "anki_no_cards",
  "anki_card_cap",
  "anki_export_too_large",
  "anki_no_cards_to_export",
  "anki_bad_deck",
  "anki_unavailable",
]);

/** The ui_copy key for an Anki API failure (`anki.error.<code>`), else a generic one. */
export function errorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code && KNOWN.has(e.code)) return `anki.error.${e.code}`;
    if (e.status === 429) return "anki.error.rate_limited";
    if (e.status === null || e.status === 503) return "anki.error.anki_unavailable";
    if (e.status === 413) return "anki.error.anki_too_large";
  }
  return "anki.error.unknown";
}

/** The Anki flag from /api/me (`features.anki`): the buttons and the tile show only while it is on. */
export function useAnkiEnabled(): boolean {
  const me = useMe();
  return me.data?.features.anki === true;
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

async function failure(res: Response): Promise<ApiError> {
  let code: string | null = null;
  let message = `HTTP ${res.status}`;
  let rid: string | null = res.headers.get("x-request-id");
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string; request_id?: string | null } };
    code = body.error?.code ?? null;
    message = body.error?.message ?? message;
    rid = body.error?.request_id ?? rid;
  } catch {
    // not JSON: keep the status
  }
  return new ApiError(message, res.status, code, rid);
}

/**
 * A binary request with the signed-in token. The generated SDK cannot send a raw body or hand back a file, so these
 * two endpoints (import, export) use `fetch` directly; an expired token is refreshed once, like `authed`.
 */
async function binaryFetch(url: string, init: RequestInit): Promise<Response> {
  const run = async (token: string): Promise<Response> => {
    try {
      return await fetch(url, { ...init, headers: { ...init.headers, authorization: `Bearer ${token}` } });
    } catch {
      throw new ApiError(UNREACHABLE, null, null, null);
    }
  };
  const token = await accessToken();
  if (!token) throw new ApiError("", 401, "unauthorized", null);
  let res = await run(token);
  if (res.status === 401) {
    const fresh = await refreshAccessToken();
    if (fresh) res = await run(fresh);
  }
  if (!res.ok) throw await failure(res);
  return res;
}

export async function importDeck(file: File, prefix: string | null): Promise<ImportReport> {
  const q = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
  const res = await binaryFetch(`/api/anki/import${q}`, {
    method: "POST",
    headers: { "content-type": "application/octet-stream" },
    body: file,
  });
  return (await res.json()) as ImportReport;
}

export function filenameOf(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      // fall through to the plain name
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ?? fallback;
}

export async function exportDeck(deck: string | null): Promise<{ blob: Blob; filename: string }> {
  const q = deck ? `?deck=${encodeURIComponent(deck)}` : "";
  const res = await binaryFetch(`/api/anki/export${q}`, { method: "GET" });
  return { blob: await res.blob(), filename: filenameOf(res.headers.get("content-disposition"), "naadi.apkg") };
}

export function useAnkiDecks(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: decksKey(userId),
    enabled: enabled && userId !== null,
    queryFn: () => authed<DecksOut>((headers) => getAnkiDecks({ headers })),
  });
}

/** Cards and decks changed (an import, a delete): everything that lists them is stale. */
export function useInvalidateCards() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["anki-decks"] });
    void qc.invalidateQueries({ queryKey: ["cards-due"] });
    void qc.invalidateQueries({ queryKey: ["cards-stats"] });
  };
}

export function useDeleteImportedDeck() {
  const invalidate = useInvalidateCards();
  return useMutation({
    mutationFn: (deck: string) =>
      authed<{ deleted: number }>((headers) => deleteImportedAnkiDeck({ headers, query: { deck } })),
    onSuccess: invalidate,
  });
}
