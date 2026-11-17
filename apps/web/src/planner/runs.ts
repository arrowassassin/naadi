/**
 * Applying a rebalance goes through the `planner` graph on the Aegra runtime, always with the student's confirm:
 * the graph stores the proposal, stops at an interrupt and changes the plan only when resumed with `accept`.
 *
 * - A proposal made by a run still waiting (the student's own Rebalance, or the nightly coach's, after a first
 *   look) is resumed on its thread.
 * - A proposal without a live run (made by the nightly coach) is first brought to its confirm by a new run, which
 *   re-checks the plan. If that proposal is still exactly what the student looked at, their Accept/Decline is the
 *   answer to the interrupt; if the plan moved on, the student is shown the new proposal first.
 */
import type { RebalanceOut } from "@naadi/shared";

export interface Interrupt {
  rebalance_id: string;
  fingerprint: string;
  moves: number;
  drops: number;
  warnings: number;
}

export interface Outcome {
  outcome: "applied" | "declined" | "stale" | "gone" | "nothing_to_do" | "flag_off";
  detail?: { moved?: number; dropped?: number };
}

/** The slice of the LangGraph SDK client the planner uses (a fake in tests). */
export interface Runtime {
  threads: {
    create(): Promise<{ thread_id: string }>;
    getState(threadId: string): Promise<{
      values: Partial<Outcome>;
      tasks: { interrupts?: { value: unknown }[] }[];
    }>;
  };
  runs: {
    create(
      threadId: string,
      assistantId: string,
      payload: { input: Record<string, unknown> },
    ): Promise<{ run_id: string }>;
    join(threadId: string, runId: string): Promise<unknown>;
    wait(
      threadId: string,
      assistantId: string,
      payload: { command: { resume: { decision: "accept" | "decline" } } },
    ): Promise<unknown>;
  };
}

export type Decision = "accept" | "decline";

export type DecideResult =
  | { kind: "done"; outcome: Outcome }
  | { kind: "changed" } // the plan moved on: a different proposal is now waiting
  | { kind: "nothing" };

function interruptOf(state: Awaited<ReturnType<Runtime["threads"]["getState"]>>): Interrupt | null {
  for (const task of state.tasks) {
    for (const it of task.interrupts ?? []) return it.value as Interrupt;
  }
  return null;
}

function outcomeOf(raw: unknown): Outcome {
  const o = (raw ?? {}) as Partial<Outcome>;
  return { outcome: o.outcome ?? "gone", ...(o.detail ? { detail: o.detail } : {}) };
}

/** Waits for a run started elsewhere (POST /api/plan/rebalance) to reach its confirm; null when it ended without one. */
export async function awaitConfirm(rt: Runtime, threadId: string, runId: string): Promise<Interrupt | null> {
  await rt.runs.join(threadId, runId);
  return interruptOf(await rt.threads.getState(threadId));
}

/** The student's decision on the proposal shown. */
export async function decide(rt: Runtime, row: RebalanceOut, decision: Decision): Promise<DecideResult> {
  let threadId = row.thread_id;
  let waiting: Interrupt | null = null;
  if (threadId) {
    try {
      waiting = interruptOf(await rt.threads.getState(threadId));
    } catch {
      waiting = null; // the thread is gone (retention) or not readable: start a new run below
    }
  }
  if (!waiting || waiting.rebalance_id !== row.id) {
    threadId = (await rt.threads.create()).thread_id;
    const run = await rt.runs.create(threadId, "planner", { input: { trigger: "student" } });
    await rt.runs.join(threadId, run.run_id);
    waiting = interruptOf(await rt.threads.getState(threadId));
    if (!waiting) return { kind: "nothing" };
  }
  if (waiting.fingerprint !== row.proposal.fingerprint) return { kind: "changed" };
  const done = await rt.runs.wait(threadId as string, "planner", { command: { resume: { decision } } });
  return { kind: "done", outcome: outcomeOf(done) };
}
