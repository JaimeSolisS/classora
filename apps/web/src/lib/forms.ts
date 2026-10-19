import { z } from 'zod';

// The form stack (architecture §8, _docs/design-system.md "Forms"): React Hook
// Form with Zod schemas through zodResolver. Import `z` from here, never from
// 'zod' directly (ESLint enforces it), so this setting always applies first.
//
// jitless: by default Zod 4 probes `new Function` to compile faster object
// parsers. The app's Content Security Policy has no 'unsafe-eval' (#24), so
// the probe would fail and the browser would report a CSP violation. Jitless
// parsing gives the same results without eval.
z.config({ jitless: true });

export { z };
