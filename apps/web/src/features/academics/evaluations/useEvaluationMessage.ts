import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import type { EvaluationMessage } from './rules';

// The text of an evaluation field message (#308 C8): a `common` key of
// our own rules, or `errors:<code>` for a server code with its `errors`
// message.
export function useEvaluationMessage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (message: EvaluationMessage | undefined): string | undefined => {
    if (!message) {
      return undefined;
    }
    if (message.startsWith('errors:')) {
      return errorMessage(
        new ApiError({ code: message.slice('errors:'.length), status: 422 }),
        tErrors,
      );
    }
    return (t as unknown as (key: string) => string)(message);
  };
}
