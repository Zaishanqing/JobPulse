import type {Relation,SkillClassification} from './shared/api';
import type {GraphData} from './rspressGraph/types';

// The canvas needs display identities and grouping, not published KG metrics.
export type GraphRelation=Pick<Relation,'skill_id'|'canonical_name'|'classifications'|'importance_level'>;

export type ReferenceGraphNode=GraphData['nodes'][number]&{
  nodeKind:'position'|'classification'|'skill';
  skillId?:string;
};

export type ReferenceGraphLink=GraphData['links'][number]&{
  level?:number;
};

export type ReferenceGraphData={
  nodes:ReferenceGraphNode[];
  links:ReferenceGraphLink[];
  showAll?:boolean;
};

const conceptOrder=['knowledge','practice','technology','transversal_skill'];
const technologyKindOrder=[
  'algorithm_model','framework','language','library_sdk','platform_service',
  'tool','database_storage','middleware_runtime','protocol_standard','hardware_system',
];

const displayNames:Record<string,string>={
  knowledge:'知识',
  practice:'方法与实践',
  technology:'技术栈',
  transversal_skill:'通用能力',
  algorithm_model:'算法与模型',
  language:'编程语言',
};

function primaryClassification(relation:GraphRelation,facet:SkillClassification['facet']){
  return relation.classifications?.find(item=>item.facet===facet&&item.is_primary);
}

function orderedClassifications(items:Map<string,SkillClassification>,order:string[]){
  return [...items.values()].sort((left,right)=>{
    const leftIndex=order.indexOf(left.code);
    const rightIndex=order.indexOf(right.code);
    if(leftIndex!==rightIndex)return (leftIndex<0?order.length:leftIndex)-(rightIndex<0?order.length:rightIndex);
    return left.name_zh.localeCompare(right.name_zh,'zh-CN');
  });
}

function labelFor(classification:SkillClassification){
  return displayNames[classification.code]??classification.name_zh;
}

export function buildReferenceGraphData(position:string,positionName:string,relations:GraphRelation[]):ReferenceGraphData{
  const nodes:ReferenceGraphNode[]=[{
    id:position,
    label:positionName,
    routePath:position,
    nodeKind:'position',
  }];
  const links:ReferenceGraphLink[]=[];
  const skillIds=new Set<string>();
  const concepts=new Map<string,SkillClassification>();
  const relationsByConcept=new Map<string,GraphRelation[]>();

  for(const relation of relations){
    const concept=primaryClassification(relation,'concept_class');
    if(!concept)throw new Error(`技能 ${relation.skill_id} 缺少概念性质分类`);
    concepts.set(concept.code,concept);
    const bucket=relationsByConcept.get(concept.code)??[];
    bucket.push(relation);
    relationsByConcept.set(concept.code,bucket);
  }

  for(const concept of orderedClassifications(concepts,conceptOrder)){
    const conceptId=`__concept:${concept.code}`;
    nodes.push({id:conceptId,label:labelFor(concept),routePath:conceptId,nodeKind:'classification'});
    links.push({source:position,target:conceptId,level:1});
    const conceptRelations=relationsByConcept.get(concept.code)??[];

    if(concept.code!=='technology'){
      for(const relation of conceptRelations){
        addSkillNode(nodes,links,skillIds,conceptId,relation,2);
      }
      continue;
    }

    const technologyKinds=new Map<string,SkillClassification>();
    const relationsByKind=new Map<string,GraphRelation[]>();
    for(const relation of conceptRelations){
      const kind=primaryClassification(relation,'technology_kind');
      if(!kind)throw new Error(`技术技能 ${relation.skill_id} 缺少技术形态分类`);
      technologyKinds.set(kind.code,kind);
      const bucket=relationsByKind.get(kind.code)??[];
      bucket.push(relation);
      relationsByKind.set(kind.code,bucket);
    }

    for(const kind of orderedClassifications(technologyKinds,technologyKindOrder)){
      const kindId=`__technology-kind:${kind.code}`;
      nodes.push({id:kindId,label:labelFor(kind),routePath:kindId,nodeKind:'classification'});
      links.push({source:conceptId,target:kindId,level:2});
      for(const relation of relationsByKind.get(kind.code)??[]){
        addSkillNode(nodes,links,skillIds,kindId,relation,3);
      }
    }
  }

  return {nodes,links};
}

export function buildDirectSkillGraphData(position:string,positionName:string,relations:GraphRelation[]):ReferenceGraphData{
  return {
    nodes:[
      {id:position,label:positionName,routePath:position,nodeKind:'position'},
      ...relations.map(relation=>({
        id:relation.skill_id,
        label:relation.canonical_name,
        routePath:relation.skill_id,
        nodeKind:'skill' as const,
        skillId:relation.skill_id,
        importanceLevel:relation.importance_level as ReferenceGraphNode['importanceLevel'],
      })),
    ],
    links:relations.map(relation=>({source:position,target:relation.skill_id,level:1})),
  };
}

export function buildReferenceGraphLayer(graph:ReferenceGraphData,parentId:string):ReferenceGraphData{
  const parent=graph.nodes.find(node=>node.id===parentId);
  if(!parent)throw new Error(`图谱节点 ${parentId} 不存在`);
  const links=graph.links.filter(link=>link.source===parentId);
  const childIds=new Set(links.map(link=>link.target));
  return {
    nodes:graph.nodes
      .filter(node=>node.id===parentId||childIds.has(node.id))
      .map(node=>({...node})),
    links:links.map(link=>({...link})),
  };
}

export function buildExpandedReferenceGraphLayer(
  graph:ReferenceGraphData,
  parentId:string,
  expandedChildId:string,
):ReferenceGraphData{
  return buildExpandedReferenceGraphPath(graph,parentId,[expandedChildId]);
}

export function buildExpandedReferenceGraphPath(
  graph:ReferenceGraphData,
  parentId:string,
  expandedPath:string[],
):ReferenceGraphData{
  const base=buildReferenceGraphLayer(graph,parentId);
  const nodes=[...base.nodes];
  const links=[...base.links];
  const existingIds=new Set(nodes.map(node=>node.id));
  for(const expandedNodeId of expandedPath){
    const expandedNode=nodes.find(node=>node.id===expandedNodeId&&node.nodeKind==='classification');
    if(!expandedNode)break;
    const childLinks=graph.links.filter(link=>link.source===expandedNodeId);
    const childIds=new Set(childLinks.map(link=>link.target));
    for(const node of graph.nodes){
      if(childIds.has(node.id)&&!existingIds.has(node.id)){
        nodes.push({...node});
        existingIds.add(node.id);
      }
    }
    links.push(...childLinks.map(link=>({...link})));
  }
  return {
    nodes,
    links,
    showAll:true,
  };
}

function addSkillNode(
  nodes:ReferenceGraphNode[],
  links:ReferenceGraphLink[],
  skillIds:Set<string>,
  parentId:string,
  relation:GraphRelation,
  level:number,
){
  if(skillIds.has(relation.skill_id))return;
  skillIds.add(relation.skill_id);
  nodes.push({
    id:relation.skill_id,
    label:relation.canonical_name,
    routePath:relation.skill_id,
    nodeKind:'skill',
    skillId:relation.skill_id,
    importanceLevel:relation.importance_level as ReferenceGraphNode['importanceLevel'],
  });
  links.push({source:parentId,target:relation.skill_id,level});
}
