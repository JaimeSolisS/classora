import type {
  CourseTemplate,
  CourseTemplatePage,
  CourseTemplateSummary,
  SubjectOption,
} from './api';

// Course template fixtures for tests and stories (#64).

export const MATH_ID = '01900000-0000-7000-8000-00000000a001';
export const PHYSICS_ID = '01900000-0000-7000-8000-00000000a002';
export const CHEMISTRY_ID = '01900000-0000-7000-8000-00000000a003';

export const SUBJECT_OPTIONS: SubjectOption[] = [
  { id: MATH_ID, name: 'Matemáticas', levelName: 'Secundaria' },
  { id: PHYSICS_ID, name: 'Física', levelName: 'Secundaria' },
  { id: CHEMISTRY_ID, name: 'Química', levelName: 'Preparatoria' },
];

const MODULE_IDS = [
  '01900000-0000-7000-8000-00000000b001',
  '01900000-0000-7000-8000-00000000b002',
  '01900000-0000-7000-8000-00000000b003',
] as const;
const EXAM_ID = '01900000-0000-7000-8000-00000000c001';

export const ALGEBRA: CourseTemplate = {
  id: '01900000-0000-7000-8000-00000000d001',
  name: 'Álgebra intensiva',
  description: 'Curso de verano de álgebra.',
  subjects: [
    {
      id: MATH_ID,
      name: 'Matemáticas',
      level_name: 'Secundaria',
      is_active: true,
    },
  ],
  session_count: 10,
  session_minutes: 90,
  total_minutes: 900,
  module_count: 3,
  is_active: true,
  version: 1,
  min_attendance_percent: 80,
  modules: [
    {
      id: MODULE_IDS[0],
      position: 1,
      title: 'Ecuaciones',
      description: null,
      subject_id: MATH_ID,
      planned_sessions: 4,
      is_required: true,
      materials: 'Libro 1, capítulo 2',
    },
    {
      id: MODULE_IDS[1],
      position: 2,
      title: 'Funciones',
      description: 'Lineales y cuadráticas.',
      subject_id: null,
      planned_sessions: 4,
      is_required: true,
      materials: null,
    },
    {
      id: MODULE_IDS[2],
      position: 3,
      title: 'Repaso',
      description: null,
      subject_id: null,
      planned_sessions: null,
      is_required: false,
      materials: null,
    },
  ],
  assessments: [
    {
      id: EXAM_ID,
      position: 1,
      title: 'Examen de funciones',
      module_id: MODULE_IDS[1],
      is_required: true,
      min_score_percent: 60,
    },
  ],
  completion: {
    min_attendance_percent: 80,
    required_module_ids: [MODULE_IDS[0], MODULE_IDS[1]],
    required_assessment_ids: [EXAM_ID],
  },
  created_at: '2026-10-20T15:00:00Z',
  updated_at: '2026-10-20T15:00:00Z',
};

export function summaryOf(template: CourseTemplate): CourseTemplateSummary {
  return {
    id: template.id,
    name: template.name,
    subjects: template.subjects,
    session_count: template.session_count,
    session_minutes: template.session_minutes,
    total_minutes: template.total_minutes,
    module_count: template.module_count,
    is_active: template.is_active,
    version: template.version,
    updated_at: template.updated_at,
  };
}

export const PHYSICS_LAB: CourseTemplateSummary = {
  id: '01900000-0000-7000-8000-00000000d002',
  name: 'Laboratorio de física',
  subjects: [
    {
      id: PHYSICS_ID,
      name: 'Física',
      level_name: 'Secundaria',
      is_active: true,
    },
    {
      id: CHEMISTRY_ID,
      name: 'Química',
      level_name: 'Preparatoria',
      is_active: true,
    },
  ],
  session_count: 8,
  session_minutes: 45,
  total_minutes: 360,
  module_count: 2,
  is_active: false,
  version: 3,
  updated_at: '2026-10-19T15:00:00Z',
};

export const TEMPLATES: CourseTemplateSummary[] = [
  summaryOf(ALGEBRA),
  PHYSICS_LAB,
];

export function pageOf(items: CourseTemplateSummary[]): CourseTemplatePage {
  return { items, total: items.length, limit: 25, offset: 0 };
}
