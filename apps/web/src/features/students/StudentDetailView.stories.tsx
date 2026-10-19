import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { JOSE, JOSE_LIMITED } from './fixtures';
import { StudentDetailView } from './StudentDetailPage';

// The student detail (#33 J3, K4): the full view for a manager and the
// limited view (`personal: null`) for Finance Staff or an assigned tutor.
// No request.

const meta = {
  title: 'Students/StudentDetailView',
  component: StudentDetailView,
  args: {
    student: JOSE,
    canManage: true,
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <StudentDetailView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof StudentDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Full: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: 'José Ñúñez Pérez' }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('students.contacts.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: '+52 55 1234 5678' }),
    ).toHaveAttribute('href', 'tel:+52 55 1234 5678');
    await expect(
      canvas.getByRole('link', { name: i18n.t('students.edit') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('students.deactivate.action'),
      }),
    ).toBeVisible();
  },
};

export const Limited: Story = {
  args: { student: JOSE_LIMITED, canManage: false },
  play: async ({ canvas, canvasElement }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: 'José Ñúñez Pérez' }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('heading', { level: 2 })).toHaveLength(1);
    await expect(canvasElement.textContent).not.toContain('María Pérez');
    await expect(canvasElement.textContent).not.toContain('2012');
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};
