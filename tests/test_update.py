"""Routine link sweeps use isolated records and fake requests, never the live library."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update as updater
from common import read, write


def fixture(rid='A'):
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        'id': rid, 'title': 'A reviewed surgical paper', 'kind': 'article', 'pmid': '',
        'url': 'https://example.org/' + rid, 'fulltext_url': '',
        'last_checked': stamp, 'verification_status': '所列维度已核对',
        'note': {'question': '人工阅读问题', 'results': ['已核实的人工结果'],
                 'read_depth': '摘要导读', 'sources': [], 'reviewed_at': '2026-01-01'},
        'audit': {'page': {'status': '目标题名在页面中确认', 'title_confirmed': True,
                           'http_status': 200, 'sha256': 'original', 'checked_at': stamp},
                  'identity': {'differences': []},
                  'fulltext': {'status': '历史正文阅读证据', 'url': 'https://example.org/' + rid},
                  'content': {'status': '人工已核对', 'checked_at': '2026-01-01'}},
        'corrections': [],
    }


def successful_log(url, sha='original'):
    return {'status': 200, 'title_present': True, 'page_title': 'A reviewed surgical paper',
            'page_state': '目标题名在页面中确认', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'sha256': sha, 'requested_url': url, 'final_url': url, 'attempts': [{'status': 200}]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(updater, 'ROOT', self.root)
        self.root_patch.start()
        self.network_guard = patch.object(updater, 'fetch', side_effect=AssertionError('Live network is forbidden'))
        self.network_guard.start()

    def tearDown(self):
        self.network_guard.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_same_title_hash_change_queues_review_and_preserves_notes(self):
        record = fixture()
        note = copy.deepcopy(record['note'])
        content_audit = copy.deepcopy(record['audit']['content'])
        pending = {'A': {'detected_at': '2026-01-01', 'changes': {'year': {'before': 2024, 'after': 2025}}}}
        task = updater.link_tasks([record], pending, monthly=False)[0]
        log = successful_log(record['url'], sha='changed-bytes')
        updater.apply_check(record, task, log, pending)
        change = pending['A']['page_changes'][record['url']]
        self.assertEqual(change['reason'], '页面字节变化，可能排版/广告，科学内容待复核')
        self.assertEqual(change['before'], 'original')
        self.assertEqual(change['after'], 'changed-bytes')
        self.assertEqual(change['checked_at'], log['checked_at'])
        self.assertEqual(record['last_checked'], log['checked_at'])
        self.assertEqual(pending['A']['changes']['year']['after'], 2025)
        self.assertEqual(record['note'], note)
        self.assertEqual(record['audit']['content'], content_audit)

    def test_challenge_or_unmatched_page_cannot_create_scientific_change_claim(self):
        for scope in ('challenge', 'unmatched', 'previous_unmatched'):
            with self.subTest(scope=scope):
                record = fixture()
                task = updater.link_tasks([record], {}, monthly=True)[0]
                log = successful_log(record['url'], 'different')
                if scope == 'challenge':
                    log['page_state'] = '访问验证或限制页面'
                elif scope == 'unmatched':
                    log['title_present'] = False
                else:
                    record['audit']['page']['title_confirmed'] = False
                pending = {}
                updater.apply_check(record, task, log, pending)
                self.assertEqual(pending, {})
                if scope == 'challenge':
                    self.assertFalse(record['audit']['page']['title_confirmed'])

    def test_fulltext_failure_remains_separate_from_good_main_page(self):
        record = fixture()
        record['fulltext_url'] = 'https://example.org/article.pdf'
        original_full = copy.deepcopy(record['audit']['fulltext'])
        note = copy.deepcopy(record['note'])
        main_task, full_task = updater.link_tasks([record], {}, monthly=True)
        main = successful_log(record['url'])
        updater.apply_check(record, main_task, main, {})
        updater.apply_check(record, full_task, {
            'status': 404, 'title_present': False, 'page_state': '受限或请求失败',
            'checked_at': datetime.now(timezone.utc).isoformat(), 'final_url': full_task['url'], 'attempts': [{'status': 404}]
        }, {})
        self.assertTrue(record['audit']['page']['title_confirmed'])
        self.assertEqual(record['audit']['page']['http_status'], 200)
        self.assertEqual(record['last_checked'], main['checked_at'])
        self.assertEqual(record['audit']['fulltext']['status'], original_full['status'])
        self.assertEqual(record['audit']['fulltext']['link_checks'][full_task['url']]['http_status'], 404)
        self.assertEqual(record['verification_status'], '所列维度已核对')
        self.assertEqual(record['note'], note)

    def test_monthly_sources_are_distinct_and_weekly_only_checks_due_sources(self):
        record = fixture()
        pdf = 'https://example.org/article.pdf'
        xml = 'https://example.org/article.xml'
        record['fulltext_url'] = pdf
        record['audit']['fulltext']['url'] = xml
        record['note'].update(read_depth='正文重点核对', sources=[
            {'url': pdf, 'scope': 'PDF 正文'}, {'url': xml, 'scope': 'XML 正文'},
            {'url': 'https://example.org/metadata', 'scope': '题录身份核对'}])
        fresh = {'title_confirmed': True, 'http_status': 200, 'status': '已匹配',
                 'checked_at': datetime.now(timezone.utc).isoformat(), 'sha256': 'same'}
        record['audit']['fulltext']['link_checks'] = {pdf: copy.deepcopy(fresh), xml: copy.deepcopy(fresh)}
        monthly = updater.link_tasks([record], {}, monthly=True)
        self.assertEqual({task['url'] for task in monthly}, {record['url'], pdf, xml})
        self.assertEqual(len(monthly), 3)
        self.assertEqual(updater.link_tasks([record], {}, monthly=False), [])
        record['audit']['fulltext']['link_checks'][pdf]['checked_at'] = (
            datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        weekly = updater.link_tasks([record], {}, monthly=False)
        self.assertEqual([(task['scope'], task['url']) for task in weekly], [('fulltext', pdf)])
        self.assertEqual(len(updater.link_tasks([record], {'A': {'changes': {}}}, monthly=False)), 3)

    def test_fulltext_hash_change_uses_source_specific_history(self):
        record = fixture()
        record['fulltext_url'] = 'https://example.org/article.pdf'
        old = {'status': '已取得并匹配PDF', 'title_confirmed': True, 'http_status': 200,
               'sha256': 'pdf-old', 'checked_at': '2026-01-01T00:00:00+00:00'}
        record['audit']['fulltext']['link_checks'] = {record['fulltext_url']: old}
        main_before = copy.deepcopy(record['audit']['page'])
        full_task = next(t for t in updater.link_tasks([record], {}, True) if t['scope'] == 'fulltext')
        pending = {}
        updater.apply_check(record, full_task, successful_log(full_task['url'], 'pdf-new'), pending)
        self.assertEqual(record['audit']['page'], main_before)
        self.assertEqual(pending['A']['page_changes'][full_task['url']]['before'], 'pdf-old')
        self.assertEqual(pending['A']['page_changes'][full_task['url']]['scope'], 'fulltext')
        self.assertEqual(record['audit']['fulltext']['link_checks'][full_task['url']]['history'][0]['sha256'], 'pdf-old')

    def test_worker_exception_keeps_other_results_and_partial_state(self):
        first, second = fixture('A'), fixture('B')
        first['fulltext_url'] = 'https://example.org/A.pdf'
        original_notes = [copy.deepcopy(first['note']), copy.deepcopy(second['note'])]
        write(self.root / 'data/records.json', [first, second])
        state = {'source_progress': {'source': {'last_success': '2026-01-01'}}, 'last_counts': {},
                 'last_errors': [], 'last_attempt_status': 'success', 'last_link_sweep_month': '2020-01'}
        write(self.root / 'data/state.json', state)
        def discover(initial):
            return {'run_id': 'isolated-test-run'}
        def checker(task):
            task_id, url, title = task
            if task_id == 'B':
                raise RuntimeError('one broken worker')
            if url.endswith('.pdf'):
                return task_id, {'status': 403, 'title_present': False, 'page_state': '受限或请求失败',
                                 'checked_at': datetime.now(timezone.utc).isoformat(), 'final_url': url}
            return task_id, successful_log(url, 'new-main-bytes')
        with patch.object(updater, 'discover', side_effect=discover), patch.object(updater, 'page_check', side_effect=checker), patch.object(updater, 'build') as build:
            report = updater.update()
        stored = read(self.root / 'data/records.json')
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(len(report['link_checks']), 3)
        self.assertEqual(report['main_link_checks'], 2)
        self.assertEqual(report['fulltext_link_checks'], 1)
        self.assertEqual([record['note'] for record in stored], original_notes)
        self.assertTrue(stored[0]['audit']['page']['title_confirmed'])
        self.assertIn('one broken worker', stored[1]['audit']['page']['error'])
        self.assertIn('A', read(self.root / 'data/pending_changes.json'))
        final = read(self.root / 'data/state.json')
        self.assertEqual(final['last_link_sweep_month'], '2020-01')
        self.assertEqual(final['last_link_sweep_attempt_month'], datetime.now(timezone.utc).strftime('%Y-%m'))
        self.assertEqual(final['source_progress'], state['source_progress'])
        self.assertEqual({error['scope'] for error in final['last_errors']}, {'main', 'fulltext'})
        build.assert_called_once()

    def metadata_record(self):
        record = fixture('P013')
        record.update(pmid='38926000', doi='10.1/example', year='2024', verification_status='页面访问受限/待核对')
        pubmed = {'type': 'ErratumFor', 'pmid': '28040257', 'citation': 'Surgery. 2017.', 'note': ''}
        crossref = {'DOI': '10.1016/j.surg.2016.11.014', 'type': 'erratum', 'source': 'publisher'}
        record['corrections'] = [pubmed, crossref]
        return record, pubmed

    def test_crossref_extra_relation_does_not_fake_pubmed_correction_change(self):
        record, relation = self.metadata_record()
        before = copy.deepcopy(record)
        metadata = {key: record[key] for key in ('title', 'doi', 'year')}
        metadata['corrections'] = [copy.deepcopy(relation), copy.deepcopy(relation)]
        response = SimpleNamespace(status_code=200, content=b'fake')
        log = {'status': 200, 'requested_url': 'https://example.org/pubmed', 'checked_at': datetime.now(timezone.utc).isoformat()}
        with patch.object(updater, 'fetch', return_value=(response, log)), patch.object(updater, 'parse_pubmed', return_value={'38926000': metadata}):
            pending, logs = updater.metadata_changes([record])
        self.assertEqual(pending, {})
        self.assertEqual(record, before)
        self.assertNotIn('error', logs[0])

    def test_real_pubmed_change_preserves_existing_page_review_queue(self):
        record, relation = self.metadata_record()
        write(self.root / 'data/pending_changes.json', {'P013': {'changes': {}, 'page_changes': {'https://example.org/P013': {'reason': updater.PAGE_CHANGE}}}})
        metadata = {key: record[key] for key in ('title', 'doi', 'year')}
        metadata['corrections'] = [{**relation, 'note': 'New correction note'}]
        response = SimpleNamespace(status_code=200, content=b'fake')
        log = {'status': 200, 'requested_url': 'https://example.org/pubmed', 'checked_at': datetime.now(timezone.utc).isoformat()}
        with patch.object(updater, 'fetch', return_value=(response, log)), patch.object(updater, 'parse_pubmed', return_value={'38926000': metadata}):
            pending, _ = updater.metadata_changes([record])
        change = pending['P013']['changes']['corrections']
        self.assertEqual(change['source'], 'PubMed')
        self.assertEqual(len(change['before']), 1)
        self.assertEqual(change['after'][0]['note'], 'New correction note')
        self.assertTrue(pending['P013']['page_changes'])
        self.assertEqual(len(record['corrections']), 2)
        self.assertEqual(record['note']['results'], ['已核实的人工结果'])


if __name__ == '__main__':
    unittest.main()
