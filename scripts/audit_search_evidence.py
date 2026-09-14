"""Audit existing initial-search logs into explicit cycle provenance; no new search."""
from common import *
from discovery import search_cycle, begin_query, finish_query, cycle_missing, timestamp

def audit(run_files):
    runs=[read(ROOT/'data/runs'/name) for name in run_files]
    config=read(ROOT/'config/sources.json');tax=read(ROOT/'config/taxonomy.json');state=read(ROOT/'data/state.json')
    assert all(r['query_version']==config['query_version'] and r['mode']=='initial_history' and r.get('finished_at') for r in runs)
    started=min(r['started_at'] for r in runs);finished=max(r['finished_at'] for r in runs)
    cycle,unused=search_cycle(state,tax,config,started,'audited-'+runs[0]['run_id'],True,True,False)
    evidence={};missing=[];progress={}
    for key,plan in cycle['plans'].items():
        matches=[(r,q) for r in runs for q in r['queries'] if q.get('key')==key and q.get('query')==plan['query'] and q.get('complete')]
        if not matches:missing.append(key);continue
        r,q=max(matches,key=lambda item:item[1]['searched_at'])
        valid=all(p.get('status')==200 and not p.get('error') and not p.get('warning') and not p.get('error_list') for p in q.get('pages',[]))
        if key.startswith(('Europe PMC:','PubMed:')):
            valid=valid and len(set(q.get('ids',[])))==q.get('hit_count')==q.get('returned')
        if not valid:missing.append(key);continue
        checked=r['finished_at']
        progress[key]=finish_query(begin_query(state['source_progress'].get(key,{}),plan,started),plan,cycle['id'],checked,True,evidence_run_id=r['run_id'])
        evidence[key]={'run_id':r['run_id'],'query_fingerprint':digest(q['query']),'searched_at':q['searched_at'],'returned':q.get('returned'),'hit_count':q.get('hit_count'),'source_completed_at':checked}
    missing=sorted(set(missing+cycle_missing(cycle,progress,finished)))
    report={'audited_at':now(),'kind':'Recorded evidence review, not a network execution','network_requests':0,'source_run_files':run_files,'configuration_fingerprint':cycle['configuration_fingerprint'],'required_sources':len(cycle['plans']),'matched_sources':len(evidence),'missing':missing,'evidence':evidence}
    if not missing:
        cycle.update(completed_at=finished,evidence_audited_at=report['audited_at'],provenance='Reconstructed only from the listed actual complete initial-search logs.')
        state['search_cycle']=cycle;state['source_progress'].update(progress)
        state['last_successful_search']=finished;state['last_search_completed_at']=finished;state['last_complete_coverage_through']=started
        write(ROOT/'data/state.json',state)
    write(ROOT/'data/reports/initial-search-evidence-audit.json',report)
    print({'matched_sources':len(evidence),'required_sources':len(cycle['plans']),'missing':missing})
    if missing:raise ValueError('Initial search evidence does not cover the configured scope')
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('runs',nargs='+');args=parser.parse_args();audit(args.runs)
