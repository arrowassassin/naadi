import { defineConfig } from "@hey-api/openapi-ts";

// Pydantic (apps/api) is the source of truth: openapi.json is exported from it by
// `pnpm gen:contracts`, and this generates the typed client + zod schemas from that file.
export default defineConfig({
  input: "./openapi.json",
  output: {
    path: "./src/gen",
  },
  plugins: [
    "@hey-api/client-fetch",
    "zod",
    {
      name: "@hey-api/sdk",
      // Every response body is parsed with its zod schema before the caller sees it.
      validator: true,
    },
    "@hey-api/typescript",
  ],
});
