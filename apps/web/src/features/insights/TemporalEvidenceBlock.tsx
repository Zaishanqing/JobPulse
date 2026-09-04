import type {InsightCardContract} from './types';
import {insightTemporalEvidence} from './contract';

const percent = (value: number | null): string =>
  value === null ? '—' : `${(value * 100).toFixed(1)}%`;

const days = (value: number | null): string =>
  value === null ? '—' : `${value.toFixed(0)} 天`;

/**
 * Compact structured "证据时效性" block (TEMP-LAG-01).
 *
 * Renders only when `card.temporal_evidence` is non-null. Structured metrics
 * only — never model-generated prose. Mountable anywhere a real InsightCard
 * detail area exists; the insights feature currently has no card page, so this
 * component is the ready-to-mount surface with its own unit coverage.
 */
export function TemporalEvidenceBlock({
  card,
}: {
  card: InsightCardContract;
}) {
  const temporal = insightTemporalEvidence(card);
  if (!temporal.present) {
    return null;
  }
  return (
    <section aria-label="数据时效" className="temporal-evidence-block">
      <h4>数据时效</h4>
      <dl>
        <div>
          <dt>发布时间覆盖率</dt>
          <dd>{percent(temporal.publishTimeCoverage)}</dd>
        </div>
        <div>
          <dt>市场年龄 P50/P90</dt>
          <dd>
            {days(temporal.medianMarketAgeDays)} /{' '}
            {days(temporal.p90MarketAgeDays)}
          </dd>
        </div>
        <div>
          <dt>时效修正 N_eff</dt>
          <dd>
            {temporal.freshnessAdjustedNeff === null
              ? '—'
              : temporal.freshnessAdjustedNeff.toFixed(1)}
          </dd>
        </div>
        {temporal.staleEvidenceRatio !== null && (
          <div>
            <dt>过期证据占比</dt>
            <dd>{percent(temporal.staleEvidenceRatio)}</dd>
          </div>
        )}
      </dl>
      {temporal.sourceRows.length > 0 && (
        <div className="temporal-evidence-sources">
          <div className="temporal-evidence-sources-title">来源采集时滞</div>
          <ul>
            {temporal.sourceRows.map((row) => (
              <li key={row.sourceId}>
                {row.sourceId}：有效样本 {row.validSampleCount} · 中位{' '}
                {days(row.medianDelayDays)} · P90 {days(row.p90DelayDays)}
                {row.pipelineObservationCount > 0
                  ? ` · pipeline 观测 ${row.pipelineObservationCount}`
                  : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {temporal.reasonLabels.length > 0 && (
        <div className="temporal-evidence-limitations" role="note">
          {temporal.reasonLabels.join('；')}
        </div>
      )}
    </section>
  );
}
