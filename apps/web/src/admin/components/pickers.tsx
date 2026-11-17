import { cx } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search, X } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { api, qk, type SourceRow, type TaxonomyNode } from "../api";
import { INPUT, LABEL, useT } from "./ui";

export function useSources() {
  return useQuery({
    queryKey: qk.sources,
    queryFn: ({ signal }) => api.sources(signal),
    staleTime: 5 * 60_000,
    select: (d) => d.items,
  });
}

export function useTaxonomy(exam: string | undefined) {
  return useQuery({
    queryKey: qk.taxonomy(exam),
    queryFn: ({ signal }) => api.taxonomy(exam, signal),
    staleTime: 5 * 60_000,
    select: (d) => d.items,
  });
}

/** Licence words for a source (never colour only). */
export function sourceSummary(s: SourceRow, t: ReturnType<typeof useT>): string {
  const flags = [
    s.license,
    t(s.verified ? "admin.source.verified" : "admin.source.unverified"),
    s.allow_commercial ? null : t("admin.source.noncommercial"),
    s.allow_ai_ingest ? null : t("admin.source.no_ai_ingest"),
  ].filter(Boolean);
  return flags.join(" · ");
}

/** What a source is chosen for (the server's `sources.usable_for`): a stored file, a page, text, or an embed. */
export type SourceUse = "host" | "page" | "text" | "embed";

/** Whether the source's licence allows this use (the server's licence gate decides; this filters the picker). */
export function usableFor(s: SourceRow, use: SourceUse): boolean {
  return s.usable_for.includes(use);
}

/** A required `sources` row: every asset references one (HANDOFF §7.1). Only sources usable for `use` are offered. */
export function SourceSelect({
  value,
  onChange,
  use,
  label,
  invalid,
  disabled,
}: {
  value: string;
  onChange: (id: string) => void;
  use: SourceUse;
  label?: string;
  invalid?: boolean;
  disabled?: boolean;
}) {
  const t = useT();
  const id = useId();
  const sources = useSources();
  const current = sources.data?.find((s) => s.id === value);
  const usable = (sources.data ?? []).filter((s) => usableFor(s, use));
  // A saved value whose licence no longer allows this use stays visible (disabled, with the reason) until changed.
  const stale = current && !usableFor(current, use) ? current : null;
  return (
    <label htmlFor={id} className={LABEL}>
      {label ?? t("admin.field.source")}
      <span className="relative flex">
        <select
          id={id}
          required
          value={value}
          disabled={disabled || sources.isPending}
          aria-invalid={invalid || undefined}
          aria-describedby={`${id}-hint`}
          onChange={(e) => onChange(e.target.value)}
          className={cx(INPUT, "appearance-none pr-11")}
        >
          <option value="">{t(sources.isPending ? "admin.state.loading" : "admin.field.source_choose")}</option>
          {stale ? (
            <option value={stale.id} disabled>
              {t("admin.field.source_not_usable_option", { name: stale.name })}
            </option>
          ) : null}
          {usable.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <ChevronDown
          aria-hidden="true"
          size={18}
          className="pointer-events-none absolute top-1/2 right-4 -translate-y-1/2 text-text-2"
        />
      </span>
      <span id={`${id}-hint`} className="text-text-3">
        {sources.isError
          ? t("admin.field.source_failed")
          : stale
            ? t("admin.field.source_not_usable")
            : current
              ? sourceSummary(current, t)
              : sources.data && usable.length === 0
                ? t("admin.field.source_none_usable")
                : t("admin.field.source_hint")}
      </span>
    </label>
  );
}

const KIND_ORDER = ["system", "discipline", "topic"];

/** Taxonomy multi-select: selected chips (removable) and a filterable checkbox list. */
export function TaxonomyPicker({
  exam,
  value,
  onChange,
  label,
}: {
  exam: string | undefined;
  value: string[];
  onChange: (ids: string[]) => void;
  label?: string;
}) {
  const t = useT();
  const id = useId();
  const tax = useTaxonomy(exam);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);
  const byId = useMemo(() => new Map((tax.data ?? []).map((n) => [n.id, n])), [tax.data]);
  const groups = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = (tax.data ?? []).filter((n) => !f || n.label.toLowerCase().includes(f) || n.id.includes(f));
    const out = new Map<string, TaxonomyNode[]>();
    for (const n of list) out.set(n.kind, [...(out.get(n.kind) ?? []), n]);
    return [...out.entries()].sort(
      (a, b) => (KIND_ORDER.indexOf(a[0]) + 1 || 99) - (KIND_ORDER.indexOf(b[0]) + 1 || 99) || a[0].localeCompare(b[0]),
    );
  }, [tax.data, filter]);
  const selected = new Set(value);
  const toggle = (nid: string) =>
    onChange(selected.has(nid) ? value.filter((v) => v !== nid) : [...value, nid].sort((a, b) => a.localeCompare(b)));

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span id={`${id}-l`} className="text-ui-sm text-text-2">
        {label ?? t("admin.field.taxonomy")}
      </span>
      <ul aria-labelledby={`${id}-l`} className="m-0 flex list-none flex-wrap gap-2 p-0">
        {value.length === 0 ? <li className="text-ui-sm text-text-3">{t("admin.field.taxonomy_none")}</li> : null}
        {value.map((v) => (
          <li key={v}>
            <button
              type="button"
              onClick={() => toggle(v)}
              aria-label={t("admin.field.taxonomy_remove", { label: byId.get(v)?.label ?? v })}
              className="inline-flex min-h-11 items-center gap-2 rounded-pill border border-line-chip px-3 text-ui-sm text-text"
            >
              {byId.get(v)?.label ?? v}
              <X aria-hidden="true" size={14} />
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-11 items-center gap-2 self-start rounded-pill border border-line-control px-4 text-ui-sm text-text"
      >
        {t(open ? "admin.field.taxonomy_close" : "admin.field.taxonomy_open")}
        <ChevronDown aria-hidden="true" size={16} className={open ? "rotate-180" : undefined} />
      </button>
      {open ? (
        <div id={`${id}-list`} className="flex flex-col gap-3 rounded-block border border-line-soft bg-surface-2 p-3">
          <label className="relative flex">
            <span className="sr-only">{t("admin.field.taxonomy_filter")}</span>
            <Search
              aria-hidden="true"
              size={16}
              className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
            />
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              // Inside a form: Enter filters the list, it never submits the form.
              onKeyDown={(e) => {
                if (e.key === "Enter") e.preventDefault();
              }}
              placeholder={t("admin.field.taxonomy_filter")}
              className={cx(INPUT, "pl-10")}
            />
          </label>
          {tax.isPending ? (
            <p className="m-0 text-text-3">{t("admin.state.loading")}</p>
          ) : tax.isError ? (
            <p className="m-0 text-amber">{t("admin.field.taxonomy_failed")}</p>
          ) : groups.length === 0 ? (
            <p className="m-0 text-text-3">{t("admin.field.taxonomy_empty")}</p>
          ) : (
            <div className="flex max-h-72 flex-col gap-3 overflow-y-auto">
              {groups.map(([kind, nodes]) => (
                <fieldset key={kind} className="m-0 flex flex-col gap-1 border-0 p-0">
                  <legend className="mb-1 p-0 font-mono text-label-sm text-text-3 uppercase">{kind}</legend>
                  {nodes.map((n) => (
                    <label key={n.id} className="flex min-h-11 cursor-pointer items-center gap-3 text-ui text-text">
                      <input
                        type="checkbox"
                        value={n.id}
                        data-taxonomy-id={n.id}
                        checked={selected.has(n.id)}
                        onChange={() => toggle(n.id)}
                        className="size-5 accent-paper"
                      />
                      <span className="min-w-0 flex-1">{n.label}</span>
                      <span className="font-mono text-label-sm text-text-3">{n.id}</span>
                    </label>
                  ))}
                </fieldset>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
