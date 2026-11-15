import { useTranslation } from 'react-i18next';
import { isLabelledTask, taskLabelKey } from './api';

/** A task's label (`jobs.tasks.*`), or its code for a task with no label
 * (one added or renamed later, #128 G3). */
export function useTaskLabel(): (taskName: string) => string {
  const { t } = useTranslation();
  return (taskName) =>
    isLabelledTask(taskName) ? t(taskLabelKey(taskName)) : taskName;
}
