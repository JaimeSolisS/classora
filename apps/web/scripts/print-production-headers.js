// Prints PRODUCTION_HEADERS (src/security-headers.ts), one "Name: value" line
// each, for infrastructure/docker/edge/smoke.sh, which compares them with the
// headers the edge image serves (#610). Node 24 strips the module's types.
//   node apps/web/scripts/print-production-headers.js
import { PRODUCTION_HEADERS } from '../src/security-headers.ts';

for (const [name, value] of Object.entries(PRODUCTION_HEADERS)) {
  console.log(`${name}: ${value}`);
}
