/*
  DIRECTION CONTRACT — login surface (seed key 89da3334, assigned candidate 5)
  THESIS: 登录是选择身份进入对应工作台；三个演示角色门构成构图主体，拒绝“深色空壳层 + 居中弹窗”的类别默认。
  OWN-WORLD: 暖象牙画布与纸白面板，陶土主操作，翠绿稳定态，沙铜证据点缀；石墨深色在登录面完全退出。面板为聚焦表面，允许一次柔和暖色投影（登记例外）。
  STORY: 访客一眼看到三种角色各自进入的工作台，点角色一键填充或直接输入账号，登录后进入对应权限的工作台。
  FIRST VIEWPORT: 单张居中纸白卡片承载品牌、三联角色入口、图谱母题、账号表单与可追溯注记，背景以暖色晕染和岗位工作物件线稿点缀，主操作在表单底部。
  FORM: role-doors 结构，concept-seed 指派候选 5（角色三联门）；seed key 89da3334。
  FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
*/
import {useState} from 'react';
import {Button,Form,Input,Typography} from 'antd';
import {ToastAlert as Alert} from '../../../shared/components/States';
import {ArrowLeftOutlined,BankOutlined,CrownOutlined,LockOutlined,MailOutlined,PhoneOutlined,SafetyCertificateOutlined,UserOutlined,UserSwitchOutlined} from '@ant-design/icons';
import {Link,useNavigate} from 'react-router-dom';
import {useAuth} from '../AuthContext';
import type {RegistrationRole} from '../api';
import {MarketingHeader} from '../../marketing/pages/PublicSite';

// 与 scripts/seed_bootstrap_accounts.py 创建的 Quickstart 身份一一对应，密码见 QUICKSTART.md。
const demoRoles=[
  {key:'admin',role:'admin',name:'管理员',account:'demo_admin',scope:'图谱构建、审核治理与版本发布',icon:<CrownOutlined/>},
  {key:'enterprise',role:'enterprise_user',name:'企业',account:'demo_enterprise',scope:'岗位管理、候选人评估与录用决策',icon:<BankOutlined/>},
  {key:'personal',role:'personal_user',name:'个人',account:'demo_personal',scope:'简历证据、岗位匹配与差距分析',icon:<UserOutlined/>},
] as const;
const DEMO_PASSWORD='password123';
const roleWorkbench:Record<RegistrationRole,string>={admin:'/admin/build',enterprise_user:'/enterprise/recruitment',personal_user:'/matching'};

type AuthFormValues={
  role?:RegistrationRole;
  username:string;
  password:string;
  confirmPassword?:string;
  email?:string;
  phone?:string;
};

/* 图谱母题：岗位能力图谱的几何缩影，颜色全部走 token。 */
function GraphMotif(){
  return <svg className="login-graph-motif" viewBox="0 0 150 92" aria-hidden="true">
    <g className="motif-edges">
      <line x1="28" y1="18" x2="78" y2="10"/><line x1="78" y1="10" x2="122" y2="26"/>
      <line x1="28" y1="18" x2="52" y2="52"/><line x1="52" y1="52" x2="98" y2="58"/>
      <line x1="122" y1="26" x2="98" y2="58"/><line x1="98" y1="58" x2="136" y2="66"/>
      <line x1="52" y1="52" x2="24" y2="72"/><line x1="78" y1="10" x2="98" y2="58"/>
    </g>
    <circle cx="28" cy="18" r="9" className="motif-node motif-node-terracotta"/>
    <circle cx="28" cy="15.5" r="2.6" className="motif-person"/><path d="M23.4 23.5a4.6 4.6 0 0 1 9.2 0Z" className="motif-person"/>
    <circle cx="78" cy="10" r="5" className="motif-node motif-node-copper"/>
    <circle cx="122" cy="26" r="9" className="motif-node motif-node-emerald"/>
    <circle cx="122" cy="23.5" r="2.6" className="motif-person"/><path d="M117.4 31.5a4.6 4.6 0 0 1 9.2 0Z" className="motif-person"/>
    <circle cx="52" cy="52" r="6" className="motif-node motif-node-sand"/>
    <circle cx="98" cy="58" r="11" className="motif-node motif-node-terracotta-soft"/>
    <circle cx="98" cy="54.5" r="3" className="motif-person motif-person-dark"/><path d="M92.6 64.5a5.4 5.4 0 0 1 10.8 0Z" className="motif-person motif-person-dark"/>
    <circle cx="136" cy="66" r="5" className="motif-node motif-node-emerald"/>
    <circle cx="24" cy="72" r="4" className="motif-node motif-node-copper"/>
  </svg>;
}

function GalaxyMotif(){
  return <svg className="login-galaxy-motif" viewBox="0 0 150 150" aria-hidden="true">
    <defs><path id="login-constellation-star" d="M0 -8 2 -2 8 0 2 2 0 8 -2 2 -8 0 -2 -2Z"/></defs>
    <path d="M28 43 32 82 68 88 72 52 28 43M72 52 95 65 116 58 135 36" className="constellation-line"/>
    <circle cx="28" cy="43" r="10" className="constellation-halo"/>
    <circle cx="68" cy="88" r="9" className="constellation-halo"/>
    <circle cx="135" cy="36" r="10" className="constellation-halo"/>
    <use href="#login-constellation-star" transform="translate(28 43) scale(.78)" className="constellation-star constellation-star-terracotta"/>
    <use href="#login-constellation-star" transform="translate(32 82) scale(.64)" className="constellation-star constellation-star-copper"/>
    <use href="#login-constellation-star" transform="translate(68 88) scale(.7)" className="constellation-star constellation-star-emerald"/>
    <use href="#login-constellation-star" transform="translate(72 52) scale(.58)" className="constellation-star constellation-star-sand"/>
    <use href="#login-constellation-star" transform="translate(95 65) scale(.65)" className="constellation-star constellation-star-terracotta"/>
    <use href="#login-constellation-star" transform="translate(116 58) scale(.55)" className="constellation-star constellation-star-copper"/>
    <use href="#login-constellation-star" transform="translate(135 36) scale(.74)" className="constellation-star constellation-star-emerald"/>
    <circle cx="18" cy="105" r="1.6" className="constellation-dust"/>
    <circle cx="49" cy="24" r="1.4" className="constellation-dust"/>
    <circle cx="88" cy="105" r="1.8" className="constellation-dust"/>
    <circle cx="126" cy="91" r="1.3" className="constellation-dust"/>
  </svg>;
}

/* 登录页背景点缀：用岗位工作台语义的线稿保持轻量，不进入交互层。 */
function StationeryDecor(){
  return <div className="login-stationery-decor" aria-hidden="true">
    <svg className="stationery-item stationery-notebook" viewBox="0 0 110 110">
      <rect x="20" y="18" width="68" height="74" rx="7" className="stationery-paper"/>
      <path d="M32 18v74M42 31h33M42 44h33M42 57h23" className="stationery-line"/>
      <circle cx="27" cy="31" r="2" className="stationery-dot"/><circle cx="27" cy="47" r="2" className="stationery-dot"/><circle cx="27" cy="63" r="2" className="stationery-dot"/>
    </svg>
    <svg className="stationery-item stationery-pen" viewBox="0 0 110 110">
      <path d="M34 69 75 28 85 38 44 79Z" className="stationery-paper"/>
      <path d="M26 87 34 69 44 79Z" className="stationery-paper"/>
      <path d="M75 28 82 21 92 31 85 38Z" className="stationery-accent"/>
    </svg>
    <svg className="stationery-item stationery-laptop" viewBox="0 0 110 110">
      <rect x="18" y="22" width="74" height="51" rx="5" className="stationery-paper"/>
      <rect x="25" y="29" width="60" height="37" rx="2" className="stationery-screen"/>
      <path d="M12 78h86l-7 9H19Z" className="stationery-paper"/>
      <path d="M46 82h18" className="stationery-line"/>
      <circle cx="55" cy="47" r="7" className="stationery-dot"/>
    </svg>
    <svg className="stationery-item stationery-resume" viewBox="0 0 110 110">
      <path d="M25 14h48l13 13v69H25Z" className="stationery-paper"/>
      <path d="M73 14v14h13M35 44h28M35 55h38M35 66h29" className="stationery-line"/>
      <circle cx="45" cy="31" r="7" className="stationery-accent"/>
      <path d="M35 39a10 10 0 0 1 20 0" className="stationery-accent"/>
    </svg>
  </div>;
}

export function LoginPage(){
  const [form]=Form.useForm<AuthFormValues>();
  const [mode,setMode]=useState<'login'|'register'>('login');
  const [selected,setSelected]=useState<string|null>(null);
  const [error,setError]=useState('');
  const [submitting,setSubmitting]=useState(false);
  const {login,register}=useAuth();
  const navigate=useNavigate();

  const selectRole=(role:typeof demoRoles[number])=>{
    if(mode==='login')form.setFieldsValue({username:role.account,password:DEMO_PASSWORD});
    else form.setFieldValue('role',role.role);
    setSelected(role.key);
    setError('');
  };
  const switchMode=()=>{
    setMode(current=>current==='login'?'register':'login');
    setSelected(null);
    setError('');
    form.resetFields();
  };
  const submit=async(values:AuthFormValues)=>{
    setSubmitting(true);
    try{
      if(mode==='login')await login({username:values.username,password:values.password});
      else{
        const role=values.role as RegistrationRole;
        await register({role,username:values.username,password:values.password,email:values.email!,phone:values.phone!});
        navigate(roleWorkbench[role],{replace:true});
      }
    }catch(reason){
      setError(reason instanceof Error?reason.message:(mode==='login'?'登录失败，请检查账号信息':'注册失败，请检查填写信息'));
    }finally{
      setSubmitting(false);
    }
  };

  return <div className="login-site">
    <MarketingHeader/>
    <main className="login-page">
      <Link className="login-back-link" to="/"><ArrowLeftOutlined aria-hidden="true"/>返回首页</Link>
      <StationeryDecor/>
      <div className="login-graph-decor" aria-hidden="true"><GraphMotif/></div>
      <div className="login-galaxy-decor" aria-hidden="true"><GalaxyMotif/></div>
      <section className={`login-panel login-panel-${mode}`} aria-label={mode==='login'?'账号登录':'创建账号'}>
      <div className="login-panel-inner">
        <div className="login-brand">
          <img className="brand-mark" src="/jobpulse-logo.png" alt="" aria-hidden="true"/>
          <span>Job<span className="brand-pulse">Pulse</span></span>
        </div>
        <div className="login-panel-head">
          <Typography.Paragraph className="login-panel-hint">{mode==='login'?'选择角色快速登录，或直接输入账号。':'选择身份并填写账号信息，注册后将自动登录。'}</Typography.Paragraph>
        </div>
        <div className="login-doors" role="group" aria-label={mode==='login'?'选择登录角色':'选择注册身份'}>
          {demoRoles.map(role=>
            <button
              key={role.key}
              type="button"
              aria-pressed={selected===role.key}
              title={`${role.scope}（${role.account}）`}
              className={`login-door${selected===role.key?' is-selected':''}`}
              onClick={()=>selectRole(role)}
            >
              <span className="login-door-content">
                <span className="login-door-icon" aria-hidden="true">{role.icon}</span>
                <span className="login-door-name">{role.name}</span>
              </span>
            </button>
          )}
        </div>
        {error&&<Alert className="login-error" type="error" showIcon title={error}/>}
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={submit}
          onValuesChange={()=>{if(mode==='login')setSelected(null);setError('');}}
        >
          {mode==='register'&&<Form.Item name="role" hidden rules={[{required:true,message:'请选择身份'}]}><Input/></Form.Item>}
          <Form.Item name="username" label="用户名" rules={[{required:true,message:'请输入用户名'},{min:3,message:'用户名至少 3 个字符'}]}>
            <Input autoComplete="username" size="large" prefix={<UserOutlined/>} placeholder="请输入用户名"/>
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{required:true,message:'请输入密码'},{min:mode==='register'?8:1,message:'密码至少 8 个字符'}]}>
            <Input.Password autoComplete={mode==='login'?'current-password':'new-password'} size="large" prefix={<LockOutlined/>} placeholder="请输入密码"/>
          </Form.Item>
          {mode==='register'&&<>
            <Form.Item name="confirmPassword" label="确认密码" dependencies={['password']} rules={[{required:true,message:'请再次输入密码'},({getFieldValue})=>({validator(_,value){return !value||getFieldValue('password')===value?Promise.resolve():Promise.reject(new Error('两次输入的密码不一致'));}})]}>
              <Input.Password autoComplete="new-password" size="large" prefix={<LockOutlined/>} placeholder="请再次输入密码"/>
            </Form.Item>
            <div className="register-contact-fields">
              <Form.Item name="email" label="邮箱" rules={[{required:true,message:'请输入邮箱'},{type:'email',message:'请输入有效邮箱'}]}>
                <Input autoComplete="email" size="large" prefix={<MailOutlined/>} placeholder="name@example.com"/>
              </Form.Item>
              <Form.Item name="phone" label="手机号" rules={[{required:true,message:'请输入手机号'}]}>
                <Input autoComplete="tel" size="large" prefix={<PhoneOutlined/>} placeholder="请输入手机号"/>
              </Form.Item>
            </div>
          </>}
          <Button block loading={submitting} htmlType="submit" type="primary" size="large">{mode==='login'?'登录并进入工作台':'创建账号并进入工作台'}</Button>
          <Button className="login-mode-switch" block type="link" onClick={switchMode}>{mode==='login'?'首次使用？创建账号':'已有账号？返回登录'}</Button>
        </Form>
      </div>
      </section>
      <div className="login-notes">
        <span><UserSwitchOutlined/>角色入口随权限变化</span>
        <span><SafetyCertificateOutlined/>证据链与发布版本保持可追溯</span>
      </div>
    </main>
  </div>;
}
