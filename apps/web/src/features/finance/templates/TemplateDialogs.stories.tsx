import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn, screen } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  EXPENSES_OWNER_SESSION,
  MIXED_CATEGORIES,
  TEMPLATE_ENDED,
  TEMPLATE_RENT,
  VENDORS,
} from '../fixtures';
import { EndTemplateDialog } from './EndTemplateDialog';
import { TemplateDialog } from './TemplateDialog';

// The template dialogs (M7-13b H2): create (with the past-start notice, a
// taken name, and the lost-answer "may be saved" notice), edit (the fixed
// fields and the E3 notice, a version_conflict reloaded, an ended template on
// a deactivated category), and end (with the earliest date and a refusal).
// No request. axe runs on each story in both themes.

const TODAY = '2026-10-20' as LocalDate;

const narrow = {
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
  globals: { viewport: { value: 'narrow320' } },
};

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/templates"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const pickers = {
  categories: { status: 'ready', items: MIXED_CATEGORIES },
  vendors: { status: 'ready', items: VENDORS },
} as const;

const meta = {
  title: 'Finance/TemplateDialogs',
  component: TemplateDialog,
  args: {
    open: true,
    template: null,
    today: TODAY,
    ...pickers,
    onSubmit: fn(() => Promise.resolve('saved' as const)),
    onReload: fn(() => Promise.resolve(true)),
    onClose: fn(),
  },
  render: (args) => (
    <Shell>
      <TemplateDialog {...args} />
    </Shell>
  ),
} satisfies Meta<typeof TemplateDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Create: Story = {};

/** D1: a start in the past says those periods are generated at once. */
export const CreatePastStart: Story = {
  args: {
    initialValues: {
      name: 'Renta de bodega',
      startsOn: '2026-10-01',
      amount: '9500',
    },
  },
  play: async () => {
    await expect(
      await screen.findByText(i18n.t('finance.templates.dialog.pastStart')),
    ).toBeVisible();
  },
};

export const CreateExists: Story = {
  args: {
    initialValues: { name: 'Renta del local', amount: '12500' },
    initialErrors: { name: 'errors:expense_template_exists' },
  },
};

/** E2: a lost create retried into `expense_template_exists`. */
export const CreateMaybeSaved: Story = {
  args: {
    initialValues: { name: 'Renta del local', amount: '12500' },
    initialErrors: { name: 'errors:expense_template_exists' },
    initialNotice: 'maybeSavedNotReloaded',
  },
};

/** D2, E3: the fixed fields read-only, with the hint and the notice. */
export const Edit: Story = { args: { template: TEMPLATE_RENT } };

/** D2: someone else changed it; the newer version replaced the form. */
export const EditConflictReloaded: Story = {
  args: { template: TEMPLATE_RENT, initialNotice: 'conflictReloaded' },
};

/** An ended template keeps its deactivated category in the picker. */
export const EditEnded: Story = { args: { template: TEMPLATE_ENDED } };

export const CreateNarrow: Story = { ...narrow };

export const End: Story = {
  render: () => (
    <Shell>
      <EndTemplateDialog
        open
        template={TEMPLATE_RENT}
        today={TODAY}
        onSubmit={fn(() => Promise.resolve('saved' as const))}
        onReload={fn(() => Promise.resolve(true))}
        onClose={fn()}
      />
    </Shell>
  ),
};

/** D4: the server refused an end before a generated period. */
export const EndRefused: Story = {
  render: () => (
    <Shell>
      <EndTemplateDialog
        open
        template={TEMPLATE_RENT}
        today={TODAY}
        initialValues={{ endsOn: '2026-10-05' }}
        initialErrors={{ endsOn: 'errors:expense_template_ends_on_invalid' }}
        onSubmit={fn(() => Promise.resolve('saved' as const))}
        onReload={fn(() => Promise.resolve(true))}
        onClose={fn()}
      />
    </Shell>
  ),
};
