import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { EvaluationTemplate, TemplateFilters } from './api';
import { SUBJECT_OPTIONS, TEMPLATE, TEMPLATES, TWENTY } from './fixtures';
import { TemplateEditor } from './TemplateEditor';
import { TemplatesView, type TemplatesState } from './TemplatesView';

// The evaluation template pages (#308 G2): the list (with an inactive row,
// empty, empty with filters) and the editor (create, edit, 20 criteria, a
// version conflict). At 320 px the list's table scrolls inside its
// focusable region and the page never scrolls sideways; at 1280 px it
// fits. No request leaves the story. Axe runs in light and dark (the
// storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

const NO_FILTERS: TemplateFilters = {
  status: 'active',
  kind: null,
  subject: null,
  q: '',
};

interface ListArgs {
  state: TemplatesState;
  initial?: TemplateFilters;
}

function ListStory({ state, initial = NO_FILTERS }: ListArgs) {
  const [filters, setFilters] = useState(initial);
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="min-w-0 p-4">
                <TemplatesView
                  state={state}
                  filters={filters}
                  onFilters={setFilters}
                  subjects={SUBJECT_OPTIONS}
                />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const meta = {
  title: 'Academics/EvaluationTemplates',
  component: ListStory,
  args: { state: { status: 'ready', items: TEMPLATES } },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof ListStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function region(canvasElement: HTMLElement) {
  const found = within(canvasElement).getByRole('region', {
    name: i18n.t('academics.templates.title'),
  });
  await expect(found).toHaveAttribute('tabindex', '0');
  return found;
}

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

export const List: Story = {
  play: async ({ canvasElement }) => {
    await region(canvasElement);
    await expect(
      within(canvasElement).getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
  },
};

export const ListNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const found = await region(canvasElement);
    await expect(found.scrollWidth).toBeGreaterThan(found.clientWidth);
    await noSideScroll(canvasElement);
  },
};

export const ListDesktop1280: Story = {
  globals: { viewport: { value: 'desktop1280' } },
  play: async ({ canvasElement }) => {
    const found = await region(canvasElement);
    await expect(found.scrollWidth).toBeLessThanOrEqual(found.clientWidth);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', items: [] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.templates.empty')),
    ).toBeVisible();
  },
};

export const EmptyFiltered: Story = {
  args: {
    state: { status: 'ready', items: [] },
    initial: { ...NO_FILTERS, kind: 'ongoing' },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('academics.templates.emptyFiltered'),
      ),
    ).toBeVisible();
  },
};

export const EmptyFilteredNarrow320: Story = {
  ...EmptyFiltered,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => noSideScroll(canvasElement),
};

function editor(template: EvaluationTemplate | null, initialError?: unknown) {
  return function EditorStory() {
    return (
      <StoryQueryClient>
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="flex min-w-0 flex-col gap-6 p-4">
                  <h1 className="text-2xl font-semibold">
                    {template?.name ?? i18n.t('academics.templates.newTitle')}
                  </h1>
                  <TemplateEditor
                    template={template}
                    subjects={SUBJECT_OPTIONS}
                    onSave={() => new Promise<void>(() => {})}
                    onReload={() => Promise.resolve()}
                    initialError={initialError}
                  />
                </main>
              ),
            },
          ]}
        />
      </StoryQueryClient>
    );
  };
}

export const EditorCreate: Story = {
  render: editor(null),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('academics.templates.fields.kind'),
      ),
    ).toBeVisible();
  },
};

export const EditorCreateNarrow320: Story = {
  ...EditorCreate,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => noSideScroll(canvasElement),
};

export const EditorEdit: Story = {
  render: editor(TEMPLATE),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.templates.kindFixed')),
    ).toBeVisible();
  },
};

export const EditorEditDesktop1280: Story = {
  ...EditorEdit,
  globals: { viewport: { value: 'desktop1280' } },
};

export const EditorTwentyCriteria: Story = {
  render: editor(TWENTY),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('academics.templates.criteria.add'),
      }),
    ).toBeDisabled();
  },
};

export const EditorTwentyCriteriaNarrow320: Story = {
  ...EditorTwentyCriteria,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => noSideScroll(canvasElement),
};

export const EditorConflict: Story = {
  render: editor(
    TEMPLATE,
    new ApiError({ code: 'version_conflict', status: 409 }),
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toHaveTextContent(
      i18n.t('academics.templates.conflict'),
    );
  },
};
