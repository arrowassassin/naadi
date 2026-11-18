/**
 * Naadi's FSRS-6 scheduler for previews and offline reviews.
 *
 * The API is authoritative and schedules with the official Python `fsrs` package (6.3.3). This module
 * reproduces that scheduler's policy exactly, with every piece of memory-model math (initial and next
 * stability/difficulty, short-term stability, forgetting curve) delegated to ts-fsrs 6's FSRS-6 algorithm with
 * the same default weights; the whole-day interval is computed here, as Python does (see `nextInterval`). ts-fsrs's own scheduler differs from the Python one in three
 * policy details (UTC calendar-day vs 24-hour elapsed days, a monotonic Hard < Good < Easy clamp, and Hard
 * on a later learning step), so its scheduler is not used; nor is its 36500-day stability cap, which the
 * Python package does not have. Parity is proven by the shared golden vectors in
 * packages/shared/fixtures/fsrs-golden.json, replayed by both test suites (fsrs.test.ts here).
 */
import { FSRS6_DEFAULT_WEIGHTS, FSRS6_MODEL_BOUNDS, FSRS6Algorithm } from "ts-fsrs/models/fsrs-6";

export const Rating = { Again: 1, Hard: 2, Good: 3, Easy: 4 } as const;
export type Rating = (typeof Rating)[keyof typeof Rating];
export const RATINGS: readonly Rating[] = [1, 2, 3, 4];

/** 0 is a card with no review yet (no `card_reviews` row); 1–3 are the Python package's states. */
export const CardState = { New: 0, Learning: 1, Review: 2, Relearning: 3 } as const;
export type CardState = (typeof CardState)[keyof typeof CardState];

export interface FsrsConfig {
  weights: readonly number[];
  desiredRetention: number;
  learningStepsSeconds: readonly number[];
  relearningStepsSeconds: readonly number[];
  maximumInterval: number;
  enableFuzz: boolean;
}

export const DEFAULT_FSRS_CONFIG: FsrsConfig = Object.freeze({
  weights: FSRS6_DEFAULT_WEIGHTS,
  desiredRetention: 0.9,
  learningStepsSeconds: [60, 600],
  relearningStepsSeconds: [600],
  maximumInterval: 36500,
  enableFuzz: false,
});

/** Scheduling state of one card for one student (a `card_reviews` row, or a new card). */
export interface CardMemory {
  state: CardState;
  step: number | null;
  stability: number | null;
  difficulty: number | null;
  due: Date | null;
  lastReview: Date | null;
  reps: number;
  lapses: number;
}

export interface ReviewOutcome {
  rating: Rating;
  card: CardMemory & { due: Date; lastReview: Date };
  /** Whole days since the previous review (24-hour periods), 0 for a new card. */
  elapsedDays: number;
  /** The new interval in whole days (0 while the next step is minutes away). */
  scheduledDays: number;
  /** Milliseconds from the review to the next due time. */
  intervalMs: number;
}

export const NEW_CARD: CardMemory = Object.freeze({
  state: CardState.New,
  step: null,
  stability: null,
  difficulty: null,
  due: null,
  lastReview: null,
  reps: 0,
  lapses: 0,
});

const MS_PER_DAY = 86_400_000;

/** `arr[i]` where the caller has checked the bounds (the config validator guarantees 21 weights). */
function nth(arr: readonly number[], i: number): number {
  return arr[i] ?? 0;
}
/** The Python package clamps stability from below only; ts-fsrs's default bounds also cap it at 36500 days. */
const BOUNDS = Object.freeze({ ...FSRS6_MODEL_BOUNDS, sMax: Number.POSITIVE_INFINITY });
const FUZZ_RANGES = [
  { start: 2.5, end: 7.0, factor: 0.15 },
  { start: 7.0, end: 20.0, factor: 0.1 },
  { start: 20.0, end: Number.POSITIVE_INFINITY, factor: 0.05 },
] as const;

/** FNV-1a (32-bit) of a string's UTF-16 code units; the seed hash ts-fsrs uses for fuzzing. */
export function fnv1a32(s: string): number {
  let hash = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    hash ^= s.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/** First output of mulberry32 seeded with `seed`, in [0, 1). */
export function mulberry32First(seed: number): number {
  let t = (seed + 1831565813) >>> 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

/** The fuzz seed: the card id and the review count after this review (as ts-fsrs's fuzzing middleware). */
export function fuzzSeed(cardId: string, repsAfter: number): string {
  return `${cardId}${repsAfter}`;
}

/** Python's `round(x)`: to the nearest integer, ties to the even one. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return r - x === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export class Scheduler {
  readonly config: FsrsConfig;
  private readonly algo: FSRS6Algorithm;

  constructor(config: FsrsConfig = DEFAULT_FSRS_CONFIG) {
    if (config.weights.length !== 21) throw new Error("FSRS-6 needs 21 weights");
    this.config = config;
    this.algo = new FSRS6Algorithm([...config.weights], true, BOUNDS);
  }

  /** Days since the last review as the Python package counts them: whole 24-hour periods. */
  static elapsedDays(lastReview: Date | null, at: Date): number {
    if (lastReview === null) return 0;
    return Math.floor((at.getTime() - lastReview.getTime()) / MS_PER_DAY);
  }

  private retrievability(card: CardMemory, at: Date): number {
    if (card.lastReview === null || card.stability === null) return 0;
    const days = Math.max(0, Scheduler.elapsedDays(card.lastReview, at));
    return this.algo.forgetting_curve(days, card.stability);
  }

  /**
   * Whole days, as the Python package computes them: from the unrounded decay factor (ts-fsrs rounds it to 8
   * places, which moved about 2 in 450k intervals by a day) and with Python's round (half to even).
   */
  private nextInterval(stability: number): number {
    const decay = -(this.config.weights[20] as number);
    const factor = 0.9 ** (1 / decay) - 1;
    const days = pyRound((stability / factor) * (this.config.desiredRetention ** (1 / decay) - 1));
    return Math.min(Math.max(days, 1), this.config.maximumInterval);
  }

  private fuzzed(days: number, seed: string): number {
    if (!this.config.enableFuzz || days < 2.5) return days;
    let delta = 1.0;
    for (const r of FUZZ_RANGES) delta += r.factor * Math.max(Math.min(days, r.end) - r.start, 0.0);
    let min = Math.max(2, Math.round(days - delta));
    const max = Math.min(Math.round(days + delta), this.config.maximumInterval);
    min = Math.min(min, max);
    const f = mulberry32First(fnv1a32(seed));
    return Math.min(Math.floor(f * (max - min + 1) + min), this.config.maximumInterval);
  }

  private update(card: CardMemory, rating: Rating, at: Date, shortTerm: boolean): { s: number; d: number } {
    const s0 = card.stability as number;
    const d0 = card.difficulty as number;
    let s: number;
    if (shortTerm) s = this.algo.next_short_term_stability(s0, rating);
    else {
      const r = this.retrievability(card, at);
      if (rating === Rating.Again) {
        const longTerm = this.algo.next_forget_stability(d0, s0, r);
        const w = this.config.weights;
        const shortCap = s0 / Math.exp(nth(w, 17) * nth(w, 18));
        s = Math.max(Math.min(longTerm, shortCap), BOUNDS.sMin);
      } else s = this.algo.next_recall_stability(d0, s0, r, rating);
    }
    return { s, d: this.algo.next_difficulty(d0, rating) };
  }

  /** Review `card` (identified by `cardId`, for the fuzz seed) with `rating` at `at`. Pure. */
  review(cardId: string, card: CardMemory, rating: Rating, at: Date): ReviewOutcome {
    const days = card.lastReview === null ? null : Scheduler.elapsedDays(card.lastReview, at);
    const shortTerm = days !== null && days < 1;
    const learningSteps = this.config.learningStepsSeconds;
    const relearningSteps = this.config.relearningStepsSeconds;
    let state: CardState = card.state === CardState.New ? CardState.Learning : card.state;
    let step: number | null = card.state === CardState.New ? 0 : card.step;
    let stability: number;
    let difficulty: number;
    let intervalSeconds: number | null = null;
    let intervalDays: number | null = null;
    const lapses = card.lapses + (card.state === CardState.Review && rating === Rating.Again ? 1 : 0);

    if (state === CardState.Learning || state === CardState.Relearning) {
      const steps = state === CardState.Learning ? learningSteps : relearningSteps;
      if (card.stability === null || card.difficulty === null) {
        stability = this.algo.init_stability(rating);
        difficulty = Math.min(Math.max(this.algo.init_difficulty(rating), 1), 10);
      } else {
        ({ s: stability, d: difficulty } = this.update(card, rating, at, shortTerm));
      }
      const cur = step ?? 0;
      if (steps.length === 0 || (cur >= steps.length && rating !== Rating.Again)) {
        state = CardState.Review;
        step = null;
        intervalDays = this.nextInterval(stability);
      } else if (rating === Rating.Again) {
        step = 0;
        intervalSeconds = nth(steps, 0);
      } else if (rating === Rating.Hard) {
        step = cur;
        if (cur === 0 && steps.length === 1) intervalSeconds = nth(steps, 0) * 1.5;
        else if (cur === 0) intervalSeconds = (nth(steps, 0) + nth(steps, 1)) / 2;
        else intervalSeconds = nth(steps, cur);
      } else if (rating === Rating.Good && cur + 1 < steps.length) {
        step = cur + 1;
        intervalSeconds = nth(steps, step);
      } else {
        state = CardState.Review;
        step = null;
        intervalDays = this.nextInterval(stability);
      }
    } else {
      ({ s: stability, d: difficulty } = this.update(card, rating, at, shortTerm));
      if (rating === Rating.Again && relearningSteps.length > 0) {
        state = CardState.Relearning;
        step = 0;
        intervalSeconds = nth(relearningSteps, 0);
      } else intervalDays = this.nextInterval(stability);
    }

    const reps = card.reps + 1;
    let intervalMs: number;
    let scheduledDays: number;
    if (intervalDays !== null) {
      const fuzzedDays = state === CardState.Review ? this.fuzzed(intervalDays, fuzzSeed(cardId, reps)) : intervalDays;
      intervalMs = fuzzedDays * MS_PER_DAY;
      scheduledDays = fuzzedDays;
    } else {
      intervalMs = Math.round((intervalSeconds as number) * 1000);
      scheduledDays = Math.floor(intervalMs / MS_PER_DAY);
    }
    return {
      rating,
      elapsedDays: days ?? 0,
      scheduledDays,
      intervalMs,
      card: {
        state,
        step,
        stability,
        difficulty,
        due: new Date(at.getTime() + intervalMs),
        lastReview: at,
        reps,
        lapses,
      },
    };
  }

  /** The outcome of each rating, for the buttons' interval labels. */
  preview(cardId: string, card: CardMemory, at: Date): Record<Rating, ReviewOutcome> {
    return {
      1: this.review(cardId, card, 1, at),
      2: this.review(cardId, card, 2, at),
      3: this.review(cardId, card, 3, at),
      4: this.review(cardId, card, 4, at),
    };
  }
}

/** The scheduler parameters the API sends with the due list (app_config `fsrs`). */
export interface ServerSchedulerConfig {
  weights: number[];
  desired_retention: number;
  learning_steps_s: number[];
  relearning_steps_s: number[];
  maximum_interval: number;
  enable_fuzz: boolean;
}

export function configFromServer(c: ServerSchedulerConfig): FsrsConfig {
  return {
    weights: c.weights,
    desiredRetention: c.desired_retention,
    learningStepsSeconds: c.learning_steps_s,
    relearningStepsSeconds: c.relearning_steps_s,
    maximumInterval: c.maximum_interval,
    enableFuzz: c.enable_fuzz,
  };
}

/** The server's schedule of a card (`CardSchedule`) as scheduler memory. */
export function memoryFromSchedule(s: {
  state: number;
  step?: number | null;
  stability?: number | null;
  difficulty?: number | null;
  due?: string | null;
  last_review?: string | null;
  reps: number;
  lapses: number;
}): CardMemory {
  if (s.state === CardState.New) return NEW_CARD;
  return {
    state: s.state as CardState,
    step: s.step ?? null,
    stability: s.stability ?? null,
    difficulty: s.difficulty ?? null,
    due: s.due ? new Date(s.due) : null,
    lastReview: s.last_review ? new Date(s.last_review) : null,
    reps: s.reps,
    lapses: s.lapses,
  };
}

const dayFormats = new Map<string, Intl.DateTimeFormat>();

/** The calendar day of `t` in IANA zone `zone`, as YYYY-MM-DD (what the API's stats.local_date gives). */
export function localDate(t: Date, zone: string): string {
  let f = dayFormats.get(zone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
    dayFormats.set(zone, f);
  }
  const parts = Object.fromEntries(f.formatToParts(t).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
