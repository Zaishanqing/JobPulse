import type {ReactNode} from 'react';
import {Tag} from 'antd';

// 全站状态标签的唯一入口:颜色只走 antd 语义色,由 ConfigProvider 映射到
// 设计系统色板(stable=翠绿、review=沙铜、risk=错误红)。
// 页面禁止再写 antd 预设色字符串(green/red/orange),那会绕过主题 token。
export type StatusTone='stable'|'review'|'risk'|'neutral';

const toneColor:Record<StatusTone,'success'|'warning'|'error'|undefined>={
  stable:'success',
  review:'warning',
  risk:'error',
  neutral:undefined,
};

export function StatusTag({tone='neutral',children}:{tone?:StatusTone;children:ReactNode}){
  return <Tag color={toneColor[tone]}>{children}</Tag>;
}
