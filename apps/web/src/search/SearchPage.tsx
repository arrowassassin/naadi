import type { SearchHit, Segment, WebResult } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate, useRouter } from "@tanstack/react-router";
import { Search as SearchIcon, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { type Scope, useLocalSearch, useWebSearch } from "./api";
import { type SearchCopy, type SearchCopyKey, useSearchCopy } from "./copy";
import { SEARCH_FOCUS_EVENT } from "./shortcut";
import { resolveTarget } from "./targets";

export interface SearchRouteSearch {
  q?: string;
  scope?: Scope;
  /** 1 = the student asked for the web explicitly. */
  web?: 1;
}

const SCOPES: Scope[] = ["all", "chapters", "notes", "handwriting", "questions", "uploads", "pathway", "web"];
const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;
const AUTO_WEB_MIN_CHARS = 4;

type Copy = SearchCopy;
type LocalGroup = "chapters" | "notes" | "questions" | "uploads" | "pathway";
const GROUP_ORDER: LocalGroup[] = ["chapters", "notes", "questions", "uploads", "pathway"];

/** Notes and handwriting share one group, as on the Search board. */
function groupOf(hit: SearchHit): LocalGroup {
  return hit.group === "handwriting" ? "notes" : hit.group;
}

function Snippet({ segments }: { segments: Segment[] }) {
  let offset = 0;
  const keyed = segments.map((s) => {
    const key = `${offset}`;
    offset += s.text.length;
    return { ...s, key };
  });
  return (
    <span className="line-clamp-3 text-text-2 text-ui-sm">
      {keyed.map((s) =>
        s.hit ? (
          <mark
            key={s.key}
            className="bg-transparent font-semibold text-text underline decoration-text-3 underline-offset-2"
          >
            {s.text}
          </mark>
        ) : (
          <span key={s.key}>{s.text}</span>
        ),
      )}
    </span>
  );
}

const ROW =
  "grid min-h-[72px] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-line-soft border-t py-3 text-text no-underline hover:text-text";

/** The line under a title, worded from the structured fields the server sends. */
function metaLine(hit: SearchHit, copy: Copy): string {
  switch (hit.type) {
    case "question":
      return copy("search.meta.question", { context: hit.context ?? "" });
    case "note":
      return copy(
        `search.meta.note_${["section", "question", "video", "pdf"].includes(hit.anchor ?? "") ? hit.anchor : "free"}` as SearchCopyKey,
      );
    case "ink":
      return copy("search.meta.ink");
    case "pathway":
      return hit.context ?? copy("search.meta.guide");
    default:
      return hit.context ?? "";
  }
}

function HitRow({ hit, copy }: { hit: SearchHit; copy: Copy }) {
  const router = useRouter();
  const dest = resolveTarget(hit.target, router.routesByPath as Record<string, unknown>);
  const ink = hit.type === "ink";
  const kind = copy(`search.kind.${hit.type === "kb_doc" ? "upload" : hit.type}` as SearchCopyKey);
  const body = (
    <>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span
          className={cx(
            "[overflow-wrap:anywhere]",
            ink ? "font-ink text-[1.75rem] leading-[1.1]" : "font-semibold text-[1.125rem] leading-snug",
          )}
        >
          {hit.title}
        </span>
        <span className="text-text-3 text-ui-sm">{metaLine(hit, copy)}</span>
        {hit.snippet.length > 0 ? <Snippet segments={hit.snippet} /> : null}
      </span>
      <Chip tone="meta" srPrefix={copy("search.type_label")}>
        {kind}
      </Chip>
    </>
  );
  return (
    <li>
      {dest ? (
        <Link
          to={dest.to}
          params={dest.params}
          data-result=""
          className={cx(ROW, "rounded-inner focus-visible:outline-2 focus-visible:outline-offset-2")}
        >
          {body}
        </Link>
      ) : (
        <div className={ROW} data-unlinked="" aria-disabled="true" aria-describedby={`${hit.id}-soon`}>
          {body}
          <span
            id={`${hit.id}-soon`}
            className="col-span-2 font-mono text-label-sm text-text-3 uppercase tracking-[0.1em]"
          >
            {copy("search.soon")}
          </span>
        </div>
      )}
    </li>
  );
}

function safeHttpUrl(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

function WebRow({ r, copy }: { r: WebResult; copy: Copy }) {
  const href = safeHttpUrl(r.url);
  const inner = (
    <>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="font-semibold text-[1.125rem] leading-snug [overflow-wrap:anywhere]">{r.title}</span>
        <span className="text-text-3 text-ui-sm">
          {r.source_label}
          {r.published ? ` · ${r.published}` : ""}
        </span>
        {r.snippet ? <span className="line-clamp-3 text-text-2 text-ui-sm">{r.snippet}</span> : null}
      </span>
      <Chip tone="meta" srPrefix={copy("search.type_label")}>
        {copy("search.kind.web")}
      </Chip>
      {href ? <span className="sr-only">{copy("search.web_opens")}</span> : null}
    </>
  );
  return (
    <li>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          data-result=""
          className={cx(ROW, "rounded-inner focus-visible:outline-2 focus-visible:outline-offset-2")}
        >
          {inner}
        </a>
      ) : (
        <div className={ROW}>{inner}</div>
      )}
    </li>
  );
}

function GroupHeading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="m-0 mb-2 font-mono font-medium text-label text-text-3 uppercase tracking-[0.14em]">
      {children}
    </h2>
  );
}

function RowSkeletons() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-[72px] rounded-inner" />
      ))}
    </div>
  );
}

/** Our own wording (ui_copy), never the server's English; the request id still helps a report. */
function errorText(err: unknown, copy: Copy): { message: string; requestId: string | null } {
  if (err instanceof ApiError) {
    return { message: copy(err.status === 429 ? "search.error_busy" : "search.error_body"), requestId: err.requestId };
  }
  return { message: copy("search.error_body"), requestId: null };
}

/** Arrow keys move between the field and the result links. */
function useResultKeys(
  container: React.RefObject<HTMLElement | null>,
  input: React.RefObject<HTMLInputElement | null>,
): void {
  useEffect(() => {
    const root = container.current;
    if (!root) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const items = [input.current, ...root.querySelectorAll<HTMLElement>("[data-result]")].filter(
        (x): x is HTMLElement => x !== null,
      );
      const at = items.indexOf(document.activeElement as HTMLElement);
      if (at === -1) return;
      const next = e.key === "ArrowDown" ? Math.min(at + 1, items.length - 1) : Math.max(at - 1, 0);
      if (next !== at) {
        e.preventDefault();
        items[next]?.focus();
      }
    };
    root.addEventListener("keydown", onKey);
    return () => root.removeEventListener("keydown", onKey);
  }, [container, input]);
}

export function SearchPage({ search }: { search: SearchRouteSearch }) {
  const copy = useSearchCopy();
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const scope: Scope = search.scope && SCOPES.includes(search.scope) ? search.scope : "all";
  const committed = (search.q ?? "").trim();
  const [text, setText] = useState(search.q ?? "");

  // The URL is the state (back button, shared links); typing commits to it after a short pause.
  const commit = (q: string, next: Partial<SearchRouteSearch> = {}) =>
    void navigate({
      to: "/search",
      search: { q: q.trim() || undefined, scope: next.scope ?? scope, web: next.web ?? search.web },
      replace: true,
    });
  // biome-ignore lint/correctness/useExhaustiveDependencies: only a change of the typed text schedules a commit
  useEffect(() => {
    if (text.trim() === committed) return;
    const id = window.setTimeout(() => commit(text), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  const title = copy("search.title");
  useEffect(() => {
    document.title = `${title} · Naadi`;
  }, [title]);

  // Focus the field once on arrival, and again whenever Ctrl/Cmd+K is pressed on this screen.
  useEffect(() => {
    inputRef.current?.focus();
    const focus = () => {
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener(SEARCH_FOCUS_EVENT, focus);
    return () => window.removeEventListener(SEARCH_FOCUS_EVENT, focus);
  }, []);

  const q = committed.length >= MIN_CHARS ? committed : "";
  const webOnly = scope === "web";
  const local = useLocalSearch(q, webOnly ? "all" : scope, online && !webOnly);
  const askedWeb = webOnly || search.web === 1;
  // The web is fetched by itself only for a settled query: the typing has stopped (the URL holds what the
  // field holds), the local answer is in, and the query is long enough to mean something.
  const settled = text.trim() === committed && !local.isFetching && committed.length >= AUTO_WEB_MIN_CHARS;
  const autoWeb = !webOnly && scope === "all" && settled && local.data?.suggest_web === true;
  const webEnabled = online && q.length > 0 && (askedWeb || autoWeb);
  const web = useWebSearch(q, askedWeb, webEnabled);

  const groups = useMemo(() => {
    const out = new Map<LocalGroup, SearchHit[]>();
    for (const h of local.data?.hits ?? []) {
      const g = groupOf(h);
      out.set(g, [...(out.get(g) ?? []), h]);
    }
    return GROUP_ORDER.filter((g) => out.has(g)).map((g) => ({ id: g, hits: out.get(g) as SearchHit[] }));
  }, [local.data]);

  useResultKeys(rootRef, inputRef);
  const hitCount = local.data?.hits.length ?? 0;
  const webResults = web.data?.results ?? [];
  const showWebAsk = online && !webOnly && !askedWeb && !autoWeb && q !== "";
  const localSettled = !webOnly && local.data !== undefined;
  const nothingLocal = localSettled && hitCount === 0;
  const status = !q
    ? ""
    : local.isFetching || web.isFetching
      ? copy("search.loading")
      : webOnly
        ? ""
        : hitCount === 1
          ? copy("search.count_one")
          : copy("search.count", { count: String(hitCount) });

  const chip = (s: Scope) => {
    const on = s === scope;
    return (
      <button
        key={s}
        type="button"
        aria-pressed={on}
        onClick={() => commit(text, { scope: s, web: undefined })}
        className={cx(
          "min-h-11 rounded-pill px-4 text-[0.875rem]",
          on
            ? "border-0 bg-paper font-semibold text-ground"
            : "border border-line-control bg-transparent text-text hover:bg-surface-2",
        )}
      >
        {copy(`search.filter.${s}` as SearchCopyKey)}
      </button>
    );
  };

  return (
    <div ref={rootRef} className="flex flex-col gap-7 pt-6 pb-10 tablet:pt-10">
      <h1 className="sr-only">{copy("search.title")}</h1>
      <search>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            commit(text);
          }}
          className="flex items-center gap-4 border-text border-b-2 pb-4"
        >
          <SearchIcon aria-hidden="true" size={36} strokeWidth={2} className="shrink-0 text-lime" />
          <input
            ref={inputRef}
            type="search"
            name="q"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && text) {
                e.preventDefault();
                setText("");
                commit("");
              }
            }}
            aria-label={copy("search.title")}
            aria-describedby={hintId}
            placeholder={copy("search.placeholder")}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="search"
            className="min-w-0 flex-1 appearance-none border-0 bg-transparent font-display font-extrabold text-[clamp(2.25rem,1rem+6vw,5.5rem)] text-text leading-[1.05] tracking-[-0.04em] outline-none placeholder:font-bold placeholder:text-[clamp(1.75rem,1rem+4vw,3.5rem)] placeholder:text-text-muted [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-cancel-button]:appearance-none"
          />
          {text ? (
            <button
              type="button"
              aria-label={copy("search.clear")}
              onClick={() => {
                setText("");
                commit("");
                inputRef.current?.focus();
              }}
              className="flex size-11 shrink-0 items-center justify-center rounded-full border-0 bg-control text-text hover:bg-line-strong"
            >
              <X aria-hidden="true" size={18} />
            </button>
          ) : null}
        </form>
      </search>
      <p id={hintId} role="status" aria-live="polite" className="sr-only">
        {status}
      </p>

      <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
        <legend className="sr-only">{copy("search.filters")}</legend>
        {SCOPES.map(chip)}
      </fieldset>

      {!online ? (
        <ErrorState title={copy("search.offline_title")} message={copy("search.offline_body")} live={false} />
      ) : !q ? (
        <section aria-label={copy("search.intro_title")} className="flex max-w-prose flex-col gap-2">
          <p className="m-0 font-display font-bold text-headline">{copy("search.intro_title")}</p>
          <p className="m-0 text-text-2">{copy("search.intro_body")}</p>
        </section>
      ) : (
        <div aria-busy={local.isFetching || web.isFetching || undefined} className="flex flex-col gap-8">
          {!webOnly && local.isPending && !local.data ? <RowSkeletons /> : null}
          {!webOnly && local.isError && !local.data ? (
            <ErrorState
              title={copy("search.error_title")}
              {...errorText(local.error, copy)}
              onRetry={() => void local.refetch()}
              retryLabel={copy("search.error_retry")}
              retrying={local.isFetching}
            />
          ) : null}

          {groups.map((g) => (
            <section key={g.id} aria-labelledby={`grp-${g.id}`} className="flex flex-col">
              <GroupHeading id={`grp-${g.id}`}>{copy(`search.group.${g.id}` as SearchCopyKey)}</GroupHeading>
              <ul className="m-0 flex list-none flex-col p-0">
                {g.hits.map((h) => (
                  <HitRow key={h.id} hit={h} copy={copy} />
                ))}
              </ul>
            </section>
          ))}

          {nothingLocal ? (
            <section aria-label={copy("search.empty_title", { query: q })} className="flex flex-col gap-2">
              <p className="m-0 font-display font-bold text-headline">{copy("search.empty_title", { query: q })}</p>
              <p className="m-0 text-text-2">{copy("search.empty_body")}</p>
            </section>
          ) : null}

          {webEnabled ? (
            <section aria-labelledby="grp-web" className="flex flex-col">
              <GroupHeading id="grp-web">{copy("search.group.web")}</GroupHeading>
              {web.isPending ? (
                <p role="status" className="m-0 text-text-2">
                  {copy("search.web_loading")}
                </p>
              ) : web.isError ? (
                <ErrorState
                  title={copy("search.error_title")}
                  {...errorText(web.error, copy)}
                  onRetry={() => void web.refetch()}
                  retryLabel={copy("search.error_retry")}
                  retrying={web.isFetching}
                />
              ) : web.data.status === "ok" ? (
                <>
                  <p className="m-0 mb-1 text-text-3 text-ui-sm">
                    {copy("search.web_label", { sources: web.data.labels.join(", ") })}
                  </p>
                  <ul className="m-0 flex list-none flex-col p-0">
                    {webResults.map((r) => (
                      <WebRow key={r.url} r={r} copy={copy} />
                    ))}
                  </ul>
                </>
              ) : (
                <p className="m-0 text-text-2">
                  {web.data.status === "limit"
                    ? copy("search.web_limit")
                    : web.data.status === "empty"
                      ? copy("search.web_empty")
                      : copy("search.web_unavailable")}
                </p>
              )}
            </section>
          ) : null}

          {showWebAsk && localSettled ? (
            <div>
              <Button variant="secondary" onClick={() => commit(text, { web: 1 })}>
                {copy("search.web_ask")}
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
