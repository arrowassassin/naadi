import { Button, Chip } from "@naadi/ui";
import { ImageOff, Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { type SectionChoice, SectionPicker } from "../../components/SectionPicker";
import { fmtSeconds, LABEL, TEXTAREA, useT } from "../../components/ui";
import { type QueueApproveFormProps, type QueueViewProps, registerQueueView } from "../registry";

/*
 * generated_media (moved from S15b): an AI-generated image or video a student made, waiting for an admin to
 * publish it into a section. Always labelled as AI-generated (violet = the assistant's work).
 * detail: {media_kind: image|video, prompt, width, height, duration_s, ai_generated, preview_url?}
 */

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function AiLabel() {
  const t = useT();
  return (
    <Chip tone="assistant" icon={<Sparkles size={13} />}>
      {t("admin.queues.generated_media.ai_label")}
    </Chip>
  );
}

function Row({ item }: QueueViewProps) {
  const t = useT();
  const kind = str(item.detail?.media_kind);
  const w = num(item.detail?.width);
  const h = num(item.detail?.height);
  const d = num(item.detail?.duration_s);
  return (
    <span className="flex min-w-0 flex-col gap-1.5">
      <span className="break-words font-semibold text-text">
        {item.title ?? t("admin.queues.generated_media.untitled")}
      </span>
      <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
        <AiLabel />
        <span>{t(kind === "video" ? "admin.queues.generated_media.video" : "admin.queues.generated_media.image")}</span>
        {w && h ? <span className="font-mono text-label-sm">{`${w}×${h}`}</span> : null}
        {d ? <span>{fmtSeconds(Math.round(d))}</span> : null}
      </span>
    </span>
  );
}

function Detail({ item }: QueueViewProps) {
  const t = useT();
  const url = str(item.detail?.preview_url);
  const kind = str(item.detail?.media_kind);
  const prompt = str(item.detail?.prompt);
  const w = num(item.detail?.width) ?? 4;
  const h = num(item.detail?.height) ?? 3;
  return (
    <div className="flex flex-col gap-3">
      <AiLabel />
      {url ? (
        kind === "video" ? (
          // biome-ignore lint/a11y/useMediaCaption: AI-generated clips have no speech track
          <video
            controls
            preload="metadata"
            playsInline
            src={url}
            className="aspect-video w-full rounded-block border border-line bg-surface-2"
          />
        ) : (
          <img
            src={url}
            alt={item.title ?? t("admin.queues.generated_media.untitled")}
            width={w}
            height={h}
            className="block h-auto max-h-[60vh] w-full rounded-block border border-line bg-white object-contain"
          />
        )
      ) : (
        <p className="m-0 flex items-center gap-2 rounded-block border border-dashed border-line-dashed p-4 text-text-2">
          <ImageOff aria-hidden="true" size={18} />
          {t("admin.queues.generated_media.no_preview")}
        </p>
      )}
      {prompt ? (
        <p className="m-0 text-ui-sm text-text-2">
          <span className="font-mono text-label-sm text-text-3 uppercase">
            {t("admin.queues.generated_media.prompt")}
          </span>
          <br />
          {prompt}
        </p>
      ) : null}
    </div>
  );
}

function ApproveForm({ item, onSubmit, onCancel, busy }: QueueApproveFormProps) {
  const t = useT();
  const id = useId();
  const [exam, setExam] = useState("step1");
  const [choice, setChoice] = useState<SectionChoice>({ chapterId: "", sectionId: "" });
  const [caption, setCaption] = useState(item.title ?? "");
  const [tried, setTried] = useState(false);
  return (
    <form
      noValidate
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (!choice.sectionId || !caption.trim() || busy) return;
        onSubmit({ section_id: choice.sectionId, caption: caption.trim() });
      }}
    >
      <p className="m-0 text-text-2">{t("admin.queues.generated_media.approve_body")}</p>
      <SectionPicker value={choice} onChange={setChoice} exam={exam} onExam={setExam} />
      {tried && !choice.sectionId ? (
        <span className="text-ui-sm text-amber">{t("admin.queues.generated_media.section_required")}</span>
      ) : null}
      <label htmlFor={id} className={LABEL}>
        {t("admin.field.caption")}
        <textarea
          id={id}
          className={TEXTAREA}
          maxLength={1000}
          value={caption}
          aria-invalid={(tried && !caption.trim()) || undefined}
          onChange={(e) => setCaption(e.target.value)}
        />
        {tried && !caption.trim() ? (
          <span className="text-amber">{t("admin.queues.generated_media.caption_required")}</span>
        ) : null}
      </label>
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="strong" disabled={busy}>
          {t(busy ? "admin.action.saving" : "admin.queues.generated_media.publish")}
        </Button>
        <Button onClick={onCancel}>{t("admin.action.cancel")}</Button>
      </div>
    </form>
  );
}

registerQueueView("generated_media", {
  titleKey: "admin.queues.generated_media.title",
  Row,
  Detail,
  ApproveForm,
  rejectNeedsNote: true,
});
