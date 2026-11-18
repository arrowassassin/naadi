/**
 * Builder data for timed/exam sessions and the seeded mock exams (segment 09): the exam formats from the
 * public config, the mock exams of an exam (from the pool response, so titles and ids come from the seed, with no
 * extra request), and starting a mock (POST /api/blocks/mock).
 */
import { type BlockDetail, createMockBlock, type PoolMock, type PoolResponse, zMockBlockCreate } from "@naadi/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { blocksKey, useUserId } from "./api";

export type MockId = (typeof zMockBlockCreate.shape.chapter_id.options)[number];
const MOCK_IDS: readonly string[] = zMockBlockCreate.shape.chapter_id.options;

export function isMockId(id: string): id is MockId {
  return MOCK_IDS.includes(id);
}

export interface StartableMock {
  id: MockId;
  title: string;
  question_count: number;
}

/** The mock exams the server can start (the contract's ids) among those the pool lists for its exam. */
export function startableMocks(list: readonly PoolMock[]): StartableMock[] {
  return list
    .filter((m) => isMockId(m.id))
    .map((m) => ({ id: m.id as MockId, title: m.title, question_count: m.questions }));
}

/** The selected exam's mock exams. */
export function mocksOf(pool: PoolResponse | undefined): StartableMock[] {
  return startableMocks(pool?.mocks ?? []);
}

/** Which exam lists `mock` (a `?mock=` link without an exam), from the pool's exam choices; null if none. */
export function examOfMock(mock: MockId | undefined, pool: PoolResponse | undefined): string | null {
  if (mock === undefined || !pool) return null;
  return pool.exams.find((e) => (e.mock_ids ?? []).includes(mock))?.id ?? null;
}

/** The exam rules the Builder needs, from the pool response (no extra request). */
export interface BuilderRules {
  break_minutes: number;
  exams: Record<string, { blocks: number }>;
  modes: Record<string, { seconds_per_question: number | null }>;
}

export function rulesOf(pool: PoolResponse | undefined): BuilderRules | undefined {
  if (!pool?.timing) return undefined;
  return {
    break_minutes: pool.timing.break_minutes,
    exams: Object.fromEntries(pool.exams.map((e) => [e.id, { blocks: e.blocks ?? 1 }])),
    modes: Object.fromEntries(
      Object.entries(pool.timing.seconds_per_question).map(([k, v]) => [k, { seconds_per_question: v ?? null }]),
    ),
  };
}

/** How many blocks a timed/exam session of `exam` may have here: at most four chips, never above the rule. */
export function maxSections(rules: BuilderRules | undefined, exam: string, cap = 4): number {
  const blocks = rules?.exams[exam]?.blocks;
  return Math.max(1, Math.min(cap, typeof blocks === "number" ? blocks : cap));
}

/** The most blocks of `count` questions the pool can fill (at least one; the server has the last word). */
export function sectionsThatFit(total: number, count: number, most: number): number {
  if (count <= 0) return 1;
  return Math.max(1, Math.min(most, Math.floor(total / count)));
}

export function useCreateMockBlock() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapterId: MockId) =>
      authed<BlockDetail>((headers) => createMockBlock({ headers, body: { chapter_id: chapterId } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: blocksKey(userId) }),
  });
}
