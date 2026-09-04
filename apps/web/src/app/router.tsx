import {Result,Spin} from 'antd';
import type {ReactNode} from 'react';
import {Navigate,Route,Routes,useLocation,useParams} from 'react-router-dom';
import {BuildWorkbench} from '../features/build/pages/BuildWorkbench';
import {BuildRecordsWorkbench} from '../features/build/pages/BuildRecordsWorkbench';
import {JDWorkbench} from '../features/data/pages/JDWorkbench';
import {DemoOverview} from '../features/demo/pages/DemoOverview';
import {DiscoveryWorkbench} from '../features/emerging/pages/DiscoveryWorkbench';
import {EmergingDetail} from '../features/emerging/pages/EmergingDetail';
import {EmergingGraph} from '../features/emerging/pages/EmergingGraph';
import {EmergingList} from '../features/emerging/pages/EmergingList';
import {EmergingWorkbench} from '../features/emerging/pages/EmergingWorkbench';
import {EnterpriseRecruitment} from '../features/enterprise/pages/EnterpriseRecruitment';
import {PublishedJobBrowser} from '../features/enterprise/pages/PublishedJobBrowser';
import {PublishedJobDetail} from '../features/enterprise/pages/PublishedJobDetail';
import {EvidenceCourt} from '../features/evidence/pages/EvidenceCourt';
import {EvaluationGovernance} from '../features/governance/pages/EvaluationGovernance';
import {EvolutionWorkbench,TrendIntelligenceWorkbench} from '../features/evolution/pages/EvolutionWorkbench';
import {PositionGraph} from '../features/graph/pages/PositionGraph';
import {IntegrationWorkbench} from '../features/integration/pages/IntegrationWorkbench';
import {AcquisitionWorkbench} from '../features/acquisition/pages/AcquisitionWorkbench';
import {MappingWorkbench} from '../features/mappings/pages/MappingWorkbench';
import {MatchEvaluationPage} from '../features/matching/pages/MatchEvaluationPage';
import {MatchingWorkbench} from '../features/matching/pages/MatchingWorkbench';
import {UnresolvedWorkbench} from '../features/normalization/pages/UnresolvedWorkbench';
import {Panorama} from '../features/positions/pages/Panorama';
import {ResumeCenter} from '../features/profile/pages/ResumeCenter';
import {EvidenceAssistant} from '../features/rag/pages/EvidenceAssistant';
import {ReviewWorkbench} from '../features/review/pages/ReviewWorkbench';
import {TaskCenter} from '../features/tasks/pages/TaskCenter';
import {ModelServiceSettings} from '../features/settings/pages/ModelServiceSettings';
import {VersionWorkbench} from '../features/versions/pages/VersionWorkbench';
import {useAuth} from '../features/auth/AuthContext';
import {adminRoutes,dataRoutes,demoRoute} from './adminNavConfig';

function Protected({children,permission,roles}:{children:ReactNode;permission?:string;roles?:string[]}){
  const {user,loading,can}=useAuth();
  if(loading)return <div className="center-loading" aria-live="polite"><Spin size="large" description="正在验证账号权限"/></div>;
  // 壳层在未登录时整页渲染登录面，此分支仅作兜底。
  if(!user)return null;
  if(permission&&!can(permission))return <Result status="403" title="无权访问" subTitle="当前账户缺少访问此页面所需权限。"/>;
  if(roles&&!roles.includes(user.role))return <Result status="403" title="无权访问" subTitle="当前账号角色不能访问此页面。"/>;
  return <>{children}</>;
}

function LegacyEvidenceAssistantRedirect(){
  const location=useLocation();
  return <Navigate to={{pathname:dataRoutes.rag.path,search:location.search}} replace/>;
}

function LegacyMatchingEvaluationRedirect(){
  const {evaluationId=''}=useParams();
  return <Navigate to={`/matching/reports/${encodeURIComponent(evaluationId)}`} replace/>;
}

export function AppRouter(){
  return <Routes>
    <Route path="/" element={<Navigate to="/positions" replace/>}/>
    <Route path={demoRoute.path} element={<Protected roles={demoRoute.roles}><DemoOverview/></Protected>}/>
    <Route path="/positions" element={<Protected><Panorama/></Protected>}/>
    <Route path="/positions/:positionId" element={<Protected><PositionGraph/></Protected>}/>
    <Route path="/emerging" element={<Protected><EmergingList/></Protected>}/>
    <Route path="/emerging/:emergingId" element={<Protected><EmergingDetail/></Protected>}/>
    <Route path="/emerging/:emergingId/graph" element={<Protected><EmergingGraph/></Protected>}/>
    <Route path={dataRoutes.jds.path} element={<Protected roles={dataRoutes.jds.roles}><JDWorkbench/></Protected>}/>
    <Route path={dataRoutes.tasks.path} element={<Protected roles={dataRoutes.tasks.roles}><TaskCenter/></Protected>}/>
    <Route path={dataRoutes.evolution.path} element={<Protected roles={dataRoutes.evolution.roles}><EvolutionWorkbench/></Protected>}/>
    <Route path={dataRoutes.trends.path} element={<Protected roles={dataRoutes.trends.roles}><TrendIntelligenceWorkbench/></Protected>}/>
    <Route path={dataRoutes.resumes.path} element={<Protected roles={dataRoutes.resumes.roles}><ResumeCenter/></Protected>}/>
    <Route path={dataRoutes.matching.path} element={<Protected roles={dataRoutes.matching.roles}><MatchingWorkbench/></Protected>}/>
    <Route path={dataRoutes.publishedJobs.path} element={<Protected roles={dataRoutes.publishedJobs.roles}><PublishedJobBrowser/></Protected>}/>
    <Route path="/jobs/:jobId" element={<Protected roles={dataRoutes.publishedJobs.roles}><PublishedJobDetail/></Protected>}/>
    <Route path="/matching/reports/:evaluationId" element={<Protected roles={dataRoutes.matching.roles}><MatchEvaluationPage/></Protected>}/>
    <Route path="/matching/evaluations/:evaluationId" element={<Protected roles={dataRoutes.matching.roles}><LegacyMatchingEvaluationRedirect/></Protected>}/>
    <Route path={dataRoutes.enterprise.path} element={<Protected roles={dataRoutes.enterprise.roles}><EnterpriseRecruitment/></Protected>}/>
    <Route path="/enterprise/recruitment/reports/:evaluationId" element={<Protected roles={dataRoutes.enterprise.roles}><MatchEvaluationPage/></Protected>}/>
    <Route path={dataRoutes.court.path} element={<Protected roles={dataRoutes.court.roles}><EvidenceCourt/></Protected>}/>
    {/* 旧路径保留重定向，外部入口与历史链接不断。 */}
    <Route path="/evidence-assistant" element={<LegacyEvidenceAssistantRedirect/>}/>
    <Route path={dataRoutes.rag.path} element={<Protected roles={dataRoutes.rag.roles}><EvidenceAssistant/></Protected>}/>
    <Route path={dataRoutes.governance.path} element={<Protected roles={dataRoutes.governance.roles}><EvaluationGovernance/></Protected>}/>
    <Route path={dataRoutes.modelSettings.path} element={<Protected roles={dataRoutes.modelSettings.roles}><ModelServiceSettings/></Protected>}/>
    <Route path={adminRoutes.integration.path} element={<Protected permission={adminRoutes.integration.permission}><IntegrationWorkbench/></Protected>}/>
    <Route path={adminRoutes.acquisition.path} element={<Protected permission={adminRoutes.acquisition.permission}><AcquisitionWorkbench/></Protected>}/>
    <Route path={adminRoutes.build.path} element={<Protected permission={adminRoutes.build.permission}><BuildWorkbench/></Protected>}/>
    <Route path="/admin/build/records" element={<Protected permission={adminRoutes.build.permission}><BuildRecordsWorkbench/></Protected>}/>
    <Route path={adminRoutes.mappings.path} element={<Protected permission={adminRoutes.mappings.permission}><MappingWorkbench/></Protected>}/>
    <Route path={adminRoutes.normalize.path} element={<Protected permission={adminRoutes.normalize.permission}><UnresolvedWorkbench/></Protected>}/>
    <Route path={adminRoutes.review.path} element={<Protected permission={adminRoutes.review.permission}><ReviewWorkbench/></Protected>}/>
    <Route path={adminRoutes.versions.path} element={<Protected permission={adminRoutes.versions.permission}><VersionWorkbench/></Protected>}/>
    <Route path={adminRoutes.discovery.path} element={<Protected permission={adminRoutes.discovery.permission}><DiscoveryWorkbench/></Protected>}/>
    <Route path={adminRoutes.emerging.path} element={<Protected permission={adminRoutes.emerging.permission}><EmergingWorkbench/></Protected>}/>
    <Route path="*" element={<Result status="404" title="页面不存在"/>}/>
  </Routes>;
}
