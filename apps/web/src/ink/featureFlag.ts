/**
 * The ink lab (/system/ink-lab) is a developer tool: on in the Vite dev server, and in builds made with
 * VITE_FEATURE_INK_LAB=true (the e2e build). The Space's production build leaves it unset, so the route
 * does not exist there and its chunk is never shipped to a browser. When the server exposes feature
 * flags plus the admin role (segment 03), the gate moves to `features.ink_lab && is_admin`.
 */
export function inkLabEnabled(): boolean {
  return import.meta.env.DEV || import.meta.env.VITE_FEATURE_INK_LAB === "true";
}
