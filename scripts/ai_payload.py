"""Public, cumulative AI extension; clinical records and private notes stay intact."""
from common import ROOT, read, write, norm
from collections import Counter
import copy
import re


def build_ai():
    if not (ROOT / 'data/ai_records.json').exists(): return None
    tax = read(ROOT / 'config/ai_taxonomy.json'); records = copy.deepcopy(read(ROOT / 'data/ai_records.json'))
    clinical_tax = read(ROOT / 'config/taxonomy.json')
    for r in records:
        r['section_ids'] = [s['id'] for s in tax['sections'] if set(s['topics']) & set(r['topic_ids'])]
        if r['kind'] in ('project', 'demo_entry') or r['relevance'] == '可迁移方法': r['section_ids'].append('transfer')
        if r.get('changes') or r.get('revision_history') or r['kind'] == 'pending_lead': r['section_ids'].append('updates')
        r['section_ids'] = list(dict.fromkeys(r['section_ids']))
        text = norm(' '.join(r['procedures']))
        r['clinical_topic_ids'] = [t['id'] for t in clinical_tax['topics'] if t['organ'] in r['organs'] and
                                   any(norm(a) in text for a in t['aliases'] if len(norm(a)) > 3)]
        r['aliases'] = list(dict.fromkeys(r['aliases'] + [x['source_batch'] + ':' + x['id'] for x in r.get('legacy_ids', [])]))
    candidates = []; resolved_aliases = {}
    from ai_update import keys
    raw_candidates = read(ROOT / 'data/ai_candidates.json', {})
    changed = False
    for row in raw_candidates.values():
        identity = keys(row)
        hits = [r for r in records if r['kind'] == 'publication' and any(identity[k] and identity[k] == keys(r)[k] for k in identity)
                and not any(identity[k] and keys(r)[k] and identity[k] != keys(r)[k] for k in identity)]
        if len(hits) == 1:
            if row.get('resolved_to') != hits[0]['id']: row['resolved_to'] = hits[0]['id']; changed = True
            resolved_aliases[row['id']] = hits[0]['id']
            continue
        r = copy.deepcopy(row)
        for key in ['organs', 'procedures', 'approaches', 'tasks', 'model_families', 'section_ids', 'limitations', 'relations']: r.setdefault(key, [])
        r['links'] = [{'url': r['url'], 'role': 'publication', 'status': '数据库返回入口，页面与正文待核对'}] if r.get('url') else []
        r['sources'] = [{'scope': s, 'finding': '固定检索程序发现；不构成内容核验'} for s in r.get('sources', [])]
        candidates.append(r)
    if changed: write(ROOT / 'data/ai_candidates.json', raw_candidates)
    runs = []
    version = read(ROOT / 'config/ai_sources.json')['query_version']
    for path in sorted((ROOT / 'data/runs').glob('ai-*.json')):
        run = read(path, {})
        if run.get('query_version') == version: runs.extend(run.get('queries', []))
    latest = {}
    for q in runs: latest[(q['organ'], q['source'])] = q
    tasks = {'空间像素分割': r'分割|segmentation', '目标跟踪': r'跟踪|tracking', '时间阶段/步骤识别': r'阶段|步骤|流程|phase|step|workflow',
             '动作三元组': r'三元组|triplet', '视频问答/语言理解': r'问答|语言理解|VQA|question|视频描述|caption'}
    models = {'视觉/视频基础模型': r'SAM|基础模型|foundation', '视觉语言预训练': r'视觉.?语言|vision.?language|CLIP',
              '多模态大语言模型': r'大语言|MLLM|LLM|Qwen|LLaVA', '常规深度学习/其他': r'常规|CNN|卷积|HRNet|Transformer|Mamba|深度学习|非基础'}
    cells = []
    for organ in tax['organs']:
        queries = [q for (o, s), q in latest.items() if o == organ]
        for task, tp in tasks.items():
            for model, mp in models.items():
                found = [r for r in records if organ in r['organs'] and re.search(tp, ' '.join(r['tasks']), re.I)
                         and re.search(mp, ' '.join(r['model_families']), re.I) and r['kind'] != 'pending_lead']
                state = '已有身份核实资源；证据范围见各条' if found else '访问/检索受限，覆盖未完成' if any(not q['complete'] for q in queries) else '已检索；尚无纳入且核实资源' if queries else '尚未检索'
                if queries and all(q['complete'] and q.get('hit_count') == 0 for q in queries) and not found: state = '已检索未发现（所列组合检索式）'
                cells.append({'organ': organ, 'task': task, 'model': model, 'count': len(found), 'ids': [r['id'] for r in found],
                    'status': state, 'gap': '查询为器官×任务组×模型组的宽检索，不能证明此交叉格穷尽。常规模型可含基础模型先验；类别可交叉。数据集/演示不是独立临床研究。会议目录全站历史检索仍需人工补查。',
                    'queries': [{k: q.get(k) for k in ('source', 'checked_at', 'complete', 'returned', 'query')} for q in queries]})
    aliases = read(ROOT / 'data/ai_aliases.json', {})
    write(ROOT / 'data/reports/ai-coverage.json', cells)
    audit = {'records': [{k: r.get(k) for k in ('id', 'kind', 'title', 'checked_at', 'identity_status', 'fulltext_status', 'code_status', 'data_status', 'weights_status', 'sources', 'links', 'changes', 'relations', 'video')} for r in records],
             'counts': dict(Counter(r['kind'] for r in records)), 'links_processed': sum(len(r['links']) for r in records),
             'not_all_fully_read': True, 'playback_tests': 0, 'embedding_tests': 0}
    write(ROOT / 'data/reports/ai-verification.json', audit)
    return {'collection_id': 'hpb-ai-video', 'records': records, 'candidates': candidates, 'taxonomy': tax,
            'anchor_aliases': {**aliases.get('anchor_aliases', {}), **resolved_aliases}, 'state': read(ROOT / 'data/ai_state.json', {}),
            'pending_changes': read(ROOT / 'data/ai_pending_changes.json', {}), 'asset_checks': read(ROOT / 'data/ai_asset_checks.json', {}),
            'coverage': cells, 'methods': read(ROOT / 'config/ai_sources.json')}
