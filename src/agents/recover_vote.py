"""为本次虚构边界样例补一张超时票；另存修订包，不覆盖旧日志。"""
import argparse
import copy
import json
import shutil
from pathlib import Path

from .agent_signals import build_signals, enrich_minutes, prompt_snapshot, redact, validate_evidence
from .core import _check_response, _material_view, artifact, load_config, render_prompt, require
from .evidence import ObservedProvider, boundary_sample, now, verify_package, write_json
from .providers import ChatCompletionsProvider, SchedulerGatewayModel


def recover(source, output, provider=None, mode="real"):
    from src.scheduler.agents_bridge import finalize_register
    from src.scheduler.llm_gateway import LLMGateway
    source, output = Path(source), Path(output)
    require(not output.exists(), "E_CONFIG", "恢复输出必须是新目录")
    read = lambda name: json.loads((source/name).read_text(encoding="utf-8"))
    result, old = read('stage_result.json'), read('acceptance.json')
    require(old['mode'] == mode, "E_CONFIG", "禁止将离线结果升级成真实结果")
    minutes = copy.deepcopy(result['artifacts']['discussion_minutes']['data'])
    supplied = boundary_sample()
    require(supplied['config']['issue'] == minutes['issue'], "E_CONFIG", "只恢复同一边界样例议题")
    missing = set(('A1','A2','A3','A4'))-{v['agent_id'] for v in minutes['votes']}
    require(len(missing) == 1 and set(minutes['completed_steps']) == {'statement','query','vote'}, "E_CONFIG", "仅支持其余步骤完整且只缺一票")
    aid = next(iter(missing))
    require(any(s['code']=='E_AGENT_UNAVAILABLE' and s.get('agent_id')==aid and s['step']=='vote' for s in minutes['guard_signals']), "E_CONFIG", "仅恢复有记录的投票超时")
    usage = read('gateway_usage.json')
    require(usage['calls'] < 12, "E_BUDGET_LIMIT", "恢复后仍须符合 P3 的 12 次计费调用上限")
    run_id = old['run_id']
    supplied['run_id'] = run_id
    for a in supplied['artifacts'].values():
        a['meta']['run_id'] = run_id
    supplied['artifacts']['discussion_minutes'] = artifact(run_id,'P3','discussion_minutes',minutes)
    request = render_prompt(aid,'vote',supplied)
    observed = ObservedProvider(provider or ChatCompletionsProvider.from_env(),run_id,mode,max_calls=1)
    gateway = LLMGateway(observed)
    response = SchedulerGatewayModel(gateway)(request)
    visible, exclusive = _material_view(supplied,aid,load_config())
    vote = _check_response(response,aid,'vote',visible,exclusive)
    vote.update(id=f"VOTE-{minutes['issue']['issue_id']}-{minutes['round']}-{aid}",issue_id=minutes['issue']['issue_id'])
    minutes['votes'].append(vote)
    minutes['valid_vote_count'] = len(minutes['votes'])
    minutes['tally'] = {b:sum(v['ballot']==b for v in minutes['votes']) for b in ('FOR','AGAINST','ABSTAIN')}
    minutes['extensions']['prompt_snapshots'].append(prompt_snapshot(request))
    minutes['extensions']['recovery'] = {'source_package':source.name,'retry_agent':aid,'retry_count':1,'at':now(),'original_timeout_retained':True}
    minutes = enrich_minutes(minutes)
    original = [c['extensions']['original_stances'] for c in result['artifacts']['conflict_register']['data']['conflicts']]
    registered = []
    for c,stances in zip(result['artifacts']['conflict_register']['data']['conflicts'],original):
        registered.append({**c,'stances':stances})
    register = finalize_register(minutes,registered)
    result['artifacts']['discussion_minutes']['data'] = minutes
    result['artifacts']['conflict_register']['data'] = register
    result['artifacts']['agent_signals']['data'] = build_signals(minutes,register,run_id)
    result['budget_used'] += gateway.calls
    vote_event = {'event_id':'','run_id':run_id,'ts':now(),'stage':'P3','event_type':'agent.vote','actor':aid,'payload_ref':'discussion_minutes:'+vote['id']}
    result['events'].append(vote_event)
    errors = validate_evidence(minutes,register,True)
    acceptance = {**old,'status':'passed' if not errors else 'failed','errors':errors,'evidence':build_signals(minutes,register,run_id),
                  'recovery':minutes['extensions']['recovery']}
    acceptance['actual_model_attempts' if mode=='real' else 'fixture_attempts'] += 1
    usage['calls'] += gateway.calls
    usage['by_stage']['P3'] += gateway.calls
    usage['by_agent'][aid] += gateway.calls
    shutil.copytree(source,output)
    for k,v in result['artifacts'].items():
        write_json(output/(k+'.json'),v)
    write_json(output/'stage_result.json',result)
    write_json(output/'gateway_usage.json',usage)
    write_json(output/'vote_matrix.json',{'run_id':run_id,'data':minutes['vote_matrix']})
    write_json(output/'acceptance.json',acceptance)
    for filename,additions in (('events.jsonl',observed.events+[vote_event]),('spans.jsonl',observed.spans)):
        p=output/filename
        n=len(p.read_text(encoding='utf-8').splitlines())
        with p.open('a',encoding='utf-8') as f:
            for i,e in enumerate(additions,n+1):
                f.write(json.dumps(redact({**e,'seq':i}),ensure_ascii=False)+'\n')
    lines=['# B P3 超时投票恢复记录','','原包：'+source.name+'。原包未覆盖；本包保留全部历史事件与超时信号，只补一张缺失票。','',
           '恢复角色：'+aid+'。实际新增尝试 1 次；同一 P3 累计计费调用 '+str(usage['calls'])+' 次。','',
           '验收：'+acceptance['status']+'。错误：'+json.dumps(errors,ensure_ascii=False),'',
           '## 四角色陈述','']
    for s in minutes['statements']:
        lines.extend([s['agent_id']+' / '+s['position']+'：'+s['claim'],''])
    lines.extend(['## 投票矩阵',''])
    for v in minutes['vote_matrix']:
        lines.extend([v['agent_id']+' / '+v['ballot']+'：'+v['reason'],''])
    lines.extend(['## 冲突证据','',json.dumps(build_signals(minutes,register,run_id)['conflict_evidence'],ensure_ascii=False,indent=2),'',
                  'D 的未决冲突仍需交给编排器或人工处理；W4 记录完整不表示方案自动通过。',''])
    (output/'运行记录.md').write_text(redact('\n'.join(lines)),encoding='utf-8')
    return verify_package(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    if recover(a.source,a.output):
        raise SystemExit(1)
