import {Typography} from 'antd';
import type {DimensionScore,EvaluationSummary,FinalMatchResult} from '../types';
import type {EvaluationReport} from '../types';
import {buildMatchVerdict} from '../viewModels/matchVerdict';
import {overallScoreText} from '../viewModels/responsibility';
import {MatchDimensionRadar} from './MatchDimensionRadar';

type MatchReportOverviewProps={
  positionName:string;
  final:FinalMatchResult;
  report:EvaluationReport;
  summary:EvaluationSummary|null;
  dimensionScores:DimensionScore[];
  topActionName:string|null;
};

const percent=(value:number)=>`${Math.round(value*100)}%`;

export function MatchReportOverview({positionName,final,report,summary,dimensionScores,topActionName}:MatchReportOverviewProps){
  const score=overallScoreText(final.overall_score);
  const scoreNumber=score.endsWith(' 分')?score.slice(0,-2):score;
  // 匹配评价由规则化模板生成（非模型输出），同一报告措辞稳定。
  const verdict=buildMatchVerdict({
    evaluationId:report.evaluation_id,
    overallScore:final.overall_score??null,
    matchConfidence:final.match_confidence,
    hardGateStatus:final.hard_gate_status,
    dimensionScores,
    requiredSkillMissingCount:summary?.required_skill_missing_count??null,
    topActionName,
  });
  return <section id="match-report-overview" className="match-report-summary match-report-overview" aria-labelledby="match-report-overview-title">
    <div className="match-report-hero">
      <div className="match-report-verdict">
        <Typography.Text className="match-report-eyebrow">匹配结论</Typography.Text>
        <Typography.Title id="match-report-overview-title" level={3}>{positionName}</Typography.Title>
        <div className="match-verdict-score" aria-label={`综合匹配度 ${score}`}>
          <strong>{scoreNumber}</strong><span>分 · 综合匹配度</span>
        </div>
        <Typography.Text className="match-verdict-confidence">匹配置信度 {percent(final.match_confidence)} · {report.stale?'需要重新计算':'当前有效'}</Typography.Text>
        <div className="match-verdict-comment">
          <Typography.Text strong>匹配评价</Typography.Text>
          <Typography.Paragraph>{verdict}</Typography.Paragraph>
        </div>
      </div>
      <div className="match-report-radar-panel">
        <div className="match-report-radar-head">
          <div><Typography.Title level={4}>多维匹配画像</Typography.Title></div>
          <Typography.Text type="secondary">满分基准 100</Typography.Text>
        </div>
        <MatchDimensionRadar dimensionScores={dimensionScores}/>
      </div>
    </div>
  </section>;
}
