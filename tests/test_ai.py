import unittest
import sys
import json
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ai_import as imp
import ai_update as upd
import ai_payload
from common import ROOT, read


class AIImportTests(unittest.TestCase):
    def setUp(self):
        self.seed = {'id': 'HPBAI-001', 'title': 'Verified title', 'kind': 'publication', 'links': [{'url': 'https://example.org/p'}]}
        self.review = {**self.seed, 'checked_at': '2026-09-15T00:00:00+00:00', 'doi': '10.1/p', 'summary': 'Source-grounded summary',
                       'sources': [{'url': 'https://example.org/p'}], 'links': [{'url': 'https://example.org/p', 'status': 'identity checked'}]}
        self.maps = [{'source_batch': 'A', 'legacy_id': 'R01', 'canonical_id': 'HPBAI-001'}]
    def run_import(self, existing=(), clinical=()):
        return imp.upsert([self.seed], {self.seed['id']: self.review}, existing, self.maps, clinical, '2026-09-15', '2026-09-15T00:00:00Z')
    def test_twice_preserves_notes_and_source_namespace(self):
        first, aliases, report = self.run_import(clinical=[{'id': 'R01', 'note': 'clinical'}])
        first[0]['manual_note'] = 'private user note'
        again, _, r = self.run_import(first)
        self.assertEqual(first, again); self.assertEqual(r['added'], []); self.assertEqual(aliases['A:R01'], 'HPBAI-001')
        self.assertNotIn('R01', aliases)
    def test_missing_link_result_rejected(self):
        self.review['links'] = []
        with self.assertRaises(ValueError): self.run_import()
    def test_seed_claims_never_promoted(self):
        self.seed.update(summary_candidate_zh='invented number 999', model_family_claim='LLM')
        rows, _, _ = self.run_import()
        self.assertNotIn('999', json.dumps(rows)); self.assertEqual(rows[0]['model_families'], [])
    def test_strong_conflict_keeps_original(self):
        rows, _, _ = self.run_import(); rows[0]['doi'] = '10.1/conflict'
        after, _, report = self.run_import(rows)
        self.assertEqual(after, rows); self.assertTrue(report['conflicts'])
    def test_shared_repository_and_paper_doi_do_not_merge_dataset(self):
        row = {**self.review, 'kind': 'dataset'}
        self.assertFalse(imp.same_identity(row, self.review))
        self.assertFalse(imp.same_identity({'kind': 'publication', 'url': 'https://github.com/a/b'}, {'kind': 'publication', 'url': 'https://github.com/a/b'}))
    def test_explicit_old_batch_import_upgrades_and_preserves(self):
        old = {**self.review, 'id': 'R01', 'collection_id': imp.COLLECTION, 'source_batch': 'A', 'manual_note': 'keep'}
        rows, _, r = self.run_import([old]); self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'HPBAI-001'); self.assertEqual(rows[0]['manual_note'], 'keep')


class AISourceTests(unittest.TestCase):
    def test_failed_window_retried_after_long_gap(self):
        old = {'version': 'v1', 'last_success': '2026-06-01T00:00:00+00:00', 'pending': {'start': '2026-05-11', 'end': '2026-06-08'}}
        self.assertEqual(upd.window(old, '2026-09-15T00:00:00+00:00', 'v1'), old['pending'])
    def test_config_change_requires_history(self):
        self.assertIsNone(upd.window({'version': 'v0', 'last_success': '2026-09-01T00:00:00Z'}, '2026-09-15', 'v1')['start'])
    def test_queries_do_not_require_llm_and_arxiv_field_syntax(self):
        q = upd.query_for('arXiv', '胰腺外科', {'start': None, 'end': '2026-09-15'})
        self.assertIn('all:pancreaticoduodenectomy', q); self.assertIn('OR all:"deep learning"', q.replace('(all:', '(OR all:'))
        self.assertIn('all:"segment anything"', q)
    def test_partial_arxiv_never_complete(self):
        class Response:
            status_code = 200
            content = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>2</o:totalResults><entry><id>https://arxiv.org/abs/2601.00001v1</id><title>A</title></entry></feed>'
        rows, logs, ok, n = upd.arxiv_search('all:surgical', {'max_pages': 1, 'page_size': 1}, lambda *a: (Response(), {}), lambda *a: None)
        self.assertFalse(ok); self.assertEqual(n, 2); self.assertEqual(len(rows), 1)
        self.assertIn('cap', logs[-1]['error'])
    def test_arxiv_known_id_uses_id_list(self):
        called = []
        def req(u, p): called.append(p); return None, {}
        upd.arxiv_search('id:2601.00001', {'max_pages': 1, 'page_size': 100}, req, lambda *a: None)
        self.assertEqual(called[0]['id_list'], '2601.00001'); self.assertNotIn('search_query', called[0])
    def test_metadata_change_goes_to_review_not_notes(self):
        old = {'id': 'HPBAI-001', 'kind': 'publication', 'doi': '10.1/a', 'title': 'Old', 'manual_note': 'keep'}
        original = copy.deepcopy(old); candidates, pending = {}, {}
        added, revised = upd.merge([{'title': 'Changed', 'doi': '10.1/a'}], candidates, [old], pending, 'Crossref', 'liver', 'now')
        self.assertEqual(old, original); self.assertFalse(added); self.assertEqual(revised, {'HPBAI-001'}); self.assertTrue(pending)
    def test_candidates_deduplicate_sources(self):
        candidates, pending = {}, {}; row = {'title': 'Paper', 'doi': '10.1/a', 'pmid': '123'}
        upd.merge([row], candidates, [], pending, 'PubMed', 'liver', 'a')
        added, _ = upd.merge([row], candidates, [], pending, 'Europe PMC', 'liver', 'b')
        self.assertEqual(len(candidates), 1); self.assertFalse(added)
    def test_binary_body_never_read(self):
        response = Mock(status_code=200, headers={'content-type': 'video/mp4'})
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch.object(upd, 'safe_url'), patch.object(upd.requests, 'get', return_value=response):
            result = upd.bounded_page('https://example.org/demo.mp4')
        response.iter_content.assert_not_called(); self.assertFalse(result['playback_tested']); self.assertIn('未下载', result['status'])
    def test_bridging_doi_pmid_merges_prior_candidates(self):
        candidates, pending = {}, {}
        upd.merge([{'title': 'Paper', 'doi': '10.1/a'}], candidates, [], pending, 'Crossref', 'liver', 'a')
        upd.merge([{'title': 'Paper', 'pmid': '123'}], candidates, [], pending, 'PubMed', 'liver', 'b')
        self.assertEqual(len(candidates), 2)
        upd.merge([{'title': 'Paper', 'doi': '10.1/a', 'pmid': '123'}], candidates, [], pending, 'Europe PMC', 'liver', 'c')
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(next(iter(candidates.values()))['aliases']), 2)


class AIDataTests(unittest.TestCase):
    def test_all_input_rows_resolved_and_all_records_have_review(self):
        if not (ROOT / 'data/ai_records.json').exists(): self.skipTest('no AI data')
        rows = read(ROOT / 'data/ai_records.json'); ids = {r['id'] for r in rows}
        self.assertEqual(len(rows), len(ids)); self.assertTrue(all(r.get('checked_at') and r.get('sources') and r.get('links') for r in rows))
        aliases = read(ROOT / 'data/ai_aliases.json')['mappings']
        self.assertTrue(all(v in ids for v in aliases.values())); self.assertTrue(all(':' in k for k in aliases))
        clinical = read(ROOT / 'data/records.json'); self.assertFalse(ids & {r['id'] for r in clinical})
        self.assertTrue(all(not any(r['video'].values()) for r in rows))
    def test_payload_sections_and_distinct_entities(self):
        ai = ai_payload.build_ai()
        if not ai: self.skipTest('no AI data')
        self.assertEqual(len(ai['taxonomy']['sections']), 8)
        self.assertTrue(all(r['section_ids'] for r in ai['records']))
        self.assertTrue(all(r['kind'] != 'video' for r in ai['records']))
        self.assertTrue(ai['anchor_aliases'])


class AIPipelineRecoveryTests(unittest.TestCase):
    def test_failure_does_not_advance_and_recovery_resumes_saved_window(self):
        from common import write
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / 'config/ai_sources.json', {'query_version': 'test-v1', 'max_pages': 2, 'page_size': 100})
            write(root / 'data/ai_records.json', [])
            with patch.object(upd, 'ROOT', root), patch.object(upd, 'epmc_search', return_value=([], [], True, 0)) as ep, \
                 patch.object(upd, 'pubmed_search', return_value=([], [], True, 0)), \
                 patch.object(upd, 'arxiv_search', return_value=([], [], True, 0)), \
                 patch.object(upd, 'now', return_value='2026-06-01T09:00:00+00:00') as clock:
                first = upd.update(assets=False)
                self.assertEqual(first['status'], 'success')
                original = read(root / 'data/ai_state.json')['source_progress']['Europe PMC:肝脏外科']['last_success']
                clock.return_value = '2026-06-08T09:00:00+00:00'; ep.return_value = ([], [{'status': 503}], False, None)
                second = upd.update(assets=False)
                state = read(root / 'data/ai_state.json'); failed = state['source_progress']['Europe PMC:肝脏外科']
                self.assertEqual(second['status'], 'partial'); self.assertEqual(failed['last_success'], original)
                self.assertEqual(state['last_successful_search'], '2026-06-01T09:00:00+00:00')
                pending = copy.deepcopy(failed['pending'])
                clock.return_value = '2026-09-15T09:00:00+00:00'; ep.return_value = ([], [], True, 0)
                third = upd.update(assets=False)
                resumed = third['queries'][0]
                self.assertEqual(resumed['window'], pending)
                restored = read(root / 'data/ai_state.json')['source_progress']['Europe PMC:肝脏外科']
                self.assertNotIn('pending', restored)
                # A June retry must not pretend the unsearched July–September interval was searched.
                self.assertTrue(restored['last_success'].startswith('2026-06-08'))


if __name__ == '__main__': unittest.main()
