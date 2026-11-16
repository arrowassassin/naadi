import { type AnyRoute, createRoute, Navigate, Outlet } from "@tanstack/react-router";
import { useChapter, usePositions } from "./api";
import { CoursesPage, type CoursesSearch } from "./CoursesPage";
import { ReaderPage } from "./ReaderPage";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const EXAM = /^[a-z0-9]{2,16}$/;
const TAXONOMY = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function parseCoursesSearch(s: Record<string, unknown>): CoursesSearch {
  return {
    exam: typeof s.exam === "string" && EXAM.test(s.exam) ? s.exam : undefined,
    system: typeof s.system === "string" && TAXONOMY.test(s.system) ? s.system : undefined,
  };
}

/** `/learn/$chapterId`: open the chapter where the student left it (any device), else at its first section. */
function ChapterResume({ chapterId }: { chapterId: string }) {
  const chapter = useChapter(chapterId);
  const positions = usePositions(chapterId);
  if (chapter.data && !positions.isPending) {
    const sections = chapter.data.data.sections;
    const ids = new Set(sections.map((s) => s.id));
    const last = positions.data?.data.last;
    const target = last && ids.has(last.section_id) ? last.section_id : sections[0]?.id;
    if (target) {
      return <Navigate to="/learn/$chapterId/$sectionId" params={{ chapterId, sectionId: target }} replace />;
    }
  }
  // Loading, failed, or a chapter without sections: the reader renders the matching state.
  return <ReaderPage chapterId={chapterId} sectionId="" />;
}

/** One route tree under the protected app layout: `router.tsx` adds `learnRoutes(appRoute)`. */
export function learnRoutes(parent: AnyRoute) {
  const learn = createRoute({ getParentRoute: () => parent, path: "/learn", component: Outlet });
  const courses = createRoute({
    getParentRoute: () => learn,
    path: "/",
    validateSearch: parseCoursesSearch,
    component: function CoursesRoute() {
      return <CoursesPage search={courses.useSearch()} />;
    },
  });
  const chapter = createRoute({
    getParentRoute: () => learn,
    path: "$chapterId",
    params: {
      parse: (p: Record<string, string>) => {
        if (!ID.test(p.chapterId ?? "")) throw new Error("invalid chapter id");
        return { chapterId: p.chapterId as string };
      },
      stringify: (p: { chapterId: string }) => p,
    },
    component: Outlet,
  });
  const resume = createRoute({
    getParentRoute: () => chapter,
    path: "/",
    component: function ResumeRoute() {
      const { chapterId } = resume.useParams();
      return <ChapterResume chapterId={chapterId} />;
    },
  });
  const section = createRoute({
    getParentRoute: () => chapter,
    path: "$sectionId",
    component: function SectionRoute() {
      const { chapterId, sectionId } = section.useParams();
      return <ReaderPage chapterId={chapterId} sectionId={sectionId} />;
    },
  });
  return learn.addChildren([courses, chapter.addChildren([resume, section])]);
}
