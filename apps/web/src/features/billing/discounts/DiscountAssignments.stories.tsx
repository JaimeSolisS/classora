import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { DiscountAssignments } from './DiscountAssignments';
import {
  searchFixtureTargets,
  SCHOLARSHIP_DETAIL,
  SIBLING_DETAIL,
} from './fixtures';

// A discount's assignments (#367 G2): a scholarship with a student and a
// family, the "Assign" dialog with the already assigned student disabled,
// and a sibling discount (everyone who qualifies, nothing to assign). No
// request: every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/DiscountAssignments',
  component: DiscountAssignments,
  args: {
    discount: SCHOLARSHIP_DETAIL,
    onAssign: fn(() => Promise.resolve()),
    onRemove: fn(() => Promise.resolve()),
    search: (kind) => searchFixtureTargets(kind),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        path="/billing/discounts/x"
        routes={[{ path: '*', element: <DiscountAssignments {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof DiscountAssignments>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Assigned: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.discounts.assignments.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.discounts.assignments.removeFor', {
          name: 'Ana López',
        }),
      }),
    ).toBeVisible();
  },
};

export const AssignDialog: Story = {
  play: async (context) => {
    await context.userEvent.click(
      context.canvas.getByRole('button', {
        name: i18n.t('billing.discounts.assign.title'),
      }),
    );
    const dialog = await within(
      context.canvasElement.ownerDocument.body,
    ).findByRole('dialog');
    await context.userEvent.type(
      within(dialog).getByLabelText(i18n.t('billing.targets.search.student')),
      'L',
    );
    await expect(
      await within(dialog).findByRole('radio', {
        name: i18n.t('billing.discounts.assign.alreadyAssigned', {
          name: 'Ana López',
        }),
      }),
    ).toBeDisabled();
  },
};

export const Everyone: Story = {
  args: { discount: SIBLING_DETAIL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.discounts.assignments.everyone')),
    ).toBeVisible();
  },
};
