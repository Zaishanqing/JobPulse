import type {JDRecord} from '../types';

const sourceLabels:Record<string,string>={
  boss:'Boss 直聘',
  boss_zhipin:'Boss 直聘',
  zhipin:'Boss 直聘',
  liepin:'猎聘',
  feishu:'飞书招聘',
  feishu_recruitment:'飞书招聘',
  playwright:'网页采集',
  crawler_bundle:'采集导入',
  extraction_bundle:'批量导入',
  batch:'批量导入',
  manual:'手动导入',
  direct_text:'手动导入',
  enterprise_upload:'文件导入',
  file_upload:'文件导入',
  image_upload:'图片导入',
};

export function jdSourceLabel(jd:Pick<JDRecord,'source_platform'|'source_name'|'source_type'>){
  const knownLabel=(value:string|undefined|null)=>{
    if(!value)return undefined;
    const normalized=value.toLowerCase().replace(/[\s-]+/g,'_');
    if(sourceLabels[normalized])return sourceLabels[normalized];
    if(normalized.includes('liepin'))return sourceLabels.liepin;
    if(normalized.includes('feishu'))return sourceLabels.feishu;
    if(normalized.includes('zhipin')||normalized.includes('boss'))return sourceLabels.boss_zhipin;
    return undefined;
  };
  const platformLabel=knownLabel(jd.source_platform);
  if(platformLabel)return platformLabel;
  const sourceName=jd.source_name?.trim();
  const sourceNameLabel=knownLabel(sourceName);
  if(sourceNameLabel)return sourceNameLabel;
  const looksInternal=Boolean(sourceName&&(
    sourceName.length>36||sourceName.includes(':')||/[_-](?:jdv?\d?|[0-9a-f]{16,})(?:[_-]|$)/i.test(sourceName)
  ));
  if(sourceName&&!looksInternal)return sourceName;
  return knownLabel(jd.source_type)||'其他来源';
}
