import {api} from '../../../shared/api';
import type {InsightCardContract} from '../types';

export const emergingInsightCard = (emergingId: string, releaseId?: string | null) =>
  api<InsightCardContract>(
    `/innovation/insights/emerging/${encodeURIComponent(emergingId)}${
      releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : ''
    }`,
  );

export const trendInsightCard = (reportId: string, releaseId?: string | null) =>
  api<InsightCardContract>(
    `/innovation/insights/trend/${encodeURIComponent(reportId)}${
      releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : ''
    }`,
  );

export const evolutionInsightCard = (
  positionId: string,
  eventId: string,
  releaseId?: string | null,
) =>
  api<InsightCardContract>(
    `/innovation/insights/evolution/${encodeURIComponent(positionId)}/${encodeURIComponent(
      eventId,
    )}${releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : ''}`,
  );

export const matchingScenarioInsightCard = (scenarioId: string) =>
  api<InsightCardContract>(
    `/innovation/insights/matching/${encodeURIComponent(scenarioId)}`,
  );

export type {InsightCardContract} from '../types';
