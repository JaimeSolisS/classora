// The KPI sections (M8-09b D1): each one component with `{from, to}` props
// (`basis` for K1) and its own query hook. The KPI page composes them;
// #127's dashboard mounts them as widgets.
export { BalancesKpiSection } from './BalancesKpiSection';
export { CourseProfitabilitySection } from './CourseProfitabilitySection';
export { RevenueTrendSection } from './RevenueTrendSection';
export { RoomUtilizationSection } from './RoomUtilizationSection';
export { StudentRetentionSection } from './StudentRetentionSection';
export { TutorPerformanceSection } from './TutorPerformanceSection';
export {
  useCourseProfitability,
  useKpiBalances,
  useRevenueTrend,
  useRoomUtilization,
  useStudentRetention,
  useTutorPerformance,
} from './queries';
