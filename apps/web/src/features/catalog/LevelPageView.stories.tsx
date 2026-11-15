import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { GENERAL, POSGRADO, POSGRADO_SUBJECT, SECUNDARIA } from './fixtures';
import { LevelPageView } from './LevelPage';

// The level page of an inactive level (#32 I2, J4): the note, "Add subject"
// disabled, and its (inactive) subject. No request.

const meta = {
  title: 'Catalog/LevelPageView',
  component: LevelPageView,
  args: {
    level: POSGRADO,
    subjects: {
      status: 'ready',
      page: { items: [POSGRADO_SUBJECT], total: 1, limit: 25, offset: 0 },
    },
    onPage: fn(),
    onCreateSubject: fn(() => Promise.resolve()),
    onUpdateSubject: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <LevelPageView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof LevelPageView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const InactiveLevel: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: 'Posgrado' }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('catalog.levels.inactiveNote')),
    ).toBeVisible();
    const add = canvas.getByRole('button', {
      name: i18n.t('catalog.subjects.add'),
    });
    await expect(add).toBeDisabled();
    await expect(add).toHaveAccessibleDescription(
      i18n.t('catalog.levels.inactiveNote'),
    );
    await expect(
      canvas.getByRole('table', { name: i18n.t('catalog.subjects.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('catalog.levels.back') }),
    ).toHaveAttribute('href', '/catalog/education-levels');
  },
};

/** HF-12 (#597): the built-in General subject's dialog: Deactivate is
 * disabled, with the reason as its help; the name stays editable. */
export const GeneralSubjectDialog: Story = {
  args: {
    level: { ...SECUNDARIA, name: 'General', sort_order: 0 },
    subjects: {
      status: 'ready',
      page: { items: [GENERAL], total: 1, limit: 25, offset: 0 },
    },
  },
  play: async ({ canvas }) => {
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('catalog.actions.editNamed', { name: 'General' }),
      }),
    );
    const dialog = within(document.body).getByRole('dialog');
    const deactivate = within(dialog).getByRole('button', {
      name: i18n.t('catalog.actions.deactivate'),
    });
    await expect(deactivate).toBeDisabled();
    await expect(deactivate).toHaveAccessibleDescription(
      i18n.t('catalog.subjects.generalProtected'),
    );
  },
};

export const EmptyActiveLevel: Story = {
  args: {
    level: { ...POSGRADO, is_active: true },
    subjects: {
      status: 'ready',
      page: { items: [], total: 0, limit: 25, offset: 0 },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('catalog.subjects.empty')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('catalog.subjects.add') }),
    ).toBeEnabled();
  },
};
