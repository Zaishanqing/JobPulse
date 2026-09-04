import {expect,test} from 'vitest';
import {jdSourceLabel} from './jdSourceLabel';

test('uses the linked recruiting platform instead of an internal bundle identifier',()=>{
  expect(jdSourceLabel({
    source_platform:'liepin',
    source_name:'position-cleaned-publishable-v3-recleaned-20260814:jdv1_6c36f8bb8735749b90c19d3be3b79eb6',
    source_type:'extraction_bundle',
  })).toBe('猎聘');
});

test('uses a short import label when platform metadata is unavailable',()=>{
  expect(jdSourceLabel({
    source_platform:null,
    source_name:'position-cleaned-publishable-v3-recleaned-20260814:jdv1_6c36f8bb8735749b90c19d3be3b79eb6',
    source_type:'extraction_bundle',
  })).toBe('批量导入');
});

test('keeps a short user-entered source name',()=>{
  expect(jdSourceLabel({source_platform:null,source_name:'企业官网',source_type:'manual'})).toBe('企业官网');
});
