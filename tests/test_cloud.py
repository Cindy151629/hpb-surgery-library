"""Cloud recovery behavior using real disposable Git remotes and mocked HTTPS bytes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cloud_recovery import (GOOD_REF, SITE_FILES, authorize, finish_runtime, git, persist, pin_good,
                            read, restore_good, start_runtime, write, digest)
from deploy_check import record_publication, verify


class Response:
    def __init__(self, body, status=200, cors=True):
        self.content = body
        self.text = body.decode()
        self.status_code = status
        self.headers = {'Access-Control-Allow-Origin': '*'} if cors else {}

    def json(self):
        return json.loads(self.content)


def make_site(directory, version):
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'domain_id': 'hpb-surgery', 'schema_version': 1,
                          'data_version': version, 'records': []})
    envelope = {'domain_id': 'hpb-surgery', 'schema_version': 1, 'data_version': version,
                'payload': payload, 'sha256': digest(payload)}
    write(directory / 'data.json', envelope)
    write(directory / 'version.json', {'data_version': version, 'sha256': envelope['sha256']})
    (directory / 'index.html').write_text('<!doctype html><html>' + version + '</html>')
    (directory / '.nojekyll').write_bytes(b'')
    return {name: (directory / name).read_bytes() for name in SITE_FILES}


def mock_https(files, cors=True):
    def request(url, **kwargs):
        return Response(files['data.json'] if url.endswith('/data.json') else files['index.html'], cors=cors)
    return request


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'runner-one'
        self.root.mkdir()
        self.remote = self.base / 'origin.git'
        git(self.base, 'init', '--bare', str(self.remote))
        git(self.root, 'init', '-b', 'main')
        write(self.root / 'data/state.json', {'source_progress': {'source': {'last_success': '2026-01-01'}},
                                            'last_errors': [], 'last_attempt_status': 'success'})
        write(self.root / 'data/records.json', [{'id': 'kept'}])
        git(self.root, 'add', 'data')
        git(self.root, 'commit', '-m', 'Initial approved public state')
        git(self.root, 'remote', 'add', 'origin', str(self.remote))
        git(self.root, 'push', '-u', 'origin', 'main')
        git(self.remote, 'symbolic-ref', 'HEAD', 'refs/heads/main')

    def tearDown(self):
        self.temp.cleanup()

    def clone(self):
        destination = self.base / 'runner-two'
        git(self.base, 'clone', str(self.remote), str(destination))
        return destination

    def receipt(self, files, version='release-one'):
        expected = {name: digest(files[name]) for name in ('index.html', 'data.json')}
        result = verify('https://example.org/hpb/', version, mock_https(files), expected)
        record_publication(self.root, result)
        return result

    def test_authorization_requires_matching_public_receipt_endpoints(self):
        environment = {'GITHUB_REPOSITORY': 'owner/library', 'GITHUB_REF_NAME': 'main', 'GITHUB_REF_TYPE': 'branch'}
        config = {
            'public_scope_approved': True, 'repository': 'owner/library',
            'https_base_url': 'https://owner.github.io/library/',
            'status_url': 'https://raw.githubusercontent.com/owner/library/main/data/publication-status.json',
            'runtime_status_url': 'https://raw.githubusercontent.com/owner/library/main/data/runtime-status.json',
        }
        target = self.root / 'config/deployment.json'
        write(target, config)
        self.assertTrue(authorize(self.root, environment)['approved'])
        for field, replacement in [
            ('public_scope_approved', False), ('runtime_status_url', ''),
            ('status_url', config['status_url'].replace('owner/library', 'other/library')),
            ('runtime_status_url', config['runtime_status_url'].replace('/main/', '/release-good/')),
        ]:
            with self.subTest(field=field, replacement=replacement):
                write(target, {**config, field: replacement})
                with self.assertRaises(ValueError):
                    authorize(self.root, environment)

    def test_new_runtime_does_not_reuse_prior_run_counts(self):
        state = read(self.root / 'data/state.json')
        state.update(last_counts={'added': 123}, last_errors=[{'source': 'old', 'reason': 'old error'}])
        write(self.root / 'data/state.json', state)
        started = start_runtime(self.root)
        self.assertEqual(read(self.root / 'data/state.json')['last_counts'], {})
        self.assertEqual(started['last_counts'], {})
        finished = finish_runtime(self.root, {'dependencies': {'outcome': 'failure'}})
        self.assertEqual(finished['last_counts'], {})
        self.assertEqual([e['source'] for e in finished['last_errors']], ['dependencies'])
        self.assertEqual(read(self.root / 'data/state.json')['source_progress']['source']['last_success'], '2026-01-01')

    def test_failed_run_persists_checkpoint_and_watermark_to_independent_runner(self):
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': '101'}):
            start_runtime(self.root)
        write(self.root / 'data/checkpoints/epmc-query.json', {'next_cursor': 'page-2', 'rows': [{'id': '1'}]})
        write(self.root / 'data/candidates.json', {'candidate': {'id': '1'}})
        finish_runtime(self.root, {'update': {'outcome': 'failure', 'conclusion': 'failure'}})
        persist(self.root, 'main')
        other = self.clone()
        self.assertEqual(read(other / 'data/checkpoints/epmc-query.json')['next_cursor'], 'page-2')
        self.assertEqual(read(other / 'data/state.json')['source_progress']['source']['last_success'], '2026-01-01')
        self.assertEqual(read(other / 'data/runtime-status.json')['status'], 'failed')
        self.assertTrue(read(other / 'data/runtime-status.json')['observed_scheduled_run'])
        self.assertEqual(read(other / 'data/candidates.json')['candidate']['id'], '1')
        # Completing the resumed query must also remove its checkpoint remotely.
        (other / 'data/checkpoints/epmc-query.json').unlink()
        persist(other, 'main')
        tip = git(other, 'rev-parse', 'HEAD').decode().strip()
        tree = git(other, 'ls-tree', '-r', '--name-only', tip).decode()
        self.assertNotIn('data/checkpoints/epmc-query.json', tree)

    def test_persistence_excludes_private_files_and_unrelated_staged_changes(self):
        (self.root / 'work/fulltext').mkdir(parents=True)
        (self.root / 'work/fulltext/paper.xml').write_text('<body>private source</body>')
        write(self.root / 'data/manual/private.json', {'note': 'private'})
        (self.root / '.env').write_text('EXAMPLE_SECRET=not-a-real-secret')
        git(self.root, 'add', '.env', 'work', 'data/manual')
        write(self.root / 'data/candidates.json', {'allowed': {'id': '2'}})
        commit = persist(self.root, 'main')
        names = git(self.root, 'ls-tree', '-r', '--name-only', commit).decode()
        self.assertIn('data/candidates.json', names)
        for forbidden in ('work/', 'data/manual/', '.env'):
            self.assertNotIn(forbidden, names)

    def test_missing_last_good_is_explicit_and_creates_no_fake_recovery(self):
        destination = self.root / 'previous-site'
        result = restore_good(self.root, destination)
        self.assertFalse(result['available'])
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(destination.exists())

    def test_verified_artifact_survives_bad_current_files_and_new_runner(self):
        site = self.root / 'site'
        good = make_site(site, 'release-one')
        self.receipt(good)
        (site / 'private.xml').write_text('must not enter release tree')
        commit = pin_good(self.root, site)
        self.assertEqual(set(git(self.root, 'ls-tree', '--name-only', commit).decode().splitlines()),
                         set(SITE_FILES) | {'release.json'})
        # A new candidate is not allowed to replace the verified pointer using an old receipt.
        make_site(site, 'release-two')
        with self.assertRaises(ValueError):
            pin_good(self.root, site)
        other = self.clone()
        destination = other / 'previous-site'
        restored = restore_good(other, destination)
        self.assertEqual(restored['commit'], commit)
        for name, body in good.items():
            self.assertEqual((destination / name).read_bytes(), body)
        self.assertFalse((destination / 'private.xml').exists())
        # The actual restored endpoint must still satisfy external byte verification.
        expected = {name: digest(good[name]) for name in ('index.html', 'data.json')}
        result = verify('https://example.org/hpb/', 'release-one', mock_https(good), expected)
        receipt = record_publication(other, result, rollback=True, release_commit=commit)
        self.assertTrue(receipt['rollback'])
        self.assertEqual(receipt['release_commit'], commit)
        self.assertEqual(read(other / 'data/state.json')['last_published_version'], 'release-one')

    def test_second_verified_release_keeps_first_commit_immutable(self):
        site = self.root / 'site'
        original = make_site(site, 'release-one')
        self.receipt(original)
        first = pin_good(self.root, site)
        second_files = make_site(site, 'release-two')
        self.receipt(second_files, 'release-two')
        second = pin_good(self.root, site)
        self.assertNotEqual(first, second)
        self.assertEqual(git(self.root, 'show', first + ':index.html'), original['index.html'])
        self.assertEqual(git(self.root, 'rev-parse', second + '^').decode().strip(), first)

    def test_same_label_and_self_consistent_remote_hash_do_not_bypass_byte_check(self):
        intended = make_site(self.root / 'site', 'release-one')
        self.receipt(intended)
        remote = dict(intended)
        changed = json.loads(remote['data.json'])
        payload = json.loads(changed['payload'])
        payload['records'] = [{'id': 'unexpected'}]
        changed['payload'] = json.dumps(payload)
        changed['sha256'] = digest(changed['payload'])
        remote['data.json'] = json.dumps(changed).encode()
        expected = {name: digest(intended[name]) for name in ('index.html', 'data.json')}
        before = (self.root / 'data/publication-status.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'intended artifact'):
            verify('https://example.org/hpb/', 'release-one', mock_https(remote), expected)
        self.assertEqual((self.root / 'data/publication-status.json').read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'CORS'):
            verify('https://example.org/hpb/', 'release-one', mock_https(intended, cors=False), expected)

    def test_no_receipt_or_symlink_cannot_be_promoted(self):
        site = self.root / 'site'
        files = make_site(site, 'release-one')
        with self.assertRaises(ValueError):
            pin_good(self.root, site)
        self.receipt(files)
        (site / 'index.html').unlink()
        secret = self.root / 'outside.html'
        secret.write_bytes(files['index.html'])
        (site / 'index.html').symlink_to(secret)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            pin_good(self.root, site)

    def test_rollback_success_does_not_erase_failed_attempt_or_schedule_history(self):
        files = make_site(self.root / 'site', 'release-one')
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': '100'}):
            start_runtime(self.root)
            result = self.receipt(files)
        observed = read(self.root / 'data/publication-status.json')['observed_scheduled_run']
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ID': '101'}):
            start_runtime(self.root)
            result = verify('https://example.org/hpb/', 'release-one', mock_https(files))
            receipt = record_publication(self.root, result, rollback=True, release_commit='a' * 40)
        self.assertEqual(receipt['event'], 'workflow_dispatch')
        self.assertEqual(receipt['observed_scheduled_run'], observed)
        runtime = finish_runtime(self.root, {
            'verification': {'outcome': 'failure'}, 'rollback': {'outcome': 'success'},
            'rollback_check': {'outcome': 'success'}})
        self.assertEqual(runtime['status'], 'failed')
        self.assertEqual(runtime['rollback']['status'], 'verified')
        self.assertEqual(read(self.root / 'data/state.json')['source_progress']['source']['last_success'], '2026-01-01')


if __name__ == '__main__':
    unittest.main()
