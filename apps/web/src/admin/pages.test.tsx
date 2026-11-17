import { uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/*
 * Every admin route renders against the API's response shapes (contract types) with no runtime error: the
 * permission state for a non-admin, the page for an admin. The network is mocked at the api object; the shapes
 * below are the generated contract types, so a drift fails typecheck.
 */

const me = { is_admin: true };
vi.mock("../auth/api", () => ({ useMe: () => ({ data: me, isPending: false, isFetching: false, refetch: vi.fn() }) }));

const T = (k: string, v?: Record<string, string>) => uiText(null, k, v);
const NOW = "2026-11-17T10:00:00Z";
const chapter = {
  id: "ch-1",
  exam: "step1",
  kind: "chapter",
  title: "Cardiology basics",
  meta: null,
  sort: 1,
  status: "draft" as const,
  source_id: "naadi-original-v1",
  section_count: 1,
  updated_at: NOW,
  created_at: NOW,
};
const section = {
  id: "ch-1-s1",
  chapter_id: "ch-1",
  sort: 1,
  heading: "Heart chambers",
  taxonomy_ids: [],
  reading_minutes: 3,
  has_draft: false,
  has_body: true,
  draft_updated_at: null,
  live_revision_id: 5,
  updated_at: NOW,
  admin_edited_at: null,
};
const media = {
  id: "fig-1",
  kind: "svg" as const,
  title: "Chambers",
  caption: null,
  source_id: "naadi-original-v1",
  review_status: "approved" as const,
  preview_url: null,
  youtube_id: null,
  channel_id: null,
  embeddable: null,
  duration_s: null,
  width: 10,
  height: 10,
  attribution: null,
  taxonomy_ids: [],
  section_ids: ["ch-1-s1"],
  metadata_fetched_at: null,
};
const question = {
  id: "q-1",
  chapter_id: null,
  exam: "step1",
  block: null,
  stem_html: "<p>Stem</p>",
  options: ["A", "B", "C", "D", "E"].map((label) => ({ label, html: `<p>${label}</p>` })),
  answer: "C",
  answer_html: null,
  explanation_html: "<p>Because.</p>",
  why_not_html: "A — no B — no",
  educational_objective: "Objective",
  difficulty: "easy" as const,
  taxonomy_ids: [],
  tags: [],
  source_id: "naadi-original-v1",
  status: "draft" as const,
  review: {},
  created_at: NOW,
  version: "3",
};
const counts = { sections: 1, videos: 0, questions: 3, cards: 0 };

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  const ok = <T,>(v: T) => vi.fn(async () => v);
  return {
    ...real,
    api: {
      ...real.api,
      overview: ok({ admins_configured: 1, environment: "local", profiles: 3, version: "0.1.0" }),
      queues: ok({
        items: [
          { kind: "generated_media", count: 1, decides_here: true, path: null, filters: [] },
          { kind: "question_review", count: 2, decides_here: false, path: "/admin/review", filters: ["new"] },
        ],
      }),
      queue: ok({
        items: [
          {
            id: "g-1",
            kind: "generated_media",
            created_at: NOW,
            title: "Heart",
            ref: null,
            detail: { media_kind: "image", width: 10, height: 10, ai_generated: true },
          },
        ],
        next_cursor: null,
        total: 1,
      }),
      reports: ok({
        items: [
          {
            id: "r-1",
            ref_type: "section",
            ref_id: "ch-1-s1",
            body: "Typo",
            status: "open" as const,
            created_at: NOW,
            link: { chapter_id: "ch-1", section_id: "ch-1-s1" },
          },
        ],
        counts: { open: 1, triaged: 0, resolved: 0, dismissed: 0 },
      }),
      chapters: ok({ items: [chapter] }),
      chapter: ok({ chapter, sections: [section] }),
      sources: ok({
        items: [
          {
            id: "naadi-original-v1",
            name: "Naadi original",
            license: "CC BY 4.0",
            url: null,
            allow_copy: true,
            allow_modify: true,
            allow_commercial: true,
            allow_ai_ingest: true,
            verified: true,
          },
        ],
      }),
      taxonomy: ok({
        items: [{ id: "cardio", kind: "system", parent_id: null, label: "Cardiovascular", exam: ["step1"] }],
      }),
      media: ok({ items: [media], next_cursor: null }),
      questions: ok({
        items: [
          {
            id: "q-1",
            exam: "step1",
            chapter_id: null,
            status: "draft" as const,
            difficulty: "easy" as const,
            stem_text: "Stem",
            taxonomy_ids: [],
            source_id: null,
            created_at: NOW,
          },
        ],
        next_cursor: null,
      }),
      question: ok(question),
      questionLint: ok({ available: false, findings: [] }),
      questionRevisions: ok({ items: [] }),
      sectionEditor: ok({
        section,
        chapter,
        live: {
          body: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "Live body text" }] }] },
          revision_id: 5,
        },
        draft: null,
        media: [
          { id: "fig-1", kind: "svg", review_status: "approved", source_id: "naadi-original-v1", title: "Chambers" },
        ],
        questions: [],
      }),
      revisions: ok({
        items: [
          {
            id: 5,
            section_id: "ch-1-s1",
            status: "published" as const,
            origin: "seed" as const,
            note: null,
            author_user_id: null,
            parent_revision_id: null,
            restored_from_id: null,
            created_at: NOW,
            updated_at: NOW,
            published_at: NOW,
            is_live: true,
            word_count: 3,
          },
        ],
      }),
      configList: ok({
        items: [
          {
            key: "feature_flags",
            description: "Feature flags",
            source: "db",
            source_file: "app_config.yaml",
            public: true,
            invalid_reason: null,
            updated_at: NOW,
            updated_by: null,
          },
        ],
      }),
      config: ok({
        key: "feature_flags",
        value: { notes: { enabled: true, audience: "everyone" } },
        json_schema: {
          type: "object",
          additionalProperties: { $ref: "#/$defs/Flag" },
          $defs: {
            Flag: {
              type: "object",
              properties: { enabled: { type: "boolean" }, audience: { enum: ["everyone", "admins"] } },
            },
          },
        },
        source: "db",
        invalid_reason: null,
        updated_at: NOW,
        updated_by: null,
        admin_edited_at: NOW,
      }),
      configHistory: ok({ key: "feature_flags", items: [] }),
      coverage: ok({
        exam: "step1",
        goals: { sections: 1, videos: 1, questions: 15, cards: 10 },
        systems: [{ id: "cardio", label: "Cardiovascular" }],
        disciplines: [{ id: "phys", label: "Physiology" }],
        cells: [{ system_id: "cardio", discipline_id: "phys", counts, status: "thin" as const }],
        system_totals: [{ id: "cardio", label: "Cardiovascular", counts, topics: 2, topics_covered: 1 }],
        topics: [
          {
            id: "t1",
            label: "Heart sounds",
            system_id: "cardio",
            counts,
            status: "thin" as const,
            missing: ["videos" as const],
          },
        ],
      }),
      agentRuns: ok({ available: false, items: [] }),
      agentCrons: ok({ available: false, items: [] }),
      agentUsage: ok({
        days: [{ date: "2026-11-17", provider: "openrouter", purpose: "chat", calls: 4, ok: 4, failed: 0 }],
        today: [{ date: "2026-11-17", provider: "openrouter", purpose: "chat", calls: 4, ok: 4, failed: 0 }],
        caps: [{ provider: "openrouter", purpose: "chat" as const, cap: 50 }],
        tavily: { month_credits: 10, monthly_guard: 900, today_credits: 1 },
        health: [{ kind: "openrouter", trigger: "cron", ok: true, latency_ms: 120, created_at: NOW }],
      }),
    },
  };
});

const { adminRoutes } = await import("./routes");

function renderAt(url: string) {
  const root = createRootRoute({ component: Outlet });
  const router = createRouter({
    routeTree: root.addChildren([...adminRoutes(root)]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

const PAGES: [string, string][] = [
  ["/admin", "admin.overview.title"],
  ["/admin/chapters", "admin.chapters.title"],
  ["/admin/chapters/ch-1", "Cardiology basics"],
  ["/admin/sections/ch-1-s1/edit", "Heart chambers"],
  ["/admin/media", "admin.media.title"],
  ["/admin/questions", "admin.questions.title"],
  ["/admin/questions/q-1", "admin.questions.edit_title"],
  ["/admin/questions/new", "admin.questions.new_title"],
  ["/admin/config", "admin.config.title"],
  ["/admin/config/feature_flags", "feature_flags"],
  ["/admin/coverage", "admin.coverage.title"],
  ["/admin/agent-ops", "admin.ops.title"],
  ["/admin/queues", "admin.queues.title"],
  ["/admin/queues/generated_media", "admin.queues.generated_media.title"],
  ["/admin/reports", "admin.reports.title"],
];

describe("admin pages render", () => {
  beforeEach(() => {
    me.is_admin = true;
  });

  for (const [url, title] of PAGES) {
    it(url, async () => {
      const errors = vi.spyOn(console, "error").mockImplementation(() => {});
      renderAt(url);
      const name = title.startsWith("admin.") ? T(title, { id: "q-1" }) : title;
      expect(await screen.findByRole("heading", { level: 1, name }, { timeout: 5000 })).toBeInTheDocument();
      await waitFor(() => expect(document.querySelector("[aria-busy='true']")).toBeNull());
      expect(screen.queryByTestId("admin-permission")).toBeNull();
      expect(errors.mock.calls.filter((c) => !String(c[0]).includes("not wrapped in act"))).toEqual([]);
      errors.mockRestore();
    });
  }

  it("coverage cells carry a word and an icon, never colour alone", async () => {
    renderAt("/admin/coverage");
    const cell = await screen.findByText(T("admin.coverage.thin"), { selector: "[data-cell] span" });
    expect(cell.querySelector("svg")).not.toBeNull();
  });

  it("queues decided elsewhere link to their page instead of approving here", async () => {
    renderAt("/admin/queues");
    expect(await screen.findByRole("link", { name: T("admin.queues.decide_elsewhere") })).toHaveAttribute(
      "href",
      "/admin/review",
    );
  });

  it("the question preview hides the key until Reveal answer", async () => {
    renderAt("/admin/questions/q-1");
    await screen.findByRole("button", { name: T("admin.questions.reveal_answer") });
    expect(screen.queryByText(T("admin.questions.correct_answer"))).toBeNull();
  });

  it("a non-admin gets the permission state and no admin request is made", async () => {
    me.is_admin = false;
    const { api } = await import("./api");
    (api.chapters as ReturnType<typeof vi.fn>).mockClear();
    renderAt("/admin/chapters");
    expect(await screen.findByTestId("admin-permission")).toHaveTextContent(T("admin.permission.title"));
    expect(api.chapters).not.toHaveBeenCalled();
  });
});
