import type { LibraryOut } from "@naadi/shared";
import { Button, cx } from "@naadi/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, ClipboardPaste, Link as LinkIcon, Upload, WifiOff } from "lucide-react";
import { type DragEvent, type FormEvent, useId, useRef, useState } from "react";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { count } from "../learn/format";
import { ingestErrorKey, libraryKey, requestErrorKey, useAddLink, useAddPaste } from "./api";
import { type Checked, checkFiles, uploadFiles } from "./upload";

export interface Refused {
  key: string;
  name: string;
  reason: string;
}

const FIELD =
  "min-h-12 w-full min-w-0 rounded-inner border border-line-control bg-surface-2 px-4 text-text placeholder:text-text-3 focus-visible:outline-2 focus-visible:outline-offset-2";

function isHttps(raw: string): boolean {
  try {
    const u = new URL(raw.trim());
    return u.protocol === "https:" && Boolean(u.hostname);
  } catch {
    return false;
  }
}

/**
 * The Library's intake (segment 15, Library board): a drop zone with a file picker (several files at once), the
 * camera (`capture`), a link field (https only) and a paste box. Uploads need a connection: offline, the whole
 * intake is disabled with the reason shown. `primary` makes "Choose files" the view's one lime action.
 */
export function Intake({ library, primary }: { library: LibraryOut; primary: boolean }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const auth = useAuth();
  const qc = useQueryClient();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const pick = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const id = useId();
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<Refused[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<"none" | "link" | "paste">("none");
  const [link, setLink] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const addLink = useAddLink();
  const addPaste = useAddPaste();
  const enabled = library.limits.enabled;
  const disabled = !online || !enabled || busy;

  async function take(list: FileList | null) {
    const files = list ? Array.from(list) : [];
    if (!files.length || !online || !enabled) return;
    setBusy(true);
    setProblem(null);
    setNotice(null);
    try {
      const checked = await checkFiles(files, library.limits);
      const accepted: Checked[] = checked.filter((c) => c.refusal === null);
      setRefused(
        checked
          .filter((c) => c.refusal !== null)
          .map((c, i) => ({
            key: `${i}-${c.file.name}`,
            name: c.file.name,
            reason: t(ingestErrorKey(c.refusal), { max: String(library.limits.max_file_mb) }),
          })),
      );
      if (!accepted.length) return;
      const out = await uploadFiles(accepted, () => qc.invalidateQueries({ queryKey: libraryKey(userId) }));
      if (out.failed)
        setProblem(t("library.upload_some_failed", { files: count(t, "library.count_file", out.failed) }));
      else setNotice(t("library.upload_started", { files: count(t, "library.count_file", out.uploaded) }));
    } catch (e) {
      setProblem(t(requestErrorKey(e), { max: String(library.limits.max_file_mb) }));
    } finally {
      setBusy(false);
      void qc.invalidateQueries({ queryKey: libraryKey(userId) });
      if (pick.current) pick.current.value = "";
      if (camera.current) camera.current.value = "";
    }
  }

  function onDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setDrag(false);
    if (!disabled) void take(e.dataTransfer.files);
  }

  async function submitLink(e: FormEvent) {
    e.preventDefault();
    if (!isHttps(link)) {
      setLinkError(t("library.link_https_only"));
      return;
    }
    setLinkError(null);
    try {
      await addLink.mutateAsync(link.trim());
      setLink("");
      setNotice(t("library.link_added"));
      setMode("none");
    } catch (err) {
      setLinkError(t(requestErrorKey(err)));
    }
  }

  async function submitPaste(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      await addPaste.mutateAsync({ text, title: title.trim() || null });
      setText("");
      setTitle("");
      setNotice(t("library.paste_added"));
      setMode("none");
    } catch (err) {
      addPaste.reset();
      setNotice(null);
      setProblem(t(requestErrorKey(err), { max: String(library.limits.max_paste_chars) }));
    }
  }

  const reason = !online ? t("library.offline") : !enabled ? t("library.intake_paused") : null;

  return (
    <section
      aria-labelledby={`${id}-h`}
      className={cx(
        "relative flex min-h-[260px] min-w-0 flex-[2_1_480px] flex-col justify-between gap-5 rounded-tile-sm border-2 border-dashed p-6 tablet:rounded-tile tablet:p-8",
        drag && !disabled ? "border-text bg-surface-1" : "border-line-dashed",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      data-drop-zone=""
      aria-busy={busy || undefined}
    >
      <h2
        id={`${id}-h`}
        className="m-0 font-bold font-display text-[clamp(1.75rem,1.1rem+2.2vw,2.75rem)] leading-[1.02] tracking-[-0.03em]"
      >
        {t("library.drop_title")}
      </h2>
      <p className="m-0 text-text-2 text-ui-sm">
        {t("library.drop_hint", { max: String(library.limits.max_file_mb) })}
      </p>
      {reason ? (
        <p role="status" className="m-0 flex items-center gap-2 text-text-2" data-intake-disabled="">
          <WifiOff aria-hidden="true" size={16} className="shrink-0" />
          {reason}
        </p>
      ) : null}
      <input
        ref={pick}
        id={`${id}-files`}
        type="file"
        multiple
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        disabled={disabled}
        onChange={(e) => void take(e.currentTarget.files)}
        data-testid="library-file-input"
      />
      <input
        ref={camera}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        disabled={disabled}
        onChange={(e) => void take(e.currentTarget.files)}
        data-testid="library-camera-input"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={primary ? "primary" : "strong"}
          size="cta"
          icon={<Upload size={18} />}
          disabled={disabled}
          onClick={() => pick.current?.click()}
        >
          {busy ? t("library.uploading") : t("library.choose_files")}
        </Button>
        <Button
          variant="secondary"
          icon={<LinkIcon size={16} />}
          disabled={disabled}
          aria-expanded={mode === "link"}
          aria-controls={mode === "link" ? `${id}-link` : undefined}
          onClick={() => setMode(mode === "link" ? "none" : "link")}
        >
          {t("library.paste_link")}
        </Button>
        <Button
          variant="secondary"
          icon={<ClipboardPaste size={16} />}
          disabled={disabled}
          aria-expanded={mode === "paste"}
          aria-controls={mode === "paste" ? `${id}-paste` : undefined}
          onClick={() => setMode(mode === "paste" ? "none" : "paste")}
        >
          {t("library.paste_text")}
        </Button>
        <Button
          variant="secondary"
          icon={<Camera size={16} />}
          disabled={disabled}
          onClick={() => camera.current?.click()}
        >
          {t("library.camera")}
        </Button>
      </div>

      {mode === "link" ? (
        <form id={`${id}-link`} onSubmit={(e) => void submitLink(e)} className="flex flex-col gap-2" noValidate>
          <label htmlFor={`${id}-url`} className="font-semibold text-ui-sm">
            {t("library.link_label")}
          </label>
          <div className="flex flex-wrap gap-2">
            <input
              id={`${id}-url`}
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={link}
              onChange={(e) => setLink(e.currentTarget.value)}
              placeholder={t("library.link_placeholder")}
              aria-invalid={linkError ? true : undefined}
              aria-describedby={linkError ? `${id}-url-error` : undefined}
              className={cx(FIELD, "flex-[1_1_16rem]")}
              maxLength={2048}
            />
            <Button type="submit" variant="strong" disabled={addLink.isPending || !link.trim()}>
              {t("library.link_add")}
            </Button>
          </div>
          {linkError ? (
            <p id={`${id}-url-error`} role="alert" className="m-0 text-amber text-ui-sm">
              {linkError}
            </p>
          ) : null}
        </form>
      ) : null}

      {mode === "paste" ? (
        <form id={`${id}-paste`} onSubmit={(e) => void submitPaste(e)} className="flex flex-col gap-2">
          <label htmlFor={`${id}-title`} className="font-semibold text-ui-sm">
            {t("library.paste_title_label")}
          </label>
          <input
            id={`${id}-title`}
            type="text"
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.currentTarget.value)}
            placeholder={t("library.paste_title_placeholder")}
            className={FIELD}
          />
          <label htmlFor={`${id}-text`} className="font-semibold text-ui-sm">
            {t("library.paste_label")}
          </label>
          <textarea
            id={`${id}-text`}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            maxLength={library.limits.max_paste_chars}
            rows={6}
            className={cx(FIELD, "min-h-36 py-3")}
          />
          <div>
            <Button type="submit" variant="strong" disabled={addPaste.isPending || !text.trim()}>
              {t("library.paste_add")}
            </Button>
          </div>
        </form>
      ) : null}

      <div aria-live="polite" className="flex flex-col gap-2 empty:hidden">
        {notice ? <p className="m-0 text-text-2 text-ui-sm">{notice}</p> : null}
        {problem ? (
          <p role="alert" className="m-0 text-amber text-ui-sm">
            {problem}
          </p>
        ) : null}
        {refused.length ? (
          <ul aria-label={t("library.refused_title")} className="m-0 flex list-none flex-col gap-1 p-0">
            {refused.map((r) => (
              <li key={r.key} className="text-amber text-ui-sm [overflow-wrap:anywhere]">
                {t("library.refused_line", { name: r.name, reason: r.reason })}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
