// Compile-time checks, run by `tsc` in `make lint-web` (never executed).
// Each @ts-expect-error fails the type check if its line stops being an
// error, for example if the generated types ever degrade to `any`.
import { createApiClient, type ProblemDetails } from './index';

const client = createApiClient({ baseUrl: 'http://api.test' });

export async function typeChecks(): Promise<void> {
  // A path that is in the document type-checks, with a typed body.
  const { data } = await client.GET('/health/live');
  const status: 'ok' | undefined = data?.status;

  // @ts-expect-error -- not a path of the API document
  await client.GET('/health/nope');

  // @ts-expect-error -- the document has no POST on /health/live
  await client.POST('/health/live');

  const { error } = await client.GET('/health/ready');
  const problem: ProblemDetails | undefined = error;
  // @ts-expect-error -- status is the literal "ok", not any string
  const wrong: 'down' | undefined = data?.status;

  void [status, problem, wrong];
}
