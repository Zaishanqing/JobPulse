import {describe,expect,test} from 'vitest';
import {resolveDemoTaskResult,resolveEvidenceReference} from './resultRoute';
import type {PortalDemoTask,PortalDemoTaskStatus,PortalDemoTaskType} from './types';

const task=(task_type:PortalDemoTaskType,result_reference:string|null,status:PortalDemoTaskStatus='succeeded',object_id='object-1'):PortalDemoTask=>({
  task_id:`${task_type}-task`,
  task_type,
  object_type:'demo_object',
  object_id,
  service:'demo-service',
  status,
  progress:status==='succeeded'?1:0,
  error:null,
  result_reference,
  created_at:null,
  updated_at:null,
});

describe('resolveDemoTaskResult',()=>{
  test('五类 succeeded 任务只按冻结业务结果引用生成路径',()=>{
    expect(resolveDemoTaskResult(task('jd_extraction','/api/v1/extraction-tasks/jd-1'))).toEqual({
      path:null,
      reason:'Contract 只提供内部抽取任务引用，未提供前端业务结果',
    });
    expect(resolveDemoTaskResult(task('cv_extraction','/api/v1/validated-cv-snapshots/snapshot-1'))).toEqual({
      path:null,
      reason:'Snapshot 与 Resume 的前端关联尚未冻结',
    });
    expect(resolveDemoTaskResult(task('trend','trend_report:trend-1','succeeded','position-1'))).toEqual({
      path:'/analysis/evolution?positionId=position-1&resultReference=trend_report%3Atrend-1',
      reason:null,
    });
    expect(resolveDemoTaskResult(task('discovery','discovery_run:run-1'))).toEqual({
      path:'/admin/discovery?runId=run-1',
      reason:null,
    });
    expect(resolveDemoTaskResult(task('matching','/api/v1/matches/reports/evaluation-1'))).toEqual({
      path:'/matching/reports/evaluation-1',
      reason:null,
    });
    expect(resolveDemoTaskResult(task('cv_extraction','/api/v1/cv-extraction-tasks/cv-task-1'))).toEqual({
      path:null,
      reason:'CV 任务恢复 Contract 尚未冻结',
    });
    expect(resolveDemoTaskResult(task('matching','matching_evaluation:evaluation-2'))).toEqual({
      path:'/matching/reports/evaluation-2',
      reason:null,
    });
    expect(resolveDemoTaskResult(task('matching','evaluation_report:evaluation-3'))).toEqual({
      path:'/matching/reports/evaluation-3',
      reason:null,
    });
  });

  test.each<PortalDemoTaskStatus>(['pending','running','failed','cancelled'])('%s 任务不给结果路径',status=>{
    expect(resolveDemoTaskResult(task('matching','matching_evaluation:evaluation-1',status))).toEqual({
      path:null,
      reason:'任务尚未成功',
    });
  });

  test('缺失引用返回明确不可解析原因',()=>{
    expect(resolveDemoTaskResult(task('trend',null))).toEqual({
      path:null,
      reason:'结果引用格式未冻结',
    });
  });

  test('非法引用和内部 task API 路径不生成浏览器路由',()=>{
    expect(resolveDemoTaskResult(task('matching','prefix-matching_evaluation:evaluation-1'))).toEqual({
      path:null,
      reason:'结果引用格式未冻结',
    });
    expect(resolveDemoTaskResult(task('trend','/api/v1/trend-analysis/tasks/trend-task-1'))).toEqual({
      path:null,
      reason:'Contract 只提供内部任务引用，未提供前端业务结果',
    });
    expect(resolveDemoTaskResult(task('trend','/api/v1/predicted-positions/tasks/predicted-task-1'))).toEqual({
      path:null,
      reason:'Contract 只提供内部任务引用，未提供前端业务结果',
    });
    expect(resolveDemoTaskResult(task('discovery','/api/v1/position-clusters/tasks/discovery-task-1'))).toEqual({
      path:null,
      reason:'Contract 只提供内部任务引用，未提供前端业务结果',
    });
    expect(resolveDemoTaskResult(task('matching','/api/v1/matches/tasks/matching-task-1'))).toEqual({
      path:null,
      reason:'Contract 只提供内部任务引用，未提供前端业务结果',
    });
  });

  test('可信 Trend Intelligence 引用在映射冻结前不生成路径',()=>{
    expect(resolveDemoTaskResult(task('trend','trend-intelligence:run-1'))).toEqual({
      path:null,
      reason:'Trend Intelligence 结果页映射尚未冻结',
    });
  });

  test('结果 ID 和对象 ID 使用 URL 编码',()=>{
    expect(resolveDemoTaskResult(task('matching','matching_evaluation:evaluation / 中文'))).toEqual({
      path:'/matching/reports/evaluation%20%2F%20%E4%B8%AD%E6%96%87',
      reason:null,
    });
    expect(resolveDemoTaskResult(task('trend','trend_report:report / 中文','succeeded','position / 中文'))).toEqual({
      path:'/analysis/evolution?positionId=position%20%2F%20%E4%B8%AD%E6%96%87&resultReference=trend_report%3Areport%20%2F%20%E4%B8%AD%E6%96%87',
      reason:null,
    });
  });
});

describe('resolveEvidenceReference',()=>{
  test('简历证据携带明确原文区间跳转到对应简历',()=>{
    const result=resolveEvidenceReference({
      source_object_type:'validated_cv_snapshot',
      source_object_id:'snapshot-1',
      source_fragment_id:'src:1',
      quote:'负责模型服务性能优化',
      start:12,
      end:22,
      result_reference:'validated_cv_snapshot:snapshot-1#evidence:src:1:12-22',
      version:{resume_id:'resume-1'},
    });
    expect(result.reason).toBeNull();
    const url=new URL(result.path!,'http://localhost');
    expect(url.pathname).toBe('/profile/resumes');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      sourceEvidenceId:'src:1',
      sourceEvidenceStart:'12',
      sourceEvidenceEnd:'22',
      sourceEvidenceQuote:'负责模型服务性能优化',
      resumeId:'resume-1',
    });
  });

  test('无法确认原始来源的匹配证据不再跳回当前报告',()=>{
    expect(resolveEvidenceReference({
      source_object_type:'matching_evidence',
      source_object_id:'evaluation-1',
      source_fragment_id:'src:1',
      quote:'证据',
      start:0,
      end:2,
      result_reference:'matching_evidence:evaluation-1#evidence:src:1:0-2',
      version:{evaluation_id:'evaluation-1'},
    })).toEqual({path:null,reason:'当前证据没有可唯一定位的原始来源'});
  });
});
