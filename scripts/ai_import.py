"""Idempotent, namespaced import; unreviewed seed claims never become display facts."""
from common import ROOT, read, write, now, digest, doi, norm
from pathlib import Path
import copy
import json

COLLECTION = 'hpb-ai-video'
KINDS = {'publication', 'dataset', 'audit_tool', 'model_card', 'project', 'demo_entry', 'pending_lead'}
DISPLAY = ('title name aliases kind year publication_status publication_date doi arxiv pmid authors '
           'organs procedures approaches tasks model_families relevance topic_ids summary limitations '
           'content_fields identity_status fulltext_status code_status data_status weights_status '
           'checked_at sources links relations changes').split()


def identifiers(record):
    return {'doi': doi(record.get('doi')), 'pmid': str(record.get('pmid') or ''),
            'arxiv': str(record.get('arxiv') or '').split('v')[0]}


def conflicting(left, right):
    a, b = identifiers(left), identifiers(right)
    return any(a[k] and b[k] and a[k] != b[k] for k in a)


def same_identity(left, right):
    if left.get('kind') != right.get('kind') or conflicting(left, right):
        return False
    a, b = identifiers(left), identifiers(right)
    # URL and repository sharing are deliberately not merge keys.
    return any(a[k] and a[k] == b[k] for k in a)


def adapt(seed, review, prepared_on):
    if not review or review.get('id') != seed['id'] or not review.get('checked_at'):
        raise ValueError('Every imported resource needs an actual per-record review: ' + seed['id'])
    result = {k: copy.deepcopy(review[k]) for k in DISPLAY if k in review}
    result.update(id=seed['id'], collection_id=COLLECTION, prepared_on=prepared_on)
    for k in ['aliases', 'organs', 'procedures', 'approaches', 'tasks', 'model_families',
              'topic_ids', 'limitations', 'sources', 'links', 'relations', 'changes']:
        if not isinstance(result.get(k, []), list):
            raise ValueError(k + ' must be an array: ' + seed['id'])
        result.setdefault(k, [])
    result.setdefault('title', seed['title'])
    result.setdefault('name', seed.get('name', ''))
    result.setdefault('kind', seed.get('kind', 'pending_lead'))
    if result['kind'] not in KINDS:
        raise ValueError('unsupported AI entity kind')
    result.setdefault('summary', '本次未取得足够内容依据；请查看原站与核验记录。')
    result.setdefault('content_fields', {})
    result.setdefault('identity_status', '待核对')
    result.setdefault('relevance', '待核对')
    for k in ['fulltext_status', 'code_status', 'data_status', 'weights_status']:
        result.setdefault(k, '未确认')
    # Legacy validation is separate from current evidence and cannot supply summary/model claims.
    result['legacy_ids'] = copy.deepcopy(seed.get('legacy_ids', []))
    result['verification_history'] = copy.deepcopy(seed.get('verification_history', []))
    result['input_provenance'] = {'seed_id': seed['id'], 'seed_sha256': digest(json.dumps(seed, ensure_ascii=False, sort_keys=True)),
                                'unreviewed_claims_location': 'local original patch package; not promoted to display fields'}
    existing_links = {x['url']: x for x in result['links']}
    for item in seed.get('links', []):
        if item['url'] not in existing_links:
            raise ValueError('Seed link has no processing result: ' + seed['id'] + ' ' + item['url'])
    if not result['sources'] and result['summary'] != '本次未取得足够内容依据；请查看原站与核验记录。':
        raise ValueError('A displayed scientific summary needs a source: ' + seed['id'])
    result['video'] = {'playback_tested': False, 'fully_watched': False,
                       'embedding_permission_confirmed': False, 'embedding_tested': False}
    return result


def upsert(seeds, reviews, existing, mappings, clinical=(), prepared_on=None, stamp=None):
    stamp = stamp or now()
    current = {r['id']: copy.deepcopy(r) for r in existing}
    aliases = {f"{m['source_batch']}:{m['legacy_id']}": m['canonical_id'] for m in mappings}
    report = {'attempted_at': stamp, 'added': [], 'updated': [], 'unchanged': [], 'conflicts': [], 'source_mappings': mappings}
    old_ids = {r['id'] for r in clinical}
    for seed in seeds:
        rid = seed['id']; new = adapt(seed, reviews.get(rid), prepared_on)
        if rid in old_ids:
            report['conflicts'].append({'id': rid, 'reason': 'ID already belongs to the clinical collection'})
            continue
        previous = current.get(rid)
        if previous is None:
            # Upgrade a previously imported A/B row only if its namespace and source ID are explicit.
            legacy = [r for r in current.values() if r.get('collection_id') == COLLECTION and
                      aliases.get(str(r.get('source_batch', '')) + ':' + r['id']) == rid]
            exact = [r for r in current.values() if same_identity(r, new)]
            hits = {r['id']: r for r in legacy + exact}
            if len(hits) > 1:
                report['conflicts'].append({'id': rid, 'reason': 'multiple existing identities', 'matches': list(hits)})
                continue
            if hits:
                previous = next(iter(hits.values()))
        if previous and (conflicting(previous, new) or previous.get('kind', new['kind']) != new['kind']):
            report['conflicts'].append({'id': rid, 'reason': 'strong identifier/entity conflict', 'existing_id': previous['id']})
            continue
        if previous:
            # Unknown/manual fields, local curator notes and classification overrides survive.
            merged = {**previous, **new, 'imported_at': previous.get('imported_at', stamp)}
            merged['verification_history'] = list(previous.get('verification_history', []))
            for item in new['verification_history']:
                if item not in merged['verification_history']: merged['verification_history'].append(item)
            if previous['id'] != rid:
                merged.setdefault('merged_legacy_records', []).append(copy.deepcopy(previous))
                del current[previous['id']]
            before = {k: previous.get(k) for k in DISPLAY}
            after = {k: new.get(k) for k in DISPLAY}
            if before == after and previous['id'] == rid:
                report['unchanged'].append(rid)
            else:
                merged.setdefault('revision_history', []).append({'at': stamp, 'previous_review': before})
                report['updated'].append(rid)
            current[rid] = merged
        else:
            new['imported_at'] = stamp; current[rid] = new; report['added'].append(rid)
    report['canonical_count'] = len(current)
    report['source_rows'] = len(mappings)
    report['all_source_rows_resolved'] = all(m['canonical_id'] in current for m in mappings)
    return sorted(current.values(), key=lambda x: x['id']), aliases, report


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--package', type=Path, required=True)
    p.add_argument('--reviews', nargs='+', type=Path, required=True); a = p.parse_args()
    seed = read(a.package / 'hpb_ai_resources.seed.json')
    reviews = {r['id']: r for path in a.reviews for r in read(path)}
    leads = [{**r, 'kind': 'pending_lead', 'links': [{'url': r['url'], 'role': 'publication'}]}
             for r in seed.get('pending_leads', [])]
    records, aliases, report = upsert(seed['records'] + leads, reviews,
        read(ROOT / 'data/ai_records.json', []), read(a.package / 'id_aliases.json')['mappings'],
        read(ROOT / 'data/records.json', []), seed.get('prepared_on'))
    write(ROOT / 'data/ai_records.json', records)
    write(ROOT / 'data/ai_aliases.json', {'collection_id': COLLECTION, 'mappings': aliases,
        'anchor_aliases': {'AI-' + k.replace(':', '-'): v for k, v in aliases.items()}})
    reports = read(ROOT / 'data/reports/ai-import.json', {'runs': []})
    reports['runs'].append(report); write(ROOT / 'data/reports/ai-import.json', reports)
    write(ROOT / 'data/ai_pending_changes.json', read(ROOT / 'data/ai_pending_changes.json', {}))
    write(ROOT / 'data/ai_candidates.json', read(ROOT / 'data/ai_candidates.json', {}))
    print(json.dumps({k: v for k, v in report.items() if k != 'source_mappings'}, ensure_ascii=False))


if __name__ == '__main__': main()
