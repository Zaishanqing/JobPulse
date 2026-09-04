/* eslint-disable react-refresh/only-export-components */
import {createContext,useCallback,useContext,useEffect,useMemo,useState,type ReactNode} from 'react';

import {AUTH_EXPIRED_EVENT,type CurrentUser} from '../../shared/api';
import {clearAccessToken,hasAccessToken,setAccessToken,type RegisterValues} from './api';
import {currentUser,login as loginRequest,logout as logoutRequest,register as registerRequest} from './api';

type AuthState={user:CurrentUser|null;loading:boolean;login:(values:{username:string;password:string})=>Promise<void>;register:(values:RegisterValues)=>Promise<void>;logout:()=>Promise<void>;can:(permission:string)=>boolean};

const AuthContext=createContext<AuthState|undefined>(undefined);

export function AuthProvider({children}:{children:ReactNode}){
  const [user,setUser]=useState<CurrentUser|null>(null);
  const [loading,setLoading]=useState(hasAccessToken());
  const refresh=useCallback(async()=>{if(!hasAccessToken()){setUser(null);setLoading(false);return}setLoading(true);try{setUser(await currentUser())}catch{clearAccessToken();setUser(null)}finally{setLoading(false)}},[]);
  useEffect(()=>{if(!hasAccessToken())return;currentUser().then(setUser).catch(()=>{clearAccessToken();setUser(null)}).finally(()=>setLoading(false))},[]);
  useEffect(()=>{
    const expire=()=>{setUser(null);setLoading(false)};
    window.addEventListener(AUTH_EXPIRED_EVENT,expire);
    return()=>window.removeEventListener(AUTH_EXPIRED_EVENT,expire);
  },[]);
  const login=useCallback(async(values:{username:string;password:string})=>{const token=await loginRequest(values);setAccessToken(token.access_token);await refresh()},[refresh]);
  const register=useCallback(async(values:RegisterValues)=>{await registerRequest(values);await login({username:values.username,password:values.password})},[login]);
  const logout=useCallback(async()=>{
    const shouldNotifyServer=hasAccessToken();
    setUser(null);
    try{
      if(shouldNotifyServer)await logoutRequest();
    }finally{
      clearAccessToken();
    }
  },[]);
  const value=useMemo(()=>({user,loading,login,register,logout,can:(permission:string)=>Boolean(user?.permissions.includes(permission))}),[loading,login,logout,register,user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('AuthProvider is required');return value}
