import {Avatar,Button,Space} from 'antd';
import {LogoutOutlined,UserOutlined} from '@ant-design/icons';
import {useAuth} from '../AuthContext';

export function LoginPanel(){
  const {user,logout}=useAuth();
  if(!user)return null;
  return <Button className="identity-button" onClick={()=>void logout()}>
    <Space size={9}>
      <Avatar size={28} icon={<UserOutlined/>}/>
      <span className="identity-copy">
        <strong>{user.username}</strong>
        <small>退出登录</small>
      </span>
      <LogoutOutlined/>
    </Space>
  </Button>;
}
