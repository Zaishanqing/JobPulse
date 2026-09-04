import {expect,test} from 'vitest';
import {discoveryWindowLabel} from './discoveryDisplay';

test('将带运行标识的日期窗口显示为中文日期范围',()=>{
  expect(discoveryWindowLabel('2026-07-27..2026-07-29@recent-jd-202608'))
    .toBe('2026.07.27–07.29');
  expect(discoveryWindowLabel('2026-07-30..2026-08-01@recent-jd-202608'))
    .toBe('2026.07.30–08.01');
  expect(discoveryWindowLabel('2026-08-08..2026-08-08@recent-jd-202608'))
    .toBe('2026.08.08');
});

test('将旧式窗口编号解释为历史样本批次',()=>{
  expect(discoveryWindowLabel('historical-3')).toBe('历史样本（第 3 批）');
  expect(discoveryWindowLabel('window-2')).toBe('第 2 批观测');
});
