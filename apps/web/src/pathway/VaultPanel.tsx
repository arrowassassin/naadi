import type { VaultDocOut } from "@naadi/shared";
import { Button, Chip, ErrorState, Skeleton, Tile } from "@naadi/ui";
import { Camera, ExternalLink, FileText, Lock, Pencil, Trash2, Upload } from "lucide-react";
import { type ChangeEvent, type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  declaredMime,
  errorKey,
  UploadError,
  useUploadVault,
  useVaultActions,
  useVaultList,
  VAULT_ACCEPT,
} from "./api";
import { formatDate } from "./format";
import { Sheet } from "./Sheet";

function sizeText(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * The document vault: private files in their own bucket, opened only through a short-lived signed URL. Never sent to
 * any model, never kept offline: upload and open need a connection (disabled here with the reason). Expiry shows in
 * amber with a word when it is near or past.
 */
export function VaultPanel() {
  const t = useUiText();
  const online = useOnlineStatus();
  const list = useVaultList();
  const actions = useVaultActions();
  const upload = useUploadVault();
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [pick, setPick] = useState<File | null>(null);
  const [edit, setEdit] = useState<VaultDocOut | null>(null);
  const [confirm, setConfirm] = useState<VaultDocOut | null>(null);
  const [fallback, setFallback] = useState<{ id: string; url: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!fallback) return;
    const id = window.setTimeout(() => setFallback(null), 60_000);
    return () => window.clearTimeout(id);
  }, [fallback]);

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    e.target.value = "";
    setNotice(null);
    if (f) setPick(f);
  };

  function openDoc(doc: VaultDocOut) {
    setNotice(null);
    setFallback(null);
    // The tab opens now, inside the tap, so the browser allows it; the signed URL arrives a moment later.
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    actions.open.mutate(doc.id, {
      onSuccess: (out) => {
        if (tab && !tab.closed) tab.location.href = out.url;
        else setFallback({ id: doc.id, url: out.url });
      },
      onError: (err) => {
        tab?.close();
        setNotice(t(errorKey(err)));
      },
    });
  }

  const docs = list.data?.items ?? [];
  const reason = online ? null : t("vault.offline_reason");
  return (
    <Tile as="section" aria-labelledby="vault-h" className="flex flex-col gap-4" data-panel="vault">
      <div className="flex items-start justify-between gap-3">
        <h2 id="vault-h" className="m-0 font-display font-bold text-[1.5rem]">
          {t("vault.title")}
        </h2>
      </div>
      <p
        className="m-0 flex items-start gap-2 rounded-block bg-surface-2 p-3 text-text-2 text-ui-sm"
        data-banner="vault-private"
      >
        <Lock aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
        {t("vault.banner")}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          icon={<Upload size={16} />}
          disabled={!online}
          onClick={() => fileInput.current?.click()}
        >
          {t("vault.upload")}
        </Button>
        <Button
          variant="secondary"
          icon={<Camera size={16} />}
          disabled={!online}
          onClick={() => cameraInput.current?.click()}
        >
          {t("vault.scan")}
        </Button>
      </div>
      {reason ? (
        <p className="m-0 text-text-3 text-ui-sm" data-offline="vault">
          {reason}
        </p>
      ) : null}
      <input
        ref={fileInput}
        type="file"
        accept={VAULT_ACCEPT}
        className="hidden"
        onChange={onFile}
        aria-label={t("vault.upload")}
        tabIndex={-1}
        data-vault-input="file"
      />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={onFile}
        aria-label={t("vault.scan")}
        tabIndex={-1}
        data-vault-input="camera"
      />
      {notice ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {notice}
        </p>
      ) : null}
      {fallback ? (
        <p className="m-0 text-ui-sm" role="status">
          <a href={fallback.url} target="_blank" rel="noopener noreferrer" className="text-text underline">
            {t("vault.tap_to_open")}
          </a>
        </p>
      ) : null}

      {!online && !list.data ? (
        <p className="m-0 text-text-2">{t("vault.offline_list")}</p>
      ) : list.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <Skeleton className="h-14 w-full rounded-block" />
          <Skeleton className="h-14 w-full rounded-block" />
        </div>
      ) : list.isError ? (
        <ErrorState
          title={t("vault.error_title")}
          message={t(errorKey(list.error))}
          onRetry={() => void list.refetch()}
          retryLabel={t("pathway.retry")}
          retrying={list.isFetching}
        />
      ) : docs.length === 0 ? (
        <p className="m-0 text-text-2" data-empty="vault">
          {t("vault.empty")}
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {docs.map((d) => (
            <li
              key={d.id}
              data-doc={d.id}
              className="flex flex-col gap-2 border-line-soft border-t py-3 first:border-t-0"
            >
              <div className="flex items-center gap-3">
                <FileText aria-hidden="true" size={20} className="shrink-0 text-text-3" />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-semibold text-text">{d.title}</span>
                  <span className="text-text-3 text-ui-sm">
                    {t(`vault.type.${d.doc_type}`)} · {sizeText(d.bytes)}
                  </span>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {d.expiry === "none" ? null : (
                  <Chip tone={d.expiry === "ok" ? "meta" : "warning"}>
                    {d.expiry === "expired"
                      ? t("vault.expired", { date: formatDate(d.expires_on) })
                      : d.expiry === "soon"
                        ? t("vault.expires_soon", { days: String(d.expires_in_days ?? 0) })
                        : t("vault.expires_on", { date: formatDate(d.expires_on) })}
                  </Chip>
                )}
                <Button
                  variant="secondary"
                  icon={<ExternalLink size={15} />}
                  disabled={!online || actions.open.isPending}
                  onClick={() => openDoc(d)}
                  aria-label={t("vault.open_named", { title: d.title })}
                >
                  {t("vault.open")}
                </Button>
                <button
                  type="button"
                  disabled={!online}
                  onClick={() => setEdit(d)}
                  aria-label={t("vault.edit_named", { title: d.title })}
                  className="flex size-11 items-center justify-center rounded-full border border-line-control bg-transparent text-text disabled:border-dashed disabled:text-text-muted"
                >
                  <Pencil aria-hidden="true" size={15} />
                </button>
                <button
                  type="button"
                  disabled={!online}
                  onClick={() => setConfirm(d)}
                  aria-label={t("vault.delete_named", { title: d.title })}
                  className="flex size-11 items-center justify-center rounded-full border border-line-control bg-transparent text-text disabled:border-dashed disabled:text-text-muted"
                >
                  <Trash2 aria-hidden="true" size={15} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <UploadSheet
        file={pick}
        types={list.data?.types ?? ["other"]}
        maxBytes={list.data?.max_bytes ?? 20 * 1024 * 1024}
        busy={upload.isPending}
        error={upload.error}
        onClose={() => {
          setPick(null);
          upload.reset();
        }}
        onSubmit={(v) => {
          if (!pick) return;
          upload.mutate(
            { file: pick, ...v, maxBytes: list.data?.max_bytes ?? 20 * 1024 * 1024 },
            { onSuccess: () => setPick(null) },
          );
        }}
      />
      <EditSheet
        doc={edit}
        types={list.data?.types ?? ["other"]}
        busy={actions.patch.isPending}
        error={actions.patch.error}
        onClose={() => {
          setEdit(null);
          actions.patch.reset();
        }}
        onSubmit={(v) => {
          if (edit) actions.patch.mutate({ id: edit.id, ...v }, { onSuccess: () => setEdit(null) });
        }}
      />
      <Sheet
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={t("vault.delete_title")}
        handle="vault-delete"
      >
        <p className="m-0 text-text-2">{t("vault.delete_body", { title: confirm?.title ?? "" })}</p>
        {actions.remove.isError ? (
          <p role="alert" className="m-0 text-amber">
            {t(errorKey(actions.remove.error))}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <Button
            variant="strong"
            disabled={actions.remove.isPending}
            onClick={() => confirm && actions.remove.mutate(confirm.id, { onSuccess: () => setConfirm(null) })}
          >
            {t("vault.delete_confirm")}
          </Button>
          <Button variant="secondary" onClick={() => setConfirm(null)}>
            {t("pathway.cancel")}
          </Button>
        </div>
      </Sheet>
    </Tile>
  );
}

function TypeSelect({
  id,
  value,
  types,
  onChange,
}: {
  id: string;
  value: string;
  types: readonly string[];
  onChange: (v: string) => void;
}) {
  const t = useUiText();
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
    >
      {types.map((x) => (
        <option key={x} value={x}>
          {t(`vault.type.${x}`)}
        </option>
      ))}
    </select>
  );
}

function UploadSheet({
  file,
  types,
  maxBytes,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  file: File | null;
  types: readonly string[];
  maxBytes: number;
  busy: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (v: { title: string; docType: string; expiresOn: string | null }) => void;
}) {
  const t = useUiText();
  const ids = useId();
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("other");
  const [expires, setExpires] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const name = file?.name ?? null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: restart the form for each chosen file
  useEffect(() => {
    if (!name) return;
    setTitle(name.replace(/\.[^.]+$/, "").slice(0, 200));
    setDocType(types.includes("other") ? "other" : (types[0] ?? "other"));
    setExpires("");
    setProblem(null);
  }, [name]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (!title.trim()) return setProblem(t("vault.error_title_required"));
    if (!declaredMime(file)) return setProblem(t("pathway.error.vault_file_type"));
    if (file.size > maxBytes || file.size === 0) return setProblem(t("pathway.error.vault_file_too_large"));
    setProblem(null);
    onSubmit({ title: title.trim(), docType, expiresOn: expires || null });
  }
  const upErr = error instanceof UploadError ? t(`vault.upload_${error.reason}`) : error ? t(errorKey(error)) : null;
  return (
    <Sheet open={file !== null} onClose={onClose} title={t("vault.upload_title")} handle="vault-upload">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <p className="m-0 text-text-2 text-ui-sm">{file ? `${file.name} · ${sizeText(file.size)}` : ""}</p>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-t`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.title_label")}
          </label>
          <input
            id={`${ids}-t`}
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-k`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.type_label")}
          </label>
          <TypeSelect id={`${ids}-k`} value={docType} types={types} onChange={setDocType} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-e`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.expiry_label")}
          </label>
          <input
            id={`${ids}-e`}
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
        </div>
        <p className="m-0 flex items-start gap-2 text-text-3 text-ui-sm">
          <Lock aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
          {t("vault.banner")}
        </p>
        {problem || upErr ? (
          <p role="alert" className="m-0 text-amber">
            {problem ?? upErr}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" disabled={busy}>
            {busy ? t("vault.uploading") : t("vault.save")}
          </Button>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {t("pathway.cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function EditSheet({
  doc,
  types,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  doc: VaultDocOut | null;
  types: readonly string[];
  busy: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (v: { title: string; doc_type: string; expires_on: string | null }) => void;
}) {
  const t = useUiText();
  const ids = useId();
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("other");
  const [expires, setExpires] = useState("");
  const id = doc?.id ?? null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: restart the form for each document
  useEffect(() => {
    if (!doc) return;
    setTitle(doc.title);
    setDocType(doc.doc_type);
    setExpires(doc.expires_on ?? "");
  }, [id]);
  return (
    <Sheet open={doc !== null} onClose={onClose} title={t("vault.edit_title")} handle="vault-edit">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) onSubmit({ title: title.trim(), doc_type: docType, expires_on: expires || null });
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-t`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.title_label")}
          </label>
          <input
            id={`${ids}-t`}
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-k`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.type_label")}
          </label>
          <TypeSelect id={`${ids}-k`} value={docType} types={types} onChange={setDocType} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-e`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("vault.expiry_label")}
          </label>
          <input
            id={`${ids}-e`}
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
        </div>
        {error ? (
          <p role="alert" className="m-0 text-amber">
            {t(errorKey(error))}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" disabled={busy}>
            {t("vault.save")}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t("pathway.cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
