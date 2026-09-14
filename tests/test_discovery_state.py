"""Discovery state regressions: temporary fixtures only; every network call is mocked."""
import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discovery


class Response:
    status_code = 200

    def __init__(self, data=None, content=b'', status=200):
        self.data, self.content, self.status_code = data, content, status

    def json(self):
        return self.data


class DiscoveryStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'query_version': 'v3', 'overlap_days': 21, 'monthly_lookback_days': 90,
                       'epmc_endpoint': 'https://epmc.example/search',
                       'pubmed_endpoint': 'https://pubmed.example/search',
                       'sages_feed': 'https://sages.example/feed',
                       'stanford_directory': 'https://stanford.example/videos',
                       'historical_filter': '(PUB_TYPE:"guideline")', 'max_pages': 2, 'page_size': 10}
        self.topic = {'id': 'pancreas', 'organ': '胰腺', 'query_groups': [['pancreatectomy']]}
        self.put('config/sources.json', self.config)
        self.put('config/taxonomy.json', {'topics': [self.topic]})
        self.put('data/records.json', [])
        self.put('data/state.json', {'source_progress': {}, 'runs': [], 'last_monthly': '2026-09',
                                    'last_successful_search': None, 'last_successful_publish': None})
        self.calls = []

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def get(self, name='data/state.json'):
        return json.loads((self.root / name).read_text())

    def catalog(self, url, params=None):
        if url == self.config['sages_feed']:
            return Response(content=b'<rss><channel/></rss>'), {'status': 200, 'requested_url': url}
        if url == self.config['stanford_directory']:
            return Response(content=b'<a href="https://youtu.be/example">Official lecture</a>'), {'status': 200, 'requested_url': url}
        raise AssertionError('Unexpected/unmocked request: ' + url)

    def search(self, source, complete):
        def result(query, config, **kwargs):
            self.calls.append((source, query))
            if isinstance(complete, BaseException):
                raise complete
            return [], [{'status': 200 if complete else 503, 'requested_url': config['epmc_endpoint' if source == 'Europe PMC' else 'pubmed_endpoint']}], complete, 0 if complete else None
        return result

    def run_at(self, at='2026-09-14T12:00:00+00:00', epmc=True, pubmed=True, fetch=None, **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(discovery, 'ROOT', self.root))
            stack.enter_context(patch.object(discovery, 'now', return_value=at))
            stack.enter_context(patch.object(discovery, 'epmc_search', side_effect=epmc if callable(epmc) else self.search('Europe PMC', epmc)))
            stack.enter_context(patch.object(discovery, 'pubmed_search', side_effect=pubmed if callable(pubmed) else self.search('PubMed', pubmed)))
            stack.enter_context(patch.object(discovery, 'fetch', side_effect=fetch or self.catalog))
            stack.enter_context(redirect_stdout(io.StringIO()))
            return discovery.run(**kwargs)

    def old_progress(self, version='v2', at='2026-09-01T00:00:00+00:00'):
        state = self.get()
        state['source_progress']['PubMed:pancreas'] = {'last_success': at, 'query_version': version}
        state['last_successful_search'] = at
        self.put('data/state.json', state)
        return copy.deepcopy(state)

    def test_esearch_errorlist_never_becomes_zero_or_positive_success(self):
        for count, ids in [('0', []), ('1', ['123'])]:
            with self.subTest(count=count):
                body = {'esearchresult': {'count': count, 'idlist': ids,
                                         'errorlist': {'fieldnotfound': ['BADFIELD']}}}
                rows, logs, complete, total = discovery.pubmed_search('bad query', self.config,
                    request=lambda *args: (Response(body), {'status': 200}))
                self.assertFalse(complete)
                self.assertEqual(rows, [])
                self.assertIsNone(total)
                self.assertEqual(logs[0]['error_list']['fieldnotfound'], ['BADFIELD'])

    def test_valid_zero_result_is_complete_but_missing_idlist_is_not(self):
        for result, expected in [({'count': '0', 'idlist': []}, True), ({'count': '0'}, False)]:
            with self.subTest(result=result):
                values = discovery.pubmed_search('query', self.config,
                    request=lambda *args: (Response({'esearchresult': result}), {'status': 200}))
                self.assertEqual(values[2], expected)

    def test_esearch_query_error_keeps_old_success_watermark(self):
        before = self.old_progress()
        body = {'esearchresult': {'count': '0', 'idlist': [], 'errorlist': {'phrasesnotfound': ['bad']}}}
        original = discovery.pubmed_search
        def invalid(query, config):
            return original(query, config, request=lambda *args: (Response(body), {'status': 200}))
        report = self.run_at(initial=True, sources=['PubMed'], pubmed=invalid)
        state = self.get()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(state['last_successful_search'], before['last_successful_search'])
        self.assertEqual(state['source_progress']['PubMed:pancreas']['query_version'], 'v2')
        self.assertEqual(state['source_progress']['PubMed:pancreas']['pending_query_version'], 'v3')

    def test_pubmed_failed_new_version_retries_original_history_and_promotes_only_on_success(self):
        old = self.old_progress()
        self.run_at(initial=True, sources=['PubMed'], pubmed=False)
        failed = self.get()['source_progress']['PubMed:pancreas']
        self.assertEqual(failed['last_success'], old['source_progress']['PubMed:pancreas']['last_success'])
        self.assertEqual(failed['query_version'], 'v2')
        self.assertEqual(failed['pending_query_version'], 'v3')
        self.assertIn('Guideline[pt]', failed['pending_query'])
        self.assertNotIn('[EDAT]', failed['pending_query'])
        self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        successful = self.get()['source_progress']['PubMed:pancreas']
        self.assertEqual(successful['last_query'], failed['pending_query'])
        self.assertEqual(successful['window'], failed['pending_window'])
        self.assertEqual(successful['query_version'], 'v3')
        self.assertEqual(successful['last_success'], '2026-09-14T12:00:00+00:00')
        self.assertEqual(successful['last_completed_at'], '2026-09-15T12:00:00+00:00')
        self.assertNotIn('pending_query', successful)

    def test_pubmed_date_window_is_frozen_on_retry_even_after_cycle_expiry(self):
        self.old_progress(version='v3')
        self.run_at(sources=['PubMed'], pubmed=False)
        failed = self.get()['source_progress']['PubMed:pancreas']
        self.assertIn('"2026-08-11"[EDAT]', failed['pending_query'])
        self.assertIn('"2026-09-14"[MDAT]', failed['pending_query'])
        self.run_at(at='2026-11-15T12:00:00+00:00', only=['pancreas'], sources=['PubMed'])
        successful = self.get()['source_progress']['PubMed:pancreas']
        self.assertEqual(successful['last_query'], failed['pending_query'])
        self.assertEqual(successful['window'], failed['pending_window'])
        self.assertEqual(successful['last_success'], '2026-09-14T12:00:00+00:00')
        self.assertEqual(successful['last_completed_at'], '2026-11-15T12:00:00+00:00')

    def test_beginning_of_request_persists_running_and_pending_before_interruption(self):
        old = self.old_progress()
        def interrupted(query, config):
            state = self.get()
            self.assertEqual(state['last_attempt_status'], 'running')
            self.assertEqual(state['last_successful_search'], old['last_successful_search'])
            self.assertEqual(self.get('data/runs/' + state['last_run_id'] + '.json')['status'], 'running')
            progress = state['source_progress']['PubMed:pancreas']
            self.assertEqual(progress['pending_query'], query)
            self.assertEqual(progress['pending_query_version'], 'v3')
            self.assertEqual(progress['query_version'], 'v2')
            raise KeyboardInterrupt('simulated runner interruption')
        with self.assertRaises(KeyboardInterrupt):
            self.run_at(initial=True, sources=['PubMed'], pubmed=interrupted)
        interrupted_state = self.get()
        self.assertEqual(interrupted_state['last_attempt_status'], 'running')
        pending = interrupted_state['source_progress']['PubMed:pancreas']['pending_query']
        self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        self.assertEqual(self.get()['source_progress']['PubMed:pancreas']['last_query'], pending)

    def test_fresh_source_retry_joins_same_cycle_with_matching_queries(self):
        first = self.run_at(initial=True, sources=['Europe PMC'])
        self.assertEqual(first['status'], 'partial')
        self.assertTrue(first['requested_sources_complete'])
        self.assertIsNone(self.get()['last_successful_search'])
        second = self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        state = self.get()
        self.assertEqual(first['cycle_id'], second['cycle_id'])
        self.assertEqual(second['status'], 'success')
        self.assertTrue(second['all_sources_complete'])
        self.assertEqual(second['missing_source_keys'], [])
        self.assertEqual(state['last_successful_search'], '2026-09-15T12:00:00+00:00')
        self.assertEqual(state['last_complete_coverage_through'], '2026-09-14T12:00:00+00:00')
        self.assertEqual(state['last_search_completed_at'], '2026-09-15T12:00:00+00:00')

    def test_months_old_same_version_success_is_not_counted_for_single_source(self):
        state = self.old_progress(version='v3', at='2026-01-01T00:00:00+00:00')
        plan = discovery.query_plan(self.topic, 'Europe PMC', {}, self.config, '2026-01-01T00:00:00+00:00', initial=True)
        state['source_progress']['Europe PMC:pancreas'] = {'last_success': '2026-01-01T00:00:00+00:00',
                                                         'query_version': 'v3', 'last_query': plan['query']}
        self.put('data/state.json', state)
        report = self.run_at(initial=True, sources=['PubMed'])
        self.assertFalse(report['all_sources_complete'])
        self.assertIn('Europe PMC:pancreas', report['missing_source_keys'])
        self.assertEqual(self.get()['last_successful_search'], '2026-01-01T00:00:00+00:00')

    def test_source_retry_cannot_join_expired_cycle(self):
        first = self.run_at(initial=True, sources=['Europe PMC'])
        second = self.run_at(at='2026-10-06T12:00:00+00:00', initial=True, sources=['PubMed'])
        self.assertNotEqual(first['cycle_id'], second['cycle_id'])
        self.assertFalse(second['all_sources_complete'])
        self.assertIsNone(self.get()['last_successful_search'])
        self.assertEqual(self.get()['source_progress']['Europe PMC:pancreas']['last_success'], '2026-09-14T12:00:00+00:00')

    def test_changed_query_fingerprint_invalidates_cycle_even_without_version_bump(self):
        first = self.run_at(initial=True, sources=['Europe PMC'])
        changed = {**self.topic, 'query_groups': [['pancreatectomy', 'pancreatic surgery']]}
        self.put('config/taxonomy.json', {'topics': [changed]})
        second = self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        self.assertNotEqual(first['cycle_id'], second['cycle_id'])
        self.assertFalse(second['all_sources_complete'])
        self.assertIn('pancreatic surgery', next(x['query'] for x in second['queries'] if x['source'] == 'PubMed'))

    def test_success_entry_must_match_actual_query_not_only_stored_fingerprint(self):
        self.run_at(initial=True, sources=['Europe PMC'])
        state = self.get()
        state['source_progress']['Europe PMC:pancreas']['last_query'] += ' OR unrelated'
        self.put('data/state.json', state)
        report = self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        self.assertFalse(report['all_sources_complete'])
        self.assertIn('Europe PMC:pancreas', report['missing_source_keys'])

    def test_default_full_run_requires_every_source_to_succeed_in_that_run(self):
        first = self.run_at(initial=True, sources=['Europe PMC'])
        report = self.run_at(at='2026-09-15T12:00:00+00:00', epmc=False)
        self.assertNotEqual(first['cycle_id'], report['cycle_id'])
        self.assertFalse(report['all_sources_complete'])
        self.assertEqual(self.get()['source_progress']['Europe PMC:pancreas']['last_success'], '2026-09-14T12:00:00+00:00')
        self.assertIsNone(self.get()['last_successful_search'])
        success = self.run_at(at='2026-09-16T12:00:00+00:00')
        self.assertTrue(success['all_sources_complete'])
        self.assertNotEqual(report['cycle_id'], success['cycle_id'])

    def test_failed_monthly_history_remains_required_during_source_only_retry(self):
        state = self.get()
        state['last_monthly'] = '2026-08'
        self.put('data/state.json', state)
        count = 0
        def epmc(query, config, **kwargs):
            nonlocal count
            count += 1
            return [], [{'status': 200 if count == 1 else 503}], count == 1, 0
        first = self.run_at(epmc=epmc)
        self.assertFalse(first['all_sources_complete'])
        self.assertIn('Europe PMC:pancreas:history', first['missing_source_keys'])
        second = self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        self.assertFalse(second['all_sources_complete'])
        self.assertEqual(self.get()['last_monthly'], '2026-08')
        third = self.run_at(at='2026-09-16T12:00:00+00:00', sources=['Europe PMC'])
        self.assertTrue(third['all_sources_complete'])
        self.assertEqual(self.get()['last_monthly'], '2026-09')
        self.assertEqual(self.get()['historical_rotation_index'], 1)

    def test_query_exception_keeps_other_results_and_failed_pending(self):
        self.old_progress()
        report = self.run_at(initial=True, epmc=ValueError('bad source payload'))
        state = self.get()
        self.assertFalse(report['all_sources_complete'])
        self.assertIn('bad source payload', report['queries'][0]['pages'][-1]['error'])
        self.assertEqual(state['source_progress']['PubMed:pancreas']['query_version'], 'v3')
        self.assertEqual(state['source_progress']['Europe PMC:pancreas']['pending_query_version'], 'v3')
        self.assertEqual(state['last_attempt_status'], 'partial')

    def test_catalog_failure_preserves_previous_entries_and_blocks_global_success(self):
        self.put('data/video_discovery.json', {'SAGES RSS': [{'title': 'Previous', 'url': 'https://sages.example/old'}]})
        def catalog(url, params=None):
            return (None, {'status': 503}) if url == self.config['sages_feed'] else self.catalog(url, params)
        report = self.run_at(fetch=catalog)
        self.assertFalse(report['all_sources_complete'])
        self.assertIn('SAGES RSS', report['missing_source_keys'])
        self.assertEqual(self.get('data/video_discovery.json')['SAGES RSS'][0]['title'], 'Previous')
        self.assertEqual(self.get()['source_progress']['SAGES RSS']['last_attempt_status'], 'failed')

    def test_legacy_pending_query_with_changed_filter_is_not_reused(self):
        previous = {'query_version': 'v3', 'pending_at': '2026-09-01T00:00:00+00:00',
                    'pending_query': '(' + discovery.compile_query(self.topic['query_groups'], 'Europe PMC') + ') AND SRC:MED AND (PUB_TYPE:"outdated")'}
        plan = discovery.query_plan(self.topic, 'Europe PMC', previous, self.config, '2026-09-14T12:00:00+00:00')
        self.assertFalse(plan['pending_reused'])
        self.assertNotIn('outdated', plan['query'])

    def test_new_query_version_does_not_relabel_previous_watermark_after_another_failure(self):
        self.old_progress()
        self.run_at(initial=True, sources=['PubMed'], pubmed=False)
        self.config['query_version'] = 'v4'
        self.put('config/sources.json', self.config)
        self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'], pubmed=False)
        current = self.get()['source_progress']['PubMed:pancreas']
        self.assertEqual(current['query_version'], 'v2')
        self.assertEqual(current['last_success'], '2026-09-01T00:00:00+00:00')
        self.assertEqual(current['pending_query_version'], 'v4')
        self.assertEqual(current['pending_at'], '2026-09-15T12:00:00+00:00')
        self.assertNotIn('[EDAT]', current['pending_query'])

    def test_after_frozen_window_retry_next_full_run_catches_up_from_safe_watermark(self):
        self.old_progress(version='v3')
        self.run_at(pubmed=False)
        retried = self.run_at(at='2026-09-20T12:00:00+00:00', sources=['PubMed'])
        self.assertTrue(retried['all_sources_complete'])
        state = self.get()
        self.assertEqual(state['last_successful_search'], '2026-09-20T12:00:00+00:00')
        self.assertEqual(state['last_complete_coverage_through'], '2026-09-14T12:00:00+00:00')
        fresh = self.run_at(at='2026-09-21T12:00:00+00:00')
        query = next(x['query'] for x in fresh['queries'] if x['key'] == 'PubMed:pancreas')
        self.assertIn('"2026-08-24"[EDAT]', query)
        self.assertIn('"2026-09-21"[MDAT]', query)
        self.assertEqual(self.get()['last_complete_coverage_through'], '2026-09-21T12:00:00+00:00')

    def test_efetch_failure_keeps_esearch_query_pending_and_previous_version(self):
        self.old_progress()
        def ids(query, config):
            return ['123'], [{'status': 200}], True, 1
        def failed_metadata(url, params=None):
            if url.endswith('/efetch.fcgi'):
                return Response(status=503), {'status': 503, 'requested_url': url}
            return self.catalog(url, params)
        report = self.run_at(initial=True, sources=['PubMed'], pubmed=ids, fetch=failed_metadata)
        current = self.get()['source_progress']['PubMed:pancreas']
        self.assertFalse(report['requested_sources_complete'])
        self.assertEqual(current['query_version'], 'v2')
        self.assertEqual(current['pending_query_version'], 'v3')
        self.assertEqual(current['pending_query'], report['queries'][0]['query'])
        self.assertEqual(report['queries'][0]['pages'][-1]['error'], 'EFetch failed')

    def test_completed_cycle_cannot_be_reused_to_claim_new_single_source_success(self):
        completed = self.run_at(initial=True)
        previous = self.get()['last_successful_search']
        subsequent = self.run_at(at='2026-09-15T12:00:00+00:00', sources=['PubMed'])
        self.assertNotEqual(completed['cycle_id'], subsequent['cycle_id'])
        self.assertFalse(subsequent['all_sources_complete'])
        self.assertEqual(self.get()['last_successful_search'], previous)


if __name__ == '__main__':
    unittest.main()
