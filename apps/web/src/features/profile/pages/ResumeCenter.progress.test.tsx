import {act,renderHook} from '@testing-library/react';
import {afterEach,describe,expect,test,vi} from 'vitest';

import {useSmoothPercent} from './useSmoothPercent';

describe('useSmoothPercent',()=>{
  afterEach(()=>vi.useRealTimers());

  test('同一次上传的显示进度不会因较小目标值而回退',()=>{
    vi.useFakeTimers();
    const {result,rerender}=renderHook(
      ({target,resetToken}:{target:number;resetToken:number})=>useSmoothPercent(target,resetToken),
      {initialProps:{target:68,resetToken:1}},
    );

    act(()=>vi.advanceTimersByTime(700));
    expect(result.current).toBe(68);

    rerender({target:24,resetToken:1});
    act(()=>vi.advanceTimersByTime(700));
    expect(result.current).toBe(68);

    rerender({target:82,resetToken:1});
    act(()=>vi.advanceTimersByTime(700));
    expect(result.current).toBe(82);
  });

  test('新上传通过重置标识从低进度重新开始',()=>{
    vi.useFakeTimers();
    const {result,rerender}=renderHook(
      ({target,resetToken}:{target:number;resetToken:number})=>useSmoothPercent(target,resetToken),
      {initialProps:{target:76,resetToken:1}},
    );

    act(()=>vi.advanceTimersByTime(700));
    expect(result.current).toBe(76);

    rerender({target:14,resetToken:2});
    act(()=>vi.advanceTimersByTime(700));
    expect(result.current).toBe(14);
  });
});
