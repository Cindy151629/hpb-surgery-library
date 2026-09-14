"""Verify the published bytes before recording a publication or a rollback."""
from common import ROOT, digest, now, read, requests, write
from urllib.parse import urljoin, urlsplit
import json
import os
import time


def response_bytes(response):
    if hasattr(response, 'content'):
        return response.content
    if getattr(response, 'text', ''):
        return response.text.encode('utf-8')
    return json.dumps(response.json(), ensure_ascii=False).encode('utf-8')


def verify(base, expected, request=requests.get, expected_files=None):
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('publication requires an HTTPS URL without credentials')
    headers = {'Origin': 'null', 'Cache-Control': 'no-cache'}
    response = request(urljoin(base.rstrip('/') + '/', 'data.json'), headers=headers, timeout=30)
    if response.status_code != 200:
        raise ValueError('published snapshot HTTP ' + str(response.status_code))
    if response.headers.get('Access-Control-Allow-Origin') not in ('*', 'null'):
        raise ValueError('file:// CORS response missing')
    envelope = response.json()
    if (envelope.get('domain_id') != 'hpb-surgery' or envelope.get('schema_version') != 1
            or envelope.get('data_version') != expected):
        raise ValueError('published version/domain mismatch')
    if digest(envelope.get('payload', '')) != envelope.get('sha256'):
        raise ValueError('published hash mismatch')
    payload = json.loads(envelope['payload'])
    if (payload.get('data_version') != expected or payload.get('domain_id') != 'hpb-surgery'
            or payload.get('schema_version') != 1):
        raise ValueError('payload version/domain mismatch')
    html = request(base, headers=headers, timeout=30)
    if html.status_code != 200 or expected not in html.text:
        raise ValueError('published HTML version mismatch')
    hashes = {'data.json': digest(response_bytes(response)), 'index.html': digest(response_bytes(html))}
    if expected_files and any(hashes[name] != expected_files.get(name) for name in hashes):
        raise ValueError('published bytes differ from the intended artifact')
    stamp = now()
    event = os.environ.get('GITHUB_EVENT_NAME')
    return {
        'deployed': True, 'domain_id': 'hpb-surgery', 'schema_version': 1,
        'data_version': expected, 'verified_at': stamp, 'url': base,
        'sha256': envelope['sha256'], 'file_hashes': hashes, 'cors_origin_null': True,
        'event': event, 'run_id': os.environ.get('GITHUB_RUN_ID'),
        'run_url': 'https://github.com/' + os.environ.get('GITHUB_REPOSITORY', '')
                   + '/actions/runs/' + os.environ.get('GITHUB_RUN_ID', ''),
    }


def record_publication(root, result, rollback=False, release_commit=None):
    """Preserve observed schedule history, without fabricating a scheduled run."""
    root = root.resolve()
    prior = read(root / 'data/publication-status.json', {})
    runtime = read(root / 'data/runtime-status.json', {})
    config = read(root / 'config/deployment.json', {})
    receipt = dict(result)
    scheduled = receipt.get('event') == 'schedule'
    receipt.update(
        rollback=rollback, release_commit=release_commit,
        schedule_enabled=scheduled or bool(config.get('schedule_enabled')),
        observed_scheduled_run=((runtime.get('observed_scheduled_run') or receipt['verified_at']) if scheduled else
                                runtime.get('observed_scheduled_run') or prior.get('observed_scheduled_run')),
    )
    write(root / 'data/publication-status.json', receipt)
    state = read(root / 'data/state.json', {})
    state['last_successful_publish'] = receipt['verified_at']
    state['last_published_version'] = receipt['data_version']
    write(root / 'data/state.json', state)
    return receipt


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--version')
    parser.add_argument('--expected-dir', type=Path, required=True)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    version = read(args.expected_dir / 'version.json')['data_version']
    if args.version and args.version != version:
        raise ValueError('requested version differs from artifact')
    config = read(ROOT / 'config/deployment.json', {})
    if args.url.rstrip('/') != config.get('https_base_url', '').rstrip('/'):
        raise ValueError('deployed endpoint differs from the approved configured endpoint')
    hashes = {name: digest((args.expected_dir / name).read_bytes()) for name in ('index.html', 'data.json')}
    for attempt in range(5):
        try:
            result = verify(args.url, version, expected_files=hashes)
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    release = read(args.expected_dir / 'release.json', {}) if args.rollback else {}
    receipt = record_publication(ROOT, result, args.rollback, release.get('commit'))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
