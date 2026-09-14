"""Cloud-safe routine updates; never overwrites human/curated reading notes."""
from common import *
from discovery import run as discover
from harvest import page_check, parse_pubmed
from build import build
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import copy

PAGE_CHANGE = '页面字节变化，可能排版/广告，科学内容待复核'


def pubmed_corrections(items):
    """Canonicalize only PubMed relations, excluding merged Crossref update-to items."""
    result = {}
    for item in items or []:
        if not isinstance(item, dict) or not ('pmid' in item or 'citation' in item):
            continue
        relation = {key: str(item.get(key) or '').strip() for key in ('type', 'pmid', 'citation', 'note')}
        result[json.dumps(relation, sort_keys=True)] = relation
    return [result[key] for key in sorted(result)]


def metadata_changes(records):
    ids = [str(r['pmid']) for r in records if r.get('pmid') and r['kind'] == 'article']
    pending = read(ROOT / 'data/pending_changes.json', {})
    logs = []
    byid = {str(r['pmid']): r for r in records if r.get('pmid')}
    for offset in range(0, len(ids), 70):
        batch = ids[offset:offset + 70]
        response, log = fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',
                              {'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml'})
        logs.append(log)
        if response is None or response.status_code != 200:
            continue
        try:
            found = parse_pubmed(response.content)
            if set(found) != set(batch):
                log['error'] = 'metadata batch is incomplete'
            for pid, metadata in found.items():
                old = byid[pid]
                changes = {}
                for key in ('title', 'doi', 'year'):
                    before, after = norm(old.get(key, '')), norm(metadata.get(key, ''))
                    if before and after and before != after:
                        changes[key] = {'before': old.get(key), 'after': metadata.get(key)}
                before = pubmed_corrections(old.get('corrections'))
                after = pubmed_corrections(metadata.get('corrections'))
                if before != after:
                    changes['corrections'] = {'before': before, 'after': after, 'source': 'PubMed'}
                if changes:
                    stamp = log.get('checked_at') or now()
                    entry = pending.setdefault(old['id'], {'detected_at': stamp, 'changes': {}})
                    entry.setdefault('changes', {}).update(changes)
                    entry.update(last_detected_at=stamp, source=log['requested_url'],
                                 status='待专业复核；保留现有题名和阅读笔记')
                    old['verification_status'] = '题录变更待复核'
                    differences = old['audit']['identity'].setdefault('differences', [])
                    message = '官方题录有新增或变更，已放入待复核队列；现有笔记未自动覆盖。'
                    if message not in differences:
                        differences.append(message)
        except Exception as error:
            log['error'] = str(error)
    write(ROOT / 'data/pending_changes.json', pending)
    return pending, logs


def matched_page(page):
    blocked = ('访问验证', '限制页面', '受限', '请求失败', '登录', '验证码')
    return (page.get('title_confirmed') is True and page.get('http_status') == 200
            and not any(word in page.get('status', '') for word in blocked))


def fulltext_urls(record):
    """Use saved full-text evidence, never invent a URL or follow arbitrary links."""
    fulltext = record.get('audit', {}).get('fulltext', {})
    urls = [record.get('fulltext_url'), fulltext.get('url')]
    note = record.get('note') or {}
    if note.get('read_depth') == '正文重点核对':
        for source in note.get('sources', []):
            if re.search(r'正文|全文|PDF|XML|HTML', source.get('scope', ''), re.I):
                urls.append(source.get('url'))
    # Keep separately saved sources even when the preferred full-text URL changes.
    urls.extend(fulltext.get('link_checks', {}).keys())
    return list(dict.fromkeys(url for url in urls if isinstance(url, str)
                              and url.startswith(('https://', 'http://')) and url != record['url']))


def fulltext_previous(record, url):
    fulltext = record.get('audit', {}).get('fulltext', {})
    previous = fulltext.get('link_checks', {}).get(url)
    if previous is not None:
        return previous
    # Historical retrieval evidence can supply a hash, but not an invented check time.
    if (fulltext.get('url') == url and fulltext.get('sha256')
            and fulltext.get('status', '').startswith('已取得并匹配')):
        return {'status': fulltext['status'], 'http_status': 200, 'title_confirmed': True,
                'sha256': fulltext['sha256'], 'checked_at': fulltext.get('checked_at')}
    return {}


def due(page, timestamp, at):
    if not timestamp:
        return True
    try:
        previous = datetime.fromisoformat(timestamp)
        if previous.tzinfo is None:
            return True
        age = (at - previous).total_seconds() / 86400
    except (TypeError, ValueError):
        return True
    return age >= (90 if matched_page(page) else 7)


def link_tasks(records, pending, monthly, at=None):
    at = at or datetime.now(timezone.utc)
    tasks = []
    for record in records:
        rid = record['id']
        main = record['audit'].get('page', {})
        changed = rid in pending
        new = not record.get('last_checked')
        if monthly or changed or new or due(main, record.get('last_checked'), at):
            tasks.append({'task_id': rid, 'record_id': rid, 'url': record['url'],
                          'title': record['title'], 'scope': 'main'})
        for url in fulltext_urls(record):
            previous = fulltext_previous(record, url)
            timestamp = previous.get('checked_at')
            if previous and timestamp is None:
                # This only schedules a legacy source; it does not change its audit date.
                timestamp = record.get('last_checked')
            if monthly or changed or new or due(previous, timestamp, at):
                tasks.append({'task_id': rid + '_full_' + digest(url)[:16], 'record_id': rid,
                              'url': url, 'title': record['title'], 'scope': 'fulltext'})
    return tasks


def page_audit(log, task):
    page = {'status': log.get('page_state', '请求失败；未确认目标页'),
            'http_status': log.get('status'), 'final_url': log.get('final_url', task['url']),
            'requested_url': task['url'], 'title': log.get('page_title', ''),
            'title_confirmed': bool(log.get('title_present')), 'checked_at': log['checked_at'],
            'sha256': log.get('sha256'), 'error': log.get('error') or log.get('parse_error'),
            'attempts': log.get('attempts', [])}
    page['title_confirmed'] = matched_page(page)
    return page


def apply_check(record, task, log, pending):
    main = task['scope'] == 'main'
    old = copy.deepcopy(record['audit'].get('page', {}) if main else fulltext_previous(record, task['url']))
    page = page_audit(log, task)
    changed = (matched_page(old) and matched_page(page) and bool(old.get('sha256'))
               and bool(page.get('sha256')) and old['sha256'] != page['sha256'])
    if changed:
        stamp = page['checked_at']
        entry = pending.setdefault(record['id'], {'detected_at': stamp, 'changes': {}})
        entry.setdefault('page_changes', {})[task['url']] = {
            'scope': task['scope'], 'before': old['sha256'], 'after': page['sha256'],
            'previous_checked_at': old.get('checked_at'), 'checked_at': stamp, 'reason': PAGE_CHANGE}
        entry.update(last_detected_at=stamp, source=task['url'], status=PAGE_CHANGE + '；保留现有阅读笔记')
        page['content_change'] = PAGE_CHANGE
        record['verification_status'] = '页面字节变化/科学内容待复核'
    if main:
        record['audit']['page'] = page
        record['last_checked'] = page['checked_at']
        record.setdefault('verification_history', []).append(old)
        record['verification_history'] = record['verification_history'][-24:]
        if not page['title_confirmed']:
            record['verification_status'] = '页面访问受限/待核对'
        if record.get('video'):
            record['video']['link_checked_at'] = page['checked_at']
    else:
        fulltext = record['audit'].setdefault('fulltext', {})
        history = copy.deepcopy(old.get('history', []))
        if old:
            history.append({key: value for key, value in old.items() if key != 'history'})
        page['history'] = history[-12:]
        fulltext.setdefault('link_checks', {})[task['url']] = page
        fulltext['last_link_checked_at'] = max(page['checked_at'], fulltext.get('last_link_checked_at') or '')
    return changed or any(old.get(key) != page.get(key) for key in ('title_confirmed', 'http_status'))


def check_links(records, tasks, pending):
    lookup = {record['id']: record for record in records}
    checks = {}
    revised = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(page_check, (task['task_id'], task['url'], task['title'])): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                rid, log = future.result()
                if rid != task['task_id'] or not isinstance(log, dict):
                    raise ValueError('page worker returned the wrong task identity or malformed result')
                log = dict(log)
                log['checked_at'] = log.get('checked_at') or now()
            except Exception as error:
                log = {'requested_url': task['url'], 'final_url': task['url'], 'checked_at': now(),
                       'status': None, 'title_present': False, 'page_state': '独立巡检任务异常；未确认目标页',
                       'error': type(error).__name__ + ': ' + str(error)[:240], 'attempts': []}
            log.update(record_id=task['record_id'], check_scope=task['scope'], requested_url=task['url'])
            if apply_check(lookup[task['record_id']], task, log, pending):
                revised.add(task['record_id'])
            # Use the effective match flag, including challenge-page rejection.
            log['title_present'] = page_audit(log, task)['title_confirmed']
            checks[task['task_id']] = log
            # Atomic public JSON checkpoints survive a later task or runner-step failure.
            write(ROOT / 'data/records.json', records)
            write(ROOT / 'data/pending_changes.json', pending)
    return checks, revised


def update(initial=False):
    lockfile = ROOT / 'data/.update.lock'
    lockfile.parent.mkdir(exist_ok=True)
    with lockfile.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = now()
        prior = read(ROOT / 'data/state.json', {})
        records = read(ROOT / 'data/records.json')
        month = started[:7]
        last_sweep = prior.get('last_link_sweep_attempt_month', prior.get('last_link_sweep_month'))
        monthly = initial or last_sweep != month
        snapshot = ROOT / 'data/snapshots' / started.replace(':', '').replace('.', '-')
        write(snapshot / 'records.json', records)
        write(snapshot / 'state.json', prior)
        try:
            report = discover(initial)
        except Exception as error:
            state = read(ROOT / 'data/state.json', prior)
            state.update(last_attempt=started, last_attempt_status='failed',
                         last_errors=state.get('last_errors', []) +
                         [{'source': 'discovery', 'reason': type(error).__name__ + ': ' + str(error)[:240]}])
            write(ROOT / 'data/state.json', state)
            raise
        pending, metadata_logs = metadata_changes(records)
        tasks = link_tasks(records, pending, monthly)
        checks, revised = check_links(records, tasks, pending)
        state = read(ROOT / 'data/state.json')
        errors = [{'source': 'PubMed metadata', 'reason': log.get('error') or 'request failed'}
                  for log in metadata_logs if log.get('error') or log.get('status') != 200]
        errors += [{'source': rid, 'record_id': log['record_id'], 'scope': log['check_scope'],
                    'url': log['requested_url'], 'reason': log.get('error') or log.get('page_state', '页面未确认')}
                   for rid, log in checks.items() if not log.get('title_present')]
        counts = {'canonical_revised': len(revised), 'pending_changes': len(pending), 'link_checks': len(checks),
                  'main_link_checks': sum(log['check_scope'] == 'main' for log in checks.values()),
                  'fulltext_link_checks': sum(log['check_scope'] == 'fulltext' for log in checks.values()),
                  'link_unconfirmed': sum(not log.get('title_present') for log in checks.values())}
        state.setdefault('last_counts', {}).update(counts)
        state.setdefault('last_errors', []).extend(errors)
        if errors:
            state['last_attempt_status'] = 'partial'
        if monthly and len(checks) == len(tasks):
            # Completion of attempts is distinct from a successful all-source sweep.
            state['last_link_sweep_attempt_month'] = month
            if all(log.get('title_present') for log in checks.values()):
                state['last_link_sweep_month'] = month
        report.update(metadata_checks=metadata_logs, link_checks=checks,
                      **{key: value for key, value in counts.items() if key != 'link_checks'},
                      link_check_count=len(checks), pipeline_finished_at=now(), status=state['last_attempt_status'])
        write(ROOT / 'data/runs' / (report['run_id'] + '.json'), report)
        write(ROOT / 'data/records.json', records)
        write(ROOT / 'data/pending_changes.json', pending)
        write(ROOT / 'data/state.json', state)
        build()
        return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--initial', action='store_true')
    arguments = parser.parse_args()
    update(arguments.initial)
