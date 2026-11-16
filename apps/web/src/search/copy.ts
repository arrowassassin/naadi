import { useUiCopy } from "../agent/uiCopy";

/**
 * Search screen wording. The editable source is `app_config.ui_copy` (`search.*` keys, config/app_config.yaml,
 * served with GET /api/public-config); these are the built-in fallbacks shown until that loads or when a key
 * is not configured. `copy.test.ts` fails when the two drift apart.
 */
export const SEARCH_COPY_FALLBACK = {
  "search.title": "Search everything",
  "search.tab": "Search",
  "search.placeholder": "Search anything",
  "search.clear": "Clear search",
  "search.filters": "Filter results",
  "search.filter.all": "Everything",
  "search.filter.chapters": "Chapters",
  "search.filter.notes": "My notes",
  "search.filter.handwriting": "Handwriting",
  "search.filter.questions": "Questions",
  "search.filter.uploads": "Uploads",
  "search.filter.pathway": "Pathway",
  "search.filter.web": "Web",
  "search.group.chapters": "Chapters",
  "search.group.notes": "Your notes and handwriting",
  "search.group.questions": "Questions",
  "search.group.uploads": "Your uploads",
  "search.group.pathway": "Pathway",
  "search.group.web": "Web",
  "search.kind.section": "Read",
  "search.kind.upload": "Read",
  "search.kind.question": "Practice",
  "search.kind.note": "Note",
  "search.kind.ink": "Ink",
  "search.kind.pathway": "Pathway",
  "search.kind.web": "Web",
  "search.intro_title": "Search everything you study",
  "search.intro_body": "Chapters, questions, your notes and handwriting, your uploads and the pathway, in one place.",
  "search.loading": "Searching…",
  "search.count": "{count} results",
  "search.count_one": "1 result",
  "search.empty_title": "Nothing found for “{query}”",
  "search.empty_body": "Try fewer or different words, or look on the web.",
  "search.error_title": "Search could not run",
  "search.error_retry": "Try again",
  "search.offline_title": "You are offline",
  "search.offline_body": "Search needs a connection. Chapters you opened before stay readable from the Learn tab.",
  "search.web_ask": "Search the web too",
  "search.web_label": "Web results from {sources}",
  "search.web_loading": "Looking on the web…",
  "search.web_empty": "The web search found nothing for this.",
  "search.web_unavailable": "Web search is not available right now.",
  "search.web_limit": "The web search allowance is used up for now. Local results are still here.",
  "search.web_opens": "opens in a new tab",
  "search.unlinked": "Not available to open yet",
} as const;

export type SearchCopyKey = keyof typeof SEARCH_COPY_FALLBACK;

export function useSearchCopy() {
  const copy = useUiCopy();
  return (key: SearchCopyKey, vars: Record<string, string> = {}): string => {
    const configured = copy(key, vars);
    if (configured) return configured;
    return SEARCH_COPY_FALLBACK[key].replace(/\{(\w+)\}/g, (m, name: string) => vars[name] ?? m);
  };
}
