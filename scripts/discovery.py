"""Fixed, key-free discovery. Candidates are not promoted into reviewed literature."""
from common import *
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode,urljoin
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from query_config import compile_query
from harvest import parse_pubmed

def since(progress,key,version,monthly=False,at=None,overlap_days=21,monthly_lookback_days=90):
 at=at or datetime.now(timezone.utc);prev=progress.get(key,{})
 if prev.get('query_version')!=version or not prev.get('last_success'):return None
 return (datetime.fromisoformat(prev['last_success'])-timedelta(days=monthly_lookback_days if monthly else overlap_days)).date().isoformat()

def epmc_search(query,config,request=fetch,checkpoint_dir=None):
 checkpoint=Path(checkpoint_dir)/('epmc-'+digest(query)+'.json') if checkpoint_dir else None;saved=read(checkpoint,{}) if checkpoint else {}
 cursor=saved.get('next_cursor','*');rows=saved.get('rows',[]);seen={(x.get('source'),x.get('id')) for x in rows};pages=saved.get('pages',[]);total=saved.get('hit_count')
 for n in range(config['max_pages']):
  r,l=request(config['epmc_endpoint'],{'query':query,'format':'json','resultType':'lite','pageSize':config['page_size'],'cursorMark':cursor});pages.append(l)
  if r is None or r.status_code!=200:return rows,pages,False,total
  try:
   d=r.json();total=int(d['hitCount']);batch=d['resultList']['result'];next_cursor=d.get('nextCursorMark');l.update(hit_count=total,returned=len(batch),cursor=cursor,next_cursor=next_cursor)
   for x in batch:
    k=(x.get('source'),x.get('id'))
    if k not in seen:seen.add(k);rows.append(x)
   if len(rows)>=total:
    if checkpoint and checkpoint.exists():checkpoint.unlink()
    return rows,pages,True,total
   if not batch or not next_cursor or next_cursor==cursor:l['error']='pagination ended before hitCount';return rows,pages,False,total
   cursor=next_cursor
   if checkpoint:write(checkpoint,{'query':query,'next_cursor':cursor,'rows':rows,'pages':pages,'hit_count':total})
  except Exception as e:l['error']='invalid response: '+str(e);return rows,pages,False,total
 pages[-1]['error']='configured pagination cap reached; success watermark unchanged'
 return rows,pages,False,total

def pubmed_search(query,config,request=fetch):
 pages=[];ids=[];total=None
 for offset in range(0,10000,1000):
  r,l=request(config['pubmed_endpoint'],{'db':'pubmed','term':query,'retmode':'json','retmax':1000,'retstart':offset});pages.append(l)
  if r is None or r.status_code!=200:return ids,pages,False,total
  try:
   payload=r.json();d=payload['esearchresult'];l.update(query_translation=d.get('querytranslation'),warning_list=d.get('warninglist'))
   # Invalid fields/phrases can accompany count=0 or a broader translated query.
   # Preserve the diagnostic, but neither case proves the requested search completed.
   if payload.get('error') or d.get('error') or d.get('errorlist'):
    l['error_list']=d.get('errorlist');l['error']=payload.get('error') or d.get('error') or 'PubMed ESearch reported query errors';return ids,pages,False,total
   total=int(d['count']);batch=d['idlist'];ids.extend(batch);ids=list(dict.fromkeys(ids));l.update(hit_count=total,returned=len(batch),offset=offset)
   if len(ids)>=total:return list(dict.fromkeys(ids)),pages,True,total
   if not batch:return ids,pages,False,total
  except Exception as e:l['error']=str(e);return ids,pages,False,total
 pages[-1]['error']='PubMed 10000 cap: subdivide query/date; not complete'
 return ids,pages,False,total

def candidate(x,topic,at,source_url):
 source=x.get('source','MED');pid=str(x.get('id',''));di=doi(x.get('doi'))
 return {'id':'DISC-'+source+'-'+re.sub('[^a-zA-Z0-9_-]','-',pid),'title':x.get('title',''),'pmid':pid if source=='MED' else '', 'doi':di,'year':x.get('pubYear'), 'authors':x.get('authorString',''),'journal':x.get('journalTitle',''),'url':'https://pubmed.ncbi.nlm.nih.gov/'+pid+'/' if source=='MED' else 'https://europepmc.org/article/'+source+'/'+pid,'topic_ids':[topic['id']],'organ':topic['organ'],'resource_type':'待核线索','kind':'candidate','first_discovered':at,'last_seen':at,'source':'Europe PMC','identity':'数据库题录已返回；内容与目标网页待核','content_status':'尚未形成阅读笔记，不计入已精读文献','source_url':source_url,'source_payload_hash':digest(json.dumps(x,sort_keys=True))}

def merge_candidate(records,new,canonical,index=None):
 """Use strong identifiers; DOI/PMID disagreement is a conflict, never a fuzzy merge."""
 hits=[]
 for k,x in canonical.items():
  if (new.get('pmid') and str(x.get('pmid'))==new['pmid']) or (new.get('doi') and doi(x.get('doi'))==doi(new['doi'])):hits.append(k)
 def incompatible(a,b):return any(a.get(k) and b.get(k) and (doi(a[k])!=doi(b[k]) if k=='doi' else str(a[k])!=str(b[k])) for k in ['pmid','doi'])
 if hits:
  if len(hits)>1 or any(incompatible(canonical[k],new) for k in hits):return 'conflict',hits
  return 'existing',hits
 key=new['id'];prev=records.get(key)
 if prev and incompatible(prev,new):return 'conflict',[key]
 # EPMC secondary sources can carry the same DOI under different IDs.
 duplicate=index.get(new.get('doi')) if index is not None else next((k for k,x in records.items() if new.get('doi') and doi(x.get('doi'))==new['doi']),None)
 if duplicate:
  key=duplicate;prev=records[key]
  if incompatible(prev,new):return 'conflict',[key]
 if prev:
  new['id']=key;new['first_discovered']=prev['first_discovered'];new['topic_ids']=sorted(set(prev.get('topic_ids',[])+new['topic_ids']));new['related_ids']=prev.get('related_ids',[])
  changed=any(prev.get(k)!=new.get(k) for k in ['title','doi','year','journal','authors','source_payload_hash'])
  records[key]={**prev,**new};return ('revised' if changed else 'duplicate'),[key]
 records[key]=new
 if index is not None and new.get('doi'):index[new['doi']]=key
 return 'added',[key]

BIBLIOGRAPHIC_SOURCES = ('Europe PMC', 'PubMed')
PUBMED_HISTORY_FILTER = '(Guideline[pt] OR Practice Guideline[pt] OR Randomized Controlled Trial[pt] OR consensus[tiab] OR systematic review[tiab] OR guideline[tiab] OR guidelines[tiab] OR definition[tiab] OR recommendations[tiab])'


def timestamp(value):
    value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def query_text(base, source, start, end, history_filter):
    if not start:
        return base + ' AND ' + history_filter
    if source == 'Europe PMC':
        suffix = '(CREATION_DATE:[' + start + ' TO ' + end + '] OR UPDATE_DATE:[' + start + ' TO ' + end + '])'
    else:
        suffix = '("' + start + '"[EDAT] : "' + end + '"[EDAT] OR "' + start + '"[MDAT] : "' + end + '"[MDAT])'
    return base + ' AND ' + suffix


def query_plan(topic, source, previous, config, at, initial=False, monthly=False, backfill=False):
    """Freeze the exact query and coverage watermark, separately from successful progress."""
    base = '(' + compile_query(topic['query_groups'], source) + ')'
    if source == 'Europe PMC':
        base += ' AND SRC:MED'
    history_filter = config['historical_filter'] if source == 'Europe PMC' else PUBMED_HISTORY_FILTER
    scope = digest(json.dumps({'base': base, 'historical_filter': history_filter,
                               'endpoint': config['epmc_endpoint' if source == 'Europe PMC' else 'pubmed_endpoint'],
                               'version': config['query_version']}, sort_keys=True))
    pending = previous.get('pending_query')
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', pending or '')
    pending_version = previous.get('pending_query_version', previous.get('query_version'))
    # Support old checkpoints only when their query still belongs to the current
    # compiled scope. New checkpoints carry an independent version and scope hash.
    if (pending and pending_version == config['query_version']
            and previous.get('pending_scope_fingerprint', scope) == scope
            and previous.get('pending_query_fingerprint', digest(pending)) == digest(pending)
            and pending == query_text(base, source, dates[0] if dates else None,
                                      dates[-1] if dates else None, history_filter)):
        window = previous.get('pending_window')
        if not window:
            anchor = previous.get('pending_at') or at
            window = {'range_start': dates[0] if dates else None,
                      'range_end': dates[-1] if dates else None,
                      'coverage_until': anchor, 'historical': not bool(dates)}
        return {'query': pending, 'query_fingerprint': digest(pending),
                'scope_fingerprint': scope, 'query_version': config['query_version'],
                'window': window, 'pending_reused': True}
    start = None
    if not initial and not backfill:
        start = since({'key': previous}, 'key', config['query_version'], monthly,
                      overlap_days=config.get('overlap_days', 21),
                      monthly_lookback_days=config.get('monthly_lookback_days', 90))
        # A changed query also needs a historical search if the version was not bumped.
        if (previous.get('scope_fingerprint', scope) != scope
                or (previous.get('last_query') and not previous['last_query'].startswith(base + ' AND '))):
            start = None
    query = query_text(base, source, start, at[:10], history_filter)
    return {'query': query, 'query_fingerprint': digest(query), 'scope_fingerprint': scope,
            'query_version': config['query_version'], 'pending_reused': False,
            'window': {'range_start': start, 'range_end': at[:10] if start else None,
                       'coverage_until': at, 'historical': start is None}}


def search_cycle(state, taxonomy, config, at, runid, initial, monthly, partial):
    """Only an unfinished, bounded cycle may combine separate source retries."""
    catalogs = [('SAGES RSS', config['sages_feed'], 'rss'),
                ('Stanford directory', config['stanford_directory'], 'html')]
    configuration = {'version': config['query_version'],
                     'topics': [{'id': t['id'], 'groups': t['query_groups']} for t in taxonomy['topics']],
                     'catalogs': catalogs, 'historical_filter': config['historical_filter'],
                     'pubmed_history_filter': PUBMED_HISTORY_FILTER,
                     'endpoints': [config['epmc_endpoint'], config['pubmed_endpoint']],
                     'overlap_days': config.get('overlap_days', 21),
                     'monthly_lookback_days': config.get('monthly_lookback_days', 90)}
    fingerprint = digest(json.dumps(configuration, sort_keys=True))
    previous = state.get('search_cycle', {})
    try:
        reusable = (partial and previous.get('schema_version') == 1
                    and previous.get('configuration_fingerprint') == fingerprint
                    and (not initial or previous.get('initial') is True)
                    and not previous.get('completed_at')
                    and timestamp(previous['started_at']) <= timestamp(at) <= timestamp(previous['valid_until']))
    except (KeyError, TypeError, ValueError):
        reusable = False
    if reusable:
        return previous, catalogs
    plans = {}
    for topic in taxonomy['topics']:
        for source in BIBLIOGRAPHIC_SOURCES:
            key = source + ':' + topic['id']
            plans[key] = query_plan(topic, source, state['source_progress'].get(key, {}),
                                    config, at, initial, monthly)
    for key, url, kind in catalogs:
        plans[key] = {'query': url, 'query_fingerprint': digest(url), 'scope_fingerprint': digest(url),
                      'query_version': config['query_version'],
                      'window': {'range_start': None, 'range_end': None, 'coverage_until': at}}
    return {'schema_version': 1, 'id': runid, 'started_at': at,
            'valid_until': (timestamp(at) + timedelta(days=max(1, config.get('overlap_days', 21)))).isoformat(),
            'configuration_fingerprint': fingerprint, 'query_version': config['query_version'],
            'initial': initial, 'monthly': monthly,
            'plans': plans}, catalogs


def begin_query(previous, plan, at):
    # Write before calling the source so interruption cannot lose the original window.
    return {**previous, 'pending_query': plan['query'], 'pending_at': plan['window']['coverage_until'],
            'pending_query_version': plan['query_version'], 'pending_query_fingerprint': plan['query_fingerprint'],
            'pending_scope_fingerprint': plan['scope_fingerprint'], 'pending_window': plan['window'],
            'last_attempt': at, 'last_attempt_status': 'running'}


def finish_query(previous, plan, cycle_id, completed_at, complete, **details):
    if not complete:
        return {**previous, 'last_attempt_status': 'failed'}
    successful = {k: v for k, v in previous.items() if not k.startswith('pending_')}
    successful.update(last_success=plan['window']['coverage_until'], last_completed_at=completed_at,
                      query_version=plan['query_version'], last_query=plan['query'],
                      query_fingerprint=plan['query_fingerprint'], scope_fingerprint=plan['scope_fingerprint'],
                      window=plan['window'], cycle_id=cycle_id, last_attempt_status='success', **details)
    if plan['window'].get('historical') and not successful.get('first_historical_search'):
        successful['first_historical_search'] = plan['window']['coverage_until']
    return successful


def cycle_missing(cycle, progress, completed_at):
    missing = []
    for key, plan in cycle['plans'].items():
        current = progress.get(key, {})
        try:
            complete = (current.get('cycle_id') == cycle['id']
                        and current.get('query_version') == plan['query_version']
                        and current.get('query_fingerprint') == plan['query_fingerprint']
                        and digest(current.get('last_query', '')) == plan['query_fingerprint']
                        and current.get('window') == plan['window']
                        and not current.get('pending_query')
                        and current.get('last_attempt_status') == 'success'
                        and timestamp(cycle['started_at']) <= timestamp(current['last_completed_at'])
                        <= timestamp(completed_at) <= timestamp(cycle['valid_until']))
        except (KeyError, TypeError, ValueError):
            complete = False
        if not complete:
            missing.append(key)
    return missing


def run(initial=False, only=None, sources=None):
    config = read(ROOT / 'config/sources.json')
    taxonomy = read(ROOT / 'config/taxonomy.json')
    state = read(ROOT / 'data/state.json', {'source_progress': {}, 'runs': [],
                                          'last_successful_search': None, 'last_successful_publish': None})
    at = now()
    runid = at.replace(':', '').replace('.', '-')
    monthly = initial or state.get('last_monthly') != at[:7]
    progress = state.setdefault('source_progress', {})
    candidates = read(ROOT / 'data/candidates.json', {})
    if (ROOT / 'data/records.json').exists():
        canonical = {x['id']: x for x in read(ROOT / 'data/records.json')}
    else:
        inherited = read(ROOT / 'data/inherited.json')
        canonical = {x['id']: x for k in ['articles', 'videos', 'portals', 'pending_videos'] for x in inherited[k]}
    report = {'run_id': runid, 'started_at': at, 'status': 'running',
              'mode': 'initial_history' if initial else ('monthly_backfill' if monthly else 'weekly'),
              'query_version': config['query_version'], 'queries': [], 'added': 0, 'revised': 0,
              'duplicates': 0, 'existing': 0, 'conflicts': [], 'errors': [], 'publication': 'not_attempted',
              'scheduled_event': os.environ.get('GITHUB_EVENT_NAME') == 'schedule'}
    state.update(last_attempt=at, last_attempt_status='running', last_run_id=runid, last_errors=[])

    def checkpoint():
        write(ROOT / 'data/candidates.json', candidates)
        write(ROOT / 'data/runs' / f'{runid}.json', report)
        write(ROOT / 'data/state.json', state)

    checkpoint()
    cycle, catalogs = search_cycle(state, taxonomy, config, at, runid, initial, monthly, bool(only or sources))
    state['search_cycle'] = cycle
    report['cycle_id'] = cycle['id']
    report['cycle_valid_until'] = cycle['valid_until']
    index = {doi(x.get('doi')): k for k, x in candidates.items() if x.get('doi')}
    topics = [t for t in taxonomy['topics'] if not only or t['id'] in only]
    jobs = [(t, False) for t in topics]
    if cycle.get('historical_rotation_topic'):
        selected = next(t for t in taxonomy['topics'] if t['id'] == cycle['historical_rotation_topic'])
        if not only or selected['id'] in only:
            jobs.append((selected, True))
            report['historical_rotation_topic'] = selected['id']
    elif monthly and not initial and not only and topics:
        ranked = sorted(topics, key=lambda t: (sum(t['id'] in r.get('topic_ids', [])
                        and r.get('verification_status') == '核心身份与所读内容已核对' for r in canonical.values()), t['id']))
        selected = ranked[int(state.get('historical_rotation_index', 0)) % len(ranked)]
        jobs.append((selected, True))
        report['historical_rotation_topic'] = selected['id']
        cycle['historical_rotation_topic'] = selected['id']
        for source in BIBLIOGRAPHIC_SOURCES:
            key = source + ':' + selected['id'] + ':history'
            cycle['plans'][key] = query_plan(selected, source, progress.get(key, {}), config, at, backfill=True)

    def merge_discovered(item, topic, source_url, source):
        c = candidate(item, topic, at, source_url)
        c['source'] = source
        status, keys = merge_candidate(candidates, c, canonical, index)
        if status in ['added', 'revised', 'existing']:
            report[status] += 1
        elif status == 'conflict':
            report['conflicts'].append({'candidate': c, 'matched': keys})
        else:
            report['duplicates'] += 1

    for topic, backfill in jobs:
        for source in sources or BIBLIOGRAPHIC_SOURCES:
            key = source + ':' + topic['id'] + (':history' if backfill else '')
            plan = cycle['plans'][key]
            q = plan['query']
            progress[key] = begin_query(progress.get(key, {}), plan, at)
            checkpoint()
            rows, pages, ok, total = [], [], False, None
            try:
                if source == 'Europe PMC':
                    rows, pages, ok, total = epmc_search(q, config, checkpoint_dir=ROOT / 'data/checkpoints')
                    for x in rows:
                        merge_discovered(x, topic, pages[0].get('requested_url', '') if pages else '', source)
                else:
                    rows, pages, ok, total = pubmed_search(q, config)
                    known = {str(x.get('pmid')) for x in canonical.values()} | {str(x.get('pmid')) for x in candidates.values()}
                    missing = [x for x in rows if str(x) not in known]
                    for offset in range(0, len(missing), 70):
                        batch = missing[offset:offset + 70]
                        r, log = fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',
                                       {'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml'})
                        log['stage'] = 'PubMed-only metadata retrieval'
                        pages.append(log)
                        try:
                            if r is None or r.status_code != 200:
                                raise ValueError('EFetch failed')
                            mets = parse_pubmed(r.content)
                            if set(mets) != set(batch):
                                ok = False
                                log['error'] = 'EFetch missing IDs'
                            for pid, m in mets.items():
                                x = {'id': pid, 'source': 'MED', 'title': m['title'], 'doi': m['doi'],
                                     'pubYear': m['year'], 'authorString': '; '.join(m['authors']),
                                     'journalTitle': m['journal']}
                                merge_discovered(x, topic, log.get('requested_url', ''), source)
                        except Exception as error:
                            ok = False
                            log['error'] = str(error)
            except Exception as error:
                ok = False
                pages.append({'error': type(error).__name__ + ': ' + str(error), 'checked_at': now()})
            completed_at = now()
            entry = {'key': key, 'source': source, 'topic_id': topic['id'],
                     'query_fingerprint': plan['query_fingerprint'], 'historical_rotation': backfill,
                     'query': q, 'range_start': plan['window']['range_start'] or 'all available years; type-limited',
                     'window': plan['window'], 'searched_at': completed_at, 'complete': ok, 'hit_count': total,
                     'returned': len(rows), 'pages': pages,
                     'ids': rows if source == 'PubMed' else [x.get('id') for x in rows]}
            report['queries'].append(entry)
            progress[key] = finish_query(progress[key], plan, cycle['id'], completed_at, ok, hit_count=total)
            if not ok:
                report['errors'].append({'source': key, 'reason': 'request, parsing or pagination incomplete'})
            checkpoint()
            print(source, topic['id'], len(rows), '/', total, 'complete', ok, flush=True)

    # Finite official catalogs have their own attempt and success evidence.
    for key, url, kind in catalogs:
        plan = cycle['plans'][key]
        progress[key] = begin_query(progress.get(key, {}), plan, at)
        checkpoint()
        entry = {'key': key, 'source': key, 'searched_at': now(), 'query': url,
                 'query_fingerprint': plan['query_fingerprint'], 'window': plan['window'],
                 'complete': False, 'pages': [], 'scope': '官网所返回目录；不代表平台全部历史视频'}
        found = []
        try:
            r, log = fetch(url)
            entry['pages'].append(log)
            if r is not None and r.status_code == 200:
                if kind == 'rss':
                    root = ET.fromstring(r.content)
                    for x in root.findall('.//item'):
                        found.append({'title': x.findtext('title'), 'url': x.findtext('link'), 'date': x.findtext('pubDate')})
                    entry['complete'] = root.find('channel') is not None
                else:
                    soup = BeautifulSoup(r.content, 'html.parser')
                    for a in soup.select('a[href]'):
                        href = urljoin(url, a['href'])
                        if any(h in href for h in ['youtube.com/watch', 'youtu.be/', 'vimeo.com/']):
                            found.append({'title': a.get_text(' ', strip=True), 'url': href, 'date': None})
                    entry['complete'] = bool(found)
                entry.update(returned=len(found), results=found)
        except Exception as error:
            entry['error'] = str(error)
        progress[key] = finish_query(progress[key], plan, cycle['id'], now(), entry['complete'], scope=entry['scope'])
        if not entry['complete']:
            report['errors'].append({'source': key, 'reason': 'catalog unavailable or invalid'})
        report['queries'].append(entry)
        catalog = read(ROOT / 'data/video_discovery.json', {})
        previous = {x['url']: x for x in catalog.get(key, []) if x.get('url')}
        for x in found:
            if x.get('url'):
                previous[x['url']] = {**previous.get(x['url'], {}), **x, 'last_seen': at,
                                      'first_seen': previous.get(x['url'], {}).get('first_seen', at)}
        catalog[key] = list(previous.values())
        write(ROOT / 'data/video_discovery.json', catalog)
        checkpoint()

    report['finished_at'] = now()
    missing = cycle_missing(cycle, progress, report['finished_at'])
    report['missing_source_keys'] = missing
    report['requested_sources_complete'] = not report['errors']
    report['all_sources_complete'] = not missing and not report['errors']
    report['status'] = 'success' if report['all_sources_complete'] else 'partial'
    report['pending_total'] = len(candidates)
    state.update(last_attempt_status=report['status'],
                 last_counts={k: report[k] for k in ['added', 'revised', 'existing', 'duplicates', 'pending_total']},
                 last_errors=report['errors'])
    if report['all_sources_complete']:
        cycle['completed_at'] = report['finished_at']
        # Retried date-limited queries cover their original upper bound, not the
        # retry date. The next incremental run catches up from this safe watermark.
        coverage = min((p['window']['coverage_until'] for p in cycle['plans'].values()), key=timestamp)
        report['coverage_until'] = coverage
        state['last_successful_search'] = report['finished_at']
        state['last_complete_coverage_through'] = coverage
        state['last_search_completed_at'] = report['finished_at']
        state['last_monthly'] = cycle['started_at'][:7] if cycle.get('monthly') else state.get('last_monthly')
        if cycle.get('historical_rotation_topic'):
            state['historical_rotation_index'] = int(state.get('historical_rotation_index', 0)) + 1
    if report['status'] == 'partial':
        state['last_partial_search'] = report['finished_at']
    state['runs'] = (state.get('runs', []) + [runid])[-104:]
    checkpoint()
    return report

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--initial',action='store_true');p.add_argument('--topics',nargs='*');p.add_argument('--sources',nargs='+',choices=['Europe PMC','PubMed']);a=p.parse_args();run(a.initial,a.topics,a.sources)
