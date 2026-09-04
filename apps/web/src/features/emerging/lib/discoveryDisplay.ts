type ClusterDisplaySource={
  cluster_name?:string|null;
  generated_definition?:Record<string,unknown>;
  representative_titles?:string[];
};

const UUID_PATTERN=/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;
const HASH_PATTERN=/^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$/i;
const SOURCE_RECORD_ID_PATTERN=/^(?:\d{5,}|[A-Za-z]\d{8,})$/;
const OPAQUE_RECORD_ID_PATTERN=/^(?=.{20,}$)(?=.*\d)[A-Za-z0-9_:-]+$/;

const text=(value:unknown)=>typeof value==='string'&&value.trim()?value.trim():null;

export const isTechnicalIdentifier=(value:unknown)=>{
  const normalized=text(value);
  return Boolean(normalized&&(
    UUID_PATTERN.test(normalized)
    ||HASH_PATTERN.test(normalized)
    ||SOURCE_RECORD_ID_PATTERN.test(normalized)
    ||OPAQUE_RECORD_ID_PATTERN.test(normalized)
    ||/^(?:run|cluster|candidate|obs|skill|jd|emerging|position|task|version)[-_:.]/i.test(normalized)
  ));
};

export const discoveryWindowLabel=(value:unknown)=>{
  const normalized=text(value);
  if(!normalized)return '未标注窗口';
  const windowId=normalized.split('@')[0];
  const dateRange=windowId.match(/^(\d{4})-(\d{2})-(\d{2})\.\.(\d{4})-(\d{2})-(\d{2})$/);
  if(dateRange){
    const [,startYear,startMonth,startDay,endYear,endMonth,endDay]=dateRange;
    const start=`${startYear}.${startMonth}.${startDay}`;
    if(startYear===endYear&&startMonth===endMonth){
      return startDay===endDay?start:`${start}–${endMonth}.${endDay}`;
    }
    const end=startYear===endYear
      ?`${endMonth}.${endDay}`
      :`${endYear}.${endMonth}.${endDay}`;
    return `${start}–${end}`;
  }
  const historical=windowId.match(/^historical-(\d+)$/i);
  if(historical)return `历史样本（第 ${historical[1]} 批）`;
  const numbered=windowId.match(/^w(?:indow)?[-_ ]?(\d+)$/i);
  if(numbered)return `第 ${numbered[1]} 批观测`;
  return windowId;
};

export const clusterDisplayName=(cluster:ClusterDisplaySource)=>{
  const definition=text(cluster.generated_definition?.position_name);
  if(definition&&!isTechnicalIdentifier(definition))return definition;
  const representative=cluster.representative_titles?.map(text).find(value=>value&&!isTechnicalIdentifier(value));
  if(representative)return representative;
  const clusterName=text(cluster.cluster_name);
  if(clusterName&&!isTechnicalIdentifier(clusterName))return clusterName;
  return '待命名候选岗位';
};

export const readableObservationCluster=(clusterName:unknown,title:unknown)=>{
  const name=text(clusterName);
  if(name&&!isTechnicalIdentifier(name))return name;
  const observationTitle=text(title);
  return observationTitle&&!isTechnicalIdentifier(observationTitle)?`${observationTitle}岗位簇`:'待命名岗位簇';
};

const DIMENSIONS:Record<string,{label:string;description:string}>={
  growth:{label:'岗位需求增长',description:'历史窗口中有效岗位样本占比的增长趋势'},
  evidence_quality:{label:'证据质量',description:'综合考虑证据数量、字段覆盖、来源可靠性和原文可定位性'},
  result_stability:{label:'结果稳定性',description:'候选阈值小幅变化时，岗位簇成员是否保持稳定'},
  source_diversity:{label:'来源多样性',description:'去重后独立招聘来源的覆盖程度'},
  enterprise_coverage:{label:'企业覆盖度',description:'去重后不同企业的覆盖程度'},
  cross_window_persistence:{label:'跨窗口持续性',description:'包含真实岗位簇成员的历史窗口覆盖程度'},
  standard_position_distance:{label:'标准岗位差异度',description:'与最接近正式岗位的差异程度'},
};

export const dimensionLabel=(name:string)=>DIMENSIONS[name]?.label||'其他评分指标';
export const dimensionDescription=(name:string)=>DIMENSIONS[name]?.description||'用于新兴岗位识别的补充指标';

const RELATIONS:Record<string,string>={
  birth:'首次形成',
  continuation:'持续演化',
  continue:'持续演化',
  split:'拆分演化',
  merge:'合并演化',
  merged:'合并演化',
};

export const lineageRelationLabel=(value:unknown)=>{
  const normalized=text(value);
  return normalized?RELATIONS[normalized]||'其他演化关系':'未标注';
};
