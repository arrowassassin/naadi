import { Button } from "@naadi/ui";
import { type FormEvent, useId, useMemo, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { errorKey as noteErrorKey, useNotes, useNoteWriter, usePendingOps, useUserId } from "../notes/api";
import { formatClock } from "./format";

export interface VideoNotesProps {
  mediaId: string;
  /** Whole seconds of the playhead right now. */
  getTime: () => number;
  seek: (seconds: number) => void;
}

function textOf(body: unknown): string {
  const out: string[] = [];
  const walk = (n: unknown) => {
    if (!n || typeof n !== "object") return;
    const node = n as { text?: unknown; content?: unknown };
    if (typeof node.text === "string") out.push(node.text);
    if (Array.isArray(node.content)) for (const c of node.content) walk(c);
    if (Array.isArray(node.content) && node.content.length > 1) out.push(" ");
  };
  walk(body);
  return out.join("").trim();
}

function toDoc(text: string) {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
  return {
    type: "doc",
    content: paragraphs.map((p) => ({ type: "paragraph", content: [{ type: "text", text: p }] })),
  };
}

const FILTERS = { anchor_type: "video", kind: "note", limit: 200 } as const;

/** Notes taken at moments of one video: "Note at 12:22", listed by time, tap to jump there. */
export function VideoNotes({ mediaId, getTime, seek }: VideoNotesProps) {
  const t = useUiText();
  const userId = useUserId();
  const ids = useId();
  const [draftT, setDraftT] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const filters = useMemo(() => ({ ...FILTERS, anchor_id: mediaId }), [mediaId]);
  const list = useNotes(filters, { enabled: userId !== null });
  const pending = usePendingOps();
  const writer = useNoteWriter();

  if (userId === null) {
    return (
      <p data-testid="video-notes-signin" className="m-0 text-text-2 text-ui-sm">
        {t("video.note_sign_in")}
      </p>
    );
  }

  const notes = list.items
    .filter((n) => n.anchor_type === "video" && n.anchor_id === mediaId)
    .map((n) => ({
      id: n.id,
      t: (n.anchor_pos as { t?: number } | null)?.t ?? 0,
      text: n.body_text || n.excerpt || textOf(n.body),
    }))
    .sort((a, b) => a.t - b.t);
  const waiting = new Set(pending.filter((o) => !o.refused).map((o) => o.id));

  function save(e: FormEvent) {
    e.preventDefault();
    if (draftT === null) return;
    try {
      const body = text.trim() ? toDoc(text) : null;
      writer.create({ kind: "note", anchor_type: "video", anchor_id: mediaId, anchor_pos: { t: draftT }, body });
      setDraftT(null);
      setText("");
      setFailed(null);
    } catch (err) {
      setFailed(noteErrorKey(err));
    }
  }

  return (
    <div data-testid="video-notes" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="m-0 font-semibold text-ui">{t("video.notes")}</h3>
        {draftT === null ? (
          <Button
            variant="secondary"
            onClick={() => {
              setDraftT(getTime());
              setText("");
            }}
          >
            {t("video.note_at", { time: formatClock(getTime()) })}
          </Button>
        ) : null}
      </div>
      {draftT !== null ? (
        <form onSubmit={save} className="flex flex-col gap-3 rounded-block border border-line bg-surface-2 p-3">
          <label htmlFor={`${ids}-text`} className="font-mono text-label-sm text-text-2">
            {t("video.note_at", { time: formatClock(draftT) })}
          </label>
          <textarea
            id={`${ids}-text`}
            value={text}
            rows={3}
            maxLength={2000}
            placeholder={t("video.note_placeholder")}
            onChange={(e) => setText(e.target.value)}
            className="w-full rounded-block border border-line-control bg-ground px-3 py-2 text-ui text-text placeholder:text-text-3"
          />
          {failed ? (
            <p role="alert" className="m-0 text-amber text-ui-sm">
              {t("video.note_failed")}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="strong">
              {t("video.note_save")}
            </Button>
            <Button type="button" variant="secondary" onClick={() => setDraftT(null)}>
              {t("video.note_cancel")}
            </Button>
          </div>
        </form>
      ) : null}
      {notes.length === 0 ? (
        <p className="m-0 text-text-2 text-ui-sm">{t("video.notes_empty")}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {notes.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                data-note-t={n.t}
                onClick={() => seek(n.t)}
                title={t("video.note_go", { time: formatClock(n.t) })}
                className="flex min-h-11 w-full items-start gap-3 rounded-block border border-line bg-surface-1 px-3 py-2 text-left text-text hover:border-text-3"
              >
                <span className="shrink-0 font-mono text-label-sm text-text-2">{formatClock(n.t)}</span>
                <span className="min-w-0 flex-1 break-words text-ui-sm">{n.text}</span>
                {waiting.has(n.id) ? (
                  <span className="shrink-0 font-mono text-label-sm text-text-3">{t("video.note_saving")}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
