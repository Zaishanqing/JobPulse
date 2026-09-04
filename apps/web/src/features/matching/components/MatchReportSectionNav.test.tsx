import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {MatchReportSectionNav} from './MatchReportSectionNav';

afterEach(()=>{
  cleanup();
  document.body.replaceChildren();
});

test('each report navigation item scrolls its matching section to the start',()=>{
  const sectionIds=[
    'match-report-overview',
    'match-report-capabilities',
    'match-report-gaps-actions',
    'match-report-trust',
  ];
  const scrollHandlers=new Map<string,ReturnType<typeof vi.fn>>();

  sectionIds.forEach(id=>{
    const section=document.createElement('section');
    const scrollIntoView=vi.fn();
    section.id=id;
    section.scrollIntoView=scrollIntoView;
    scrollHandlers.set(id,scrollIntoView);
    document.body.appendChild(section);
  });

  render(<MatchReportSectionNav/>);
  ['总览','能力匹配','差距与行动','证据与可信度'].forEach((label,index)=>{
    fireEvent.click(screen.getByRole('button',{name:label}));
    expect(scrollHandlers.get(sectionIds[index])).toHaveBeenCalledWith({behavior:'smooth',block:'start'});
  });
});
