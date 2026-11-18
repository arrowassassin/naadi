import { cx } from "@naadi/ui";
import { useEffect, useRef } from "react";
import { useUiText } from "../copy/useUiText";
import { PageInk } from "../notebooks/PageInk";

/**
 * Handwriting on a note (the page-notes panel's Write tab): the notebook page component (WEB-B), i.e. the
 * `notebook_page` ink layer keyed by the note id on ruled paper. Leaving the tab turns ink off, so the Reader shows
 * its page bar again (ink's lime on/off control takes the bar's place while it is on: one lime action per view).
 */
export function NoteInk({
  noteId,
  on,
  onToggle,
  offline,
  pending,
  className,
}: {
  noteId: string;
  on: boolean;
  onToggle: (on: boolean) => void;
  offline?: boolean;
  pending?: boolean;
  className?: string;
}) {
  const t = useUiText();
  const toggleRef = useRef(onToggle);
  toggleRef.current = onToggle;
  useEffect(() => () => toggleRef.current(false), []);
  return (
    <PageInk
      noteId={noteId}
      paper="ruled"
      on={on}
      onToggle={onToggle}
      label={t("notes.panel_ink_surface")}
      offline={offline}
      pending={pending}
      // The panel is all paper: the save state reads in paper's secondary text colour (contrast on paper).
      className={cx("[&_[data-ink-save]]:text-paper-text-2!", className)}
    />
  );
}
