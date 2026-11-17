import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { onAuthChange, startAuth } from "./auth/session";
import { router } from "./router";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: true, staleTime: 15_000 },
  },
});

// Sign-in, sign-out or a different user: drop every cached query (never show one user's data to another)
// and re-run the route guards so protected pages redirect and /sign-in moves on.
onAuthChange((event, prev, next) => {
  if (prev !== next) queryClient.clear();
  if (event !== "TOKEN_REFRESHED" && event !== "INITIAL") void router.invalidate();
});
void startAuth();

// The service worker (Web Push now, offline caching later) is registered only in production builds.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  });
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing from index.html");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
