import {Card,Collapse,Descriptions,Empty,Space,Tag,Typography} from 'antd';
import type {EvaluationContract,SemanticRetrievalEvidence} from '../types';
import {fragmentTypeLabel,normalizeDisplayText,readableSystemValue} from '../viewModels/presentation';

const safeErrorLabel=(value:string|null|undefined)=>value?'语义召回异常（内部原因已记录）':'语义召回异常';

const evidenceText=(evidence:SemanticRetrievalEvidence)=>{
  const position=evidence.position_evidence_ref;
  const candidate=evidence.evidence_ref;
  return {
    position:normalizeDisplayText(position.quote),
    candidate:normalizeDisplayText(candidate.quote),
  };
};

export function SemanticShadowSection({evaluation}:{evaluation:EvaluationContract}){
  const status=evaluation.semantic_shadow_status??evaluation.semantic_status??'disabled';
  const candidates=evaluation.semantic_candidates??[];
  const evidence=candidates.flatMap(candidate=>candidate.evidence);
  const fallback=evaluation.semantic_shadow_evidence??[];
  const rows=evidence.length?evidence:fallback;
  const model=evaluation.semantic_embedding_model??rows[0]?.embedding_model;
  const revision=evaluation.semantic_embedding_revision??rows[0]?.embedding_revision;
  const dimension=evaluation.semantic_embedding_dimension??rows[0]?.embedding_dimension;
  const normalized=evaluation.semantic_embedding_normalized??rows[0]?.embedding_normalized;
  const normalization=evaluation.semantic_embedding_normalization??rows[0]?.embedding_normalization;
  const representation=evaluation.semantic_vector_representation??rows[0]?.vector_representation;
  const similarity=evaluation.semantic_vector_similarity??rows[0]?.vector_similarity;
  const derivation=evaluation.semantic_text_derivation_version??rows[0]?.text_derivation_version;
  const indexRevision=evaluation.semantic_index_revision??rows[0]?.index_revision;
  const collection=evaluation.semantic_collection??rows[0]?.collection;

  return <Card className="profile semantic-shadow-section" title="证据语义候选召回">
    {status==='disabled'&&<Typography.Paragraph type="secondary">本次评估未启用证据语义候选召回。</Typography.Paragraph>}
    {status==='unavailable'&&<Space orientation="vertical" size={8}>
      <Typography.Paragraph type="danger">语义召回不可用</Typography.Paragraph>
      <Tag color="error">{safeErrorLabel(evaluation.semantic_error_code)}</Tag>
      <Typography.Text type="secondary">规则结果、正式总分和推荐等级仍来自规则评估。</Typography.Text>
    </Space>}
    {status==='available'&&<Space orientation="vertical" size={16} className="full">
      <Typography.Paragraph type="secondary">该区域展示语义召回的补充候选。</Typography.Paragraph>
      <Descriptions size="small" column={{xs:1,md:3}} items={[
        {key:'count',label:'候选数量',children:rows.length},
        {key:'latency',label:'延迟',children:evaluation.semantic_latency_ms===null||evaluation.semantic_latency_ms===undefined?'未返回':`${evaluation.semantic_latency_ms.toFixed(1)} 毫秒`},
        {key:'model',label:'模型版本',children:readableSystemValue(revision)},
        {key:'index',label:'索引版本',children:readableSystemValue(indexRevision)},
      ]}/>
      {rows.length?<Collapse items={rows.map((item,index)=>{
        const text=evidenceText(item);
        return {
          key:`${item.candidate_fragment_id}-${index}`,
          label:<Space wrap>
            <Tag>{fragmentTypeLabel(item.query_fragment_type)}</Tag>
            <Tag color="success">{fragmentTypeLabel(item.candidate_fragment_type)}</Tag>
            <Typography.Text>相似度 {item.similarity.toFixed(4)} · 排名 {item.rank}</Typography.Text>
          </Space>,
          children:<Descriptions size="small" column={1} items={[
            {key:'position',label:'岗位证据',children:text.position},
            {key:'candidate',label:'简历证据',children:text.candidate},
            {key:'lineage',label:'版本血缘',children:<Space wrap>
              <Tag>模型 {readableSystemValue(item.embedding_model)}</Tag>
              <Tag>版本已记录</Tag>
              <Tag>{item.embedding_dimension} 维</Tag>
              <Tag>{item.embedding_normalized?'已做二范数归一化':'未归一化'}</Tag>
              <Tag>{readableSystemValue(item.text_derivation_version)}</Tag>
              <Tag>{readableSystemValue(item.index_revision)}</Tag>
              {item.retrieval_trace_id&&<Typography.Text copyable={{text:item.retrieval_trace_id}}>追踪记录已保存</Typography.Text>}
            </Space>},
          ]}/>,
        };
      })}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次语义召回已完成，未发现额外证据候选。"/>}
      {(model||dimension||normalized!==undefined||normalization||representation||similarity||derivation||collection)&&<Descriptions size="small" column={{xs:1,md:3}} items={[
        {key:'model-id',label:'模型',children:readableSystemValue(model)},
        {key:'dimension',label:'维度',children:dimension??'未返回'},
        {key:'normalized',label:'归一化',children:normalized===undefined?'未返回':`${normalized?'是':'否'}${normalization?` · ${readableSystemValue(normalization)}`:''}`},
        {key:'representation',label:'表示',children:readableSystemValue(representation)},
        {key:'similarity',label:'相似度',children:readableSystemValue(similarity)},
        {key:'derivation',label:'文本派生版本',children:readableSystemValue(derivation)},
        {key:'collection',label:'向量集合',children:readableSystemValue(collection)},
      ]}/>}
    </Space>}
  </Card>;
}
