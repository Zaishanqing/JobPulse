import type {MenuProps} from 'antd';
import {
  AimOutlined,
  ApartmentOutlined,
  AppstoreOutlined,
  CompassOutlined,
  DashboardOutlined,
  RiseOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from '@ant-design/icons';

type MenuItem=Required<MenuProps>['items'][number];

/** 侧边栏分组。只放真实存在的路由，角色/权限过滤后空组整体隐藏。 */
export type NavGroupKey='matching'|'graph'|'emerging'|'evidence'|'system';

export const navGroupMeta:Record<NavGroupKey,{label:string;icon:React.ReactNode}>={
  matching:{label:'能力匹配',icon:<AimOutlined/>},
  graph:{label:'能力图谱',icon:<ApartmentOutlined/>},
  emerging:{label:'新兴治理',icon:<RiseOutlined/>},
  evidence:{label:'证据治理',icon:<SafetyCertificateOutlined/>},
  system:{label:'系统管理',icon:<SettingOutlined/>},
};

const navGroupOrder:NavGroupKey[]=['matching','graph','emerging','evidence','system'];

export interface AdminRoute{
  path:string;
  permission:string;
  label:string;
  group:NavGroupKey;
  /** 路由保留（深链接/守卫），但不作为侧边栏独立入口（如归一化审核已并入审核中心 Tab）。 */
  hideInNav?:boolean;
}

export interface RoleRoute{
  path:string;
  roles:string[];
  label:string;
  group:NavGroupKey;
}

export const dataRoutes:Record<string,RoleRoute>={
  jds:{path:'/data/jds',roles:['enterprise_user','reviewer','admin','developer'],label:'JD 数据中心',group:'system'},
  tasks:{path:'/tasks',roles:['reviewer','admin','developer'],label:'任务中心',group:'system'},
  evolution:{path:'/analysis/evolution',roles:['reviewer','admin','developer'],label:'能力演化',group:'graph'},
  trends:{path:'/analysis/trends',roles:['reviewer','admin','developer'],label:'趋势情报',group:'graph'},
  resumes:{path:'/profile/resumes',roles:['personal_user'],label:'我的简历',group:'matching'},
  matching:{path:'/matching',roles:['personal_user'],label:'岗位匹配',group:'matching'},
  publishedJobs:{path:'/jobs',roles:['personal_user'],label:'企业岗位',group:'matching'},
  enterprise:{path:'/enterprise/recruitment',roles:['enterprise_user'],label:'招聘工作台',group:'matching'},
  court:{path:'/evidence/court',roles:['personal_user','enterprise_user','reviewer','admin','developer'],label:'证据法庭',group:'evidence'},
  rag:{path:'/evidence/assistant',roles:['personal_user','enterprise_user','reviewer','admin','developer'],label:'证据问答',group:'evidence'},
  governance:{path:'/governance/evaluation',roles:['reviewer','admin','developer'],label:'评估与反馈',group:'evidence'},
  modelSettings:{path:'/admin/model-service',roles:['admin','developer'],label:'模型服务配置',group:'system'},
};

export const demoRoute:RoleRoute={path:'/demo',roles:['personal_user','enterprise_user','reviewer','admin','developer'],label:'演示总览',group:'matching'};

/** RBAC routes are declared once and shared by navigation and route guards. */
export const adminRoutes:Record<string,AdminRoute>={
  integration:{path:'/admin/integration',permission:'integration.status.view',label:'数据同步',group:'system'},
  acquisition:{path:'/admin/acquisition',permission:'acquisition.read',label:'数据采集',group:'system'},
  build:{path:'/admin/build',permission:'kg.build.manage',label:'图谱构建',group:'graph'},
  mappings:{path:'/admin/mappings',permission:'kg.build.manage',label:'岗位与技能映射',group:'graph'},
  review:{path:'/admin/review',permission:'kg.review.manage',label:'审核中心',group:'graph'},
  normalize:{path:'/admin/normalize',permission:'kg.normalization.manage',label:'归一化审核',group:'graph',hideInNav:true},
  versions:{path:'/admin/versions',permission:'kg.version.manage',label:'图谱版本管理',group:'graph'},
  discovery:{path:'/admin/discovery',permission:'emerging.discovery.manage',label:'新兴岗位发现',group:'emerging'},
  emerging:{path:'/admin/emerging',permission:'emerging.candidate.manage',label:'新兴岗位候选',group:'emerging'},
};

export const adminRouteList:AdminRoute[]=Object.values(adminRoutes);

export function visibleAdminRoutes(can:(perm:string)=>boolean):AdminRoute[]{
  return adminRouteList.filter(route=>can(route.permission));
}

/** 浏览型页面保持平铺，其余按业务域折叠分组；空组不渲染；组内子项不带 icon。 */
export function buildMenuItems(can:(perm:string)=>boolean,role:string):MenuItem[]{
  const entries:Array<RoleRoute|AdminRoute>=[
    ...Object.values(dataRoutes).filter(route=>route.roles.includes(role)),
    ...visibleAdminRoutes(can),
  ];
  const items:MenuItem[]=[
    {key:demoRoute.path,icon:<DashboardOutlined/>,label:demoRoute.label},
    {key:'/positions',icon:<AppstoreOutlined/>,label:'岗位全景'},
    {key:'/emerging',icon:<CompassOutlined/>,label:'新兴岗位'},
    ...(role==='reviewer'||role==='admin'||role==='developer'?[{key:dataRoutes.trends.path,icon:<LineChartOutlined/>,label:dataRoutes.trends.label}]:[]),
  ];
  for(const groupKey of navGroupOrder){
    const children=entries
      .filter(entry=>entry.path!==dataRoutes.trends.path&&entry.group===groupKey&&!('hideInNav' in entry&&entry.hideInNav))
      .map(entry=>({key:entry.path,label:entry.label}));
    if(!children.length)continue;
    items.push({key:`group:${groupKey}`,icon:navGroupMeta[groupKey].icon,label:navGroupMeta[groupKey].label,children});
  }
  return items;
}

/** 供壳层在路由变化时展开命中的分组。 */
export function navGroupKeyForPath(path:string):string|null{
  if(path===dataRoutes.trends.path)return null;
  const entry=[...Object.values(dataRoutes),...adminRouteList].find(route=>route.path===path);
  return entry?`group:${entry.group}`:null;
}
