import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { CLASSROOMS } from '../create/fixtures';
import { CancelSeriesDialog } from './CancelSeriesDialog';
import { ConflictOccurrenceDialog } from './ConflictOccurrenceDialog';
import { EditOccurrenceDialog } from './EditOccurrenceDialog';
import { EditSeriesDialog } from './EditSeriesDialog';
import {
  OCCURRENCE_SESSION,
  OCCURRENCES,
  SERIES_CONFLICTS,
  SERIES_DETAIL,
  SOFT_SERIES_CONFLICTS,
} from './fixtures';
import { SeriesOverrideSection } from './SeriesOverrideSection';

// The series dialogs (#54b E3): "Edit this occurrence", "Edit series" and
// "Cancel future occurrences", each empty, with an error and (the edits)
// with a conflict list; the #236 override branches under a series 409 (soft
// only with the button, mixed, a non-holder), the series override dialog
// (empty, a reason error, submitting) and a conflict row's dialog. No
// request: the submits are stubs. Axe runs in light and dark.

const ZONE = 'America/Mexico_City';

function problem(code: string, status = 409): ApiError {
  return new ApiError({
    code,
    status,
    requestId: `req-story-${code}`,
    problem: {
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-story-${code}`,
    },
  });
}

const meta = {
  title: 'Scheduling/SeriesDialogs',
  parameters: { layout: 'fullscreen' },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

async function dialog() {
  return within(await screen.findByRole('dialog'));
}

function occurrence(
  extra: Partial<Parameters<typeof EditOccurrenceDialog>[0]>,
) {
  return (
    <EditOccurrenceDialog
      occurrence={OCCURRENCES[0]!}
      session={OCCURRENCE_SESSION}
      classrooms={CLASSROOMS}
      timeZone={ZONE}
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      {...extra}
    />
  );
}

export const OccurrenceEmpty: Story = {
  render: () => occurrence({}),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByLabelText(i18n.t('scheduling.fields.start')),
    ).toHaveValue('10:00');
  },
};

export const OccurrenceVersionConflict: Story = {
  render: () => occurrence({ initialFailure: problem('version_conflict') }),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByRole('button', { name: i18n.t('scheduling.series.reload') }),
    ).toBeVisible();
  },
};

export const OccurrenceNotEditable: Story = {
  render: () => occurrence({ initialFailure: problem('session_not_editable') }),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('session_not_editable', { ns: 'errors' })),
    ).toBeVisible();
  },
};

export const OccurrenceConflict: Story = {
  render: () => occurrence({ initialConflicts: [SERIES_CONFLICTS[1]!] }),
  play: async () => {
    const view = await dialog();
    await expect(view.getByRole('alert')).toHaveTextContent(
      i18n.t('scheduling.severity.hard'),
    );
  },
};

function editSeries(extra: Partial<Parameters<typeof EditSeriesDialog>[0]>) {
  return (
    <EditSeriesDialog
      open
      series={SERIES_DETAIL}
      defaultFromDate="2031-01-13"
      classrooms={CLASSROOMS}
      timeZone={ZONE}
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      canOverride
      {...extra}
    />
  );
}

export const SeriesEditEmpty: Story = {
  render: () => editSeries({}),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByLabelText(i18n.t('scheduling.series.untilDate')),
    ).toHaveValue('2031-01-22');
  },
};

export const SeriesEditError: Story = {
  render: () => editSeries({ initialFailure: problem('series_offer_pending') }),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('series_offer_pending', { ns: 'errors' })),
    ).toBeVisible();
  },
};

export const SeriesEditConflicts: Story = {
  render: () => editSeries({ initialConflicts: SERIES_CONFLICTS }),
  play: async () => {
    const view = await dialog();
    const alert = view.getByRole('alert');
    await expect(alert.querySelectorAll('h3')).toHaveLength(2);
    await expect(alert).toHaveTextContent(i18n.t('scheduling.severity.soft'));
    await expect(
      view.getByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
  },
};

export const SeriesEditSoftConflicts: Story = {
  render: () => editSeries({ initialConflicts: SOFT_SERIES_CONFLICTS }),
  play: async () => {
    const view = await dialog();
    await expect(view.getByRole('alert').querySelectorAll('h3')).toHaveLength(
      3,
    );
    await expect(
      view.getByRole('button', { name: i18n.t('scheduling.override.action') }),
    ).toBeVisible();
  },
};

export const SeriesEditNotHolder: Story = {
  render: () =>
    editSeries({ initialConflicts: SOFT_SERIES_CONFLICTS, canOverride: false }),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.override.notAllowed')),
    ).toBeVisible();
  },
};

function overrideSection(
  extra: Partial<Parameters<typeof SeriesOverrideSection>[0]>,
) {
  return (
    <main className="p-4">
      <SeriesOverrideSection
        conflicts={SOFT_SERIES_CONFLICTS}
        heading={i18n.t('scheduling.series.conflicts.createHeading', {
          count: SOFT_SERIES_CONFLICTS.length,
        })}
        canOverride
        initialReason=""
        onOverride={fn(() => Promise.resolve())}
        onRefused={fn()}
        {...extra}
      />
    </main>
  );
}

export const OverrideDialogEmpty: Story = {
  render: () => overrideSection({}),
  play: async () => {
    const view = await dialog();
    await expect(view.getAllByRole('heading', { level: 4 })).toHaveLength(3);
    await expect(
      view.getByText(
        i18n.t('scheduling.series.override.description', { count: 3 }),
      ),
    ).toBeVisible();
  },
};

export const OverrideDialogReasonError: Story = {
  render: () => overrideSection({}),
  play: async () => {
    const view = await dialog();
    await userEvent.click(
      view.getByRole('button', { name: i18n.t('scheduling.override.submit') }),
    );
    await expect(
      view.getByText(i18n.t('scheduling.override.reasonRequired')),
    ).toBeVisible();
  },
};

export const OverrideDialogSubmitting: Story = {
  render: () =>
    overrideSection({
      initialReason: 'Domingos acordados con la familia',
      onOverride: () => new Promise<void>(() => undefined),
    }),
  play: async () => {
    const view = await dialog();
    await userEvent.click(
      view.getByRole('button', { name: i18n.t('scheduling.override.submit') }),
    );
    await expect(
      await view.findByText(i18n.t('scheduling.override.submitting')),
    ).toBeVisible();
  },
};

export const ConflictRowOverride: Story = {
  render: () => (
    <ConflictOccurrenceDialog
      occurrence={OCCURRENCES[4]!}
      timeZone={ZONE}
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
    />
  ),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('scheduling.rules.outside_operating_hours')),
    ).toBeVisible();
  },
};

function cancelSeries(
  extra: Partial<Parameters<typeof CancelSeriesDialog>[0]>,
) {
  return (
    <CancelSeriesDialog
      open
      version={SERIES_DETAIL.version}
      defaultFromDate="2031-01-13"
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      {...extra}
    />
  );
}

export const CancelEmpty: Story = {
  render: () => cancelSeries({}),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByLabelText(i18n.t('scheduling.series.cancel.reason')),
    ).toHaveValue('');
  },
};

export const CancelError: Story = {
  render: () =>
    cancelSeries({
      initialValues: { mode: 'all', fromDate: '', reason: 'Fin de curso' },
      initialFailure: problem('series_ended'),
    }),
  play: async () => {
    const view = await dialog();
    await expect(
      view.getByText(i18n.t('series_ended', { ns: 'errors' })),
    ).toBeVisible();
  },
};
