import { Button, Chip, cx, Tile } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight, Eye, RotateCcw, TriangleAlert } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { api, type ConfigEntry, isConflict, qk } from "../api";
import {
  AdminPage,
  Empty,
  Failure,
  fmtDateTime,
  InlineError,
  Loading,
  MONO_AREA,
  PILL,
  PILL_OFF,
  PILL_ON,
  useT,
} from "../components/ui";
import { changedCount, jsonDiff } from "../config/diff";
import { Field, LineDiff } from "../config/SchemaForm";
import { buildSpec, type Hint, type JsonSchema, validate } from "../config/schema";

/** `/admin/config`: every app_config key with its description, source and validity. */
export function ConfigListPage() {
  const t = useT();
  const list = useQuery({ queryKey: qk.configList, queryFn: ({ signal }) => api.configList(signal) });
  return (
    <AdminPage label={t("admin.label")} title={t("admin.config.title")}>
      <p className="m-0 max-w-[48rem] text-text-2">{t("admin.config.intro")}</p>
      {list.isPending ? (
        <Loading rows={6} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <Empty title={t("admin.config.empty")} />
      ) : (
        <ul className="m-0 grid list-none gap-3 p-0 tablet:grid-cols-2">
          {list.data.items.map((k) => (
            <li key={k.key}>
              <Link
                to="/admin/config/$configKey"
                params={{ configKey: k.key }}
                className="flex h-full flex-col gap-2 rounded-block border border-line-soft bg-surface-1 p-5 text-text no-underline hover:border-line-control hover:text-text"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="break-all font-mono font-medium">{k.key}</span>
                  <ArrowRight aria-hidden="true" size={16} className="shrink-0" />
                </span>
                <span className="text-ui-sm text-text-2">{k.description}</span>
                <span className="flex flex-wrap items-center gap-2">
                  <Chip tone={k.source === "db" ? "done" : "meta"}>
                    {t(k.source === "db" ? "admin.config.source_db" : "admin.config.source_file", {
                      file: k.source_file,
                    })}
                  </Chip>
                  {k.public ? <Chip tone="meta">{t("admin.config.public")}</Chip> : null}
                  {k.invalid_reason ? (
                    <Chip tone="warning" icon={<TriangleAlert size={13} />}>
                      {t("admin.config.invalid")}
                    </Chip>
                  ) : null}
                  {k.updated_at ? (
                    <span className="text-ui-sm text-text-3">
                      {t("admin.config.updated", { time: fmtDateTime(k.updated_at) })}
                    </span>
                  ) : null}
                </span>
                {k.invalid_reason ? (
                  <span className="break-words text-ui-sm text-amber">{k.invalid_reason}</span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </AdminPage>
  );
}

function History({ entry, onSaved }: { entry: ConfigEntry; onSaved?: (at: string | null) => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const hist = useQuery({
    queryKey: qk.configHistory(entry.key),
    queryFn: ({ signal }) => api.configHistory(entry.key, signal),
  });
  const revert = useMutation({
    mutationFn: (id: number) => api.revertConfig(entry.key, { history_id: id, expected_updated_at: entry.updated_at }),
    onSuccess: (next) => {
      onSaved?.(next.updated_at);
      qc.setQueryData(qk.config(entry.key), next);
      void qc.invalidateQueries({ queryKey: qk.configHistory(entry.key) });
      void qc.invalidateQueries({ queryKey: qk.configList });
      setOpen(null);
    },
  });
  return (
    <section aria-labelledby="chist-h" className="flex flex-col gap-3">
      <h2 id="chist-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("admin.config.history")}
      </h2>
      <InlineError error={revert.error}>
        {isConflict(revert.error) ? <p className="m-0 text-text-2">{t("admin.conflict.reload_hint")}</p> : null}
      </InlineError>
      {hist.isPending ? (
        <Loading rows={2} />
      ) : hist.isError ? (
        <Failure error={hist.error} onRetry={() => void hist.refetch()} retrying={hist.isFetching} />
      ) : hist.data.items.length === 0 ? (
        <Empty title={t("admin.config.history_empty")} />
      ) : (
        <ol className="m-0 flex list-none flex-col p-0" data-testid="config-history">
          {hist.data.items.map((h) => (
            <li key={h.id} className="flex flex-col gap-3 border-line-soft border-t py-3 first:border-t-0">
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone="meta">{h.op}</Chip>
                <span className="text-ui-sm text-text-2">
                  {t("admin.config.replaced_at", { time: fmtDateTime(h.replaced_at) })}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button aria-expanded={open === h.id} onClick={() => setOpen(open === h.id ? null : h.id)}>
                  {t(open === h.id ? "admin.revision.hide_diff" : "admin.revision.show_diff")}
                </Button>
                <Button icon={<RotateCcw size={16} />} disabled={revert.isPending} onClick={() => revert.mutate(h.id)}>
                  {t(revert.isPending && revert.variables === h.id ? "admin.action.saving" : "admin.config.revert")}
                </Button>
              </div>
              {open === h.id ? (
                <LineDiff lines={jsonDiff(entry.value, h.value)} label={t("admin.config.history_diff_label")} />
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Editor({
  entry,
  saved,
  onSaved,
}: {
  entry: ConfigEntry;
  saved: boolean;
  onSaved: (at: string | null) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const id = useId();
  const spec = useMemo(() => buildSpec(entry.json_schema as JsonSchema), [entry.json_schema]);
  const [draft, setDraft] = useState<unknown>(entry.value);
  const [view, setView] = useState<"form" | "json">(spec.kind === "json" ? "json" : "form");
  const [raw, setRaw] = useState(() => JSON.stringify(entry.value, null, 2));
  const [rawBad, setRawBad] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  // Only when switching views: typing in the JSON view must not be reformatted.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reformat on a view switch only, not on every edit
  useEffect(() => {
    if (view === "json") setRaw(JSON.stringify(draft, null, 2));
  }, [view]);
  const diff = useMemo(() => jsonDiff(entry.value, draft), [entry.value, draft]);
  const changes = changedCount(diff);
  const hints = useMemo(() => validate(spec, draft), [spec, draft]);
  const byPath = useMemo(() => {
    const m = new Map<string, Hint[]>();
    for (const h of hints) m.set(h.path, [...(m.get(h.path) ?? []), h]);
    return m;
  }, [hints]);
  const save = useMutation({
    mutationFn: () =>
      api.putConfig(entry.key, { value: draft, expected_updated_at: entry.updated_at, admin_edited: true }),
    onSuccess: (next) => {
      onSaved(next.updated_at);
      setReviewing(false);
      qc.setQueryData(qk.config(entry.key), next);
      void qc.invalidateQueries({ queryKey: qk.configHistory(entry.key) });
      void qc.invalidateQueries({ queryKey: qk.configList });
    },
  });
  const conflict = isConflict(save.error);
  const reload = async () => {
    save.reset();
    setReviewing(false);
    await qc.invalidateQueries({ queryKey: qk.config(entry.key) });
  };

  return (
    <AdminPage
      label={t("admin.config.title")}
      title={entry.key}
      actions={
        reviewing ? null : (
          <Button
            variant="primary"
            size="cta"
            icon={<Eye size={18} />}
            disabled={changes === 0 || rawBad}
            onClick={() => setReviewing(true)}
          >
            {t("admin.config.review", { n: String(changes) })}
          </Button>
        )
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/admin/config" className="min-h-11 text-text-2">
          {t("admin.config.back")}
        </Link>
        <Chip tone={entry.source === "db" ? "done" : "meta"}>
          {t(entry.source === "db" ? "admin.config.source_db_short" : "admin.config.source_file_short")}
        </Chip>
        {entry.updated_at ? (
          <span className="text-ui-sm text-text-3">
            {t("admin.config.updated", { time: fmtDateTime(entry.updated_at) })}
          </span>
        ) : null}
        {saved && changes === 0 ? (
          <span role="status" className="text-ui-sm text-text-2">
            {t("admin.config.saved_live")}
          </span>
        ) : null}
      </div>
      {entry.invalid_reason ? (
        <p
          role="alert"
          className="m-0 flex items-start gap-2 rounded-block border border-amber/60 bg-surface-2 p-4 text-text"
        >
          <TriangleAlert aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
          <span className="break-words">{t("admin.config.invalid_reason", { reason: entry.invalid_reason })}</span>
        </p>
      ) : null}

      {reviewing ? (
        <Tile as="section" aria-labelledby={`${id}-rv`} className="flex flex-col gap-4">
          <h2 id={`${id}-rv`} className="m-0 font-display font-bold text-[1.5rem]">
            {t("admin.config.review_title")}
          </h2>
          <LineDiff lines={diff} label={t("admin.config.diff_label")} />
          {hints.length ? (
            <p className="m-0 text-ui-sm text-amber">{t("admin.config.hints_pending", { n: String(hints.length) })}</p>
          ) : null}
          <InlineError error={save.error}>
            {conflict ? (
              <div className="flex flex-col gap-2">
                <p className="m-0 text-text-2">{t("admin.config.conflict")}</p>
                <div>
                  <Button variant="strong" onClick={() => void reload()}>
                    {t("admin.config.reload")}
                  </Button>
                </div>
              </div>
            ) : null}
          </InlineError>
          <div className="flex flex-wrap gap-3">
            <Button variant="primary" size="cta" disabled={save.isPending || conflict} onClick={() => save.mutate()}>
              {t(save.isPending ? "admin.action.saving" : "admin.config.save")}
            </Button>
            <Button size="cta" onClick={() => setReviewing(false)}>
              {t("admin.config.keep_editing")}
            </Button>
          </div>
        </Tile>
      ) : null}

      <div className={cx("flex flex-col gap-5", reviewing && "hidden")}>
        <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
          <legend className="sr-only">{t("admin.config.view_label")}</legend>
          {spec.kind === "json" ? null : (
            <button
              type="button"
              aria-pressed={view === "form"}
              onClick={() => setView("form")}
              className={cx(PILL, view === "form" ? PILL_ON : PILL_OFF)}
            >
              {t("admin.config.view_form")}
            </button>
          )}
          <button
            type="button"
            aria-pressed={view === "json"}
            onClick={() => setView("json")}
            className={cx(PILL, view === "json" ? PILL_ON : PILL_OFF)}
          >
            {t("admin.config.view_json")}
          </button>
          {changes ? (
            <Button
              onClick={() => {
                setDraft(entry.value);
                setRaw(JSON.stringify(entry.value, null, 2));
                setRawBad(false);
              }}
            >
              {t("admin.config.discard")}
            </Button>
          ) : null}
        </fieldset>
        {hints.length ? (
          <p role="status" className="m-0 text-ui-sm text-amber">
            {t("admin.config.hints", { n: String(hints.length) })}
          </p>
        ) : null}
        <Tile className="min-w-0">
          {view === "form" ? (
            <Field spec={spec} value={draft} onChange={setDraft} label={entry.key} path="" hints={byPath} />
          ) : (
            <label className="flex flex-col gap-2 text-ui-sm text-text-2">
              {t("admin.config.json_label")}
              <textarea
                spellCheck={false}
                autoCapitalize="off"
                className={cx(MONO_AREA, "min-h-[28rem]")}
                value={raw}
                aria-invalid={rawBad || undefined}
                onChange={(e) => {
                  setRaw(e.target.value);
                  try {
                    setDraft(JSON.parse(e.target.value));
                    setRawBad(false);
                  } catch {
                    setRawBad(true);
                  }
                }}
              />
              {rawBad ? <span className="text-amber">{t("admin.config.json_invalid")}</span> : null}
              {hints.slice(0, 20).map((h) => (
                <span key={`${h.path}:${h.key}`} className="text-amber">
                  {t("admin.error.at_path", { path: h.path || entry.key, message: t(h.key, h.vars) })}
                </span>
              ))}
            </label>
          )}
        </Tile>
      </div>
      <Tile className="min-w-0">
        <History entry={entry} onSaved={onSaved} />
      </Tile>
    </AdminPage>
  );
}

/** `/admin/config/$configKey`: form from the key's JSON Schema, JSON view, diff before save, history and revert. */
export function ConfigKeyPage({ configKey }: { configKey: string }) {
  const t = useT();
  const [savedAt, setSavedAt] = useState<string | null | undefined>(undefined);
  const q = useQuery({
    queryKey: qk.config(configKey),
    queryFn: ({ signal }) => api.config(configKey, signal),
    refetchOnWindowFocus: false,
  });
  if (q.isPending) return <Loading rows={4} tall />;
  if (q.isError) {
    return (
      <AdminPage label={t("admin.config.title")} title={configKey}>
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </AdminPage>
    );
  }
  // A new server value (save, revert, reload) starts the editor from it.
  return (
    <Editor
      key={q.data.updated_at ?? "file"}
      entry={q.data}
      saved={savedAt !== undefined && savedAt === q.data.updated_at}
      onSaved={setSavedAt}
    />
  );
}
