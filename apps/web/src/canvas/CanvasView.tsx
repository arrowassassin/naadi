import { cx, ErrorState, Skeleton } from "@naadi/ui";
import {
  ChartColumn,
  FileText,
  Film,
  ImagePlus,
  type LucideIcon,
  MessageCircle,
  PanelRight,
  Table2,
  Workflow,
  X,
} from "lucide-react";
import { type ComponentType, lazy, Suspense, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useKnownCopy } from "../assistant/copy";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import {
  type CanvasKind,
  type CanvasKindProps,
  type CanvasRef,
  closeCanvas,
  closeCanvasItem,
  focusCanvasItem,
  getCanvasKind,
  updateCanvasItem,
  usableKind,
  useCanvas,
  useCanvasKindsVersion,
} from "./registry";

/*
 * The canvas chrome (registry contract): tabs, close, violet (assistant) / paper (student) framing, and the
 * shared error and loading states. Kinds only render their content.
 */

const lazyByKind = new WeakMap<CanvasKind, ComponentType<CanvasKindProps>>();

function lazyFor(kind: CanvasKind): ComponentType<CanvasKindProps> {
  let C = lazyByKind.get(kind);
  if (!C) {
    C = lazy(kind.load);
    lazyByKind.set(kind, C);
  }
  return C;
}

/** Icons the registered kinds name (lucide names); an unknown name falls back to the panel icon. */
const KIND_ICONS: Record<string, LucideIcon> = {
  "file-text": FileText,
  workflow: Workflow,
  "table-2": Table2,
  "chart-column": ChartColumn,
  film: Film,
  "image-plus": ImagePlus,
};

function KindIcon({ name, size = 16 }: { name: string; size?: number }) {
  const Icon = KIND_ICONS[name] ?? PanelRight;
  return <Icon size={size} aria-hidden={true} />;
}

export function useFeatures(): Record<string, boolean> {
  const me = useMe();
  return me.data?.features ?? {};
}

export function useCanvasTitle(): (item: CanvasRef) => string {
  const t = useUiText();
  const copy = useKnownCopy();
  return (item) => {
    if (item.title_key) {
      const c = copy(item.title_key);
      if (c) return c;
    }
    if (item.title) return item.title;
    const kind = getCanvasKind(item.kind);
    return t(kind?.label_key ?? "canvas.untitled");
  };
}

/** One canvas item: its kind validated and lazily loaded; invalid or unknown → the shared error state. */
export function CanvasItemView({ item }: { item: CanvasRef }) {
  const t = useUiText();
  const features = useFeatures();
  useCanvasKindsVersion();
  const kind = usableKind(item.kind, features);
  if (!kind) {
    return <ErrorState live={false} title={t("canvas.unavailable_title")} message={t("canvas.unavailable")} />;
  }
  const parsed = kind.schema.safeParse(item.data);
  if (!parsed.success) {
    return <ErrorState live={false} title={t("canvas.invalid_title")} message={t("canvas.invalid")} />;
  }
  const Component = lazyFor(kind);
  return (
    <div
      data-canvas-item={item.key}
      data-canvas-kind={item.kind}
      data-origin={item.origin}
      className={cx(
        "flex min-h-0 flex-1 flex-col gap-3 rounded-block p-4",
        item.origin === "student" ? "bg-paper text-ground" : "border border-violet-line bg-violet-ground text-text",
      )}
    >
      <span
        className={cx(
          "font-mono text-label-sm uppercase",
          item.origin === "student" ? "text-paper-text-2" : "text-violet-text",
        )}
      >
        {t(item.origin === "student" ? "canvas.origin_student" : "canvas.origin_assistant")}
      </span>
      <Suspense fallback={<Skeleton className="h-48 w-full rounded-block" />}>
        <Component
          item={{ ...item, data: parsed.data as Record<string, unknown> }}
          close={() => closeCanvasItem(item.key)}
          update={(next) => updateCanvasItem(item.key, next)}
        />
      </Suspense>
    </div>
  );
}

export interface CanvasTabsProps {
  items: CanvasRef[];
  active: string | null;
  /** The chat tab (side panel layout), shown first when given. */
  chat?: { selected: boolean; onSelect: () => void; label: string };
  onSelect: (key: string) => void;
}

export function CanvasTabs({ items, active, chat, onSelect }: CanvasTabsProps) {
  const t = useUiText();
  const title = useCanvasTitle();
  const features = useFeatures();
  return (
    <div
      role="toolbar"
      aria-label={t("canvas.tabs")}
      className="flex min-w-0 gap-1 overflow-x-auto px-2 py-1.5 [-webkit-overflow-scrolling:touch]"
    >
      {chat ? (
        <button
          type="button"
          aria-pressed={chat.selected}
          onClick={chat.onSelect}
          className={cx(
            "flex min-h-11 shrink-0 items-center gap-2 rounded-pill px-3 text-ui-sm",
            chat.selected ? "bg-paper font-semibold text-ground" : "text-text-2 hover:bg-surface-2",
          )}
        >
          <MessageCircle size={16} aria-hidden={true} />
          {chat.label}
        </button>
      ) : null}
      {items.map((item) => {
        const selected = !chat?.selected && active === item.key;
        const kind = usableKind(item.kind, features);
        const name = title(item);
        return (
          <span
            key={item.key}
            className={cx(
              "flex min-h-11 shrink-0 items-center rounded-pill",
              selected ? "bg-paper text-ground" : "text-text-2 hover:bg-surface-2",
            )}
          >
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onSelect(item.key)}
              className={cx(
                "flex min-h-11 max-w-[14rem] items-center gap-2 pl-3 text-ui-sm",
                selected && "font-semibold",
              )}
            >
              <KindIcon name={kind?.icon ?? "panel-right"} />
              <span className="truncate">{name}</span>
            </button>
            <button
              type="button"
              onClick={() => closeCanvasItem(item.key)}
              aria-label={t("canvas.close_item", { title: name })}
              className="flex size-11 items-center justify-center rounded-pill"
            >
              <X size={14} aria-hidden={true} />
            </button>
          </span>
        );
      })}
    </div>
  );
}

/**
 * The canvas as a full-screen sheet (iPad portrait, phone): over everything, modal (the rest of the app is
 * inert while it is open), Escape closes it.
 */
export function CanvasSheet() {
  const t = useUiText();
  const { items, active } = useCanvas();
  const ref = useRef<HTMLDivElement>(null);
  const open = items.length > 0;
  const current = items.find((i) => i.key === active) ?? items[0];

  useEffect(() => {
    if (!open) return;
    const shell = document.querySelector<HTMLElement>("[data-shell]");
    const before = document.activeElement as HTMLElement | null;
    shell?.setAttribute("inert", "");
    ref.current?.focus();
    return () => {
      shell?.removeAttribute("inert");
      before?.focus?.();
    };
  }, [open]);

  if (!open || !current) return null;
  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-label={t("canvas.title")}
      tabIndex={-1}
      data-canvas="sheet"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          closeCanvas();
        }
      }}
      className="fixed inset-0 z-50 flex flex-col bg-ground pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] outline-none"
    >
      <header className="flex items-center gap-2 border-line-soft border-b px-2 py-1">
        <div className="min-w-0 flex-1">
          <CanvasTabs items={items} active={current.key} onSelect={focusCanvasItem} />
        </div>
        <button
          type="button"
          onClick={closeCanvas}
          aria-label={t("canvas.close")}
          className="flex size-12 shrink-0 items-center justify-center rounded-pill border border-line-control text-text"
        >
          <X size={18} aria-hidden={true} />
        </button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 tablet:p-6">
        <CanvasItemView key={current.key} item={current} />
      </div>
    </div>,
    document.body,
  );
}
