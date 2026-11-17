import { Button, Chip, type ChipTone, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Ban, Check, CircleDashed, Clock, Lock, type LucideIcon, Send, TriangleAlert, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";
import { ApiError } from "../../auth/apiError";
import { useUiText } from "../../copy/useUiText";
import { AdminApiError, CONTRACT_CODE, UNREACHABLE_CODE } from "../api";

export type T = ReturnType<typeof useUiText>;
export const useT = useUiText;

export const INPUT =
  "min-h-12 w-full min-w-0 rounded-inner border border-line-control bg-ground px-4 text-ui text-text disabled:text-text-muted aria-invalid:border-amber";
export const TEXTAREA =
  "min-h-28 w-full min-w-0 rounded-inner border border-line-control bg-ground px-4 py-3 text-ui text-text aria-invalid:border-amber";
export const MONO_AREA = `${TEXTAREA} font-mono text-ui-sm leading-relaxed`;
export const LABEL = "flex min-w-0 flex-col gap-2 text-ui-sm text-text-2";
export const PILL =
  "inline-flex min-h-11 items-center gap-2 rounded-pill border px-4 text-ui-sm no-underline transition-colors duration-(--duration-control) ease-out";
export const PILL_ON = "border-paper bg-paper font-semibold text-ground hover:text-ground";
export const PILL_OFF = "border-line-control text-text hover:bg-surface-2 hover:text-text";

/** The page frame of every admin view: mono label, h1, optional actions. */
export function AdminPage({
  label,
  title,
  actions,
  children,
  className,
}: {
  label: string;
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cx("flex min-w-0 flex-col gap-6", className)}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <span className="font-mono text-label text-text-3 uppercase">{label}</span>
          <h1 id={id} className="m-0 break-words font-display text-headline">
            {title}
          </h1>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function Loading({ rows = 3, tall = false }: { rows?: number; tall?: boolean }) {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-3">
      <p role="status" className="sr-only">
        {t("admin.state.loading")}
      </p>
      {Array.from({ length: rows }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: identical decorative placeholders
        <Skeleton key={i} className={tall ? "h-40 rounded-block" : "h-16 rounded-block"} />
      ))}
    </div>
  );
}

/** Structured error codes the server sends (409 and friends), worded here. */
export const ERROR_CODE_KEYS: Record<string, string> = {
  stale_write: "admin.error.code.stale_write",
  stale_draft: "admin.error.code.stale_draft",
  stale_parent: "admin.error.code.stale_parent",
  bad_parent: "admin.error.code.bad_parent",
  id_taken: "admin.error.code.id_taken",
  bad_transition: "admin.error.code.bad_transition",
  stale_order: "admin.error.code.stale_order",
  not_restorable: "admin.error.code.not_restorable",
};

/** 422 content errors (error.details[].msg) per node path. */
export const NODE_ERROR_KEYS: Record<string, string> = {
  unknown_node: "admin.error.node.unknown_node",
  unknown_mark: "admin.error.node.unknown_mark",
  bad_attrs: "admin.error.node.bad_attrs",
  bad_value: "admin.error.node.bad_value",
  bad_content: "admin.error.node.bad_content",
  too_large: "admin.error.node.too_large",
  not_a_doc: "admin.error.node.not_a_doc",
};

/** The words for a failed call: our own copy for transport/contract failures, else the server's message. */
export function errorMessage(err: unknown, t: T): string {
  if (err instanceof ApiError) {
    if (err.code === UNREACHABLE_CODE || err.status === null) return t("admin.error.unreachable");
    if (err.code === CONTRACT_CODE) return t("admin.error.contract");
    const coded = err.code ? ERROR_CODE_KEYS[err.code] : undefined;
    if (coded) return t(coded);
    if (err.status === 403) return t("admin.permission.body");
    if (err.status === 404) return t("admin.error.not_found");
    if (err.status === 429) return t("admin.error.rate_limited");
    if (err.status === 413) return t("admin.error.too_large");
    if (err.status === 415) return t("admin.error.bad_type");
    if (err.message && !/^HTTP \d{3}$/.test(err.message)) return err.message;
  }
  return t("admin.error.generic");
}

export function requestIdOf(err: unknown): string | null {
  return err instanceof ApiError ? err.requestId : null;
}

export function Failure({
  error,
  title,
  onRetry,
  retrying,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const t = useT();
  if (error instanceof ApiError && error.status === 403) return <PermissionState />;
  return (
    <ErrorState
      title={title ?? t("admin.error.title")}
      message={errorMessage(error, t)}
      requestId={requestIdOf(error)}
      onRetry={onRetry}
      retrying={retrying}
      retryLabel={t("admin.action.retry")}
    />
  );
}

/** A non-admin (or a revoked admin): never any admin data, only this. */
export function PermissionState() {
  const t = useT();
  return (
    <div
      role="alert"
      data-testid="admin-permission"
      className="flex flex-col gap-3 rounded-block border border-line-strong bg-surface-1 p-6"
    >
      <span className="flex items-center gap-2 font-semibold text-text">
        <Lock aria-hidden="true" size={18} />
        {t("admin.permission.title")}
      </span>
      <p className="m-0 text-text-2">{t("admin.permission.body")}</p>
    </div>
  );
}

export function Empty({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-block border border-dashed border-line-dashed p-6">
      <p className="m-0 font-semibold text-text">{title}</p>
      {body ? <p className="m-0 text-text-2">{body}</p> : null}
      {action}
    </div>
  );
}

/** A mutation's failure shown inline (forms, dialogs). Gate refusals and 422 paths are worded by the caller. */
export function InlineError({ error, children }: { error: unknown; children?: ReactNode }) {
  const t = useT();
  if (!error) return null;
  const gate = error instanceof AdminApiError ? error.gate() : null;
  const fields = error instanceof AdminApiError && error.status === 422 ? error.fields() : [];
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-block border border-amber/60 bg-surface-2 p-4">
      <span className="flex items-start gap-2 text-text">
        <TriangleAlert aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
        <span className="min-w-0">
          {gate ? <GateText reason={gate.reason} refId={gate.refId} /> : errorMessage(error, t)}
        </span>
      </span>
      {fields.length ? (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {fields.map((f) => (
            <li key={`${f.path}:${f.msg}`} className="text-ui-sm text-text-2">
              {t("admin.error.at_path", {
                path: f.path.replace(/^body\.body\b/, "body"),
                message: NODE_ERROR_KEYS[f.msg] ? t(NODE_ERROR_KEYS[f.msg] ?? "") : f.msg,
              })}
            </li>
          ))}
        </ul>
      ) : null}
      {children}
      {requestIdOf(error) ? (
        <span className="font-mono text-label-sm break-all text-text-3">
          {t("admin.error.request_id", { id: requestIdOf(error) ?? "" })}
        </span>
      ) : null}
    </div>
  );
}

/** `admin.gate.<reason>` with the offending media/ref id. */
export const GATE_KEYS: Record<string, string> = {
  no_source: "admin.gate.no_source",
  no_license: "admin.gate.no_license",
  copy_not_allowed: "admin.gate.copy_not_allowed",
  noncommercial_on_paid: "admin.gate.noncommercial_on_paid",
  share_alike_in_proprietary: "admin.gate.share_alike_in_proprietary",
  no_ai_ingest: "admin.gate.no_ai_ingest",
  not_embeddable: "admin.gate.not_embeddable",
  metadata_stale: "admin.gate.metadata_stale",
  media_not_approved: "admin.gate.media_not_approved",
  media_missing: "admin.gate.media_missing",
  question_missing: "admin.gate.question_missing",
  no_sections: "admin.gate.no_sections",
  section_empty: "admin.gate.section_empty",
  media_in_use: "admin.gate.media_in_use",
  media_in_body: "admin.gate.media_in_body",
  duplicate_video: "admin.gate.duplicate_video",
  chapter_missing: "admin.gate.chapter_missing",
  taxonomy_missing: "admin.gate.taxonomy_missing",
  lint_errors: "admin.gate.lint_errors",
  section_missing: "admin.gate.section_missing",
  missing_section_id: "admin.gate.missing_section_id",
  missing_caption: "admin.gate.missing_caption",
  storage_unavailable: "admin.gate.storage_unavailable",
  bad_storage_path: "admin.gate.bad_storage_path",
  reject_unsupported: "admin.gate.reject_unsupported",
  unknown: "admin.gate.unknown",
};

export function GateText({ reason, refId }: { reason: string; refId: string }) {
  const t = useT();
  const key = GATE_KEYS[reason] ?? GATE_KEYS.unknown ?? "admin.gate.unknown";
  return (
    <span data-gate-reason={reason} className="flex flex-col gap-1">
      <span className="font-semibold">{t("admin.gate.title")}</span>
      <span>{t(key, { reason })}</span>
      {refId ? <span className="font-mono text-label-sm text-text-2">{t("admin.gate.ref", { id: refId })}</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------------------------------------------- status

interface StatusLook {
  key: string;
  tone: ChipTone;
  Icon: LucideIcon;
}

const STATUS: Record<string, StatusLook> = {
  draft: { key: "admin.status.draft", tone: "planned", Icon: CircleDashed },
  in_review: { key: "admin.status.in_review", tone: "warning", Icon: Clock },
  auto_checked: { key: "admin.status.auto_checked", tone: "meta", Icon: Check },
  published: { key: "admin.status.published", tone: "done", Icon: Send },
  retired: { key: "admin.status.retired", tone: "meta", Icon: Ban },
  unreviewed: { key: "admin.status.unreviewed", tone: "warning", Icon: Clock },
  approved: { key: "admin.status.approved", tone: "done", Icon: Check },
  rejected: { key: "admin.status.rejected", tone: "meta", Icon: X },
  open: { key: "admin.status.open", tone: "warning", Icon: CircleDashed },
  triaged: { key: "admin.status.triaged", tone: "meta", Icon: Clock },
  resolved: { key: "admin.status.resolved", tone: "done", Icon: Check },
  dismissed: { key: "admin.status.dismissed", tone: "meta", Icon: Ban },
};

/** Every status the console shows, as a word plus an icon (never colour only). */
export function StatusChip({ status, prefix }: { status: string; prefix?: string }) {
  const t = useT();
  const look = STATUS[status];
  if (!look) {
    return (
      <Chip tone="meta" srPrefix={prefix}>
        {status}
      </Chip>
    );
  }
  return (
    <Chip tone={look.tone} icon={<look.Icon size={13} />} srPrefix={prefix}>
      {t(look.key)}
    </Chip>
  );
}

export const STATUS_KEYS: Record<string, string> = Object.fromEntries(
  Object.entries(STATUS).map(([k, v]) => [k, v.key]),
);

// ---------------------------------------------------------------------------------------------------- dialog

/**
 * A modal <dialog> (focus trap, Escape and backdrop are the platform's). `open` drives showModal/close.
 */
export function Dialog({
  open,
  title,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className={cx(
        "m-auto max-h-[92dvh] w-[min(96vw,44rem)] max-w-none overflow-hidden rounded-tile-sm border border-line bg-surface-1 p-0 text-text backdrop:bg-ground/85",
        wide && "w-[min(96vw,72rem)]",
      )}
    >
      {open ? (
        <div className="flex max-h-[92dvh] flex-col">
          <div className="flex items-start gap-3 border-line-soft border-b px-5 py-4 tablet:px-6">
            <h2 id={id} className="m-0 min-w-0 flex-1 font-display font-bold text-[1.375rem] leading-tight">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label={t("admin.action.close")}
              className="flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control bg-surface-2 text-text"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 tablet:px-6">{children}</div>
        </div>
      ) : null}
    </dialog>
  );
}

// ---------------------------------------------------------------------------------------------------- misc

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFmt.format(d);
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateTimeFmt.format(d);
}

export function fmtNumber(n: number): string {
  return new Intl.NumberFormat().format(n);
}

/** Seconds as m:ss (or h:mm:ss). */
export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s) || s < 0) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = Math.floor(s % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(r).padStart(2, "0")}`;
}

/** A row of segmented filter pills (radio semantics). */
export function FilterPills<V extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: V;
  options: readonly { value: V; label: string }[];
  onChange: (v: V) => void;
}) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
      <legend className="mb-2 p-0 font-mono text-label-sm text-text-3 uppercase">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={cx(PILL, value === o.value ? PILL_ON : PILL_OFF)}
          >
            {value === o.value ? <Check aria-hidden="true" size={14} /> : null}
            {o.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Load-more button for cursor lists. */
export function MoreButton({ onClick, busy, show }: { onClick: () => void; busy: boolean; show: boolean }) {
  const t = useT();
  if (!show) return null;
  return (
    <div>
      <Button variant="secondary" onClick={onClick} disabled={busy}>
        {t(busy ? "admin.state.loading" : "admin.action.load_more")}
      </Button>
    </div>
  );
}
