import { useTranslation } from 'react-i18next';
import type {
  CreditPolicy,
  PreviewBlocking,
  PreviewWarning,
  SubjectKind,
  WithdrawalOutcome,
} from './api';

// The words of a withdrawal (#387 A, C1): the subject's kind, the policy
// behind a line's credit, the outcome, a warning, a blocking reason (with
// a package's reason) and a form message (a `common` key, or an `errors`
// key prefixed with `errors:`). Unknown codes show as themselves.

const SUBJECT_KINDS = new Set<string>([
  'group_enrollment',
  'cohort_enrollment',
  'package',
]);
const POLICIES = new Set<string>([
  'pro_rata',
  'nothing_delivered',
  'open_ended_fee',
  'fully_used',
]);
const WARNINGS = new Set<string>([
  'future_covered_sessions',
  'fee_posted_now',
  'tuition_posted_now',
  'open_ended_fee',
]);
const BLOCKING = new Set<string>([
  'withdrawal_exists',
  'withdrawal_closeout_pending',
  'package_not_withdrawable',
  'student_has_no_family',
  'withdrawal_spans_accounts',
]);
const PACKAGE_REASONS = new Set<string>([
  'cancelled',
  'withdrawn',
  'expired',
  'exhausted',
]);

export function useWithdrawalLabels() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  // A runtime key (a form message): the typed `t` can't check it.
  const translate = t as unknown as (
    key: string,
    params: Record<string, string>,
  ) => string;

  return {
    subjectKind: (kind: SubjectKind) =>
      SUBJECT_KINDS.has(kind)
        ? t(`billing.withdrawals.subjectKinds.${kind as 'package'}`)
        : kind,
    policy: (policy: CreditPolicy) =>
      POLICIES.has(policy)
        ? t(`billing.withdrawals.policies.${policy as 'pro_rata'}`)
        : policy,
    outcome: (outcome: WithdrawalOutcome) =>
      t(`billing.withdrawals.outcomes.${outcome}`),
    warning: (code: PreviewWarning['code'], count: number) =>
      WARNINGS.has(code)
        ? t(`billing.withdrawals.warnings.${code as 'fee_posted_now'}`, {
            count,
          })
        : code,
    blocking: (code: PreviewBlocking['code'], reason: string | null) => {
      if (!BLOCKING.has(code)) return code;
      if (code === 'package_not_withdrawable') {
        const why =
          reason && PACKAGE_REASONS.has(reason)
            ? t(
                `billing.withdrawals.blocking.packageReasons.${reason as 'expired'}`,
              )
            : (reason ?? '');
        return t('billing.withdrawals.blocking.package_not_withdrawable', {
          reason: why,
        });
      }
      return t(`billing.withdrawals.blocking.${code as 'withdrawal_exists'}`);
    },
    /** A form message key, with its parameters. */
    message: (key: string, params: Record<string, string> = {}): string =>
      key.startsWith('errors:')
        ? tErrors(key.slice('errors:'.length) as 'validation_error')
        : translate(key, params),
  };
}
