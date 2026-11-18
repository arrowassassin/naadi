import { describe, expect, it } from "vitest";
import { buildSpec, emptyValue, type FieldSpec, type JsonSchema, validate } from "./schema";

// The shape pydantic's model_json_schema() emits ($defs, anyOf-null for Optional, enums, maps).
const SCHEMA: JsonSchema = {
  type: "object",
  title: "ExamRules",
  required: ["block", "modes"],
  properties: {
    block: { $ref: "#/$defs/Block" },
    modes: { type: "array", items: { type: "string", enum: ["tutor", "timed", "exam"] }, minItems: 1 },
    note: { anyOf: [{ type: "string", maxLength: 300 }, { type: "null" }], default: null },
    flags: { type: "object", additionalProperties: { type: "boolean" } },
    groups: { type: "object", additionalProperties: { type: "object", properties: { a: { type: "string" } } } },
    tuple: { type: "array", items: [{ type: "string" }, { type: "integer" }] },
    either: { anyOf: [{ type: "string" }, { type: "integer" }] },
  },
  $defs: {
    Block: {
      type: "object",
      required: ["size", "minutes"],
      properties: {
        size: { type: "integer", minimum: 1, maximum: 80, title: "Size" },
        minutes: { type: "number", exclusiveMinimum: 0 },
        code: { type: "string", pattern: "^[a-z]+$" },
        enabled: { type: "boolean" },
      },
    },
  },
};

const field = (spec: FieldSpec, name: string): FieldSpec => {
  if (spec.kind !== "object") throw new Error("not an object");
  const f = spec.fields.find((x) => x.name === name);
  if (!f) throw new Error(`no field ${name}`);
  return f.spec;
};

describe("JSON-schema form generation", () => {
  const spec = buildSpec(SCHEMA);

  it("maps objects, $refs, arrays, enums, booleans, numbers and strings to fields", () => {
    expect(spec.kind).toBe("object");
    const block = field(spec, "block");
    expect(block.kind).toBe("object");
    expect(field(block, "size")).toMatchObject({ kind: "number", integer: true, min: 1, max: 80, title: "Size" });
    expect(field(block, "minutes")).toMatchObject({ kind: "number", integer: false, min: 0 });
    expect(field(block, "code")).toMatchObject({ kind: "string", pattern: "^[a-z]+$" });
    expect(field(block, "enabled").kind).toBe("boolean");
    const modes = field(spec, "modes");
    expect(modes).toMatchObject({ kind: "array", minItems: 1 });
    expect(modes.kind === "array" && modes.item).toMatchObject({ kind: "enum", options: ["tutor", "timed", "exam"] });
  });

  it("treats Optional[X] as a nullable X", () => {
    expect(field(spec, "note")).toMatchObject({ kind: "string", nullable: true, maxLength: 300 });
  });

  it("maps string-keyed maps of primitives, and falls back to JSON for anything deeper", () => {
    expect(field(spec, "flags")).toMatchObject({ kind: "map", value: { kind: "boolean" } });
    expect(field(spec, "groups").kind).toBe("json");
    expect(field(spec, "tuple").kind).toBe("json");
    expect(field(spec, "either").kind).toBe("json");
  });

  it("falls back to JSON beyond the depth limit and on a cyclic $ref", () => {
    let deep: JsonSchema = { type: "string" };
    for (let i = 0; i < 8; i++) deep = { type: "object", properties: { x: deep } };
    let s = buildSpec(deep);
    let depth = 0;
    while (s.kind === "object") {
      s = s.fields[0]?.spec as FieldSpec;
      depth++;
    }
    expect(s.kind).toBe("json");
    expect(depth).toBeLessThan(8);
    const cyclic: JsonSchema = { $ref: "#/$defs/A", $defs: { A: { $ref: "#/$defs/A" } } };
    expect(buildSpec(cyclic).kind).toBe("json");
  });

  it("gives validation hints with paths and ui_copy keys", () => {
    const ok = { block: { size: 40, minutes: 60 }, modes: ["tutor"], note: null, flags: { a: true } };
    expect(validate(spec, ok)).toEqual([]);
    const bad = {
      block: { size: 0.5, minutes: 60, code: "A1" },
      modes: ["nope"],
      note: "x".repeat(301),
      flags: { a: 1 },
    };
    const hints = validate(spec, bad).map((h) => `${h.path}:${h.key}`);
    expect(hints).toEqual(
      expect.arrayContaining([
        "block.size:admin.config.hint.integer",
        "block.code:admin.config.hint.pattern",
        "modes.0:admin.config.hint.enum",
        "note:admin.config.hint.max_length",
        "flags.a:admin.config.hint.type",
      ]),
    );
    expect(validate(spec, { block: { size: 3 } }).map((h) => `${h.path}:${h.key}`)).toEqual(
      expect.arrayContaining(["block.minutes:admin.config.hint.required", "modes:admin.config.hint.required"]),
    );
    expect(validate(spec, { block: { size: 99, minutes: 1 }, modes: [] }).map((h) => h.key)).toEqual(
      expect.arrayContaining(["admin.config.hint.max", "admin.config.hint.min_items"]),
    );
  });

  it("builds starting values for new items", () => {
    expect(emptyValue(field(spec, "block"))).toEqual({ size: 1, minutes: 0 });
    expect(emptyValue(field(spec, "modes"))).toEqual([]);
    const modes = field(spec, "modes");
    expect(modes.kind === "array" ? emptyValue(modes.item) : null).toBe("tutor");
  });
});
