import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, runUsers, toLink } from "../auth-users";
import { type ConsoleGuard, settle, trackApi } from "../fixtures";

/** Helpers shared by the segment 20 specs (video.spec.ts, anki.spec.ts): a throwaway student per test. */
export const API_DIR = path.resolve(import.meta.dirname, "../../../api");

export function studentEmail(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

export function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text().startsWith("Refused to apply a stylesheet"))) {
      errors.push(m.text());
    }
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  const guard: ConsoleGuard = {
    allowHttpError: () => {},
    duringTool: async (fn) => {
      tool++;
      try {
        return await fn();
      } finally {
        await page.waitForTimeout(300);
        tool--;
      }
    },
  };
  return { errors, guard };
}

export async function freshStudent(browser: Browser, baseURL: string, info: TestInfo, kind: string) {
  const email = studentEmail(info, kind);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (to: string, before?: (page: Page) => Promise<void>): Promise<Page> => {
    const context = await browser.newContext({ ...info.project.use, baseURL, acceptDownloads: true });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page);
    await before?.(page);
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, to));
    await page.waitForURL((u) => u.pathname === to, { timeout: 30_000 });
    await settle(page);
    return page;
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { open, done, email };
}

/** A private chapter with a YouTube video for this user (apps/api/tests/courses/e2e_rich.py; local DB only). */
export function richChapter(action: "create" | "delete", email: string): { chapter_id: string; section_id?: string } {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.courses.e2e_rich", action];
  const out = execFileSync("uv", [...args, "--email", email, "--db", db], { encoding: "utf8", timeout: 60_000 });
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as { chapter_id: string; section_id?: string };
}

/**
 * Write a tiny `.apkg` (made up in code by apps/api/tests/anki/builders.py: nothing is a real deck) and return its
 * path. `cards` are [front, back] pairs, all in one note type, in `deck`.
 */
export function writeApkg(file: string, deck: string, cards: [string, string][]): string {
  const code = [
    "import json, sys",
    "from pathlib import Path",
    "from tests.anki.builders import BASIC, NoteSpec, write_apkg",
    "a = json.loads(sys.argv[2])",
    "notes = [NoteSpec(BASIC.mid, [f, b], f'e2e-{i}', 'e2e', deck=2) for i, (f, b) in enumerate(a['cards'])]",
    "write_apkg(Path(sys.argv[1]), 'anki21b', notes, decks={1: 'Default', 2: a['deck']})",
  ].join("\n");
  execFileSync(
    "uv",
    ["--directory", API_DIR, "run", "--frozen", "python", "-c", code, file, JSON.stringify({ deck, cards })],
    { timeout: 60_000, stdio: ["ignore", "pipe", "pipe"] },
  );
  return file;
}
