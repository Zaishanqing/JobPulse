const sections=[
  {id:'match-report-overview',label:'总览'},
  {id:'match-report-capabilities',label:'能力匹配'},
  {id:'match-report-gaps-actions',label:'差距与行动'},
  {id:'match-report-trust',label:'证据与可信度'},
];

export function MatchReportSectionNav(){
  return <nav className="match-report-section-nav" aria-label="报告章节导航">
    {sections.map(section=><button type="button" key={section.id} onClick={()=>document.getElementById(section.id)?.scrollIntoView?.({behavior:'smooth',block:'start'})}>{section.label}</button>)}
  </nav>;
}
