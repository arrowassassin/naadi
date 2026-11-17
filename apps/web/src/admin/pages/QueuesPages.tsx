import { Button, cx, Tile } from "@naadi/ui";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight, Check, X } from "lucide-react";
import { useId, useState } from "react";
import { ApiError } from "../../auth/apiError";
import { api, type QueueCount, type QueueItem, qk } from "../api";
import { DynamicLink, RefLink } from "../components/RefLink";
import {
  AdminPage,
  Dialog,
  Empty,
  Failure,
  fmtDateTime,
  fmtNumber,
  InlineError,
  LABEL,
  Loading,
  MoreButton,
  TEXTAREA,
  useT,
} from "../components/ui";
import { queueView, rejectNeedsNote } from "../queues/registry";
import "../queues/views";

function useQueueTitle(): (kind: string) => string {
  const t = useT();
  return (kind) => {
    const v = queueView(kind);
    return v ? t(v.titleKey) : kind;
  };
}

/** Kinds decided elsewhere (S21's question review): a link to that page with its filters, no approve/reject. */
function ElsewhereLink({ q }: { q: QueueCount }) {
  const t = useT();
  if (!q.path) return null;
  return (
    <span className="flex flex-col gap-2">
      <DynamicLink to={q.path} className="inline-flex min-h-11 items-center gap-2 self-start text-text">
        {t("admin.queues.decide_elsewhere")}
        <ArrowRight aria-hidden="true" size={16} />
      </DynamicLink>
      {q.filters.length ? (
        <span className="flex flex-wrap gap-2 text-ui-sm text-text-2">
          {q.filters.map((f) => {
            // Filter names are the owning segment's ui_copy; one it has not worded yet is left out, never shown raw.
            const key = `admin.queues.${q.kind}.filter.${f}`;
            const word = t(key);
            return word === key ? null : (
              <span
                key={f}
                className="max-w-full rounded-pill border border-line-chip px-2.5 py-1 [overflow-wrap:anywhere]"
              >
                {word}
              </span>
            );
          })}
        </span>
      ) : null}
    </span>
  );
}

/** `/admin/queues`: every approval queue with its pending count. */
export function QueuesPage() {
  const t = useT();
  const title = useQueueTitle();
  const list = useQuery({ queryKey: qk.queues, queryFn: ({ signal }) => api.queues(signal) });
  return (
    <AdminPage label={t("admin.label")} title={t("admin.queues.title")}>
      {list.isPending ? (
        <Loading rows={3} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <Empty title={t("admin.queues.none")} />
      ) : (
        <ul className="m-0 grid list-none gap-4 p-0 tablet:grid-cols-2 laptop:grid-cols-3">
          {list.data.items.map((q) => (
            <li key={q.kind} className="min-w-0">
              <Tile as="article" className="flex h-full min-w-0 flex-col gap-3" data-queue={q.kind}>
                <h2 className="m-0 font-semibold text-[1.125rem] [overflow-wrap:anywhere]">{title(q.kind)}</h2>
                <span className="font-display font-bold text-[2.5rem] leading-none tracking-[-0.03em]">
                  {fmtNumber(q.count)}
                </span>
                <span className="text-ui-sm text-text-2">
                  {t(q.count === 1 ? "admin.queues.pending_one" : "admin.queues.pending", { n: fmtNumber(q.count) })}
                </span>
                {q.decides_here ? (
                  <Link
                    to="/admin/queues/$kind"
                    params={{ kind: q.kind }}
                    className="inline-flex min-h-11 items-center gap-2 self-start text-text"
                  >
                    {t("admin.queues.open")}
                    <span className="sr-only">{title(q.kind)}</span>
                    <ArrowRight aria-hidden="true" size={16} />
                  </Link>
                ) : (
                  <ElsewhereLink q={q} />
                )}
              </Tile>
            </li>
          ))}
        </ul>
      )}
    </AdminPage>
  );
}

function GenericRow({ item }: { item: QueueItem }) {
  const t = useT();
  return (
    <span className="flex min-w-0 flex-col gap-1">
      <span className="break-words font-semibold text-text">{item.title ?? item.id}</span>
      <span className="text-ui-sm text-text-2">
        {t("admin.queues.created", { time: fmtDateTime(item.created_at) })}
      </span>
    </span>
  );
}

function Decide({ kind, item, onDone }: { kind: string; item: QueueItem; onDone: (id: string) => void }) {
  const t = useT();
  const ids = useId();
  const view = queueView(kind);
  const needsNote = rejectNeedsNote(kind);
  const [note, setNote] = useState("");
  const [form, setForm] = useState(false);
  const [noteMissing, setNoteMissing] = useState(false);
  const approve = useMutation({
    mutationFn: (params?: Record<string, unknown>) =>
      api.approve(kind, item.id, { note: note.trim() || null, params: params ?? {} }),
    onSuccess: () => {
      setForm(false);
      onDone(item.id);
    },
  });
  const reject = useMutation({
    mutationFn: () => api.reject(kind, item.id, { note: note.trim() || null }),
    onSuccess: () => onDone(item.id),
  });
  const gone = [approve.error, reject.error].some((e) => e instanceof ApiError && e.status === 404);
  const busy = approve.isPending || reject.isPending;
  const Detail = view?.Detail;
  const ApproveForm = view?.ApproveForm;
  return (
    <Tile as="section" aria-labelledby={`${ids}-h`} className="flex min-w-0 flex-col gap-4" data-focused-item={item.id}>
      <h2 id={`${ids}-h`} className="m-0 break-words font-display font-bold text-[1.375rem]">
        {item.title ?? item.id}
      </h2>
      <span className="text-ui-sm text-text-2">
        {t("admin.queues.created", { time: fmtDateTime(item.created_at) })}
      </span>
      <RefLink refTo={item.ref} />
      {Detail ? <Detail item={item} /> : null}
      <label htmlFor={`${ids}-n`} className={LABEL}>
        {t(needsNote ? "admin.queues.note_reject_required" : "admin.field.note_optional")}
        <textarea
          id={`${ids}-n`}
          className={TEXTAREA}
          maxLength={1000}
          value={note}
          aria-invalid={noteMissing || undefined}
          onChange={(e) => {
            setNote(e.target.value);
            setNoteMissing(false);
          }}
        />
        {noteMissing ? <span className="text-amber">{t("admin.queues.note_missing")}</span> : null}
      </label>
      {gone ? (
        <p role="alert" className="m-0 text-amber">
          {t("admin.queues.gone")}
        </p>
      ) : (
        <InlineError error={approve.error ?? reject.error} />
      )}
      <div className="flex flex-wrap gap-3">
        <Button
          variant="primary"
          size="cta"
          icon={<Check size={18} />}
          disabled={busy}
          onClick={() => (ApproveForm ? setForm(true) : approve.mutate(undefined))}
        >
          {t(approve.isPending ? "admin.action.saving" : "admin.action.approve")}
        </Button>
        <Button
          size="cta"
          icon={<X size={18} />}
          disabled={busy}
          onClick={() => {
            if (needsNote && !note.trim()) {
              setNoteMissing(true);
              return;
            }
            reject.mutate();
          }}
        >
          {t(reject.isPending ? "admin.action.saving" : "admin.action.reject")}
        </Button>
        {gone ? <Button onClick={() => onDone(item.id)}>{t("admin.queues.skip")}</Button> : null}
      </div>
      {ApproveForm ? (
        <Dialog open={form} onClose={() => setForm(false)} title={t("admin.queues.approve_title")}>
          <div className="flex flex-col gap-4">
            <ApproveForm
              item={item}
              busy={approve.isPending}
              onSubmit={(p) => approve.mutate(p)}
              onCancel={() => setForm(false)}
            />
            <InlineError error={approve.error} />
          </div>
        </Dialog>
      ) : null}
    </Tile>
  );
}

/** `/admin/queues/$kind`: the items of one queue; the focused item's approve is the view's one lime action. */
export function QueuePage({ kind }: { kind: string }) {
  const t = useT();
  const qc = useQueryClient();
  const title = useQueueTitle();
  const [done, setDone] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const list = useInfiniteQuery({
    queryKey: qk.queue(kind),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.queue(kind, { limit: 30, cursor: pageParam }, signal),
    getNextPageParam: (p) => p.next_cursor ?? null,
  });
  const items = (list.data?.pages.flatMap((p) => p.items) ?? []).filter((i) => !done.has(i.id));
  const total = Math.max(0, (list.data?.pages[0]?.total ?? 0) - done.size);
  const focused = items.find((i) => i.id === focus) ?? items[0] ?? null;
  const view = queueView(kind);
  const Row = view?.Row ?? GenericRow;

  function onDone(id: string) {
    const idx = items.findIndex((i) => i.id === id);
    const next = items[idx + 1] ?? items[idx - 1] ?? null;
    setDone((d) => new Set(d).add(id));
    setFocus(next?.id ?? null);
    void qc.invalidateQueries({ queryKey: qk.queues });
    void qc.invalidateQueries({ queryKey: ["admin", "media"] });
    void qc.invalidateQueries({ queryKey: ["admin", "reports"] });
  }

  return (
    <AdminPage label={t("admin.queues.title")} title={title(kind)}>
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/admin/queues" className="min-h-11 text-text-2">
          {t("admin.queues.back")}
        </Link>
        {list.data ? (
          <span className="text-ui-sm text-text-2">
            {t(total === 1 ? "admin.queues.pending_one" : "admin.queues.pending", { n: fmtNumber(total) })}
          </span>
        ) : null}
      </div>
      {list.isPending ? (
        <Loading rows={4} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 && !list.hasNextPage ? (
        <Empty title={t("admin.queues.empty")} body={t("admin.queues.empty_body")} />
      ) : (
        <div className="grid min-w-0 items-start gap-5 laptop:grid-cols-[2fr_3fr]">
          <div className="flex min-w-0 flex-col gap-3">
            <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label={title(kind)}>
              {items.map((i) => {
                const on = focused?.id === i.id;
                return (
                  <li key={i.id}>
                    <button
                      type="button"
                      aria-pressed={on}
                      onClick={() => setFocus(i.id)}
                      data-queue-item={i.id}
                      className={cx(
                        "flex w-full rounded-block border p-4 text-left",
                        on ? "border-paper bg-surface-2" : "border-line-soft bg-surface-1 hover:border-line-control",
                      )}
                    >
                      <Row item={i} />
                    </button>
                  </li>
                );
              })}
            </ul>
            <MoreButton
              show={Boolean(list.hasNextPage)}
              busy={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
            />
          </div>
          {focused ? <Decide key={focused.id} kind={kind} item={focused} onDone={onDone} /> : null}
        </div>
      )}
    </AdminPage>
  );
}
