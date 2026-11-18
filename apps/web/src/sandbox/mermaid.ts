/**
 * Runs inside the sandboxed diagram iframe (sandbox/mermaid.html). The parent posts
 * `{ type: "naadi:mermaid:render", id, code }`; this renders it with Mermaid and posts back
 * `{ type: "naadi:mermaid:result", id, svg }` or `{ ..., error }`. The parent sanitises the SVG before it
 * touches the app's DOM, so nothing here is trusted.
 */
import mermaid from "mermaid";

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "dark",
  fontFamily: "Inter, system-ui, sans-serif",
  htmlLabels: false,
  flowchart: { htmlLabels: false },
});

interface RenderRequest {
  type: "naadi:mermaid:render";
  id: string;
  code: string;
}

function isRequest(v: unknown): v is RenderRequest {
  const r = v as Partial<RenderRequest> | null;
  return (
    typeof r === "object" &&
    r !== null &&
    r.type === "naadi:mermaid:render" &&
    typeof r.id === "string" &&
    /^[a-z][a-z0-9-]{0,63}$/.test(r.id) &&
    typeof r.code === "string" &&
    r.code.length <= 20_000
  );
}

// Renders run one at a time: Mermaid keeps global layout state.
let queue: Promise<void> = Promise.resolve();

window.addEventListener("message", (event: MessageEvent) => {
  if (event.source !== window.parent || !isRequest(event.data)) return;
  const { id, code } = event.data;
  queue = queue.then(async () => {
    try {
      const { svg } = await mermaid.render(id, code);
      window.parent.postMessage({ type: "naadi:mermaid:result", id, svg }, "*");
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      window.parent.postMessage({ type: "naadi:mermaid:result", id, error }, "*");
    } finally {
      document.getElementById(id)?.remove();
      document.getElementById(`d${id}`)?.remove();
    }
  });
});

window.parent.postMessage({ type: "naadi:mermaid:ready" }, "*");
