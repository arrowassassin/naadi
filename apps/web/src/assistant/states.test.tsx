import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeTutor, renderInApp } from "../test/assistant";
import { AssistantHost } from "./AssistantHost";
import { useAssistantAvailable } from "./ask";
import { Composer } from "./Composer";
import { parseCustomEvent, parseInterrupt } from "./protocol";
import { TutorRuntimeProvider } from "./runtime";
import { resetAssistantUiForTests, setAssistantUi } from "./store";
import { ThreadView } from "./Thread";
import type { Tutor } from "./useTutor";

const env = vi.hoisted(() => ({
  features: { assistant: true } as Record<string, boolean>,
  exam: { data: { active: null } as { active: unknown } | undefined, isError: false },
}));

vi.mock("../auth/session", () => ({
  useAuth: () => ({
    status: "signed-in",
    session: { user: { id: "u1" }, access_token: "t" },
    config: { ui_copy: {}, supabase_url: null },
  }),
  accessToken: async () => "t",
  refreshAccessToken: async () => "t2",
  // used by the notes client the canvas "Keep" handler writes through (segment 13)
  onAuthChange: () => () => {},
  AUTH_STORAGE_KEY: "naadi.auth",
}));
vi.mock("../auth/api", () => ({
  useMe: () => ({ data: { features: env.features } }),
  authed: async () => ({ items: [] }),
}));
vi.mock("../exam/api", () => ({ useActiveExam: () => env.exam }));

beforeEach(() => {
  env.features = { assistant: true };
  env.exam = { data: { active: null }, isError: false };
  resetAssistantUiForTests();
});
afterEach(() => resetAssistantUiForTests());

function Host() {
  return (
    <div data-shell="">
      <div data-assistant-dock="" id="dock" />
      <AssistantHost dockSlot={document.getElementById("dock") ?? document.body} onExamRoute={false} />
    </div>
  );
}

describe("assistant in the shell", () => {
  it("feature off: no ask handler, nothing rendered", async () => {
    env.features = {};
    const available = renderHook(() => useAssistantAvailable());
    renderInApp(() => <Host />);
    await act(async () => {});
    expect(available.result.current).toBe(false);
    expect(screen.queryByRole("form", { name: "Ask Naadi" })).toBeNull();
  });

  it("exam active: no handler, no bar, the exam-mode notice instead", async () => {
    env.exam = { data: { active: { block_id: "b1", mode: "exam" } }, isError: false };
    const available = renderHook(() => useAssistantAvailable());
    renderInApp(() => <Host />);
    expect(await screen.findByText("The assistant is off during exam mode.")).toBeInTheDocument();
    expect(available.result.current).toBe(false);
    expect(screen.queryByRole("form", { name: "Ask Naadi" })).toBeNull();
  });

  it("on and no exam: the handler is registered and the Dock bar is shown", async () => {
    const available = renderHook(() => useAssistantAvailable());
    renderInApp(() => <Host />);
    expect(await screen.findByRole("form", { name: "Ask Naadi" })).toBeInTheDocument();
    expect(available.result.current).toBe(true);
    expect(screen.getByRole("button", { name: "Search the web" })).toHaveAttribute("aria-pressed", "false");
  });
});

function Conversation({ tutor }: { tutor: Tutor }) {
  return (
    <TutorRuntimeProvider tutor={tutor}>
      <ThreadView tutor={tutor} empty={<p>empty</p>} onAsk={() => {}} />
      <Composer tutor={tutor} />
    </TutorRuntimeProvider>
  );
}

const ai = (id: string, content: string) => ({ type: "ai", id, content });
const human = (id: string, content: string) => ({ type: "human", id, content });

function blocks(...raw: Record<string, unknown>[]) {
  return raw.map((b, i) => {
    const ev = parseCustomEvent({ type: "block", block: { id: `b${i}`, ...b } });
    if (ev?.type !== "block") throw new Error("bad block");
    return ev.block;
  });
}

describe("conversation states", () => {
  it("offline: the input is disabled and says why", async () => {
    renderInApp(() => <Conversation tutor={fakeTutor({ phase: "offline" })} />);
    const input = await screen.findByRole("textbox", { name: "Ask Naadi" });
    expect(input).toBeDisabled();
    expect(input).toHaveAccessibleDescription("You're offline. The assistant needs a connection.");
    expect(screen.queryByRole("list", { name: "Quick actions" })).toBeNull();
  });

  it("thinking before the first token, then the tool status line", async () => {
    const { unmount } = renderInApp(() => (
      <Conversation
        tutor={fakeTutor({ phase: "thinking", isRunning: true, messages: [human("h1", "why?")] as Tutor["messages"] })}
      />
    ));
    expect(await screen.findByText("Thinking…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop the answer" })).toBeInTheDocument();
    unmount();
    renderInApp(() => (
      <Conversation
        tutor={fakeTutor({
          phase: "streaming",
          isRunning: true,
          tool: { type: "tool", tool: "search_knowledge", status: "running" },
        })}
      />
    ));
    expect(await screen.findByText("Searching the course and your notes…")).toBeInTheDocument();
  });

  it("renders the answer, its blocks and sources under it, and the disclaimer once", async () => {
    const tutor = fakeTutor({
      messages: [
        human("h1", "Explain the gap"),
        ai("a1", "The gap is **Na − (Cl + HCO3)** [1]."),
        human("h2", "And Winters?"),
        ai("a2", "1.5 × HCO3 + 8."),
      ] as Tutor["messages"],
      blocks: blocks(
        { message_id: "a1", type: "notice", data: { kind: "disclaimer" } },
        { message_id: "a1", type: "card_preview", data: { cards: [{ front: "Gap?", back: "Na − (Cl + HCO3)" }] } },
        { message_id: "a2", type: "notice", data: { kind: "disclaimer" } },
      ),
      citations: [
        {
          id: "c1",
          message_id: "a1",
          title: "Anion gap",
          source: "Naadi",
          layer: "course",
          url: null,
          target: null,
          license: null,
          n: 1,
        },
      ],
    });
    renderInApp(() => <Conversation tutor={tutor} />, ["/cards"]);
    const first = await waitFor(() => {
      const el = document.querySelector('[data-message-id="a1"]');
      if (!el) throw new Error("not yet");
      return el as HTMLElement;
    });
    expect(within(first).getByText("Flashcards · 1")).toBeInTheDocument();
    expect(within(first).getByRole("region", { name: "Sources" })).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Source 1: Anion gap" })).toBeInTheDocument();
    expect(screen.getAllByText("For exam study only, not for patient care.")).toHaveLength(1);
    expect(screen.getByText("And Winters?")).toBeInTheDocument();
  });

  it("an interrupt shows the confirm chip and blocks a new send until answered", async () => {
    const user = userEvent.setup();
    const confirm = parseInterrupt({
      type: "confirm",
      tool: "remember",
      preview: { kind: "memory", key: "k", fact: "Exam 3 March" },
      editable: true,
    });
    const tutor = fakeTutor({ phase: "interrupted", confirm });
    setAssistantUi({ draft: "next question" });
    renderInApp(() => <Conversation tutor={tutor} />);
    expect(await screen.findByRole("heading", { name: "Remember this?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(tutor.resume).toHaveBeenCalledWith({ decision: "accept" });
  });

  it("session expired mid-answer: the draft stays, Continue refreshes and resumes", async () => {
    const user = userEvent.setup();
    const tutor = fakeTutor({
      phase: "session_expired",
      error: { status: 401, requestId: null },
      messages: [human("h1", "q"), ai("a1", "partial ans")] as Tutor["messages"],
    });
    setAssistantUi({ draft: "what I typed" });
    renderInApp(() => <Conversation tutor={tutor} />);
    expect(await screen.findByText("Your session ended mid-answer")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Ask Naadi" })).toHaveValue("what I typed");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(tutor.continueAfterRefresh).toHaveBeenCalled();
  });

  it("error with retry; a refused exam-mode request has no retry", async () => {
    const user = userEvent.setup();
    const tutor = fakeTutor({ phase: "error", error: { status: 503, requestId: "req-1" } });
    const { unmount } = renderInApp(() => <Conversation tutor={tutor} />);
    expect(await screen.findByText("Something went wrong on the server. Try again in a moment.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(tutor.retry).toHaveBeenCalled();
    unmount();
    renderInApp(() => <Conversation tutor={fakeTutor({ phase: "error", error: { status: 409, requestId: null } })} />);
    expect(await screen.findByText("The assistant is off during exam mode.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("quick actions send their label with the ask kind; the web toggle flips", async () => {
    const user = userEvent.setup();
    const tutor = fakeTutor();
    renderInApp(() => <Conversation tutor={tutor} />);
    await user.click(await screen.findByRole("button", { name: "What should I study next?" }));
    expect(tutor.send).toHaveBeenCalledWith("What should I study next?", { ask: "ask" });
    const web = screen.getByRole("button", { name: "Search the web" });
    await user.click(web);
    expect(web).toHaveAttribute("aria-pressed", "true");
  });
});
