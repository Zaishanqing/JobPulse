import {beforeEach,expect,test,vi} from 'vitest';

vi.mock('../../../shared/api',()=>({api:vi.fn()}));

import {api} from '../../../shared/api';
import {getFormalDiscoveryExperiment,importFormalExperimentResults,listFormalExperimentClusters,replayFormalDiscoveryExperiment,startDiscovery} from './index';

beforeEach(()=>vi.clearAllMocks());

test('D5 online diagnostic pins EMERGE v3.2 and the frozen short-window dataset',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({task_id:'task-1',status:'succeeded'});

  await startDiscovery();

  expect(call).toHaveBeenCalledWith('/position-clusters/tasks',expect.objectContaining({method:'POST'}));
  expect(JSON.parse(call.mock.calls[0][1].body)).toEqual({
    algorithm:'emerge_v3_2',
    dataset_id:'d5-short-window-main-v1-37585b4079dd',
  });
});

test('formal experiment replay starts the executable replay endpoint',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({status:'passed'});

  await replayFormalDiscoveryExperiment();

  expect(call).toHaveBeenCalledWith('/portal/admin/discovery-formal-experiment/replay',{
    method:'POST',
    body:'{}',
  });
});

test('formal experiment panel reads the immutable report endpoint',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({experiment_id:'EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823'});

  await getFormalDiscoveryExperiment();

  expect(call).toHaveBeenCalledWith('/portal/admin/discovery-formal-experiment');
});

test('formal experiment cluster detail reads the full 2811-cluster projection endpoint',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue([{cluster_key:'aiagent研发'}]);

  await listFormalExperimentClusters();

  expect(call).toHaveBeenCalledWith('/portal/admin/discovery-formal-experiment/clusters');
});

test('formal experiment results import posts into the publication chain',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({experiment_id:'EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823',imported:10,existing:0,cluster_keys:['aiagent研发']});

  await importFormalExperimentResults();

  expect(call).toHaveBeenCalledWith('/emerging-positions/import-formal-experiment',{
    method:'POST',
    body:'{}',
  });
});
