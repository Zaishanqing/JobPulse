import {expect,test} from 'vitest';
import type {DiscoveryCandidate} from '../api';
import {latestCandidatesByIdentity,sortCandidatesForReview} from './CandidateLifecycle';

const candidate=(overrides:Partial<DiscoveryCandidate>):DiscoveryCandidate=>({
  candidate_id:'candidate',status:'stable_emerging_role',first_seen_window_id:'2026-01-10..2026-01-12',last_seen_window_id:'2026-01-22..2026-01-24',age:5,current_cluster_id:'cluster',previous_cluster_ids:[],canonical_title:'Agentic RAG 应用工程师',display_title:'Agentic RAG 应用工程师',definition:{},identity_profile:{titles:[],skills:[],responsibilities:[],member_jd_ids:[],observed_window_ids:[]},evidence:{},support_count:20,company_coverage:4,skill_similarity:null,responsibility_similarity:null,title_similarity:null,membership_overlap:null,identity_similarity:.9,novelty_score:.7,emergence_score:.78,identity_stability:4,created_at:null,updated_at:null,
  ...overrides,
});

test('同一候选身份只保留最近观测结果，其他岗位不受影响',()=>{
  const old=candidate({candidate_id:'old'});
  const recent=candidate({candidate_id:'recent',first_seen_window_id:'2026-07-27..2026-07-29',last_seen_window_id:'2026-08-08..2026-08-10'});
  const distinct=candidate({candidate_id:'distinct',canonical_title:'AI Coding 算法工程师',display_title:'AI Coding 算法工程师'});

  const result=latestCandidatesByIdentity([old,distinct,recent]);

  expect(result.map(item=>item.candidate_id)).toEqual(['recent','distinct']);
});

test('最近时间相同时优先保留生命周期更成熟的候选',()=>{
  const incubating=candidate({candidate_id:'incubating',status:'incubating'});
  const stable=candidate({candidate_id:'stable',status:'stable_emerging_role'});

  expect(latestCandidatesByIdentity([incubating,stable])).toEqual([stable]);
});

test('可审核候选置顶，弱信号置底',()=>{
  const weak=candidate({candidate_id:'weak',status:'weak_signal',last_seen_window_id:'2026-08-08..2026-08-10'});
  const incubating=candidate({candidate_id:'incubating',status:'incubating'});
  const reviewable=candidate({candidate_id:'reviewable',status:'stable_emerging_role'});

  const result=sortCandidatesForReview(
    [weak,incubating,reviewable],
    item=>item.candidate_id==='reviewable',
  );

  expect(result.map(item=>item.candidate_id)).toEqual(['reviewable','incubating','weak']);
});
