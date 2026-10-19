import type { Page } from '@playwright/test';

// Calls the API with fetch() from the page, through the web origin (the Vite
// proxy): Chromium sends its own Origin header and stores and sends the
// session cookie (page.request keeps its own cookies and sets no Origin).

export type ApiResult = { status: number; body: unknown };

export type ApiInit = {
  method?: string;
  json?: unknown;
  headers?: Record<string, string>;
};

export async function api(
  page: Page,
  path: string,
  init: ApiInit = {},
): Promise<ApiResult> {
  return page.evaluate(
    async ({ path, init }) => {
      const headers: Record<string, string> = { ...init.headers };
      if (init.json !== undefined) {
        headers['Content-Type'] = 'application/json';
      }
      const response = await fetch(path, {
        method: init.method ?? 'GET',
        headers,
        body: init.json === undefined ? undefined : JSON.stringify(init.json),
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : null };
    },
    { path, init },
  );
}

/** The `csrf_token` of a SessionUser body (login or /auth/me). */
export function csrfTokenOf(result: ApiResult): string {
  return (result.body as { csrf_token: string }).csrf_token;
}
