import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import Root from '../../../app/ApplicationShell';
import type {EmergingPosition} from '../api';
import {resetEmergingCacheForTests} from '../cache';

vi.mock('../../../GraphView',()=>({GraphView:({relations,onSelect,viewMode}:{relations:Array<{skill_id:string;canonical_name:string}>;onSelect:(id:string)=>void;viewMode:string})=><div aria-label="岗位技能关系图" data-view={viewMode}>{relations.map(item=><button key={item.skill_id} onClick={()=>onSelect(item.skill_id)}>{item.canonical_name}</button>)}</div>}));
const response=(data:unknown,status=200)=>Promise.resolve(new Response(JSON.stringify({code:status===200?0:status,message:status===200?'ok':'API 失败',data}),{status,headers:{'Content-Type':'application/json'}}));
const detail:EmergingPosition={
  emerging_id:'EM_GRAPH',cluster_id:'CL_GRAPH',position_name:'地图算法工程师',
  core_responsibilities:['优化路径推荐'],required_skills:[{raw_skill:'Python',evidence:[{source_jd_id:'formal-bundle:1',original_text_snippet:'使用 Python 开发路线推荐',data_source:'招聘平台',window_id:'2026-08-07'}]}],
  bonus_skills:[{raw_skill:'Rust'}],industry_scenarios:['地图导航'],germination_score:null,score_dimensions:{},
  evidence_jd_ids:['企业名','playwright'],status:'published',standard_position:null,
};
function setup(data=detail){
  localStorage.setItem('main_access_token','token');
  const fetch=vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')
    ?response({user_id:'USER',username:'reader',role:'personal_user',permissions:['emerging.read_published']})
    :url.endsWith('/api/v1/portal/emerging-positions/EM_GRAPH')?response(data):response(null,404));
  vi.stubGlobal('fetch',fetch);
  return fetch;
}
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();resetEmergingCacheForTests()});

test('详情按钮进入独立图谱、查看证据并返回详情，不请求标准图谱服务',async()=>{
  const fetch=setup();
  render(<Root initialPath="/emerging/EM_GRAPH"/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看图谱'}));
  expect(await screen.findByRole('heading',{name:'地图算法工程师能力图谱'})).toBeInTheDocument();
  const canvas=screen.getByLabelText('岗位技能关系图');
  expect(within(canvas).getByRole('button',{name:'Python'})).toBeInTheDocument();
  fireEvent.click(within(canvas).getByRole('button',{name:'Rust'}));
  expect(within(screen.getByLabelText('技能详情')).getByRole('heading',{name:'Rust'})).toBeInTheDocument();
  fireEvent.click(within(canvas).getByRole('button',{name:'Python'}));
  fireEvent.click(within(screen.getByLabelText('技能详情')).getByRole('button',{name:'查看证据'}));
  expect(await screen.findByRole('dialog')).toHaveTextContent('使用 Python 开发路线推荐');
  expect(screen.queryByText(/不能用于正式发布|尚未发布|强校验/)).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:/close|关闭/i}));
  fireEvent.click(screen.getByRole('button',{name:/返回/}));
  expect(await screen.findByRole('heading',{name:'岗位定义'})).toBeInTheDocument();
  expect(fetch.mock.calls.every(([url])=>url.endsWith('/auth/me')||url.includes('/portal/emerging-positions/'))).toBe(true);
});

test('图谱直接链接可加载，切换视图及画像明细',async()=>{
  setup();
  render(<Root initialPath="/emerging/EM_GRAPH/graph"/>);
  await screen.findByRole('heading',{name:'地图算法工程师能力图谱'});
  fireEvent.click(screen.getByText('层级树'));
  expect(screen.getByLabelText('岗位技能关系图')).toHaveAttribute('data-view','hierarchy');
  fireEvent.click(screen.getByRole('tab',{name:'核心职责'}));
  expect(await screen.findByText('优化路径推荐')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab',{name:'应用场景'}));
  expect(await screen.findByText('地图导航')).toBeInTheDocument();
});

test('没有技能时显示空态，不误用别的岗位图谱',async()=>{
  setup({...detail,required_skills:[],bonus_skills:[]});
  render(<Root initialPath="/emerging/EM_GRAPH/graph"/>);
  expect(await screen.findByText('暂无技能')).toBeInTheDocument();
  expect(screen.queryByLabelText('岗位技能关系图')).not.toBeInTheDocument();
});

test('发布表为空时，资产列表、详情、图谱及直接链接全程只读取资产',async()=>{
  localStorage.setItem('main_access_token','token');
  const asset={...detail,emerging_id:'formal:地图算法',source_kind:'discovery_asset',status:'discovered',support_jd_count:12,asset_definition:{field_evidence:{position_summary:{content:'地图路线推荐岗位'}}}};
  const fetch=vi.fn((url:string)=>{
    const path=decodeURIComponent(url);
    if(path.endsWith('/auth/me'))return response({user_id:'USER',username:'reader',role:'personal_user',permissions:['emerging.read_published']});
    if(path.endsWith('/portal/emerging-assets'))return response([asset]);
    if(path.endsWith('/portal/emerging-assets/formal:地图算法'))return response(asset);
    if(path.endsWith('/portal/emerging-position-signals'))return response({signals:[]});
    if(path.endsWith('/portal/emerging-positions'))return response([]);
    return response(null,404);
  });
  vi.stubGlobal('fetch',fetch);
  const mounted=render(<Root initialPath="/emerging"/>);
  fireEvent.click(await screen.findByRole('button',{name:/查看详情/}));
  expect(await screen.findByText('地图路线推荐岗位')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'人工优化'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'查看证据上下文'}));
  expect(await screen.findByRole('dialog')).toHaveTextContent('使用 Python 开发路线推荐');
  expect(screen.queryByText('该证据不能用于正式发布。')).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:/close|关闭/i}));
  fireEvent.click(screen.getByRole('button',{name:'查看图谱'}));
  expect(await screen.findByRole('heading',{name:'地图算法工程师能力图谱'})).toBeInTheDocument();
  expect(within(screen.getByLabelText('岗位技能关系图')).getByRole('button',{name:'Python'})).toBeInTheDocument();
  mounted.unmount();
  render(<Root initialPath="/emerging/formal%3A%E5%9C%B0%E5%9B%BE%E7%AE%97%E6%B3%95/graph"/>);
  expect(await screen.findByRole('heading',{name:'地图算法工程师能力图谱'})).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url])=>url.includes('/emerging-positions')||url.includes('knowledge-graph'))).toBe(false);
});

test('资产详情保留六处人工优化，保存后刷新与图谱读取修改后的画像',async()=>{
  localStorage.setItem('main_access_token','token');
  let current={...detail,emerging_id:'formal:地图算法',source_kind:'discovery_asset',status:'discovered',asset_definition:{field_evidence:{position_summary:{content:'原始概述'}}}};
  const fetch=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/auth/me'))return response({user_id:'ADMIN',username:'admin',role:'admin',permissions:['emerging.read_published','emerging.candidate.manage']});
    if(decodeURIComponent(url).endsWith('/portal/emerging-assets/formal:地图算法')){
      if(init?.method==='PUT'){
        const values=JSON.parse(String(init.body));
        current={...current,...values,asset_definition:{...current.asset_definition,field_evidence:values.field_evidence}};
      }
      return response(current);
    }
    return response(null,404);
  });
  vi.stubGlobal('fetch',fetch);
  const mounted=render(<Root initialPath="/emerging/formal%3A%E5%9C%B0%E5%9B%BE%E7%AE%97%E6%B3%95"/>);
  const optimize=await screen.findAllByRole('button',{name:'人工优化'});
  expect(optimize).toHaveLength(6);
  fireEvent.click(optimize[0]);
  const dialog=await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('岗位名称'),{target:{value:'人工优化的地图算法岗'}});
  fireEvent.change(within(dialog).getByLabelText('岗位概述'),{target:{value:'人工优化后的概述'}});
  fireEvent.click(within(dialog).getByRole('button',{name:'保存优化'}));
  expect(await screen.findByRole('heading',{name:'人工优化的地图算法岗'})).toBeInTheDocument();
  expect(screen.getByText('人工优化后的概述')).toBeInTheDocument();
  mounted.unmount();
  render(<Root initialPath="/emerging/formal%3A%E5%9C%B0%E5%9B%BE%E7%AE%97%E6%B3%95/graph"/>);
  expect(await screen.findByRole('heading',{name:'人工优化的地图算法岗能力图谱'})).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='PUT')).toHaveLength(1);
});
