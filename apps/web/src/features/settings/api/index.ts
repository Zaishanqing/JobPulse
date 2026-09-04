import {api} from '../../../shared/api';

export type ModelServiceConfig={
  provider:'deepseek';
  base_url:string;
  model:string;
  api_key_configured:boolean;
  version:number;
  updated_at:string|null;
};

export type ModelServiceConfigInput={base_url:string;model:string;api_key?:string};

export function getModelServiceConfig(){
  return api<ModelServiceConfig>('/system/model-service-config');
}

export function saveModelServiceConfig(input:ModelServiceConfigInput){
  return api<ModelServiceConfig>('/system/model-service-config',{method:'PUT',body:JSON.stringify(input)});
}

export function testModelServiceConnection(input:ModelServiceConfigInput){
  return api<{status:'available';message:string}>('/system/model-service-config/test',{method:'POST',body:JSON.stringify(input)});
}
