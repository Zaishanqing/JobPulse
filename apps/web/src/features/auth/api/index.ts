import {api,clearAccessToken,hasAccessToken,setAccessToken,type CurrentUser} from '../../../shared/api';
export {clearAccessToken,hasAccessToken,setAccessToken};
export type RegistrationRole='admin'|'enterprise_user'|'personal_user';
export type RegisterValues={role:RegistrationRole;username:string;password:string;email:string;phone:string};
export const register=(values:RegisterValues)=>api<{user_id:string;role:RegistrationRole;username:string}>('/auth/register',{method:'POST',body:JSON.stringify(values)});
export const login=(values:{username:string;password:string})=>api<{access_token:string}>('/auth/login',{method:'POST',body:JSON.stringify(values)});
export const currentUser=()=>api<CurrentUser>('/auth/me');
export const logout=()=>api<{logged_out:boolean}>('/auth/logout',{method:'POST',body:JSON.stringify({})});
