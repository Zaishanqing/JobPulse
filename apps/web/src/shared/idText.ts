/**
 * ID 展示约定：业务名称和业务序号做主标签，内部 ID 不作为用户可读序号。
 * 仍保留 copyable 能力，便于排查时复制真实 ID，但页面不展示不可读的哈希片段。
 */

/** 内部 ID 仅作为复制内容，页面显示稳定的中文占位。 */
export const shortId=(value?:string|null):string=>{
  return value?'内部记录':'未返回';
};

/** 运行状态码在前端只展示中文文案。 */
const statusLabel:Record<string,string>={
  pending:'等待处理',pending_review:'待审核',queued:'排队中',created:'已创建',running:'处理中',processing:'处理中',reconciling:'同步中',
  succeeded:'已完成',completed:'已完成',current:'当前有效',approved:'已审核',claimed:'审核中',reviewing:'审核中',modified:'已调整',
  failed:'处理失败',rejected:'已驳回',cancelled:'已取消',revoked:'已撤销',stale:'已过期',needs_rematch:'需要重新匹配',
  draft:'草稿',published:'已发布',paused:'已暂停',active:'启用',inactive:'停用',reachable:'可达',unreachable:'不可达',
  reached:'已达目标',already_satisfied:'当前已满足',hard_blocked:'硬性条件阻断',position_evidence_insufficient:'岗位证据不足',no_positive_actions:'无正收益行动',budget_excluded:'时间预算不足',incomplete:'未完成',unknown:'未知',
};

export const statusText=(status?:string|null):string=>status?(statusLabel[status]??'状态未知'):'状态未知';

const positionDomainLabels:Record<string,string>={
  ai_intelligent_systems:'人工智能与智能系统',blockchain_web3:'区块链与可信计算',cloud_distributed:'云原生与分布式系统',
  computing_hardware:'计算系统与芯片',cybersecurity_privacy:'网络安全与隐私',data_engineering:'数据工程与数据库',
  digital_governance:'数字化治理与标准',embedded_iot_edge:'嵌入式、物联网与边缘计算',hci_graphics_xr:'人机交互、图形与 XR',
  network_communications:'网络与通信',quantum_computing:'量子信息与量子计算',robotics_autonomy:'机器人与自主系统',
  software_engineering:'软件工程',tech:'技术',ai:'人工智能与智能系统',ml:'机器学习',lang:'编程语言',frontend:'前端工程',
  ai_algorithm_research:'人工智能算法与研究',ai_application_engineering:'人工智能应用与智能系统',
  data_big_data:'数据与大数据',cloud_platform_operations:'云平台与运维可靠性',cybersecurity:'网络与数据安全',
  chip_hardware:'芯片与智能硬件',quantum_information:'量子信息技术',test_quality:'测试与质量保障',
  digital_product:'数字产品',user_experience_design:'用户体验与数字设计',project_delivery:'项目管理与数字化交付',
  solution_technical_service:'解决方案与技术服务',digital_operations:'数字化运营',tech_education:'信息技术教育与培训',
  data_production_evaluation:'数据生产与模型评测',it_management_analysis:'信息技术管理与业务分析',
};

/** 岗位领域仅展示业务中文，不把内部领域编码直接暴露给用户。 */
export const domainText=(value?:string|null):string=>{
  const text=value?.trim();
  if(!text)return '未分类领域';
  if(/[\u3400-\u9fff]/.test(text))return text;
  return positionDomainLabels[text.toLowerCase()]||'未分类领域';
};
