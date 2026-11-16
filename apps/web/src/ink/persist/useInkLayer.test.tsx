import "fake-indexeddb/auto";
import type { InkEngine } from "@naadi/ink";
import { type InkItem, uuidv7 } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ state: { status: "signed-in", session: { user: { id: "user-1" } } } as unknown }));
vi.mock("../../auth/session", () => ({ useAuth: () => auth.state, accessToken: async () => "token" }));
vi.mock("../../copy/useUiText", () => ({ useUiText: () => (k: string) => k }));

import { inkDb, resetInkDbForTests } from "./db";
import { InkSaveStatus } from "./InkSaveStatus";
import type { ChangesPayload, InkTransport } from "./sync";
import { useInkLayer } from "./useInkLayer";

function stroke(): InkItem {
  return {
    id: uuidv7(),
    tool: "pen",
    color: "ink",
    size: 0.002,
    points: [[0.1, 0.1, 0.5, 0]],
    createdAt: 1,
    updatedAt: 1_763_000_000_000,
  };
}

function transport(): InkTransport & { beacon: ReturnType<typeof vi.fn>; send: ReturnType<typeof vi.fn> } {
  const remote = stroke();
  return {
    load: vi.fn(async () => ({ rev: 3, items: [remote], reset: false, outdated: 0 })),
    send: vi.fn(
      async (_b: number, items: InkItem[]): Promise<ChangesPayload> => ({
        rev: 4,
        applied: items.map((i) => i.id),
        rejected: [],
        remote: [],
        reset: false,
      }),
    ),
    beacon: vi.fn(() => true),
  } as never;
}

function Harness({ t }: { t: InkTransport }) {
  const layer = useInkLayer("section", "s-1", { transport: t });
  (Harness as unknown as { last: typeof layer }).last = layer;
  return <InkSaveStatus layer={layer} />;
}

function wrap(children: ReactNode) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const last = () => (Harness as unknown as { last: ReturnType<typeof useInkLayer> }).last;

beforeEach(async () => {
  resetInkDbForTests();
  await inkDb().delete();
  resetInkDbForTests();
});

describe("useInkLayer", () => {
  it("loads, hands remote items to the engine via applyRemote, and saves diffs", async () => {
    const t = transport();
    render(wrap(<Harness t={t} />));
    await waitFor(() => expect(last().ready).toBe(true));
    const engine = { applyRemote: vi.fn() } as unknown as InkEngine;
    act(() => last().canvasProps.engineRef(engine));
    await waitFor(() => expect(engine.applyRemote).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("ink.save.saved"));
    const mine = stroke();
    act(() => last().canvasProps.onStrokesChange({ added: [mine], updated: [], removed: [] }));
    expect(screen.getByRole("status")).toHaveTextContent("ink.save.saving");
    await waitFor(() => expect(t.send).toHaveBeenCalled(), { timeout: 3000 });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("ink.save.saved"));
  });

  it("pagehide sends a keepalive beacon with what is pending", async () => {
    const t = transport();
    t.send.mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });
    render(wrap(<Harness t={t} />));
    await waitFor(() => expect(last().ready).toBe(true));
    const mine = stroke();
    act(() => last().canvasProps.onStrokesChange({ added: [mine], updated: [], removed: [] }));
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(t.beacon).toHaveBeenCalledTimes(1);
    expect(t.beacon.mock.calls[0]?.[1]).toEqual([mine]);
  });
});
