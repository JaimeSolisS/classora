import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { apiCall, type ApiCallOptions } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { rawCall } from './auth-routes';

// Test-only pages for the auth tests (#28): a page with a query and a page
// with a mutation, both through apiCall.

/** GET /api/v1/test/data in a query; shows "data: <value>" or the error. */
export function QueryPage({ path = '/api/v1/test/data' }: { path?: string }) {
  const query = useQuery({
    queryKey: ['test', 'data', path],
    queryFn: ({ signal }) => apiCall(rawCall('GET', path), { signal }),
    retry: false,
  });
  if (query.isError) {
    return <ErrorAlert error={query.error} />;
  }
  return <p data-testid="query">{query.isSuccess ? 'loaded' : 'loading'}</p>;
}

/** A button that sends POST /api/v1/test/action as a mutation. */
export function ActionPage({ options }: { options?: ApiCallOptions }) {
  const [result, setResult] = useState('idle');
  const mutation = useMutation({
    mutationFn: () => apiCall(rawCall('POST', '/api/v1/test/action'), options),
    onSuccess: () => setResult('done'),
  });
  return (
    <div>
      <button type="button" onClick={() => mutation.mutate()}>
        Run action
      </button>
      <p data-testid="result">{result}</p>
      {mutation.isError ? <ErrorAlert error={mutation.error} /> : null}
    </div>
  );
}
