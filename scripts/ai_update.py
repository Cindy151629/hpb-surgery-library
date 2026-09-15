"""Key-free AI bibliography discovery and bounded official-resource monitoring.

Only source metadata is merged automatically. Scientific notes remain curated.
Each query has its own durable window; a failed source never advances success.
"""
from common import ROOT, read, write, now, digest, doi, norm, fetch, safe_url
from discovery import epmc_search, pubmed_search
from harvest import parse_pubmed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urljoin, quote
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
import requests
import json
import re
import time

ORGANS = {
    '肝脏外科': '(hepatectomy OR "liver resection" OR "liver surgery")',
    '胆囊与胆道外科': '(cholecystectomy OR gallbladder OR biliary)',
    '胰腺外科': '(pancreatectomy OR pancreaticoduodenectomy OR pancreatoduodenectomy OR pancreaticojejunostomy OR "pancreatic surgery")',
    '可迁移方法': '(surgical OR surgery OR laparoscopic)'}
VIDEO = '(video OR videos OR laparoscopic OR robotic)'
TASK = '(segmentation OR tracking OR "phase recognition" OR "workflow recognition" OR "action triplet" OR "video question answering")'
MODEL = '("deep learning" OR "foundation model" OR "segment anything" OR "vision language" OR "large language model" OR transformer OR "artificial intelligence")'


def window(previous, at, version, monthly=False):
    """A pending query is retried verbatim, even after weeks offline."""
    if previous.get('version') == version and previous.get('pending'):
        return dict(previous['pending'])
    last = (previous.get('covered_through') or previous.get('last_success')) if previous.get('version') == version else None
    start = (datetime.fromisoformat(last) - timedelta(days=90 if monthly else 21)).date().isoformat() if last else None
    return {'start': start, 'end': at[:10]}


def query_for(source, organ, w):
    q = f'{ORGANS[organ]} AND {VIDEO} AND {TASK} AND {MODEL}'
    if source == 'Europe PMC':
        q = ' AND '.join('TITLE_ABS:' + group for group in (ORGANS[organ], VIDEO, TASK, MODEL))
    if source == 'arXiv':
        # arXiv uses explicit field prefixes; group terms stay independently ORed.
        q = re.sub(r'"[^"]+"|[A-Za-z][A-Za-z ]*(?= OR|\))', lambda m: m.group(0), q)
        parts = []
        for group in [ORGANS[organ], VIDEO, TASK, MODEL]:
            parts.append('(' + ' OR '.join('all:' + term.strip() for term in group.strip('()').split(' OR ')) + ')')
        q = ' AND '.join(parts)
        if w['start']: q += ' AND lastUpdatedDate:[' + w['start'].replace('-', '') + '0000 TO ' + w['end'].replace('-', '') + '2359]'
    elif w['start']:
        q += f' AND UPDATE_DATE:[{w["start"]} TO {w["end"]}]' if source == 'Europe PMC' else f' AND ("{w["start"]}"[MDAT] : "{w["end"]}"[MDAT])'
    return q


def arxiv_search(query, config, request=fetch, sleep=time.sleep):
    rows, logs, total = [], [], None
    ns = {'a': 'http://www.w3.org/2005/Atom', 'o': 'http://a9.com/-/spec/opensearch/1.1/', 'x': 'http://arxiv.org/schemas/atom'}
    for page in range(config['max_pages']):
        sleep(config.get('arxiv_interval_seconds', 3))
        params = {
            'search_query': query, 'start': page * config['page_size'], 'max_results': config['page_size'],
            'sortBy': 'lastUpdatedDate', 'sortOrder': 'descending'}
        if query.startswith('id:'):
            params.pop('search_query'); params['id_list'] = query[3:]
        response, log = request('https://export.arxiv.org/api/query', params)
        logs.append(log)
        if response is None or response.status_code != 200: return rows, logs, False, total
        try:
            root = ET.fromstring(response.content)
            total = int(root.findtext('o:totalResults', namespaces=ns))
            entries = root.findall('a:entry', ns)
            for entry in entries:
                identifier = entry.findtext('a:id', '', ns)
                if '/api/errors' in identifier: raise ValueError('arXiv returned an API error entry')
                rows.append({'title': ' '.join(entry.findtext('a:title', '', ns).split()),
                    'arxiv': identifier.rsplit('/', 1)[-1].split('v')[0], 'url': identifier.replace('http:', 'https:'),
                    'doi': entry.findtext('x:doi', '', ns), 'year': entry.findtext('a:published', '', ns)[:4],
                    'updated': entry.findtext('a:updated', '', ns), 'publication_date': entry.findtext('a:published', '', ns),
                    'journal_ref': entry.findtext('x:journal_ref', '', ns)})
            log.update(offset=page * config['page_size'], returned=len(entries), hit_count=total)
            if len(rows) >= total: return rows, logs, True, total
            if not entries: raise ValueError('arXiv pagination ended before totalResults')
        except Exception as error:
            log['error'] = str(error); return rows, logs, False, total
    logs[-1]['error'] = 'pagination cap reached; success watermark unchanged'
    return rows, logs, False, total


def keys(row):
    result = {k: (doi(row.get(k)) if k == 'doi' else str(row.get(k) or '').split('v')[0] if k == 'arxiv' else str(row.get(k) or '')) for k in ('doi', 'pmid', 'arxiv')}
    if result['doi'].startswith('10.48550/arxiv.'):
        result['arxiv'] = result['arxiv'] or result['doi'].split('arxiv.', 1)[1].split('v')[0]
        result['doi'] = ''  # arXiv's DOI alias is not a conflicting journal DOI.
    return result


def merge(rows, candidates, curated, pending, source, organ, stamp):
    added, revised = set(), set()
    for row in rows:
        if not row.get('title'): continue
        ids = keys(row)
        def matches(other):
            old = keys(other)
            return any(ids[k] and ids[k] == old[k] for k in ids)
        hits = [r for r in curated if r['kind'] == 'publication' and matches(r)]
        # A dataset's associated-paper DOI is not the dataset entity's identity.
        if hits:
            for old in hits:
                diff = {k: {'before': old.get(k), 'after': row[k]} for k in ('title', 'doi', 'pmid', 'arxiv', 'journal_ref')
                        if row.get(k) and (k != 'doi' or ids['doi']) and norm(old.get(k, '')) != norm(row[k])}
                if diff:
                    pending[old['id'] + ':metadata:' + source] = {'id': old['id'], 'source': source, 'detected_at': stamp,
                        'changes': diff, 'status': '题录/版本变化待复核；人工笔记未覆盖', 'url': row.get('url')}
                    revised.add(old['id'])
            continue
        found = [r for r in candidates.values() if matches(r)]
        conflict = any(any(ids[k] and keys(r)[k] and ids[k] != keys(r)[k] for k in ids) for r in found)
        identity = next((k + ':' + ids[k] for k in ('doi', 'pmid', 'arxiv') if ids[k]), 'title:' + norm(row['title']))
        rid = sorted(r['id'] for r in found)[0] if found and not conflict else 'HPBAI-CAND-' + digest(identity)[:16]
        old = candidates.get(rid, {})
        if len(found) > 1 and not conflict:
            old = dict(old)
            old['sources'] = list(dict.fromkeys(s for r in found for s in r.get('sources', [])))
            old['search_organs'] = list(dict.fromkeys(s for r in found for s in r.get('search_organs', [])))
            old['aliases'] = list(dict.fromkeys(a for r in found for a in [r['id']] + r.get('aliases', [])))
            old['first_discovered'] = min(r.get('first_discovered', stamp) for r in found)
            for other in found:
                if other['id'] != rid: candidates.pop(other['id'])
        if not old: added.add(rid)
        # Identifiers and original provider title only; no generated scientific summary.
        candidates[rid] = {**old, **row, 'id': rid, 'kind': 'candidate', 'collection_id': 'hpb-ai-video',
            'first_discovered': old.get('first_discovered', stamp), 'last_seen': stamp,
            'sources': list(dict.fromkeys(old.get('sources', []) + [source])),
            'search_organs': list(dict.fromkeys(old.get('search_organs', []) + [organ])),
            'identity_status': '标识冲突，待核对' if conflict else '数据库候选；内容与资源页面尚待核对'}
    return added, revised


def bounded_page(url, max_bytes=1500000, timeout=20):
    """Read bounded text only. No video, PDFs, datasets, weights or model execution."""
    log = {'url': url, 'checked_at': now(), 'identity_match': None, 'playback_tested': False}
    try:
        current = url
        for _ in range(8):
            safe_url(current)
            with requests.get(current, timeout=timeout, stream=True, allow_redirects=False,
                              headers={'User-Agent': 'HPB-Research-Library/AI-metadata-monitor'}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get('Location'):
                    current = urljoin(current, r.headers['Location']); continue
                content_type = r.headers.get('content-type', '').lower()
                log.update(http_status=r.status_code, final_url=current, content_type=content_type)
                if r.status_code != 200:
                    log['status'] = '访问受限/请求失败'; return log
                if not any(t in content_type for t in ('text/html', 'text/plain', 'application/json', 'xml')):
                    log['status'] = '仅检查响应头；非文本资源未下载'; return log
                body = bytearray()
                for chunk in r.iter_content(16384):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        log['status'] = '页面超出文本读取上限；未完成'; return log
                text = body.decode(r.encoding or 'utf-8', errors='replace')
                if re.search(r'Checking your browser|Just a moment|verify you are human|unusual traffic', text, re.I):
                    log['status'] = '验证/限制页面；身份未确认'; return log
                title = re.search(r'<title[^>]*>(.*?)</title>', text, re.S | re.I)
                log.update(status='文本页面取得；不等于内容已复核', sha256=digest(bytes(body)),
                           title=re.sub('<[^>]+>', '', title.group(1)).strip() if title else '', bytes=len(body))
                return log
        raise ValueError('redirect limit')
    except Exception as error:
        log.update(status='请求失败', error=type(error).__name__ + ': ' + str(error)[:200])
    return log


def update(initial=False, request=None, assets=True):
    transport = request or (lambda u, p=None: fetch(u, p, timeout=20, retries=0 if 'export.arxiv.org' in u else 1))
    config = read(ROOT / 'config/ai_sources.json')
    config.update(epmc_endpoint='https://www.ebi.ac.uk/europepmc/webservices/rest/search',
                  pubmed_endpoint='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi')
    at = now(); state = read(ROOT / 'data/ai_state.json', {}); previous_success = state.get('last_successful_search')
    def request(url, params=None):
        if 'export.arxiv.org' in url and state.get('arxiv_retry_after', '') > at:
            return None, {'requested_url': url, 'parameters': params, 'attempted_at': now(), 'skipped': True,
                          'error': 'arXiv限流冷却期；未发起本次网络请求', 'retry_after': state['arxiv_retry_after']}
        response, log = transport(url, params)
        if 'export.arxiv.org' in url and response is not None and response.status_code == 429:
            state['arxiv_retry_after'] = (datetime.fromisoformat(at) + timedelta(hours=6)).isoformat()
        return response, log
    monthly = state.get('last_monthly_complete') != at[:7]
    state.update(last_attempt=at, status='running', errors=[])
    progress = state.setdefault('source_progress', {})
    candidates = read(ROOT / 'data/ai_candidates.json', {}); curated = read(ROOT / 'data/ai_records.json', [])
    pending = read(ROOT / 'data/ai_pending_changes.json', {})
    run = {'run_id': 'ai-' + at.replace(':', '').replace('.', '-'), 'started_at': at,
           'query_version': config['query_version'], 'queries': [], 'source_checks': [], 'status': 'running'}
    added, revised = set(), set()
    def save():
        write(ROOT / 'data/ai_candidates.json', candidates); write(ROOT / 'data/ai_pending_changes.json', pending)
        write(ROOT / 'data/ai_state.json', state); write(ROOT / 'data/runs' / (run['run_id'] + '.json'), run)
    save()
    for source in ('Europe PMC', 'PubMed', 'arXiv'):
        for organ in ORGANS:
            for catchup in range(2):
                key = source + ':' + organ; old = progress.get(key, {})
                w = window(old, at, config['query_version'], monthly)
                q = query_for(source, organ, w)
                progress[key] = {**old, 'version': config['query_version'], 'pending': w, 'last_attempt': at}
                save()
                rows, logs, complete, total = [], [], False, None
                try:
                    if source == 'Europe PMC':
                        items, logs, complete, total = epmc_search(q, config, request, ROOT / 'data/checkpoints')
                        rows = [{'title': x.get('title'), 'doi': x.get('doi', ''), 'pmid': x.get('id') if x.get('source') == 'MED' else '',
                                 'year': x.get('pubYear'), 'url': 'https://europepmc.org/article/' + x.get('source', 'MED') + '/' + x['id']} for x in items]
                    elif source == 'PubMed':
                        ids, logs, complete, total = pubmed_search(q, config, request)
                        for offset in range(0, len(ids), 100):
                            batch = ids[offset:offset + 100]
                            r, log = request('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi', {'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml'})
                            logs.append(log)
                            if r is None or r.status_code != 200: complete = False; continue
                            found = parse_pubmed(r.content)
                            if set(found) != set(batch): complete = False; log['error'] = 'incomplete metadata batch'
                            rows.extend({**x, 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + pid + '/'} for pid, x in found.items())
                    else: rows, logs, complete, total = arxiv_search(q, config, request)
                except Exception as error:
                    logs.append({'error': type(error).__name__ + ': ' + str(error)[:200]}); complete = False
                new, changed = merge(rows, candidates, curated, pending, source, organ, at)
                added.update(new); revised.update(changed)
                item = {'source': source, 'organ': organ, 'query': q, 'window': w, 'checked_at': now(),
                        'complete': complete, 'hit_count': total, 'returned': len(rows), 'pages': logs,
                        'added': len(new), 'revised': len(changed)}
                run['queries'].append(item)
                if complete:
                    progress[key].update(last_success=now(), covered_through=w['end'], completed_window=w); progress[key].pop('pending', None)
                else: state['errors'].append({'source': key, 'reason': '请求、解析或分页未完成；成功进度保留', 'query': q})
                save()
                if not complete or w['end'] >= at[:10]: break
    # Crossref is an exact-DOI version/correction watcher, not a pretend exhaustive search.
    for r in curated:
        if not keys(r)['doi'] or r['kind'] != 'publication': continue
        key = 'Crossref:' + doi(r['doi']); response, log = request('https://api.crossref.org/works/' + quote(doi(r['doi']), safe=''))
        log.update(source='Crossref exact DOI', resource_id=r['id'])
        try:
            if response is None or response.status_code != 200: raise ValueError('request failed')
            m = response.json()['message']
            if doi(m.get('DOI')) != doi(r['doi']): raise ValueError('DOI identity conflict')
            metadata = {k: m.get(k) for k in ('title', 'author', 'published', 'published-online', 'published-print', 'relation', 'update-to', 'updated-by', 'type')}
            old = progress.get(key, {})
            if old.get('metadata') and any(old['metadata'][k] != metadata.get(k) for k in old['metadata']):
                pending[key] = {'id': r['id'], 'detected_at': at, 'before': old['metadata'], 'after': metadata, 'status': '出版/更正元数据变化待复核'}
                revised.add(r['id'])
            progress[key] = {'last_success': at, 'metadata': metadata}; log['complete'] = True
        except Exception as error:
            log.update(complete=False, error=str(error)); state['errors'].append({'source': key, 'reason': str(error)})
        run['source_checks'].append(log); save()
    # Known arXiv IDs also catch versions whose new title no longer matches broad terms.
    for r in curated:
        if r['kind'] != 'publication' or not r.get('arxiv'): continue
        rows, logs, complete, total = arxiv_search('id:' + str(r['arxiv']).split('v')[0], config, request)
        _, changed = merge(rows, candidates, curated, pending, 'arXiv known ID', '已知资源', at)
        revised.update(changed)
        key = 'arXiv-ID:' + r['id']
        if complete and rows: progress[key] = {'last_success': at}
        else: state['errors'].append({'source': key, 'reason': '已知版本查询未完成'})
        run['source_checks'].append({'source': key, 'complete': complete and bool(rows), 'pages': logs}); save()
    if assets:
        checks = read(ROOT / 'data/ai_asset_checks.json', {})
        urls = sorted({l['url'] for r in curated for l in r.get('links', [])
                       if l.get('role') in ('code', 'dataset', 'data', 'model_card', 'project', 'demo', 'demo_entry', 'model_weights', 'weights', 'correction')})
        with ThreadPoolExecutor(max_workers=3) as pool:
            for result in pool.map(bounded_page, urls):
                url = result['url']; before = checks.get(url, {})
                if before and any(before.get(k) != result.get(k) for k in ('sha256', 'http_status')):
                    affected = [r['id'] for r in curated if any(l['url'] == url for l in r.get('links', []))]
                    pending['asset:' + digest(url)[:16]] = {'ids': affected, 'detected_at': at, 'url': url,
                        'before': before, 'after': result, 'status': '页面/访问条件变化；代码提交不计为新论文，内容待复核'}
                    revised.update(affected)
                checks[url] = result
                if not result.get('sha256'): state['errors'].append({'source': url, 'reason': result['status']})
                write(ROOT / 'data/ai_asset_checks.json', checks)
                save()
        run['asset_checks'] = len(urls)
    all_searches = all(q['complete'] for q in run['queries']) and all(q.get('complete') for q in run['source_checks'])
    if all_searches:
        state['last_successful_search'] = at
        if monthly: state['last_monthly_complete'] = at[:7]
    elif previous_success: state['last_successful_search'] = previous_success
    any_success = any(q['complete'] for q in run['queries']) or any(q.get('complete') for q in run['source_checks'])
    state['status'] = ('partial' if any_success else 'failed') if state['errors'] else 'success'
    state['counts'] = {'added': len(added), 'revised': len(revised), 'pending': sum(not r.get('resolved_to') for r in candidates.values()) + len(pending), 'failed': len(state['errors'])}
    run.update(status=state['status'], counts=state['counts'], completed_at=now())
    state['last_run_id'] = run['run_id']; save()
    return run


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--initial', action='store_true'); p.add_argument('--skip-assets', action='store_true')
    a = p.parse_args(); print(json.dumps(update(a.initial, assets=not a.skip_assets)['counts'], ensure_ascii=False))
