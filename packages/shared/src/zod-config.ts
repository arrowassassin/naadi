import * as z from "zod";

// zod 4 compiles object parsers with `new Function` when a schema is *constructed*, unless JIT is off.
// The CSP has no 'unsafe-eval', and even zod's probe for eval support is reported as a CSP violation
// (Firefox logs it as a console error). This module must be evaluated before any generated schema, so
// index.ts imports it first.
z.config({ jitless: true });
