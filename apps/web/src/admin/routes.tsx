import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Outlet } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import type * as Pages from "./pages";

/*
 * The admin console (segment 19) under the signed-in app layout. This file is the only admin module in the
 * student bundle: it holds route definitions and nothing else. Every page and the layout come from ./pages,
 * imported lazily, so the editor, TipTap commands and admin code are one chunk that loads only when an admin
 * opens /admin. Non-admins get the permission state from the layout (and 403 from the API), never data.
 */

type PageName = keyof typeof Pages;
const load = () => import("./pages");
function page<N extends PageName>(name: N) {
  return lazy(() => load().then((m) => ({ default: m[name] })));
}

const AdminLayout = page("AdminLayout");
const Overview = page("OverviewPage");
const Chapters = page("ChaptersPage");
const Chapter = page("ChapterPage");
const SectionEdit = page("SectionEditorPage");
const Media = page("MediaPage");
const Questions = page("QuestionsPage");
const Question = page("QuestionPage");
const ConfigList = page("ConfigListPage");
const ConfigKey = page("ConfigKeyPage");
const Coverage = page("CoveragePage");
const AgentOps = page("AgentOpsPage");
const Queues = page("QueuesPage");
const Queue = page("QueuePage");
const Reports = page("ReportsPage");

function Fallback() {
  return <Skeleton className="mt-6 h-40 w-full rounded-tile" />;
}

function Layout() {
  return (
    <Suspense fallback={<Fallback />}>
      <AdminLayout>
        <Suspense fallback={<Fallback />}>
          <Outlet />
        </Suspense>
      </AdminLayout>
    </Suspense>
  );
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const KEY = /^[a-z][a-z0-9_.]{0,79}$/;
const KIND = /^[a-z][a-z0-9_]{1,47}$/;
const str = (v: unknown, re: RegExp): string | undefined => (typeof v === "string" && re.test(v) ? v : undefined);
const TOKEN = /^[a-z0-9_]{1,32}$/;
const TEXT = /^.{1,200}$/;

function idParam<K extends string>(name: K, re: RegExp = ID) {
  return {
    parse: (p: Record<string, string>) => {
      const v = p[name] ?? "";
      if (!re.test(v)) throw new Error(`invalid ${name}`);
      return { [name]: v } as Record<K, string>;
    },
    stringify: (p: Record<K, string>) => p,
  };
}

export interface ChaptersSearch {
  exam?: string;
  status?: string;
  q?: string;
}
export interface MediaSearch {
  kind?: string;
  review?: string;
  q?: string;
}
export interface QuestionsSearch {
  exam?: string;
  status?: string;
  q?: string;
  chapter?: string;
}
export interface ExamSearch {
  exam?: string;
}
export interface StatusSearch {
  status?: string;
}

/** `/admin/*`. router.tsx adds `...adminRoutes(appRoute)`. */
export function adminRoutes<P extends AnyRoute>(parent: P) {
  const layout = createRoute({ getParentRoute: () => parent, id: "admin-console", component: Layout });
  const overview = createRoute({ getParentRoute: () => layout, path: "/admin", component: () => <Overview /> });
  const chapters = createRoute({
    getParentRoute: () => layout,
    path: "/admin/chapters",
    validateSearch: (s: Record<string, unknown>): ChaptersSearch => ({
      exam: str(s.exam, TOKEN),
      status: str(s.status, TOKEN),
      q: str(s.q, TEXT),
    }),
    component: function ChaptersRoute() {
      return <Chapters search={chapters.useSearch()} />;
    },
  });
  const chapter = createRoute({
    getParentRoute: () => layout,
    path: "/admin/chapters/$chapterId",
    params: idParam("chapterId"),
    component: function ChapterRoute() {
      return <Chapter chapterId={chapter.useParams().chapterId} />;
    },
  });
  const sectionEdit = createRoute({
    getParentRoute: () => layout,
    path: "/admin/sections/$sectionId/edit",
    params: idParam("sectionId"),
    component: function SectionEditRoute() {
      const { sectionId } = sectionEdit.useParams();
      return <SectionEdit key={sectionId} sectionId={sectionId} />;
    },
  });
  const media = createRoute({
    getParentRoute: () => layout,
    path: "/admin/media",
    validateSearch: (s: Record<string, unknown>): MediaSearch => ({
      kind: str(s.kind, TOKEN),
      review: str(s.review, TOKEN),
      q: str(s.q, TEXT),
    }),
    component: function MediaRoute() {
      return <Media search={media.useSearch()} />;
    },
  });
  const questions = createRoute({
    getParentRoute: () => layout,
    path: "/admin/questions",
    validateSearch: (s: Record<string, unknown>): QuestionsSearch => ({
      exam: str(s.exam, TOKEN),
      status: str(s.status, TOKEN),
      q: str(s.q, TEXT),
      chapter: str(s.chapter, ID),
    }),
    component: function QuestionsRoute() {
      return <Questions search={questions.useSearch()} />;
    },
  });
  const question = createRoute({
    getParentRoute: () => layout,
    path: "/admin/questions/$questionId",
    params: idParam("questionId"),
    component: function QuestionRoute() {
      const { questionId } = question.useParams();
      return <Question key={questionId} questionId={questionId} />;
    },
  });
  const configList = createRoute({
    getParentRoute: () => layout,
    path: "/admin/config",
    component: () => <ConfigList />,
  });
  const configKey = createRoute({
    getParentRoute: () => layout,
    path: "/admin/config/$configKey",
    params: idParam("configKey", KEY),
    component: function ConfigKeyRoute() {
      const { configKey: key } = configKey.useParams();
      return <ConfigKey key={key} configKey={key} />;
    },
  });
  const coverage = createRoute({
    getParentRoute: () => layout,
    path: "/admin/coverage",
    validateSearch: (s: Record<string, unknown>): ExamSearch => ({ exam: str(s.exam, TOKEN) }),
    component: function CoverageRoute() {
      return <Coverage search={coverage.useSearch()} />;
    },
  });
  const agentOps = createRoute({
    getParentRoute: () => layout,
    path: "/admin/agent-ops",
    component: () => <AgentOps />,
  });
  const queues = createRoute({ getParentRoute: () => layout, path: "/admin/queues", component: () => <Queues /> });
  const queue = createRoute({
    getParentRoute: () => layout,
    path: "/admin/queues/$kind",
    params: idParam("kind", KIND),
    component: function QueueRoute() {
      const { kind } = queue.useParams();
      return <Queue key={kind} kind={kind} />;
    },
  });
  const reports = createRoute({
    getParentRoute: () => layout,
    path: "/admin/reports",
    validateSearch: (s: Record<string, unknown>): StatusSearch => ({ status: str(s.status, TOKEN) }),
    component: function ReportsRoute() {
      return <Reports search={reports.useSearch()} />;
    },
  });
  return [
    layout.addChildren([
      overview,
      chapters,
      chapter,
      sectionEdit,
      media,
      questions,
      question,
      configList,
      configKey,
      coverage,
      agentOps,
      queues,
      queue,
      reports,
    ]),
  ];
}
