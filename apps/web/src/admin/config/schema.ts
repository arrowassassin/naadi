/**
 * A form description generated from a config key's JSON Schema (the pydantic model's `model_json_schema()`).
 * Objects, arrays, enums, booleans, numbers, strings and string-keyed maps of primitives get fields; anything
 * deeper or unusual (unions, tuples, nested maps, more than MAX_DEPTH levels) falls back to a JSON field.
 * The server validates every save with the same pydantic model; these are hints before saving.
 */

export type JsonSchema = {
  type?: string | string[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema | JsonSchema[];
  additionalProperties?: boolean | JsonSchema;
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  definitions?: Record<string, JsonSchema>;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  minItems?: number;
  maxItems?: number;
  format?: string;
};

interface Base {
  title: string | null;
  description: string | null;
  nullable: boolean;
}
export type FieldSpec =
  | (Base & { kind: "object"; fields: { name: string; required: boolean; spec: FieldSpec }[] })
  | (Base & { kind: "array"; item: FieldSpec; minItems?: number; maxItems?: number })
  | (Base & { kind: "map"; value: FieldSpec })
  | (Base & { kind: "enum"; options: (string | number | boolean)[] })
  | (Base & { kind: "boolean" })
  | (Base & { kind: "number"; integer: boolean; min?: number; max?: number })
  | (Base & { kind: "string"; minLength?: number; maxLength?: number; pattern?: string; multiline: boolean })
  | (Base & { kind: "json" });

export const MAX_DEPTH = 4;

function resolve(s: JsonSchema, root: JsonSchema, seen: Set<string>): JsonSchema {
  let cur = s;
  while (cur.$ref) {
    const ref = cur.$ref;
    if (seen.has(ref)) return {};
    seen.add(ref);
    const m = /^#\/(\$defs|definitions)\/(.+)$/.exec(ref);
    const target = m ? (m[1] === "$defs" ? root.$defs : root.definitions)?.[decodeURIComponent(m[2] ?? "")] : undefined;
    if (!target) return {};
    const { $ref: _r, ...rest } = cur;
    cur = { ...target, ...rest };
  }
  if (cur.allOf?.length === 1 && cur.allOf[0]) {
    const { allOf, ...rest } = cur;
    return resolve({ ...allOf[0], ...rest }, root, seen);
  }
  return cur;
}

function types(s: JsonSchema): string[] {
  return Array.isArray(s.type) ? s.type : s.type ? [s.type] : [];
}

const base = (s: JsonSchema, nullable: boolean): Base => ({
  title: s.title ?? null,
  description: s.description ?? null,
  nullable,
});

const isPrimitiveSpec = (f: FieldSpec) =>
  f.kind === "string" || f.kind === "number" || f.kind === "boolean" || f.kind === "enum";

export function buildSpec(schema: JsonSchema, root: JsonSchema = schema, depth = 0, nullable = false): FieldSpec {
  const s = resolve(schema, root, new Set());
  // Optional[X] = anyOf [X, null]
  const union = s.anyOf ?? s.oneOf;
  if (union) {
    const nonNull = union.filter((u) => {
      const ts = types(resolve(u, root, new Set()));
      return !(ts.length === 1 && ts[0] === "null");
    });
    const hasNull = nonNull.length < union.length;
    if (nonNull.length === 1 && nonNull[0]) {
      const { anyOf: _a, oneOf: _o, ...rest } = s;
      return buildSpec({ ...nonNull[0], ...rest }, root, depth, nullable || hasNull);
    }
    return { ...base(s, nullable || hasNull), kind: "json" };
  }
  const ts = types(s).filter((x) => x !== "null");
  const isNullable = nullable || types(s).includes("null");
  if (s.const !== undefined) return { ...base(s, isNullable), kind: "enum", options: [s.const as string] };
  if (s.enum) {
    const opts = s.enum.filter((v) => v !== null) as (string | number | boolean)[];
    return { ...base(s, isNullable || s.enum.includes(null)), kind: "enum", options: opts };
  }
  if (ts.length > 1) return { ...base(s, isNullable), kind: "json" };
  const t = ts[0] ?? (s.properties ? "object" : undefined);
  if (depth > MAX_DEPTH && (t === "object" || t === "array")) return { ...base(s, isNullable), kind: "json" };
  switch (t) {
    case "boolean":
      return { ...base(s, isNullable), kind: "boolean" };
    case "integer":
    case "number":
      return {
        ...base(s, isNullable),
        kind: "number",
        integer: t === "integer",
        min: s.minimum ?? s.exclusiveMinimum,
        max: s.maximum ?? s.exclusiveMaximum,
      };
    case "string":
      return {
        ...base(s, isNullable),
        kind: "string",
        minLength: s.minLength,
        maxLength: s.maxLength,
        pattern: s.pattern,
        multiline: (s.maxLength ?? 0) > 200,
      };
    case "array": {
      if (Array.isArray(s.items) || !s.items) return { ...base(s, isNullable), kind: "json" };
      return {
        ...base(s, isNullable),
        kind: "array",
        item: buildSpec(s.items, root, depth + 1),
        minItems: s.minItems,
        maxItems: s.maxItems,
      };
    }
    case "object": {
      if (s.properties && Object.keys(s.properties).length) {
        const req = new Set(s.required ?? []);
        return {
          ...base(s, isNullable),
          kind: "object",
          fields: Object.entries(s.properties).map(([name, p]) => ({
            name,
            required: req.has(name),
            spec: buildSpec(p, root, depth + 1),
          })),
        };
      }
      if (s.additionalProperties && typeof s.additionalProperties === "object") {
        const value = buildSpec(s.additionalProperties, root, depth + 1);
        if (isPrimitiveSpec(value)) return { ...base(s, isNullable), kind: "map", value };
      }
      return { ...base(s, isNullable), kind: "json" };
    }
    default:
      return { ...base(s, isNullable), kind: "json" };
  }
}

export interface Hint {
  /** Dotted path into the value, e.g. "groups.2.label" (empty for the root). */
  path: string;
  /** ui_copy key of the hint. */
  key: string;
  vars?: Record<string, string>;
}

const kindOf = (v: unknown): string =>
  v === null ? "null" : Array.isArray(v) ? "array" : typeof v === "number" ? "number" : typeof v;

/** Client-side validation hints for a value against its spec (the server's pydantic model decides). */
export function validate(spec: FieldSpec, value: unknown, path = ""): Hint[] {
  const at = (key: string, vars?: Record<string, string>): Hint[] => [{ path, key, vars }];
  if (value === null || value === undefined)
    return spec.nullable || spec.kind === "json" ? [] : at("admin.config.hint.required");
  switch (spec.kind) {
    case "json":
      return [];
    case "boolean":
      return typeof value === "boolean" ? [] : at("admin.config.hint.type", { type: "boolean" });
    case "enum":
      return spec.options.some((o) => o === value) ? [] : at("admin.config.hint.enum");
    case "number": {
      if (typeof value !== "number" || !Number.isFinite(value)) return at("admin.config.hint.type", { type: "number" });
      if (spec.integer && !Number.isInteger(value)) return at("admin.config.hint.integer");
      if (spec.min !== undefined && value < spec.min) return at("admin.config.hint.min", { min: String(spec.min) });
      if (spec.max !== undefined && value > spec.max) return at("admin.config.hint.max", { max: String(spec.max) });
      return [];
    }
    case "string": {
      if (typeof value !== "string") return at("admin.config.hint.type", { type: "string" });
      if (spec.minLength !== undefined && value.length < spec.minLength)
        return at("admin.config.hint.min_length", { n: String(spec.minLength) });
      if (spec.maxLength !== undefined && value.length > spec.maxLength)
        return at("admin.config.hint.max_length", { n: String(spec.maxLength) });
      if (spec.pattern) {
        try {
          if (!new RegExp(spec.pattern, "u").test(value)) return at("admin.config.hint.pattern");
        } catch {
          return [];
        }
      }
      return [];
    }
    case "array": {
      if (!Array.isArray(value)) return at("admin.config.hint.type", { type: "array" });
      const out: Hint[] = [];
      if (spec.minItems !== undefined && value.length < spec.minItems)
        out.push(...at("admin.config.hint.min_items", { n: String(spec.minItems) }));
      if (spec.maxItems !== undefined && value.length > spec.maxItems)
        out.push(...at("admin.config.hint.max_items", { n: String(spec.maxItems) }));
      for (const [i, v] of value.entries()) out.push(...validate(spec.item, v, path ? `${path}.${i}` : String(i)));
      return out;
    }
    case "map": {
      if (kindOf(value) !== "object") return at("admin.config.hint.type", { type: "object" });
      return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) =>
        validate(spec.value, v, path ? `${path}.${k}` : k),
      );
    }
    case "object": {
      if (kindOf(value) !== "object") return at("admin.config.hint.type", { type: "object" });
      const obj = value as Record<string, unknown>;
      return spec.fields.flatMap((f) => {
        const p = path ? `${path}.${f.name}` : f.name;
        if (!(f.name in obj)) return f.required ? [{ path: p, key: "admin.config.hint.required" }] : [];
        return validate(f.spec, obj[f.name], p);
      });
    }
  }
}

/** A starting value for a new array item or map entry. */
export function emptyValue(spec: FieldSpec): unknown {
  switch (spec.kind) {
    case "object":
      return Object.fromEntries(spec.fields.filter((f) => f.required).map((f) => [f.name, emptyValue(f.spec)]));
    case "array":
      return [];
    case "map":
      return {};
    case "enum":
      return spec.options[0] ?? null;
    case "boolean":
      return false;
    case "number":
      return spec.min ?? 0;
    case "string":
      return "";
    case "json":
      return null;
  }
}
