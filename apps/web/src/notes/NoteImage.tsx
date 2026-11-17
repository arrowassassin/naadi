import { NodeViewWrapper, type ReactNodeViewProps } from "@tiptap/react";
import { ImageOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { signedImageUrl } from "./storage";

/** An image in a note: signed on demand from its storage path (private bucket), with loading and failed states. */
export function NoteImageView({ node, selected }: ReactNodeViewProps) {
  const t = useUiText();
  const path = typeof node.attrs.path === "string" ? node.attrs.path : null;
  const alt = typeof node.attrs.alt === "string" && node.attrs.alt ? node.attrs.alt : t("notes.image_alt");
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false });

  useEffect(() => {
    let alive = true;
    setState({ url: null, failed: false });
    if (!path) {
      setState({ url: null, failed: true });
      return;
    }
    void signedImageUrl(path).then(
      (url) => alive && setState({ url, failed: url === null }),
      () => alive && setState({ url: null, failed: true }),
    );
    return () => {
      alive = false;
    };
  }, [path]);

  return (
    <NodeViewWrapper
      as="figure"
      data-note-image=""
      className={`my-3 overflow-hidden rounded-block-sm border-2 ${selected ? "border-ground" : "border-transparent"}`}
    >
      {state.url ? (
        <img src={state.url} alt={alt} className="block h-auto max-w-full" draggable={false} />
      ) : (
        <div
          role="img"
          aria-label={state.failed ? t("notes.image_failed") : t("notes.image_loading")}
          className="flex aspect-[4/3] items-center justify-center gap-2 border border-paper-text-2 border-dashed text-paper-text-2 text-ui-sm"
        >
          {state.failed ? <ImageOff aria-hidden="true" size={18} /> : null}
          <span aria-hidden="true">{state.failed ? t("notes.image_failed") : t("notes.image_loading")}</span>
        </div>
      )}
    </NodeViewWrapper>
  );
}
