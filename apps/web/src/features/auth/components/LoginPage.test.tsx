import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {MemoryRouter,Route,Routes} from 'react-router-dom';

import {AuthProvider} from '../AuthContext';
import {LoginPage} from './LoginPage';

const response=(data:unknown,status=200)=>Promise.resolve(new Response(JSON.stringify({code:status===200?0:status,message:status===200?'success':'failed',data,trace_id:'test-trace'}),{status,headers:{'Content-Type':'application/json'}}));

function renderLogin(){
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage/>}/>
          <Route path="/admin/build" element={<div>管理员工作台</div>}/>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(()=>{
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

test('同页注册管理员后调用 register、login、me 并进入对应工作台',async()=>{
  let registeredBody:unknown;
  const fetchMock=vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input);
    if(url.endsWith('/api/v1/auth/register')){
      registeredBody=JSON.parse(String(init?.body));
      return response({user_id:'admin-1',role:'admin',username:'new_admin'});
    }
    if(url.endsWith('/api/v1/auth/login'))return response({access_token:'admin-token',token_type:'bearer'});
    if(url.endsWith('/api/v1/auth/me'))return response({user_id:'admin-1',role:'admin',username:'new_admin',permissions:['account.manage']});
    return response({},404);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderLogin();

  fireEvent.click(screen.getByRole('button',{name:'首次使用？创建账号'}));
  expect(screen.getByRole('region',{name:'创建账号'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'管理员'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'企业'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'个人'})).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:'管理员'}));
  fireEvent.change(screen.getByPlaceholderText('请输入用户名'),{target:{value:'new_admin'}});
  fireEvent.change(screen.getByPlaceholderText('请输入密码'),{target:{value:'password123'}});
  fireEvent.change(screen.getByPlaceholderText('请再次输入密码'),{target:{value:'password123'}});
  fireEvent.change(screen.getByPlaceholderText('name@example.com'),{target:{value:'new_admin@example.com'}});
  fireEvent.change(screen.getByPlaceholderText('请输入手机号'),{target:{value:'13800000000'}});
  fireEvent.click(screen.getByRole('button',{name:'创建账号并进入工作台'}));

  expect(await screen.findByText('管理员工作台')).toBeInTheDocument();
  expect(localStorage.getItem('main_access_token')).toBe('admin-token');
  expect(registeredBody).toEqual({role:'admin',username:'new_admin',password:'password123',email:'new_admin@example.com',phone:'13800000000'});
  await waitFor(()=>expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me',expect.anything()));
});

test('可返回登录且原有三角色快速登录仍会填充演示账号',()=>{
  vi.stubGlobal('fetch',vi.fn());
  renderLogin();

  fireEvent.click(screen.getByRole('button',{name:'首次使用？创建账号'}));
  fireEvent.click(screen.getByRole('button',{name:'已有账号？返回登录'}));
  fireEvent.click(screen.getByRole('button',{name:'管理员'}));

  expect(screen.getByPlaceholderText('请输入用户名')).toHaveValue('demo_admin');
  expect(screen.getByPlaceholderText('请输入密码')).toHaveValue('password123');
  expect(screen.queryByLabelText('邮箱')).not.toBeInTheDocument();
});
