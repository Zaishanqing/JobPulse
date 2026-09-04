import {App as AntApp,Button,ConfigProvider,Layout,Menu,Spin,Typography} from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {SystemNoticeHost} from '../shared/components/States';
import {ArrowLeftOutlined} from '@ant-design/icons';
import {useEffect,useState} from 'react';
import {BrowserRouter,MemoryRouter,Navigate,useLocation,useNavigate} from 'react-router-dom';

import {AuthProvider,useAuth} from '../features/auth/AuthContext';
import {LoginPage} from '../features/auth/components/LoginPage';
import {LoginPanel} from '../features/auth/components/LoginPanel';
import {PublicSite} from '../features/marketing/pages/PublicSite';
import {AppRouter} from './router';
import {buildMenuItems,navGroupKeyForPath} from './adminNavConfig';
import '../styles.css';

export {BuildWorkbench} from '../features/build/pages/BuildWorkbench';
export {EvidenceViewer} from '../features/evidence/components/EvidenceViewer';
export {ReviewWorkbench} from '../features/review/pages/ReviewWorkbench';

const routeTitles: Array<[RegExp,string,string]> = [
  [/^\/demo$/, '演示总览', '从真实资源检查三条业务演示链路'],
  [/^\/positions\/[^/]+$/, '岗位图谱', '查看岗位、技能关系与证据链'],
  [/^\/positions$/, '岗位全景', '浏览已发布且可追溯的岗位基线'],
  [/^\/emerging\/[^/]+$/, '新兴岗位详情', '查看定义、证据与演化信息'],
  [/^\/emerging\/[^/]+\/graph$/, '新兴岗位图谱', ''],
  [/^\/emerging$/, '新兴岗位', ''],
  [/^\/data\/jds$/, 'JD 数据中心', '导入、解析、确认并发布岗位描述'],
  [/^\/tasks$/, '任务中心', '查看任务状态、日志与恢复动作'],
  [/^\/analysis\/evolution$/, '能力演化', '比较图谱快照、技能趋势与解释风险'],
  [/^\/analysis\/trends$/, '趋势情报', '分析外部多来源信号、技能趋势与解释风险'],
  [/^\/profile\/resumes$/, '我的简历', '管理简历、技能证据与历史匹配记录'],
  [/^\/matching$/, '岗位匹配', '以简历证据解释岗位能力覆盖与差距'],
  [/^\/jobs\/[^/]+$/, '企业岗位详情', '查看已发布岗位与投递前置条件'],
  [/^\/jobs$/, '企业岗位', '浏览当前已发布的企业招聘岗位'],
  [/^\/matching\/evaluations\/[^/]+$/, '岗位匹配评估', '查看评分、双方证据与能力补齐路径'],
  [/^\/enterprise\/recruitment$/, '招聘工作台', '管理企业岗位、候选人评估与录用决策'],
  [/^\/evidence\/court$/, '证据法庭', '逐条核验证据链与来源可信度'],
  [/^\/evidence\/assistant$/, '证据问答', '基于正式证据连续追问，引用可回溯来源'],
  [/^\/governance\/evaluation$/, '评估与反馈', '运行质量评估并处理用户治理反馈'],
  [/^\/admin\/integration$/, '数据同步', '检查跨服务工作流与数据状态'],
  [/^\/admin\/model-service$/, '模型服务配置', '配置 JD 智能抽取使用的模型服务'],
  [/^\/admin\/build$/, '图谱构建', '构建、检查并发布岗位能力图谱'],
  [/^\/admin\/build\/records$/, '构建记录', '查看岗位历史构建版本与发布门禁'],
  [/^\/admin\/mappings$/, '岗位与技能映射', '维护主系统目录与图谱实体的对应关系'],
  [/^\/admin\/normalize$/, '技能归一化', '处理未解析技能与目录映射'],
  [/^\/admin\/review$/, '审核中心', '领取并处理待审核任务'],
  [/^\/admin\/versions$/, '版本管理', '对比历史版本并创建回滚版本'],
  [/^\/admin\/discovery$/, '岗位发现', '运行新兴岗位发现任务'],
  [/^\/admin\/emerging$/, '候选治理', '审核、发布并晋升岗位候选'],
];

const backRoutes: Array<[RegExp,string]> = [
  [/^\/admin\/build\/records$/, '/admin/build'],
  [/^\/positions\/[^/]+$/, '/positions'],
  [/^\/emerging\/[^/]+$/, '/emerging'],
  [/^\/jobs\/[^/]+$/, '/jobs'],
  [/^\/matching\/reports\/[^/]+$/, '/matching'],
  [/^\/enterprise\/recruitment\/reports\/[^/]+$/, '/enterprise/recruitment'],
];

const innerMenuKeys: Array<[RegExp,string]> = [
  [/^\/emerging\/[^/]+\/graph$/, '/emerging'],
  [/^\/admin\/build\/records$/, '/admin/build'],
  [/^\/positions\/[^/]+$/, '/positions'],
  [/^\/emerging\/[^/]+$/, '/emerging'],
  [/^\/jobs\/[^/]+$/, '/jobs'],
  [/^\/matching\/reports\/[^/]+$/, '/matching'],
  [/^\/enterprise\/recruitment\/reports\/[^/]+$/, '/enterprise/recruitment'],
];

function resolveRoute(pathname:string){
  return routeTitles.find(([pattern])=>pattern.test(pathname))?.slice(1) as [string,string]|undefined;
}

function resolveBackRoute(pathname:string,search:string){
  const requested=new URLSearchParams(search).get('returnTo');
  if(requested&&requested.startsWith('/')&&!requested.startsWith('//'))return requested;
  if(/^\/emerging\/[^/]+\/graph$/.test(pathname))return pathname.slice(0,-'/graph'.length);
  return backRoutes.find(([pattern])=>pattern.test(pathname))?.[1];
}

function resolveMenuSelected(pathname:string){
  return innerMenuKeys.find(([pattern])=>pattern.test(pathname))?.[1]??pathname;
}

function BrandMark(){
  return <img className="brand-mark" src="/jobpulse-logo.png" alt="" aria-hidden="true"/>;
}

function Shell(){
  const nav=useNavigate();
  const location=useLocation();
  const {user,loading,can}=useAuth();
  const selectedKey=resolveMenuSelected(location.pathname);
  const activeGroupKey=navGroupKeyForPath(selectedKey);
  // 分组导航：路由命中哪个组就展开哪个组，用户手动开合的状态保留。
  const [openKeys,setOpenKeys]=useState<string[]>(activeGroupKey?[activeGroupKey]:[]);
  useEffect(()=>{
    const id=requestAnimationFrame(()=>setOpenKeys(keys=>activeGroupKey&&!keys.includes(activeGroupKey)?[...keys,activeGroupKey]:keys));
    return ()=>cancelAnimationFrame(id);
  },[activeGroupKey]);
  // 未登录不渲染壳层：整页登录面取代空侧边栏与无效顶栏。
  if(loading)return <div className="boot-screen"><Spin description="正在验证账号权限"/></div>;
  if(!user){
    if(location.pathname==='/login')return <LoginPage/>;
    if(location.pathname==='/'||location.pathname==='/features'||location.pathname==='/overview')return <PublicSite/>;
    return <Navigate to="/login" replace state={{returnTo:`${location.pathname}${location.search}`}}/>;
  }
  if(location.pathname==='/login'){
    const requested=(location.state as {returnTo?:string}|null)?.returnTo;
    const returnTo=requested?.startsWith('/')&&!requested.startsWith('//')?requested:'/positions';
    return <Navigate to={returnTo} replace/>;
  }
  if(location.pathname==='/features'||location.pathname==='/overview')return <PublicSite/>;
  const items=buildMenuItems(can,user.role);
  const [title,description]=resolveRoute(location.pathname)??['JobPulse','岗位能力动态演化工作台'];
  const backTo=resolveBackRoute(location.pathname,location.search);

  return (
    <Layout className="root app-shell">
      <a className="skip-link" href="#main-content">跳到主内容</a>
      <Layout.Sider width={256} collapsedWidth={0} breakpoint="md" theme="light" className="app-sider">
        <div className="brand"><BrandMark/><span>Job<span className="brand-pulse">Pulse</span></span></div>
        <div className="nav-caption">工作空间</div>
        <Menu
          className="app-menu"
          theme="light"
          mode="inline"
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={keys=>setOpenKeys(keys as string[])}
          onClick={item=>nav(item.key)}
          items={items}
        />
      </Layout.Sider>
      <Layout className="app-main">
        <Layout.Header className="top">
          {backTo&&<Button type="text" icon={<ArrowLeftOutlined/>} className="back-nav-button" onClick={()=>nav(backTo)}>返回</Button>}
          {!backTo&&<div className="route-context">
            <span className="mobile-wordmark">JobPulse</span>
            {/* 顶部栏承担全局定位：固定工作台层级，当前页面名称由路由统一提供。 */}
            <div className="route-breadcrumb" aria-label="页面位置">
              <Typography.Text className="route-parent">工作台</Typography.Text>
              <span className="route-separator" aria-hidden="true">/</span>
              <Typography.Text className="route-title">{title}</Typography.Text>
            </div>
            <Typography.Text className="route-description">{description}</Typography.Text>
          </div>}
          <div className="top-actions">
            <LoginPanel/>
          </div>
        </Layout.Header>
        <Layout.Content className="content" id="main-content">
          <div className="workspace-surface">
            <AppRouter/>
          </div>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

export default function Root({initialPath}:{initialPath?:string}){
  const app=(
    <AntApp><SystemNoticeHost/><AuthProvider><Shell/></AuthProvider></AntApp>
  );
  return (
    <ConfigProvider locale={zhCN} theme={{
      token:{
        colorPrimary:'#b94d2e',
        colorInfo:'#b94d2e',
        colorSuccess:'#1f883d',
        colorWarning:'#b58a62',
        colorError:'#b94a3b',
        colorText:'#252827',
        colorTextSecondary:'#696861',
        colorBgLayout:'#f6f2ea',
        colorBgContainer:'#fffdfc',
        colorBorder:'#ded7cc',
        borderRadius:10,
        borderRadiusLG:14,
        fontSize:15,
        boxShadowSecondary:'0 14px 36px rgba(53,43,34,.12)',
        fontFamily:'"Noto Sans SC","Microsoft YaHei","PingFang SC",system-ui,sans-serif',
      },
      components:{
        Button:{fontWeight:600},
        Menu:{
          itemBg:'transparent',
          subMenuItemBg:'transparent',
          itemColor:'#514f49',
          itemHoverColor:'#a94429',
          itemHoverBg:'#f8efe9',
          itemSelectedBg:'#f3e3d9',
          itemSelectedColor:'#a94429',
          itemBorderRadius:10,
        },
        Table:{headerBg:'#f2eee7',headerColor:'#4b4943',rowHoverBg:'#faf4ed',selectionColumnWidth:64},
        Card:{headerBg:'transparent'},
      },
    }}>
      {initialPath
        ?<MemoryRouter initialEntries={[initialPath]}>{app}</MemoryRouter>
        :<BrowserRouter>{app}</BrowserRouter>}
    </ConfigProvider>
  );
}
