import type { TFunction } from 'i18next';
import { classificationKey } from '@/features/scheduling/change-requests/rules';
import type { Language } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { ChangeRequestItem, ChangeRequestOutcome } from './api';
import { isAsRequested } from './rules';

// The texts of a change request on "My change requests" (#62b B2), pure:
// the caller passes `t`, the center zone and the language.

export interface ListLabelContext {
  t: TFunction<'common'>;
  timeZone: string;
  language: Language;
}

function date(instant: string, context: ListLabelContext): string {
  return formatInstant(instant, {
    timeZone: context.timeZone,
    language: context.language,
    style: 'date',
  });
}

/** "makeup until 28 Nov 2026" or "no makeup". */
function makeupText(
  outcome: ChangeRequestOutcome,
  context: ListLabelContext,
): string {
  return outcome.makeup_allowed && outcome.makeup_until
    ? context.t('scheduling.changeRequest.makeupUntil', {
        date: date(outcome.makeup_until, context),
      })
    : context.t('scheduling.changeRequest.noMakeup');
}

/** "On time — makeup until 28 Nov 2026" (the #62 review wording). */
export function policyOutcomeText(
  outcome: ChangeRequestOutcome,
  context: ListLabelContext,
): string {
  return context.t('scheduling.changeRequest.outcomeText', {
    classification: context.t(classificationKey(outcome)),
    makeup: makeupText(outcome, context),
  });
}

/**
 * The outcome shown on a request (#62 C5): the request-time one, labelled
 * "as requested", while pending, rejected or withdrawn; the final one once
 * approved ("Approved as an exception — makeup until 28 Nov 2026", or
 * "Approved — On time — makeup until …").
 */
export function outcomeText(
  item: Pick<ChangeRequestItem, 'status' | 'outcome' | 'decision'>,
  context: ListLabelContext,
): string {
  if (isAsRequested(item)) {
    return context.t('changeRequests.list.asRequested', {
      outcome: policyOutcomeText(item.outcome, context),
    });
  }
  if (item.decision?.exception) {
    return context.t('changeRequests.list.approvedException', {
      makeup: makeupText(item.outcome, context),
    });
  }
  return context.t('changeRequests.list.approvedOutcome', {
    outcome: policyOutcomeText(item.outcome, context),
  });
}

/** A local date and time range in the center zone. */
export function whenText(
  startsAt: string,
  endsAt: string,
  context: ListLabelContext,
): string {
  const options = { timeZone: context.timeZone, language: context.language };
  return context.t('sessionRequests.when', {
    date: formatInstant(startsAt, { ...options, style: 'date' }),
    start: formatInstant(startsAt, { ...options, style: 'time' }),
    end: formatInstant(endsAt, { ...options, style: 'time' }),
  });
}

/** A short name of a request: "Cancellation, Matemáticas, 9 Nov 2026 10:00". */
export function requestLabel(
  item: Pick<ChangeRequestItem, 'kind' | 'class_session'>,
  context: ListLabelContext,
): string {
  const options = { timeZone: context.timeZone, language: context.language };
  return context.t('changeRequests.list.label', {
    kind: context.t(`scheduling.changeRequest.kinds.${item.kind}`),
    subject: item.class_session.subject.name,
    date: formatInstant(item.class_session.starts_at, {
      ...options,
      style: 'date',
    }),
    start: formatInstant(item.class_session.starts_at, {
      ...options,
      style: 'time',
    }),
  });
}
