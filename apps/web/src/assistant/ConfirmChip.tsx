import { Button, cx } from "@naadi/ui";
import { Check, Minus, Pencil, Plus, Trash2, X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { ContentRenderer } from "../content/ContentRenderer";
import { useUiText } from "../copy/useUiText";
import { DocEditor } from "./DocEditor";
import type {
  CardItem,
  ConfirmRequest,
  EditArgs,
  Preview,
  PreviewOf,
  ResumeValue,
  TipTapDoc,
  TipTapNode,
} from "./protocol";

/*
 * The confirm chip (violet: the assistant proposes, the student decides) shown while a write tool waits on its
 * interrupt. Accept / Edit / Cancel resume the run with {decision}; nothing is written before Accept or a saved
 * Edit, and Cancel leaves no trace (the server's contract). Edit is offered only when the server says the
 * preview is editable AND the protocol defines the edited arguments for that preview (note, cards, plan item,
 * memory).
 */

type T = ReturnType<typeof useUiText>;

const EDITABLE_KINDS = new Set<Preview["kind"]>(["note", "cards", "plan_item", "memory"]);
const INPUT =
  "min-h-12 w-full rounded-inner border border-line-control bg-ground px-3 py-2 text-text text-ui focus-visible:border-violet";

function titleKey(p: Preview): string {
  if (p.kind === "note") return `agent.confirm.note_${p.mode}`;
  return `agent.confirm.${p.kind}`;
}

function NodeView({ node, sectionId }: { node: TipTapNode; sectionId: string }) {
  const t = useUiText();
  return (
    <ContentRenderer
      doc={{ type: "doc", content: [node] }}
      sectionId={sectionId}
      text={t}
      mode="prod"
      headingBase={3}
      className="text-ui!"
    />
  );
}

function NotePreview({ p }: { p: PreviewOf<"note"> }) {
  const t = useUiText();
  return (
    <div className="flex flex-col gap-2">
      {p.anchor.label ? (
        <p className="m-0 text-text-2 text-ui-sm">{t("agent.confirm.note_anchor", { label: p.anchor.label })}</p>
      ) : null}
      <div className="max-h-72 overflow-y-auto rounded-inner border border-violet-line bg-ground/60 p-3">
        <ContentRenderer
          doc={p.doc}
          sectionId="confirm-note"
          text={t}
          mode="prod"
          headingBase={3}
          className="text-ui!"
        />
      </div>
    </div>
  );
}

function CardsPreview({ cards }: { cards: CardItem[] }) {
  return (
    <ol className="m-0 flex max-h-72 list-none flex-col gap-2 overflow-y-auto p-0">
      {cards.map((c, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: cards are positional
        <li key={i} className="flex flex-col gap-1 rounded-inner bg-surface-2 p-3 text-ui">
          <span className="font-semibold text-text">{c.front}</span>
          <span className="text-text-2">{c.back}</span>
        </li>
      ))}
    </ol>
  );
}

function Rows({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-ui-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-violet-text">{k}</dt>
          <dd className="m-0 break-words text-text">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function blockRows(t: T, p: PreviewOf<"block">): [string, string][] {
  const none = t("agent.confirm.any");
  return [
    [t("agent.confirm.block_count"), String(p.count)],
    [t("agent.confirm.block_mode"), t(`agent.confirm.block_mode_${p.mode}`)],
    [t("agent.confirm.block_systems"), p.systems.length ? p.systems.join(", ") : none],
    [t("agent.confirm.block_disciplines"), p.disciplines.length ? p.disciplines.join(", ") : none],
    [t("agent.confirm.block_status"), t(`agent.confirm.block_status_${p.status}`)],
  ];
}

function planRows(t: T, p: PreviewOf<"plan_item">): [string, string][] {
  const rows: [string, string][] = [
    [t("agent.confirm.plan_day"), p.day],
    [t("agent.confirm.plan_title"), p.title],
  ];
  if (p.target_minutes !== null)
    rows.push([t("agent.confirm.plan_minutes"), t("agent.confirm.minutes", { n: String(p.target_minutes) })]);
  return rows;
}

const OP_ICON = { replace: Pencil, insert_after: Plus, delete: Minus } as const;

function ChapterDiff({ p }: { p: PreviewOf<"chapter_diff"> }) {
  const t = useUiText();
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-text-2 text-ui-sm">
        {t(p.target === "revision" ? "agent.confirm.diff_revision" : "agent.confirm.diff_overlay", {
          heading: p.heading,
        })}
      </p>
      <ol className="m-0 flex max-h-96 list-none flex-col gap-3 overflow-y-auto p-0" data-diff="">
        {p.ops.map((op, i) => {
          const Icon = OP_ICON[op.op];
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: ops are positional
            <li key={i} className="flex flex-col gap-2 rounded-inner border border-violet-line p-3">
              <span className="flex items-center gap-2 font-mono text-label-sm text-violet-text uppercase">
                <Icon aria-hidden="true" size={14} />
                {t(`agent.confirm.diff_${op.op}`, { n: String(op.index + 1) })}
              </span>
              {op.before ? (
                <div className="flex flex-col gap-1" data-diff-side="before">
                  <span className="font-mono text-label-sm text-text-3 uppercase">
                    {t("agent.confirm.diff_before")}
                  </span>
                  <div className="rounded-inner border border-dashed border-line-dashed p-2 text-text-2 line-through decoration-text-3">
                    <NodeView node={op.before} sectionId={`diff-${i}-b`} />
                  </div>
                </div>
              ) : null}
              {op.after ? (
                <div className="flex flex-col gap-1" data-diff-side="after">
                  <span className="font-mono text-label-sm text-text-3 uppercase">{t("agent.confirm.diff_after")}</span>
                  <div className="rounded-inner border border-violet-line bg-violet-ground p-2">
                    <NodeView node={op.after} sectionId={`diff-${i}-a`} />
                  </div>
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function PreviewView({ p }: { p: Preview }) {
  const t = useUiText();
  switch (p.kind) {
    case "note":
      return <NotePreview p={p} />;
    case "cards":
      return <CardsPreview cards={p.cards} />;
    case "block":
      return <Rows rows={blockRows(t, p)} />;
    case "plan_item":
      return <Rows rows={planRows(t, p)} />;
    case "chapter_diff":
      return <ChapterDiff p={p} />;
    case "memory":
    case "forget":
      return <p className="m-0 rounded-inner bg-surface-2 p-3 text-text">{p.fact}</p>;
  }
}

function CardsEditor({ cards, onChange }: { cards: CardItem[]; onChange: (c: CardItem[]) => void }) {
  const t = useUiText();
  const id = useId();
  return (
    <ol className="m-0 flex max-h-96 list-none flex-col gap-3 overflow-y-auto p-0">
      {cards.map((c, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: cards are positional
        <li key={i} className="flex flex-col gap-2 rounded-inner bg-surface-2 p-3">
          <label htmlFor={`${id}-f${i}`} className="text-text-2 text-ui-sm">
            {t("agent.confirm.card_front", { n: String(i + 1) })}
          </label>
          <textarea
            id={`${id}-f${i}`}
            value={c.front}
            rows={2}
            onChange={(e) => onChange(cards.map((x, j) => (j === i ? { ...x, front: e.target.value } : x)))}
            className={INPUT}
          />
          <label htmlFor={`${id}-b${i}`} className="text-text-2 text-ui-sm">
            {t("agent.confirm.card_back", { n: String(i + 1) })}
          </label>
          <textarea
            id={`${id}-b${i}`}
            value={c.back}
            rows={2}
            onChange={(e) => onChange(cards.map((x, j) => (j === i ? { ...x, back: e.target.value } : x)))}
            className={INPUT}
          />
          {cards.length > 1 ? (
            <Button
              variant="secondary"
              icon={<Trash2 size={16} />}
              className="self-start"
              onClick={() => onChange(cards.filter((_, j) => j !== i))}
            >
              {t("agent.confirm.card_remove", { n: String(i + 1) })}
            </Button>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** The edit form per preview kind; returns the tool arguments as edited, or null while they are invalid. */
function useEditArgs(p: Preview): { form: ReactNode; args: EditArgs | null } {
  const t = useUiText();
  const id = useId();
  const [doc, setDoc] = useState<TipTapDoc | null>(p.kind === "note" ? p.doc : null);
  const [cards, setCards] = useState<CardItem[]>(p.kind === "cards" ? p.cards : []);
  const [day, setDay] = useState(p.kind === "plan_item" ? p.day : "");
  const [title, setTitle] = useState(p.kind === "plan_item" ? p.title : "");
  const [fact, setFact] = useState(p.kind === "memory" ? p.fact : "");
  switch (p.kind) {
    case "note":
      return {
        form: doc ? <DocEditor doc={doc} onChange={setDoc} label={t("agent.confirm.edit_note")} tone="violet" /> : null,
        args: doc ? { doc } : null,
      };
    case "cards": {
      const valid = cards.length > 0 && cards.every((c) => c.front.trim() && c.back.trim());
      return {
        form: <CardsEditor cards={cards} onChange={setCards} />,
        args: valid ? { cards: cards.map((c) => ({ front: c.front.trim(), back: c.back.trim() })) } : null,
      };
    }
    case "plan_item":
      return {
        form: (
          <div className="flex flex-col gap-3">
            <label htmlFor={`${id}-d`} className="flex flex-col gap-1 text-text-2 text-ui-sm">
              {t("agent.confirm.plan_day")}
              <input
                id={`${id}-d`}
                type="date"
                value={day}
                onChange={(e) => setDay(e.target.value)}
                className={INPUT}
              />
            </label>
            <label htmlFor={`${id}-t`} className="flex flex-col gap-1 text-text-2 text-ui-sm">
              {t("agent.confirm.plan_title")}
              <input
                id={`${id}-t`}
                type="text"
                maxLength={200}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className={INPUT}
              />
            </label>
          </div>
        ),
        args: /^\d{4}-\d{2}-\d{2}$/.test(day) && title.trim() ? { day, title: title.trim() } : null,
      };
    case "memory":
      return {
        form: (
          <label htmlFor={`${id}-m`} className="flex flex-col gap-1 text-text-2 text-ui-sm">
            {t("agent.confirm.memory_fact")}
            <textarea
              id={`${id}-m`}
              rows={3}
              maxLength={500}
              value={fact}
              onChange={(e) => setFact(e.target.value)}
              className={INPUT}
            />
          </label>
        ),
        args: fact.trim() ? { fact: fact.trim() } : null,
      };
    default:
      return { form: null, args: null };
  }
}

function EditForm({
  p,
  busy,
  onSave,
  onBack,
}: {
  p: Preview;
  busy: boolean;
  onSave: (args: EditArgs) => void;
  onBack: () => void;
}) {
  const t = useUiText();
  const { form, args } = useEditArgs(p);
  return (
    <div className="flex flex-col gap-3" data-confirm-edit="">
      {form}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="assistant"
          icon={<Check size={18} />}
          disabled={busy || !args}
          onClick={() => args && onSave(args)}
        >
          {t("agent.confirm.save_edit")}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onBack}>
          {t("agent.confirm.back")}
        </Button>
      </div>
    </div>
  );
}

export function ConfirmChip({
  request,
  busy,
  onResume,
}: {
  request: ConfirmRequest;
  busy: boolean;
  onResume: (v: ResumeValue) => void;
}) {
  const t = useUiText();
  const [editing, setEditing] = useState(false);
  const [sent, setSent] = useState(false);
  const p = request.preview;
  const canEdit = request.editable && EDITABLE_KINDS.has(p.kind);
  const headingId = useId();
  const decide = (v: ResumeValue) => {
    setSent(true);
    onResume(v);
  };
  const off = busy || sent;
  return (
    <section
      aria-labelledby={headingId}
      data-confirm={p.kind}
      data-tool={request.tool}
      className={cx("flex flex-col gap-3 rounded-block border border-violet bg-violet-ground p-4")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="m-0 font-display font-bold text-[1.0625rem] text-text">
          {t(titleKey(p))}
        </h3>
        <span className="inline-flex items-center gap-1.5 rounded-pill border border-violet-line px-2.5 py-1 font-mono text-label-sm text-violet-text uppercase">
          {t("agent.confirm.waiting")}
        </span>
      </div>
      {editing ? (
        <EditForm
          p={p}
          busy={off}
          onSave={(args) => decide({ decision: "edit", args })}
          onBack={() => setEditing(false)}
        />
      ) : (
        <>
          <PreviewView p={p} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="assistant"
              icon={<Check size={18} />}
              disabled={off}
              onClick={() => decide({ decision: "accept" })}
            >
              {t("agent.confirm.accept")}
            </Button>
            {canEdit ? (
              <Button variant="secondary" icon={<Pencil size={16} />} disabled={off} onClick={() => setEditing(true)}>
                {t("agent.confirm.edit")}
              </Button>
            ) : null}
            <Button
              variant="secondary"
              icon={<X size={16} />}
              disabled={off}
              onClick={() => decide({ decision: "cancel" })}
            >
              {t("agent.confirm.cancel")}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
