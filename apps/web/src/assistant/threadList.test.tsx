import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderInApp } from "../test/assistant";
import { ThreadList } from "./ThreadList";

vi.mock("../auth/session", () => ({
  useAuth: () => ({ status: "signed-in", session: { user: { id: "u1" }, access_token: "t" }, config: { ui_copy: {} } }),
}));
vi.mock("../auth/api", async () => {
  const { unwrap } = await import("../auth/apiError");
  return {
    authed: (make: (h: Record<string, string>) => Promise<unknown>) =>
      unwrap(make({ authorization: "Bearer t" }) as never),
  };
});

interface Call {
  method: string;
  url: string;
  body: unknown;
}

let calls: Call[] = [];
const INITIAL = [
  {
    id: "t1",
    aegra_thread_id: "a1",
    title: "Anion gap workup",
    created_at: "2026-11-10T10:00:00Z",
    last_message_at: "2026-11-10T10:05:00Z",
    page_ref: null,
  },
  {
    id: "t2",
    aegra_thread_id: "a2",
    title: "RTA types",
    created_at: "2026-11-11T10:00:00Z",
    last_message_at: null,
    page_ref: null,
  },
];

let threads = INITIAL;

beforeEach(() => {
  calls = [];
  threads = INITIAL;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (method === "GET") {
        const q = new URL(url, "http://x").searchParams.get("q") ?? "";
        const items = threads.filter((t) => t.title.toLowerCase().includes(q.toLowerCase()));
        return new Response(JSON.stringify({ items }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (method === "PATCH") {
        const id = url.split("/").at(-1);
        const body = JSON.parse(String(init.body)) as { title: string };
        threads = threads.map((t) => (t.id === id ? { ...t, title: body.title } : t));
        return new Response(JSON.stringify(threads.find((t) => t.id === id)), { status: 200 });
      }
      if (method === "DELETE") {
        const id = url.split("/").at(-1);
        threads = threads.filter((t) => t.id !== id);
        return new Response(null, { status: 204 });
      }
      return new Response("{}", { status: 404 });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("thread list", () => {
  it("lists past chats, reopens one by its runtime thread id, marks the open one", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderInApp(() => <ThreadList currentThreadId="a2" onOpen={onOpen} onNew={() => {}} />);
    const first = await screen.findByRole("button", { name: /^Anion gap workup/ });
    expect(screen.getByRole("button", { name: /^RTA types/ })).toHaveAttribute("aria-current", "true");
    await user.click(first);
    expect(onOpen).toHaveBeenCalledWith("a1");
  });

  it("searches by title on the server", async () => {
    const user = userEvent.setup();
    renderInApp(() => <ThreadList currentThreadId={null} onOpen={() => {}} onNew={() => {}} />);
    await screen.findByRole("button", { name: /^Anion gap workup/ });
    await user.type(screen.getByRole("searchbox", { name: "Search past chats" }), "rta");
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Anion gap workup/ })).toBeNull());
    expect(calls.some((c) => c.method === "GET" && c.url.includes("q=rta"))).toBe(true);
  });

  it("renames inline", async () => {
    const user = userEvent.setup();
    renderInApp(() => <ThreadList currentThreadId={null} onOpen={() => {}} onNew={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Rename “RTA types”" }));
    const input = screen.getByRole("textbox", { name: "New title" });
    await user.clear(input);
    await user.type(input, "Renal tubular acidosis{Enter}");
    await screen.findByRole("button", { name: /^Renal tubular acidosis/ });
    expect(calls).toContainEqual({
      method: "PATCH",
      url: "/api/assistant/threads/t2",
      body: { title: "Renal tubular acidosis" },
    });
  });

  it("deletes only after a confirm; deleting the open chat starts a new one", async () => {
    const user = userEvent.setup();
    const onNew = vi.fn();
    renderInApp(() => <ThreadList currentThreadId="a1" onOpen={() => {}} onNew={onNew} />);
    await user.click(await screen.findByRole("button", { name: "Delete “Anion gap workup”" }));
    const confirm = screen.getByText(/cannot be brought back/).closest("li") as HTMLElement;
    await user.click(within(confirm).getByRole("button", { name: "Keep" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Delete “Anion gap workup”" }));
    await user.click(
      within(screen.getByText(/cannot be brought back/).closest("li") as HTMLElement).getByRole("button", {
        name: "Delete",
      }),
    );
    await waitFor(() => expect(onNew).toHaveBeenCalled());
    expect(calls).toContainEqual({ method: "DELETE", url: "/api/assistant/threads/t1", body: undefined });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Anion gap workup/ })).toBeNull());
  });
});
