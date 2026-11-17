import { Button, cx } from "@naadi/ui";
import { ArrowDown, ArrowUp, Minus, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { INPUT, LABEL, MONO_AREA, TEXTAREA, useT } from "../components/ui";
import { type DiffLine, withContext } from "./diff";
import { emptyValue, type FieldSpec, type Hint } from "./schema";

interface Props {
  spec: FieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  label: string;
  path: string;
  hints: ReadonlyMap<string, Hint[]>;
}

const MAP_PAGE = 50;

function HintText({ hints }: { hints: Hint[] | undefined }) {
  const t = useT();
  if (!hints?.length) return null;
  return (
    <span className="flex flex-col text-ui-sm text-amber">
      {hints.map((h) => (
        <span key={`${h.key}:${h.path}`}>{t(h.key, h.vars)}</span>
      ))}
    </span>
  );
}

function Described({ spec }: { spec: FieldSpec }) {
  return spec.description ? <span className="text-ui-sm text-text-3">{spec.description}</span> : null;
}

function NumberField({ spec, value, onChange, label, path, hints }: Props & { spec: FieldSpec & { kind: "number" } }) {
  const id = useId();
  const [raw, setRaw] = useState(typeof value === "number" ? String(value) : "");
  useEffect(() => {
    setRaw((r) => (Number(r) === value ? r : typeof value === "number" ? String(value) : ""));
  }, [value]);
  const h = hints.get(path);
  return (
    <label htmlFor={id} className={LABEL}>
      <span className="font-mono text-label-sm text-text-2">{label}</span>
      <input
        id={id}
        inputMode={spec.integer ? "numeric" : "decimal"}
        className={INPUT}
        value={raw}
        aria-invalid={Boolean(h?.length) || undefined}
        onChange={(e) => {
          setRaw(e.target.value);
          const n = Number(e.target.value);
          onChange(e.target.value.trim() === "" ? null : Number.isFinite(n) ? n : e.target.value);
        }}
      />
      <Described spec={spec} />
      <HintText hints={h} />
    </label>
  );
}

function JsonField({ value, onChange, label, path, hints, spec }: Props) {
  const t = useT();
  const id = useId();
  const [raw, setRaw] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setRaw((r) => {
      try {
        return JSON.stringify(JSON.parse(r)) === JSON.stringify(value ?? null)
          ? r
          : JSON.stringify(value ?? null, null, 2);
      } catch {
        return r;
      }
    });
  }, [value]);
  return (
    <label htmlFor={id} className={LABEL}>
      <span className="font-mono text-label-sm text-text-2">{label}</span>
      <textarea
        id={id}
        spellCheck={false}
        autoCapitalize="off"
        className={cx(MONO_AREA, "min-h-32")}
        value={raw}
        aria-invalid={bad || undefined}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setBad(false);
          } catch {
            setBad(true);
          }
        }}
      />
      <span className="text-text-3">{t("admin.config.json_field_hint")}</span>
      <Described spec={spec} />
      {bad ? <span className="text-ui-sm text-amber">{t("admin.config.json_invalid")}</span> : null}
      <HintText hints={hints.get(path)} />
    </label>
  );
}

function MapField({ spec, value, onChange, label, path, hints }: Props & { spec: FieldSpec & { kind: "map" } }) {
  const t = useT();
  const id = useId();
  const obj = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
  const [filter, setFilter] = useState("");
  const [newKey, setNewKey] = useState("");
  const [limit, setLimit] = useState(MAP_PAGE);
  const keys = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return Object.keys(obj).filter(
      (k) =>
        !f ||
        k.toLowerCase().includes(f) ||
        String(obj[k] ?? "")
          .toLowerCase()
          .includes(f),
    );
  }, [obj, filter]);
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-3 rounded-block border border-line-soft p-4">
      <legend className="px-1 font-mono text-label-sm text-text-2">{label}</legend>
      <Described spec={spec} />
      <label htmlFor={`${id}-f`} className={LABEL}>
        {t("admin.config.map_filter", { n: String(Object.keys(obj).length) })}
        <input
          id={`${id}-f`}
          type="search"
          className={INPUT}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </label>
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {keys.slice(0, limit).map((k) => (
          <li key={k} className="flex min-w-0 items-start gap-2">
            <div className="min-w-0 flex-1">
              <Field
                spec={spec.value}
                value={obj[k]}
                label={k}
                path={path ? `${path}.${k}` : k}
                hints={hints}
                onChange={(v) => onChange({ ...obj, [k]: v })}
              />
            </div>
            <Button
              className="mt-7"
              aria-label={t("admin.config.remove_entry", { key: k })}
              icon={<Trash2 size={16} />}
              onClick={() => {
                const { [k]: _gone, ...rest } = obj;
                onChange(rest);
              }}
            />
          </li>
        ))}
      </ul>
      {keys.length > limit ? (
        <Button onClick={() => setLimit((l) => l + MAP_PAGE)}>
          {t("admin.config.show_more", { n: String(keys.length - limit) })}
        </Button>
      ) : null}
      <div className="flex flex-wrap items-end gap-2">
        <label htmlFor={`${id}-k`} className={cx(LABEL, "flex-1")}>
          {t("admin.config.new_key")}
          <input
            id={`${id}-k`}
            className={cx(INPUT, "font-mono")}
            value={newKey}
            autoCapitalize="off"
            spellCheck={false}
            onChange={(e) => setNewKey(e.target.value)}
          />
        </label>
        <Button
          icon={<Plus size={16} />}
          disabled={!newKey.trim() || newKey.trim() in obj}
          onClick={() => {
            onChange({ ...obj, [newKey.trim()]: emptyValue(spec.value) });
            setFilter(newKey.trim());
            setNewKey("");
          }}
        >
          {t("admin.config.add_entry")}
        </Button>
      </div>
      <HintText hints={hints.get(path)} />
    </fieldset>
  );
}

/** One field of the generated form (recursive). */
export function Field(props: Props) {
  const { spec, value, onChange, label, path, hints } = props;
  const t = useT();
  const id = useId();
  if (spec.nullable && spec.kind !== "json" && (value === null || value === undefined)) {
    return (
      <div className="flex flex-col gap-2">
        <span className="font-mono text-label-sm text-text-2">{label}</span>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-ui-sm text-text-3">{t("admin.config.not_set")}</span>
          <Button icon={<Plus size={16} />} onClick={() => onChange(emptyValue(spec))}>
            {t("admin.config.set_value")}
          </Button>
        </div>
      </div>
    );
  }
  const clear = spec.nullable ? (
    <Button className="self-start" icon={<Minus size={16} />} onClick={() => onChange(null)}>
      {t("admin.config.unset", { field: label })}
    </Button>
  ) : null;

  switch (spec.kind) {
    case "json":
      return <JsonField {...props} />;
    case "number":
      return (
        <div className="flex flex-col gap-2">
          <NumberField {...props} spec={spec} />
          {clear}
        </div>
      );
    case "boolean":
      return (
        <div className="flex flex-col gap-1">
          <label htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-3 text-ui text-text">
            <input
              id={id}
              type="checkbox"
              checked={value === true}
              onChange={(e) => onChange(e.target.checked)}
              className="size-5 accent-paper"
            />
            <span className="font-mono text-label-sm text-text-2">{label}</span>
            <span className="text-ui-sm">{t(value === true ? "admin.config.on" : "admin.config.off")}</span>
          </label>
          <Described spec={spec} />
          <HintText hints={hints.get(path)} />
        </div>
      );
    case "enum":
      return (
        <label htmlFor={id} className={LABEL}>
          <span className="font-mono text-label-sm text-text-2">{label}</span>
          <select
            id={id}
            className={INPUT}
            value={JSON.stringify(value ?? null)}
            onChange={(e) => onChange(JSON.parse(e.target.value))}
          >
            {spec.options.some((o) => o === value) ? null : (
              <option value={JSON.stringify(value ?? null)}>{String(value)}</option>
            )}
            {spec.options.map((o) => (
              <option key={JSON.stringify(o)} value={JSON.stringify(o)}>
                {String(o)}
              </option>
            ))}
          </select>
          <Described spec={spec} />
          <HintText hints={hints.get(path)} />
          {clear}
        </label>
      );
    case "string":
      return (
        <label htmlFor={id} className={LABEL}>
          <span className="font-mono text-label-sm text-text-2">{label}</span>
          {spec.multiline || (typeof value === "string" && value.length > 120) ? (
            <textarea
              id={id}
              className={TEXTAREA}
              value={typeof value === "string" ? value : ""}
              maxLength={spec.maxLength}
              onChange={(e) => onChange(e.target.value)}
            />
          ) : (
            <input
              id={id}
              className={INPUT}
              value={typeof value === "string" ? value : ""}
              maxLength={spec.maxLength}
              onChange={(e) => onChange(e.target.value)}
            />
          )}
          <Described spec={spec} />
          <HintText hints={hints.get(path)} />
          {clear}
        </label>
      );
    case "map":
      return <MapField {...props} spec={spec} />;
    case "object": {
      const obj = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
      const body = spec.fields.map((f) => {
        const p = path ? `${path}.${f.name}` : f.name;
        if (!(f.name in obj) && !f.required) {
          return (
            <div key={f.name} className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-label-sm text-text-2">{f.spec.title ?? f.name}</span>
              <Button icon={<Plus size={16} />} onClick={() => onChange({ ...obj, [f.name]: emptyValue(f.spec) })}>
                {t("admin.config.set_value")}
              </Button>
            </div>
          );
        }
        return (
          <Field
            key={f.name}
            spec={f.spec}
            value={obj[f.name]}
            label={f.name}
            path={p}
            hints={hints}
            onChange={(v) => onChange({ ...obj, [f.name]: v })}
          />
        );
      });
      if (!path) return <div className="flex min-w-0 flex-col gap-5">{body}</div>;
      return (
        <fieldset className="m-0 flex min-w-0 flex-col gap-4 rounded-block border border-line-soft p-4">
          <legend className="px-1 font-mono text-label-sm text-text-2">{label}</legend>
          <Described spec={spec} />
          {body}
          <HintText hints={hints.get(path)} />
          {clear}
        </fieldset>
      );
    }
    case "array": {
      const arr = Array.isArray(value) ? value : [];
      return (
        <fieldset className="m-0 flex min-w-0 flex-col gap-3 rounded-block border border-line-soft p-4">
          <legend className="px-1 font-mono text-label-sm text-text-2">{label}</legend>
          <Described spec={spec} />
          <ol className="m-0 flex list-none flex-col gap-3 p-0">
            {arr.map((item, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: array items are positional in the config value
              <li key={i} className="flex min-w-0 items-start gap-2">
                <div className="min-w-0 flex-1">
                  <Field
                    spec={spec.item}
                    value={item}
                    label={`${label} ${i + 1}`}
                    path={path ? `${path}.${i}` : String(i)}
                    hints={hints}
                    onChange={(v) => onChange(arr.map((x, k) => (k === i ? v : x)))}
                  />
                </div>
                <div className="mt-7 flex flex-col gap-1">
                  <Button
                    aria-label={t("admin.action.move_up", { name: `${label} ${i + 1}` })}
                    disabled={i === 0}
                    icon={<ArrowUp size={16} />}
                    onClick={() => {
                      const next = [...arr];
                      [next[i - 1], next[i]] = [next[i], next[i - 1]];
                      onChange(next);
                    }}
                  />
                  <Button
                    aria-label={t("admin.action.move_down", { name: `${label} ${i + 1}` })}
                    disabled={i === arr.length - 1}
                    icon={<ArrowDown size={16} />}
                    onClick={() => {
                      const next = [...arr];
                      [next[i + 1], next[i]] = [next[i], next[i + 1]];
                      onChange(next);
                    }}
                  />
                  <Button
                    aria-label={t("admin.config.remove_item", { n: String(i + 1) })}
                    icon={<Trash2 size={16} />}
                    onClick={() => onChange(arr.filter((_, k) => k !== i))}
                  />
                </div>
              </li>
            ))}
          </ol>
          <Button
            className="self-start"
            icon={<Plus size={16} />}
            disabled={spec.maxItems !== undefined && arr.length >= spec.maxItems}
            onClick={() => onChange([...arr, emptyValue(spec.item)])}
          >
            {t("admin.config.add_item")}
          </Button>
          <HintText hints={hints.get(path)} />
          {clear}
        </fieldset>
      );
    }
  }
}

/** A line diff with words for each change (never colour alone). */
export function LineDiff({ lines, label }: { lines: DiffLine[]; label: string }) {
  const t = useT();
  const rows = withContext(lines);
  if (!lines.some((l) => l.op !== "equal")) return <p className="m-0 text-text-2">{t("admin.diff.no_changes")}</p>;
  return (
    <section
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
      tabIndex={0}
      className="max-h-[60vh] overflow-auto rounded-block border border-line-soft bg-ground"
    >
      <ol className="m-0 list-none p-0 font-mono text-[0.8125rem] leading-relaxed">
        {rows.map((r, i) =>
          r.op === "skip" ? (
            // biome-ignore lint/suspicious/noArrayIndexKey: diff rows are positional
            <li key={i} className="px-3 py-1 text-text-3">
              {t("admin.diff.skipped", { n: String(r.n) })}
            </li>
          ) : (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: diff rows are positional
              key={i}
              className={cx(
                "grid grid-cols-[1.5rem_1fr] px-2 whitespace-pre-wrap break-all",
                r.op === "insert" && "bg-surface-2 text-text",
                r.op === "delete" && "text-text-3 line-through",
                r.op === "equal" && "text-text-2",
              )}
            >
              <span aria-hidden="true">{r.op === "insert" ? "+" : r.op === "delete" ? "−" : " "}</span>
              <span>
                {r.op === "equal" ? null : (
                  <span className="sr-only">{t(r.op === "insert" ? "admin.diff.insert" : "admin.diff.delete")}: </span>
                )}
                {r.text}
              </span>
            </li>
          ),
        )}
      </ol>
    </section>
  );
}
