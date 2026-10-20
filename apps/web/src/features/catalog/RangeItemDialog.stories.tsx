import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { SCHOOL_YEAR, WINTER } from './fixtures';
import { RangeItemDialog } from './RangeItemDialog';
import {
  CLOSURE_MAX_SPAN_DAYS,
  CLOSURE_TYPES,
  PERIOD_MAX_SPAN_DAYS,
  PERIOD_TYPES,
} from './rangeRules';

// The period and closure dialogs (#38 E2, E3, E6): create a period, edit a
// period (type read-only), the period_overlap alert, and edit a closure. No
// request: every call is a stub. The dialog is portalled to <body>.

const periodTypes = PERIOD_TYPES.map((value) => ({
  value,
  label: i18n.t(`catalog.periodTypes.${value}`),
}));
const closureTypes = CLOSURE_TYPES.map((value) => ({
  value,
  label: i18n.t(`catalog.closureTypes.${value}`),
}));

const EDIT_PERIOD = {
  name: SCHOOL_YEAR.name,
  type: SCHOOL_YEAR.period_type,
  startsOn: SCHOOL_YEAR.starts_on,
  endsOn: SCHOOL_YEAR.ends_on,
  isActive: true,
  version: SCHOOL_YEAR.version,
};

const meta = {
  title: 'Catalog/RangeItemDialog',
  component: RangeItemDialog,
  args: {
    open: true,
    title: i18n.t('catalog.periods.add'),
    typeOptions: periodTypes,
    maxSpanDays: PERIOD_MAX_SPAN_DAYS,
    deactivateHelp: i18n.t('catalog.periods.deactivateHelp'),
    item: null,
    onSubmit: fn(() => Promise.resolve()),
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onClose: fn(),
  },
} satisfies Meta<typeof RangeItemDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const CreatePeriod: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('catalog.periods.add'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.range.type')),
    ).toHaveValue('school_year');
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.range.startsOn')),
    ).toHaveAttribute('type', 'date');
  },
};

export const EditPeriod: Story = {
  args: {
    title: i18n.t('catalog.periods.edit'),
    item: EDIT_PERIOD,
    typeLocked: true,
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    const type = within(dialog).getByLabelText(i18n.t('catalog.range.type'));
    await expect(type).toHaveAttribute('readonly');
    await expect(type).toHaveValue(i18n.t('catalog.periodTypes.school_year'));
    await expect(type).toHaveAccessibleDescription(
      i18n.t('catalog.periods.typeLocked'),
    );
  },
};

export const PeriodOverlap: Story = {
  args: {
    item: EDIT_PERIOD,
    typeLocked: true,
    title: i18n.t('catalog.periods.edit'),
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({ code: 'period_overlap', status: 409, requestId: 'r' }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    const end = within(dialog).getByLabelText(i18n.t('catalog.range.endsOn'));
    await context.userEvent.clear(end);
    await context.userEvent.type(end, '2027-07-20');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.save'),
      }),
    );
    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(
        i18n.t('errors:period_overlap'),
      ),
    );
    await expect(context.args.onClose).not.toHaveBeenCalled();
  },
};

export const EditClosure: Story = {
  args: {
    title: i18n.t('catalog.closures.edit'),
    typeOptions: closureTypes,
    maxSpanDays: CLOSURE_MAX_SPAN_DAYS,
    deactivateHelp: i18n.t('catalog.closures.deactivateHelp'),
    item: {
      name: WINTER.name,
      type: WINTER.closure_type,
      startsOn: WINTER.starts_on,
      endsOn: WINTER.ends_on,
      isActive: true,
      version: WINTER.version,
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.range.type')),
    ).toHaveValue('vacation');
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.deactivate'),
      }),
    ).toHaveAccessibleDescription(i18n.t('catalog.closures.deactivateHelp'));
  },
};
