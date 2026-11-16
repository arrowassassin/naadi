import {
  BookOpen,
  Calendar,
  ListChecks,
  type LucideIcon,
  NotebookPen,
  Route,
  Search,
  Sun,
  UserRound,
} from "lucide-react";

/** Icon names the server's nav config may use. An unknown name renders no icon (the label still shows). */
export const NAV_ICONS: Record<string, LucideIcon> = {
  sun: Sun,
  "book-open": BookOpen,
  "list-checks": ListChecks,
  calendar: Calendar,
  "notebook-pen": NotebookPen,
  route: Route,
  "user-round": UserRound,
  search: Search,
};

export function NavIcon({ name, size = 18 }: { name: string; size?: number }) {
  const Icon = NAV_ICONS[name];
  return Icon ? <Icon aria-hidden="true" size={size} strokeWidth={2} /> : null;
}
