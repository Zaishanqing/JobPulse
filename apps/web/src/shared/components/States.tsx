import {useEffect,useRef,type ReactNode} from 'react';
import {Alert as AntAlert,App,Button,Spin,Typography} from 'antd';
import type {AlertProps} from 'antd';
import {InboxOutlined} from '@ant-design/icons';
import {errorTitle,localizeSystemMessage} from '../api';

export type LoadState<T>=
  |{kind:'loading'}
  |{kind:'error';message:string;status?:number}
  |{kind:'success';data:T};

type ToastAlertProps=Omit<AlertProps,'message'>&{message?:ReactNode;announceOnceKey?:string};
type SystemNotice={type:'error'|'warning';duration:number;content:ReactNode;onceKey?:string};
const systemNoticeListeners=new Set<(notice:SystemNotice)=>void>();
const announcedSystemNotices=new Set<string>();

function publishSystemNotice(notice:SystemNotice){
  systemNoticeListeners.forEach(listener=>listener(notice));
}

/** 全站唯一消息宿主，复用 AntApp 的 message 队列，保证消息依次向下排列。 */
export function SystemNoticeHost(){
  const {message}=App.useApp();
  useEffect(()=>{
    const listener=(notice:SystemNotice)=>{
      if(notice.onceKey){
        if(announcedSystemNotices.has(notice.onceKey))return;
        announcedSystemNotices.add(notice.onceKey);
      }
      void message.open({type:notice.type,duration:notice.duration,content:notice.content});
    };
    systemNoticeListeners.add(listener);
    return()=>{systemNoticeListeners.delete(listener)};
  },[message]);
  return null;
}

function noticeText(value:ReactNode){
  return typeof value==='string'||typeof value==='number'?String(value):'';
}

/**
 * 错误和警告沿用系统既有的顶部居中胶囊消息；信息和成功提示仍可作为页内说明。
 * 这样业务失败不会再撑出整块红色或黄色卡片。
 */
export function ToastAlert({type='info',title,message,description,action,announceOnceKey,...props}:ToastAlertProps){
  const rawHeading=title??message;
  const heading=typeof rawHeading==='string'?localizeSystemMessage(rawHeading):rawHeading;
  const localizedDescription=typeof description==='string'?localizeSystemMessage(description):description;
  const signature=[type,noticeText(heading),noticeText(localizedDescription)].join('|');
  const announced=useRef('');
  const isFloating=type==='error'||type==='warning';
  useEffect(()=>{
    if(!isFloating||announced.current===signature)return;
    announced.current=signature;
    publishSystemNotice({
      type,
      duration:action?8:5,
      onceKey:announceOnceKey,
      content:<span className="system-toast-content">
        <span><span className="system-toast-title">{heading}</span>{localizedDescription?<><span className="system-toast-divider">：</span>{localizedDescription}</>:null}</span>
        {action?<span className="system-toast-action">{action}</span>:null}
      </span>,
    });
  },[action,announceOnceKey,heading,isFloating,localizedDescription,signature,type]);
  if(isFloating)return null;
  return <AntAlert {...props} type={type} title={heading} description={localizedDescription} action={action}/>;
}

export function Failure({message,status,retry}:{message:string;status?:number;retry?:()=>void}){
  return <ToastAlert
    className="state-panel"
    type="error"
    showIcon
    title={errorTitle({status})}
    description={status===403?'当前账号没有访问该功能的权限。':message}
    action={retry?<Button onClick={retry}>重试</Button>:undefined}
  />;
}

export function EmptyState({text='暂无数据',centered=false}:{text?:string;centered?:boolean}){
  return <div className={`empty-state${centered?' center-empty':''}`}><InboxOutlined/><span>{text}</span></div>;
}

export function WorkbenchState<T>({title,state,retry,render}:{title?:string;state:LoadState<T[]>;retry:()=>void;render:(items:T[])=>ReactNode}){
  return <>
    {title&&<div className="page-heading"><Typography.Title level={2}>{title}</Typography.Title></div>}
    {state.kind==='loading'
      ?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>
      :state.kind==='error'
        ?<Failure {...state} retry={retry}/>
        :state.data.length===0
          ?<EmptyState centered/>
          :render(state.data)}
  </>;
}
