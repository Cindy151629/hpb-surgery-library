"""Readable audit exports calculated from the current cumulative records."""
from common import *
from collections import Counter

def reports():
 records=read(ROOT/'data/records.json');coverage=read(ROOT/'data/reports/coverage.json',[]);state=read(ROOT/'data/state.json',{});built=read(ROOT/'data/reports/build.json',{})
 counts=dict(Counter(r['kind'] for r in records));depth=dict(Counter(r['note']['read_depth'] for r in records if r.get('note')))
 limited=[r['id'] for r in records if (r.get('note') or {}).get('completion')=='source_limited'];attempts=sum(bool(r.get('audit',{}).get('page',{}).get('checked_at')) for r in records)
 summary={'generated_at':now(),'data_version':built.get('data_version'),'counts':counts,'reading':depth,'current_page_attempts':attempts,'core_identity_confirmed':sum(r['audit']['identity'].get('confirmed',False) for r in records),'page_title_matched':sum(r['audit']['page'].get('title_confirmed',False) for r in records),'matched_fulltext_obtained':sum(r.get('fulltext_status')=='已取得并匹配全文' for r in records),'substantive_notes':sum((r.get('note') or {}).get('completion')=='substantive' for r in records),'source_limited':limited,'videos_playback_passed':sum(r.get('video',{}).get('playback_status')=='passed' for r in records),'embeds_passed':sum(r.get('video',{}).get('embed_test')=='passed' for r in records),'last_successful_search':state.get('last_successful_search'),'last_successful_publish':state.get('last_successful_publish')}
 write(ROOT/'data/reports/audit-summary.json',summary)
 def cell(x):return str(x or '未知').replace('|','／').replace('\n',' ')
 lines=['# 肝胆胰资源逐条核验报告','',f'生成时间：{summary["generated_at"]}；数据版本：{summary["data_version"]}。', '',f'共 {len(records)} 条主记录，{attempts} 条保留原始链接请求记录；本次生成不是重新复核，实际日期见各条。核心身份确认 {summary["core_identity_confirmed"]}；目标题名匹配页面 {summary["page_title_matched"]}；已取得并匹配全文 {summary["matched_fulltext_obtained"]}。这些维度不相互替代。', '',f'实质内容笔记 {summary["substantive_notes"]} 条；资料受限：{", ".join(limited)}。摘要导读不是全文精读。原站链接均保留；视频播放或嵌入未通过实测的，不提供站内播放按钮。', '', '| ID | 类型 | 原始题名 | 核心题录 | 页面 | 阅读范围 | 核验结论 | 最近实际检查 |','|---|---|---|---|---|---|---|---|']
 for r in records:lines.append('| '+' | '.join(cell(x) for x in [r['id'],r['resource_type'],r['title'],r['audit']['identity']['status'],r['audit']['page']['status'],(r.get('note') or {}).get('read_depth') if r.get('note') else '原站文字；未完整观看',r['verification_status'],r['last_checked']])+' |')
 lines+=['','完整字段、证据链接、历史记录及题录修订见 record-audit.json 和网页中每条记录的“查看逐条核验记录”。']
 (ROOT/'data/reports/逐条核验报告.md').write_text('\n'.join(lines))
 lines=['# 专题覆盖与缺口报告','',f'生成时间：{now()}。覆盖矩阵由 {len(coverage)} 个专题×类型单元格计算。','', '首次历史检索偏重指南、共识、随机试验和系统综述；增量检索按入库/修改日期，保留21天重叠；每月回看90天并轮转一个历史薄弱专题。技术/解剖非随机研究和中文官方目录尚有系统检索缺口。Embase、Web of Science、CNKI付费检索本轮未完成授权检索。', '', 'Europe PMC、PubMed 的各专题查询及SAGES、Stanford目录分别保存成功进度；请求错误、无效JSON或分页未完成不会当作零命中。第一版检索式范围过宽，已作废；第二版限定题名/摘要，已完成历史及增量查询；随后通过已知重要资源反查，在第三版补充指南/定义表述和手术别名。每次检索的实际查询及分页日志均保留，覆盖表采用当前检索版本。', '', '| 器官 | 专题 | 类型 | 已核/受限 | 状态 | 缺口 |','|---|---|---|---|---|---|']
 for c in coverage:lines.append('| '+' | '.join(cell(x) for x in [c['organ'],c['topic'],c['type'],str(c['verified'])+'/'+str(c['restricted']),c['state'],c['gap']])+' |')
 lines+=['','实际检索式、时间、来源、命中量和分页记录见 coverage.json、data/runs/ 和网页“检索与覆盖”。数量只描述本库范围，不代表学科全面性。']
 (ROOT/'data/reports/专题覆盖与缺口报告.md').write_text('\n'.join(lines));print(json.dumps(summary,ensure_ascii=False));return summary
if __name__=='__main__':reports()
