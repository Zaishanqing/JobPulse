import {useEffect,useState} from 'react';
import {App,Button,Card,Form,Input,Space,Spin,Tag,Typography} from 'antd';
import {ApiError} from '../../../shared/api';
import {Failure} from '../../../shared/components/States';
import {
  getModelServiceConfig,
  saveModelServiceConfig,
  testModelServiceConnection,
  type ModelServiceConfig,
  type ModelServiceConfigInput,
} from '../api';

type FormValues={base_url:string;api_key?:string;model:string};

export function ModelServiceSettings(){
  const {message}=App.useApp();
  const [form]=Form.useForm<FormValues>();
  const [config,setConfig]=useState<ModelServiceConfig>();
  const [loading,setLoading]=useState(true);
  const [saving,setSaving]=useState(false);
  const [testing,setTesting]=useState(false);
  const [error,setError]=useState<ApiError>();

  const load=async()=>{
    setLoading(true);setError(undefined);
    try{
      const value=await getModelServiceConfig();
      setConfig(value);
      form.setFieldsValue({base_url:value.base_url,model:value.model,api_key:undefined});
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  };

  useEffect(()=>{
    let active=true;
    void getModelServiceConfig().then(value=>{
      if(!active)return;
      setConfig(value);
      form.setFieldsValue({base_url:value.base_url,model:value.model,api_key:undefined});
    }).catch(reason=>{if(active)setError(reason as ApiError)})
      .finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[form]);

  const values=async():Promise<ModelServiceConfigInput>=>{
    const result=await form.validateFields();
    return {
      base_url:result.base_url.trim(),
      model:result.model.trim(),
      ...(result.api_key?.trim()?{api_key:result.api_key.trim()}:{}),
    };
  };

  const save=async()=>{
    setSaving(true);setError(undefined);
    try{
      const next=await saveModelServiceConfig(await values());
      setConfig(next);form.setFieldValue('api_key',undefined);
      message.success('模型服务配置已保存');
    }catch(reason){setError(reason as ApiError)}
    finally{setSaving(false)}
  };

  const test=async()=>{
    setTesting(true);setError(undefined);
    try{await testModelServiceConnection(await values());message.success('连接成功')}
    catch(reason){setError(reason as ApiError)}
    finally{setTesting(false)}
  };

  return <>
    <div className="page-heading">
      <Typography.Title level={2}>模型服务配置</Typography.Title>
      <Typography.Paragraph type="secondary">配置 JD 智能抽取使用的 DeepSeek 模型服务。</Typography.Paragraph>
    </div>
    {error&&<Failure message={error.message} status={error.status} retry={()=>void load()}/>}
    <Card className="model-service-settings" title={<Space size={10}><span>DeepSeek</span>{config?.api_key_configured?<Tag color="success">已配置</Tag>:<Tag>未配置</Tag>}</Space>}>
      {loading?<div className="center-loading"><Spin/><span className="state-panel-hint">正在读取配置</span></div>:<Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item label="API 地址" name="base_url" rules={[{required:true,message:'请输入 API 地址'},{type:'url',message:'请输入有效的 API 地址'}]}>
          <Input placeholder="https://api.deepseek.com" autoComplete="url"/>
        </Form.Item>
        <Form.Item label="API Key" name="api_key" rules={config?.api_key_configured?[]:[{required:true,message:'请输入 API Key'}]}>
          <Input.Password placeholder={config?.api_key_configured?'已配置，留空保持不变':'请输入 API Key'} autoComplete="new-password"/>
        </Form.Item>
        <Form.Item label="模型名称" name="model" rules={[{required:true,message:'请输入模型名称'}]}>
          <Input placeholder="deepseek-v4-flash"/>
        </Form.Item>
        <Space>
          <Button loading={testing} disabled={saving} onClick={()=>void test()}>测试连接</Button>
          <Button type="primary" loading={saving} disabled={testing} onClick={()=>void save()}>保存配置</Button>
        </Space>
      </Form>}
    </Card>
  </>;
}
