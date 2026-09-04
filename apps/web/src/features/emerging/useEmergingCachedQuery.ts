import {useCallback,useEffect,useRef,useState} from 'react';
import {useAuth} from '../auth/AuthContext';
import {ApiError} from '../../shared/api';
import type {LoadState} from '../../shared/components/States';
import {loadEmergingCache,readEmergingCache} from './cache';

export function useEmergingCachedQuery<T>(key:string|null,loader:()=>Promise<T>){
  const {user}=useAuth();
  const userId=user?.user_id??'anonymous';
  const initial=key?readEmergingCache<T>(userId,key):undefined;
  const [state,setState]=useState<LoadState<T>>(
    initial===undefined?{kind:'loading'}:{kind:'success',data:initial},
  );
  const sequence=useRef(0);
  const identity=`${userId}:${key??'disabled'}`;
  const activeIdentity=useRef(identity);
  activeIdentity.current=identity;

  const execute=useCallback(async(force=false)=>{
    const requestSequence=++sequence.current;
    if(!key){
      return;
    }
    const cached=readEmergingCache<T>(userId,key);
    if(cached!==undefined)setState({kind:'success',data:cached});
    else setState({kind:'loading'});
    try{
      const data=await loadEmergingCache(userId,key,loader,force);
      if(sequence.current===requestSequence&&activeIdentity.current===identity){
        setState({kind:'success',data});
      }
    }catch(reason){
      if(sequence.current!==requestSequence||activeIdentity.current!==identity)return;
      const error=reason as ApiError;
      setState({kind:'error',message:error.message,status:error.status});
    }
  },[identity,key,loader,userId]);

  useEffect(()=>{
    const timer=window.setTimeout(()=>void execute(),0);
    return()=>{
      window.clearTimeout(timer);
    };
  },[execute]);

  return {state,reload:useCallback(()=>execute(true),[execute]),userId};
}
