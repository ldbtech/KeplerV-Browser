import type { Goal } from "../types";

export interface DashboardGoalList {
  items: Goal[];
  total: number;
  hasMore: boolean;
}

export function buildDashboardGoalList(
  goals: Goal[],
  query: string,
  expanded: boolean,
  limit = 5,
): DashboardGoalList {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const ordered = [...goals].sort((left, right) => {
    const leftCreated = Date.parse(left.createdAt) || 0;
    const rightCreated = Date.parse(right.createdAt) || 0;
    return rightCreated - leftCreated;
  });
  const filtered = normalizedQuery
    ? ordered.filter((goal) => `${goal.title} ${goal.description}`.toLocaleLowerCase().includes(normalizedQuery))
    : ordered;
  const showAll = expanded || normalizedQuery.length > 0;

  return {
    items: showAll ? filtered : filtered.slice(0, limit),
    total: filtered.length,
    hasMore: !showAll && filtered.length > limit,
  };
}
