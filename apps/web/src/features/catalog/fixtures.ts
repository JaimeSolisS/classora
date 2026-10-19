import type { EducationLevel, Subject } from './api';

// Fixtures of the catalog pages (#32 I3, J4): the seeded levels plus an
// inactive one, and subjects. No request.

const AT = '2026-10-19T15:00:00Z';

function level(
  index: number,
  name: string,
  sortOrder: number,
  overrides: Partial<EducationLevel> = {},
): EducationLevel {
  return {
    id: `00000000-0000-4000-8000-00000000c0${String(index).padStart(2, '0')}`,
    name,
    sort_order: sortOrder,
    is_active: true,
    active_subject_count: 0,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const PRIMARIA = level(1, 'Primaria', 10, { active_subject_count: 3 });
export const SECUNDARIA = level(2, 'Secundaria', 20, {
  active_subject_count: 1,
  version: 4,
});
export const PREPARATORIA = level(3, 'Preparatoria', 30);
export const POSGRADO = level(4, 'Posgrado', 50, {
  is_active: false,
  version: 2,
});

export const LEVELS: EducationLevel[] = [
  PRIMARIA,
  SECUNDARIA,
  PREPARATORIA,
  POSGRADO,
];

export function subject(
  index: number,
  name: string,
  of: EducationLevel = SECUNDARIA,
  overrides: Partial<Subject> = {},
): Subject {
  return {
    id: `00000000-0000-4000-8000-00000000d0${String(index).padStart(2, '0')}`,
    education_level_id: of.id,
    education_level_name: of.name,
    name,
    is_active: true,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const ALGEBRA = subject(1, 'Álgebra');
export const HISTORIA = subject(2, 'Historia', SECUNDARIA, {
  is_active: false,
  version: 3,
});
export const POSGRADO_SUBJECT = subject(3, 'Métodos', POSGRADO, {
  is_active: false,
});
