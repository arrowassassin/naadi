/**
 * @naadi/shared: the API contract.
 *
 * Everything under ./gen is generated from openapi.json (exported from the API's Pydantic models) by
 * `pnpm gen:contracts`; never edit it by hand. Every SDK call parses its response with the matching
 * zod schema, so a payload that drifts from the contract fails loudly instead of rendering wrong data.
 */
import "./zod-config";

export { client } from "./gen/client.gen";
export * from "./gen/sdk.gen";
export type * from "./gen/types.gen";
export * from "./gen/zod.gen";
// Hand-written (no endpoint carries strokes yet); its JSON Schema is committed as ink.schema.json.
export * from "./generation";
export * from "./ink";
export * from "./library";
// ui_copy text with a non-empty fallback generated from config/app_config.yaml (segment 05).
export * from "./uiCopy";
