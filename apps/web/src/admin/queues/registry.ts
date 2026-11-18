/**
 * Approval-queue views (contract: ledger 20261117T042408-seg19-admin-91a115 + amendment 0e47ff).
 *
 * A segment that registers a server queue (`register_queue(kind, ...)` in naadi_api/admin/queues.py) registers
 * its web view here from a module under its own source root, and adds one import line to ./views/index.ts.
 * Kinds without a view render the generic row (title, created time, deep link). Every string a view shows is a
 * ui_copy key `admin.queues.<kind>.*`.
 */
import type { ComponentType } from "react";
import type { QueueItem } from "../api";

export type { QueueItem };

export interface QueueViewProps {
  item: QueueItem;
}

/** Props of an approve form for kinds whose approve takes `params` (amendment: e.g. generated_media). */
export interface QueueApproveFormProps {
  item: QueueItem;
  /** Submit the decision with the kind's params (validated by the server's approve_fn). */
  onSubmit: (params: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
}

export interface QueueView {
  /** ui_copy key of the queue's name: admin.queues.<kind>.title (the registering segment adds it). */
  titleKey: string;
  /** Compact row in the list (structured detail -> ui_copy). */
  Row: ComponentType<QueueViewProps>;
  /** Optional expanded preview of the focused item (IFrame player, question preview). */
  Detail?: ComponentType<QueueViewProps>;
  /** Reject requires a note (default true). */
  rejectNeedsNote?: boolean;
  /** Optional: approving opens this form, and its params go with the approve call. */
  ApproveForm?: ComponentType<QueueApproveFormProps>;
}

const KIND = /^[a-z][a-z0-9_]{1,47}$/;
const views = new Map<string, QueueView>();

/** Register the view for one queue kind. Throws on an invalid kind or a second registration of the same kind. */
export function registerQueueView(kind: string, view: QueueView): void {
  if (!KIND.test(kind)) throw new Error(`registerQueueView: invalid queue kind "${kind}"`);
  if (views.has(kind)) throw new Error(`registerQueueView: "${kind}" is already registered`);
  if (!view || typeof view.Row !== "function" || typeof view.titleKey !== "string" || !view.titleKey) {
    throw new Error(`registerQueueView: "${kind}" needs a titleKey and a Row component`);
  }
  views.set(kind, Object.freeze({ ...view }));
}

/** The registered view of a kind, or null (the hub then renders the generic row). */
export function queueView(kind: string): QueueView | null {
  return views.get(kind) ?? null;
}

/** Every kind with a registered view. */
export function registeredQueueViews(): string[] {
  return [...views.keys()].sort();
}

/** Whether reject needs a note for this kind (default true, also for kinds without a view). */
export function rejectNeedsNote(kind: string): boolean {
  return views.get(kind)?.rejectNeedsNote ?? true;
}

/** Test hook: forget one registration. */
export function unregisterQueueView(kind: string): void {
  views.delete(kind);
}
