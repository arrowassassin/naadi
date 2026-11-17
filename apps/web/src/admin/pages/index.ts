/** Every admin page: one module, so the whole console is one lazy chunk (see ../routes.tsx). */
// The console's shipped ui_copy defaults load with it (segment 22: not in the first load).
import "@naadi/shared/ui-copy-admin";

export { AdminLayout } from "./AdminLayout";
export { AgentOpsPage } from "./AgentOpsPage";
export { ChapterPage } from "./ChapterPage";
export { ChaptersPage } from "./ChaptersPage";
export { ConfigKeyPage, ConfigListPage } from "./ConfigPages";
export { CoveragePage } from "./CoveragePage";
export { MediaPage } from "./MediaPage";
export { OverviewPage } from "./OverviewPage";
export { QuestionPage } from "./QuestionPage";
export { QuestionsPage } from "./QuestionsPage";
export { QueuePage, QueuesPage } from "./QueuesPages";
export { ReportsPage } from "./ReportsPage";
export { SectionEditorPage } from "./SectionEditorPage";
