// First import: installs the Trusted Types default policy before any other module can reach a script sink.
import "./security/trustedTypes";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { onAuthChange, startAuth } from "./auth/session";
import { bootOffline } from "./offline/boot";
import { registerServiceWorker } from "./pwa/register";
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
// The offline layer (segment 18): outbox replay for whoever signs in, Dexie fallbacks, the service worker.
bootOffline(queryClient);

// The service worker (Web Push, offline caching; production builds only) with its "New version ready" flow.
registerServiceWorker();

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing from index.html");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
