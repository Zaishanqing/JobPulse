import {ApartmentOutlined,ArrowRightOutlined,BarChartOutlined,DatabaseOutlined,SafetyCertificateOutlined,ShareAltOutlined} from '@ant-design/icons';
import {NavLink,Link,useLocation} from 'react-router-dom';
import './HeroBackground.css';

type MarketingPage='home'|'features'|'overview';

const navItems:[MarketingPage,string,string][]=[
  ['home','首页','/'],
  ['features','功能介绍','/features'],
  ['overview','数据概览','/overview'],
];

function BrandMark(){
  return <img className="brand-mark" src="/jobpulse-logo.png" alt="" aria-hidden="true"/>;
}

export function MarketingHeader({active}:{active?:MarketingPage}){
  return <header className="marketing-header">
    <Link className="marketing-brand" to="/" aria-label="JobPulse 首页">
      <BrandMark/><span>Job<span className="brand-pulse">Pulse</span></span>
    </Link>
    <nav className="marketing-nav" aria-label="公开页面导航">
      {navItems.map(([key,label,path])=><NavLink
        key={key}
        end={key==='home'}
        className={`marketing-nav-link${active===key?' is-active':''}`}
        to={path}
      >{label}</NavLink>)}
      <a
        className="marketing-nav-link"
        href="/user-guide.html?v=20260901"
        target="_blank"
        rel="noreferrer"
        title="在新窗口打开用户手册"
      >用户手册</a>
    </nav>
    <div className="marketing-header-actions">
      <Link className="marketing-login" to="/login">登录使用</Link>
    </div>
  </header>;
}

function HeroOverlay(){
  return (
    <div className="home-hero__overlay" aria-hidden="true">
      <div className="match-score">
        <span className="match-score__label">智能匹配</span>
        <strong className="match-score__value">98%</strong>
        <span className="match-score__line"/>
      </div>
      <article className="visual-card visual-card--candidate">
        <div className="visual-card__icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="8" r="3.4"/>
            <path d="M5.8 19c.8-3.1 3.1-4.8 6.2-4.8s5.4 1.7 6.2 4.8"/>
          </svg>
        </div>
        <div className="visual-card__body">
          <strong>候选人</strong>
          <span className="placeholder-line placeholder-line--long"/>
          <span className="placeholder-line placeholder-line--short"/>
        </div>
      </article>
      <article className="visual-card visual-card--position">
        <div className="visual-card__icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <rect x="4" y="7.5" width="16" height="11.5" rx="2"/>
            <path d="M9 7.5V6.2A2.2 2.2 0 0 1 11.2 4h1.6A2.2 2.2 0 0 1 15 6.2v1.3"/>
            <path d="M4 12h16"/>
          </svg>
        </div>
        <div className="visual-card__body">
          <strong>职位</strong>
          <span className="placeholder-line placeholder-line--long"/>
          <span className="placeholder-line placeholder-line--short"/>
        </div>
      </article>
      <article className="visual-card visual-card--insight">
        <div className="visual-card__icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M5.5 19v-5"/>
            <path d="M12 19V7.5"/>
            <path d="M18.5 19v-8"/>
          </svg>
        </div>
        <div className="visual-card__body">
          <strong>数据洞察</strong>
          <span className="placeholder-line placeholder-line--long"/>
          <span className="placeholder-line placeholder-line--short"/>
        </div>
      </article>
      <article className="visual-card visual-card--graph">
        <div className="visual-card__icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <circle cx="6" cy="6.5" r="2.2"/>
            <circle cx="18" cy="9" r="2.2"/>
            <circle cx="10.5" cy="18" r="2.2"/>
            <path d="M8 7.7l7.8 1"/>
            <path d="M7 8.7l2.8 8"/>
            <path d="M17.2 10.9l-5.4 5.5"/>
          </svg>
        </div>
        <div className="visual-card__body">
          <strong>人才知识图谱</strong>
          <span className="placeholder-line placeholder-line--long"/>
          <span className="placeholder-line placeholder-line--short"/>
        </div>
      </article>
    </div>
  );
}

// 保留旧公开首页主体，后续营销页面可复用。
export function LegacyHomePage(){
  return <main className="home-page">
    <section className="marketing-hero home-hero">
      <div className="home-hero__background" aria-hidden="true"/>
      <div className="home-hero__inner">
        <div className="marketing-hero-copy home-hero__content">
          <h1 className="marketing-hero-title-image">
            <img src="/home-hero-title.png" alt="持续感知职业变化，让人才决策有据可循"/>
          </h1>
          <span className="marketing-heading-rule"/>
          <p className="marketing-hero-description">连接多源职业证据，识别岗位既有岗位能力演化、新兴职业与趋势信号，<br/>为人才匹配与职业发展提供可信依据。</p>
          <div className="marketing-hero-actions">
            <Link className="marketing-button marketing-button-primary" to="/login">登录使用<ArrowRightOutlined aria-hidden="true"/></Link>
            <Link className="marketing-button marketing-button-secondary" to="/features">了解详情</Link>
          </div>
          <div className="marketing-proof-list" aria-label="产品能力">
            <span><small>可信知识</small><strong>证据可追溯</strong></span>
            <span><small>职业感知</small><strong>变化可发现</strong></span>
            <span><small>人才决策</small><strong>结果可解释</strong></span>
          </div>
        </div>
        <HeroOverlay/>
      </div>
    </section>
  </main>;
}

function HomePage(){
  return <LegacyHomePage/>;
}

function FeaturesPage(){
  const chains=[
    {icon:<ApartmentOutlined/>,title:'01｜可信职业知识',flow:'多源数据 → 证据 → 审核发布 → 版本知识',text:'从多源岗位数据中提取职业事实，以证据、来源版本和审核机制约束正式发布，形成可信、可追溯、可持续演化的职业知识基础。',cta:'查看知识治理'},
    {icon:<ShareAltOutlined/>,title:'02｜职业动态感知',flow:'既有岗位能力演化 → 新兴岗位发现 → 趋势信号',text:'基于版本化职业知识与多源外部信号，持续识别岗位能力演化、新兴职业与趋势变化，形成面向职业世界的动态认知。',cta:'查看动态感知'},
    {icon:<BarChartOutlined/>,title:'03｜人才决策推理',flow:'人才画像 → 正式匹配 → 差距解释 → 假设分析 → 行动规划',text:'基于候选人事实与岗位要求开展多维匹配和能力差距分析，通过假设分析推演与最小行动集生成可执行的职业发展路径。',cta:'查看人才决策'},
  ];
  return <main className="marketing-subpage marketing-features-page">
    <div className="marketing-subpage-heading"><h1>三类核心能力<br/>贯通职业认知到人才决策</h1><p>JobPulse 从可信职业知识出发，持续感知职业变化，并将动态认知转化为可解释、可执行的人才决策。</p></div>
    <div className="marketing-feature-grid">
      {chains.map(chain=><article className="marketing-feature" key={chain.title}><div className="marketing-feature-heading"><span className="marketing-feature-icon">{chain.icon}</span><h2>{chain.title}</h2></div><p className="marketing-feature-flow">{chain.flow}</p><p>{chain.text}</p><Link to="/login">{chain.cta}<ArrowRightOutlined aria-hidden="true"/></Link></article>)}
    </div>
  </main>;
}

function OverviewPage(){
  const overview=[
    {icon:<ApartmentOutlined/>,label:'岗位数据',value:'岗位全景',detail:'浏览已发布且可追溯的岗位基线'},
    {icon:<DatabaseOutlined/>,label:'技能关系',value:'能力图谱',detail:'从技能点粒度理解岗位能力结构'},
    {icon:<BarChartOutlined/>,label:'匹配分析',value:'差距报告',detail:'用简历证据解释人岗覆盖与差距'},
    {icon:<SafetyCertificateOutlined/>,label:'治理状态',value:'版本发布',detail:'保留审核、来源与不可变版本记录'},
  ];
  return <main className="marketing-subpage marketing-overview-page">
    <div className="marketing-subpage-heading marketing-overview-heading"><h1>看清岗位能力如何流动</h1><p>岗位、能力、匹配与版本变化在 JobPulse 中彼此关联，每一条结果都能追溯到它的来源。</p></div>
    <div className="marketing-overview-grid">
      {overview.map(item=><article className="marketing-overview-card" key={item.label}><span className="marketing-feature-icon">{item.icon}</span><h2>{item.value}</h2><p>{item.detail}</p></article>)}
    </div>
    <div className="marketing-overview-note"><span><SafetyCertificateOutlined/></span><p><b>证据优先，结论可追溯</b><br/>进入系统后，岗位、技能、匹配与发布状态都会回到明确的数据源和处理记录。</p><Link className="marketing-button marketing-button-primary" to="/login">登录查看<ArrowRightOutlined aria-hidden="true"/></Link></div>
  </main>;
}

export function PublicSite(){
  const {pathname}=useLocation();
  const active:MarketingPage=pathname==='/features'?'features':pathname==='/overview'?'overview':'home';
  return <div className="marketing-site">
    <MarketingHeader active={active}/>
    {active==='features'?<FeaturesPage/>:active==='overview'?<OverviewPage/>:<HomePage/>}
  </div>;
}
