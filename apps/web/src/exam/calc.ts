/**
 * The exam's four-function calculator (like test day's): digits, decimal point, + − × ÷, =, sign change,
 * C (clear all) and CE (clear entry). Pure reducer; no eval, no precedence (each operator applies the pending
 * one first, as a pocket calculator does). Results are rounded to 10 significant digits to hide binary
 * floating-point noise (0.1 + 0.2 shows 0.3).
 */
export type Op = "+" | "-" | "*" | "/";
export type CalcKey = { digit: string } | { op: Op } | "." | "=" | "sign" | "C" | "CE";

export interface CalcState {
  display: string;
  acc: number | null;
  op: Op | null;
  /** The next digit starts a new entry (after an operator or =). */
  fresh: boolean;
  error: boolean;
}

export const INITIAL: CalcState = { display: "0", acc: null, op: null, fresh: true, error: false };

const MAX_DIGITS = 12;

export function format(n: number): string {
  if (!Number.isFinite(n)) return "Error";
  const rounded = Number.parseFloat(n.toPrecision(10));
  const s = String(rounded);
  if (s.replace(/[-.]/g, "").length > MAX_DIGITS || Math.abs(rounded) >= 1e12) return rounded.toExponential(6);
  return Object.is(rounded, -0) ? "0" : s;
}

function apply(a: number, op: Op, b: number): number {
  switch (op) {
    case "+":
      return a + b;
    case "-":
      return a - b;
    case "*":
      return a * b;
    case "/":
      return b === 0 ? Number.NaN : a / b;
  }
}

export function press(s: CalcState, key: CalcKey): CalcState {
  if (key === "C") return INITIAL;
  if (s.error) return key === "CE" ? INITIAL : s;
  if (key === "CE") return { ...s, display: "0", fresh: true };
  if (key === ".") {
    if (s.fresh) return { ...s, display: "0.", fresh: false };
    return s.display.includes(".") ? s : { ...s, display: `${s.display}.` };
  }
  if (key === "sign") {
    if (s.display === "0") return s;
    return { ...s, display: s.display.startsWith("-") ? s.display.slice(1) : `-${s.display}` };
  }
  if (typeof key === "object" && "digit" in key) {
    if (!/^[0-9]$/.test(key.digit)) return s;
    if (s.fresh) return { ...s, display: key.digit, fresh: false };
    if (s.display.replace(/[-.]/g, "").length >= MAX_DIGITS) return s;
    return { ...s, display: s.display === "0" ? key.digit : s.display + key.digit };
  }
  const current = Number.parseFloat(s.display);
  const total = s.acc !== null && s.op !== null && !s.fresh ? apply(s.acc, s.op, current) : (s.acc ?? current);
  if (!Number.isFinite(total)) return { ...INITIAL, display: "Error", error: true };
  if (key === "=") {
    const value = s.acc !== null && s.op !== null ? apply(s.acc, s.op, current) : current;
    if (!Number.isFinite(value)) return { ...INITIAL, display: "Error", error: true };
    return { display: format(value), acc: null, op: null, fresh: true, error: false };
  }
  // An operator: apply the pending one (unless the student is just switching operators), then wait.
  const acc = s.fresh && s.op !== null ? (s.acc ?? current) : total;
  return { display: format(acc), acc, op: key.op, fresh: true, error: false };
}

/** Keyboard keys inside the calculator (only while it has focus). */
export function keyToCalc(key: string): CalcKey | null {
  if (/^[0-9]$/.test(key)) return { digit: key };
  switch (key) {
    case "+":
    case "-":
    case "*":
    case "/":
      return { op: key };
    case "x":
    case "X":
      return { op: "*" };
    case ".":
    case ",":
      return ".";
    case "=":
    case "Enter":
      return "=";
    case "Escape":
      return "C";
    case "Backspace":
    case "Delete":
      return "CE";
    default:
      return null;
  }
}
