"""Migrate once, then render a cumulative, offline-capable HPB library."""
from common import *
from difflib import SequenceMatcher
from collections import Counter
from urllib.parse import urlparse
import copy

ORGAN={'肝脏':'肝脏外科','胆囊与胆道':'胆囊与胆道外科','胰腺':'胰腺外科','移植扩展':'移植及扩展专题','共同技术与围手术期':'共同技术与围手术期'}
CONFLICTS={'L004','L016','L020','B020','B031','B032','B034','B062','J002','J003','J005','J008','J014','J015','CN006','CN007','CN020'}
def classify(x,tax):
 text=' '.join([x.get('title',''),x.get('topic',''),' '.join(x.get('topic_tags',[]))]);normalized=norm(text)
 ids=[t['id'] for t in tax['topics'] if any(norm(a) in normalized for a in t['aliases'] if len(norm(a))>2)]
 return ids
def rtype(x,group):
 if group=='portals':return '合集入口','portal'
 if group=='pending_videos':return '待核线索','lead'
 if group=='videos':return ('课程模块','course') if x.get('platform')=='webop' or '课程' in x.get('video_type','') else ('视频','video')
 if x['id']=='W001':return '学会专题','society'
 if x['id']=='P013' or re.match(r'Corrigendum|Erratum',x.get('title',''),re.I):return '勘误/出版更正','article'
 t=x.get('type','')
 if '指南' in t or '共识' in t or '标准' in t or '定义' in t:return '指南/共识','article'
 if '技术' in t or '解剖' in t:return '技术/解剖文章','article'
 if '综述' in t or 'Meta' in t:return '综述','article'
 return '原始研究','article'
def body_evidence(x,n,pages,full):
 i=x['id'];f=full.get(i,{});p=pages.get(i+'_full',{}) or pages.get(i,{})
 if f.get('identity_match') and f.get('body_present'):return {'status':'已取得并匹配XML正文；阅读范围见笔记','format':'XML','url':f.get('requested_url'),'sha256':f.get('sha256')}
 if p.get('pdf_pages') and p.get('title_present'):return {'status':'已取得并匹配PDF；阅读范围见笔记','format':'PDF','url':p.get('final_url'),'sha256':p.get('sha256')}
 if n and n.get('read_depth')=='正文重点核对':return {'status':'已核读所列正文或公开正文片段；完整范围见来源','format':'HTML/来源片段','url':n.get('sources',[{}])[0].get('url'),'sha256':p.get('sha256','')}
 return {'status':'未取得已匹配的完整正文；摘要/题录范围见笔记','format':None,'url':x.get('fulltext_url') or x['url']}

def migrate():
 tax=read(ROOT/'config/taxonomy.json');d=read(ROOT/'data/inherited.json');pm=read(WORK/'pubmed.json',{});ep=read(WORK/'epmc.json',{});pages=read(WORK/'page_checks.json',{});full=read(WORK/'fulltext_checks.json',{});cross=read(WORK/'crossref_checks.json',{});play=read(WORK/'video_playback_checks.json',{});notes={}
 for path in sorted(WORK.glob('notes_*.json')):
  piece=read(path,{})
  if notes.keys()&piece.keys():raise ValueError('duplicate reviewed note IDs: '+str(notes.keys()&piece.keys()))
  notes.update(piece)
 additions=read(ROOT/'data/additions.json',[])
 for a in additions:
  d['articles'].append({k:v for k,v in a.items() if k!='note'})
  if a.get('note'):notes[a['id']]=a['note']
 summaries=read(WORK/'video_summaries.json',{});overrides=read(ROOT/'data/curated_overrides.json',{});previous={r['id']:r for r in read(ROOT/'data/records.json',[])}
 records=[];audit=[];stamp=now()
 for group in ['articles','videos','portals','pending_videos']:
  for x in d[group]:
   i=x['id'];original_fields=copy.deepcopy(x);override=overrides.get(i,{});x={**x,**{k:v for k,v in override.get('fields',{}).items() if k in ['doi','authors','authors_full','journal','year','fulltext_url','type','volume','issue','pages']}};n=notes.get(i);p=pages.get(i,{});m=pm.get(str(x.get('pmid','')),{});em=ep.get(str(x.get('pmid','')),{});cr=cross.get(i,{});differences=[];fields={};confirmed=False
   if m:
    fields['title']=SequenceMatcher(None,norm(m['title']),norm(x['title'])).ratio()>.86
    fields['doi']=not x.get('doi') or doi(m.get('doi'))==doi(x.get('doi'));fields['pmid']=m.get('pmid')==str(x.get('pmid'))
    fields['year']=str(x.get('year'))==str(m.get('year')) if m.get('year') else None
    author=(m.get('authors') or [''])[0].split(' ')[0];fields['first_author']=norm(author) in norm(x.get('authors_full') or x.get('authors') or '') if author else None
    fields['journal']=norm(em.get('journalInfo',{}).get('journal',{}).get('title',''))==norm(x.get('journal','')) or norm(m.get('journal',''))==norm(x.get('journal',''))
    confirmed=fields['title'] and fields['doi'] and fields['pmid']
    for k,label in [('title','题名'),('doi','DOI'),('year','年份（可能为在线与刊期差异）'),('first_author','首位作者')]:
     if fields.get(k) is False:differences.append(label+'与当前PubMed字段有差异；保留原记录待逐字段复核')
   elif cr.get('identity_match'):confirmed=True;fields={'title_and_doi':True,'authors_year_journal':'Crossref已返回，逐字段人工确认范围另记'}
   elif p.get('title_present'):fields={'page_title':True,'authors_year_journal':'仅匹配题名，作者/年份/期刊未逐字段确认'}
   if cr.get('status')==200 and cr.get('identity_match') is False:differences.append('Crossref当前题名/DOI与原记录匹配不足，保留冲突');confirmed=False
   if override.get('identity_confirmed') and override.get('evidence'):
    confirmed=True;fields.update(override.get('matched_fields',{}))
   identity={'status':('核心题录身份已确认；其余字段见下' if confirmed else '身份待核对或访问受限'),'confirmed':confirmed,'fields':fields,'differences':differences,'sources':([{'name':'PubMed','url':'https://pubmed.ncbi.nlm.nih.gov/'+str(x.get('pmid'))+'/','checked_at':m.get('checked_at')}] if m else [])+override.get('evidence',[]),'checked_at':override.get('checked_at') or m.get('checked_at') or cr.get('checked_at') or p.get('checked_at')}
   if cr:identity['sources'].append({'name':'Crossref','url':cr.get('requested_url'),'checked_at':cr.get('checked_at'),'identity_match':cr.get('identity_match')})
   if p:identity['sources'].append({'name':'原站','url':p.get('final_url'),'checked_at':p.get('checked_at'),'title_present':p.get('title_present',False)})
   if not confirmed and p.get('title_present'):identity['status']='目标页面题名已匹配；完整题录身份仍待核对'
   page={'status':p.get('page_state','请求失败；未确认目标页面'),'http_status':p.get('status'),'final_url':p.get('final_url',x['url']),'title':p.get('page_title',''),'title_confirmed':bool(p.get('title_present')),'checked_at':p.get('checked_at'),'sha256':p.get('sha256'),'error':p.get('error'),'attempts':p.get('attempts',[])}
   if p.get('pdf_pages') and p.get('title_present'):page['status']='已取得目标题名匹配PDF'
   rt,kind=rtype(x,group);topicids=classify(x,tax);organ=ORGAN.get(x.get('organ'),'共同技术与围手术期');organs=list(dict.fromkeys([organ]+[t['organ'] for t in tax['topics'] if t['id'] in topicids]));depth=n.get('read_depth') if n else '资料受限'
   if kind in ['article','society'] and not n:n={'zh_title':x['title'],'question':'该条内容笔记尚未完成。','design':'资料正在核对，当前仅保留题录。','results':[],'interpretation':'未形成可复核的科学解读。','limitations':['尚未完成来源阅读，不计入已完成笔记。'],'read_depth':'资料受限','sources':[],'completion':'incomplete','reviewed_at':None}
   ft=body_evidence(x,n,pages,full);content={'status':('按所列来源核对笔记；仍需专业复核冲突' if i in CONFLICTS else '已形成有来源的内容笔记') if n and n.get('completion')=='substantive' else '资料受限；不得视为完整阅读','scope':n.get('read_depth') if n else '原站文字与题名；未观看视频'}
   verified=confirmed and not differences and p.get('title_present') and (kind in ['article','society'] and n.get('completion')=='substantive' and i not in CONFLICTS)
   row={'id':i,'domain_id':'hpb-surgery','title':x['title'],'zh_title':n.get('zh_title','') if n else x.get('topic',''),'organ':organ,'organs':organs,'topic':x.get('topic',''),'topic_tags':x.get('topic_tags',[]),'topic_ids':topicids,'resource_type':rt,'original_type':x.get('type',x.get('video_type','')),'kind':kind,'year':x.get('year'),'language':x.get('language') or ('中文' if i.startswith('CN') else '未知'),'source':x.get('platform') or x.get('journal') or x.get('publisher') or '官方来源','journal':x.get('journal'),'authors':x.get('authors_full') or x.get('authors'),'publisher':x.get('publisher'),'doi':x.get('doi',''),'pmid':str(x.get('pmid','')),'pmcid':x.get('pmcid',''),'url':x['url'],'fulltext_url':x.get('fulltext_url',''),'fulltext_status':depth if kind in ['article','society'] else '不适用（视频或目录）','verification_status':'核心身份与所读内容已核对' if verified else ('科学内容待专业复核' if i in CONFLICTS else '部分核对/受限'),'approach':x.get('approach') or ('机器人' if re.search('robot|机器人',x['title'],re.I) else '腹腔镜' if re.search('laparoscop|腹腔镜',x['title'],re.I) else '开放' if re.search(r'\bopen\b|开腹|开放',x['title'],re.I) else '多路径/未明确'),'note':n,'audit':{'identity':identity,'page':page,'fulltext':ft,'content':content},'history_text':x.get('current_check_detail') or x.get('verification_detail') or x.get('prior_verification') or x.get('verification'),'history_date':x.get('current_checked_on') or x.get('verified_on'),'old_links':list(dict.fromkeys(u for u in [x.get('url'),x.get('fulltext_url'),x.get('evidence_url'),x.get('metadata_api_url')] if u)),'first_discovered':x.get('first_discovered') or '2026-09-13','last_checked':p.get('checked_at') or identity['checked_at'],'last_successful_verification':identity['checked_at'] if confirmed else None,'linked_article_ids':x.get('linked_article_ids',[]),'linked_video_ids':x.get('linked_video_ids',[]),'corrections':m.get('corrections',[])+cr.get('updates',[]),'article_relations':[]}
   row['language_detail']=row['language'];row['language']='中文' if '中文' in row['language'] else '英文' if '英文' in row['language'] or '英语' in row['language'] else '未知'
   row['approach_detail']=row['approach'];row['approach']='机器人' if '机器人' in row['approach'] else '腹腔镜' if '腹腔镜' in row['approach'] else row['approach']
   row['read_depth']=depth if n else '不适用'
   if kind in ['article','society']:row['fulltext_status']='已取得并匹配全文' if ft['format'] in ['PDF','XML'] else '已读部分正文/公开索引片段' if depth=='正文重点核对' else '摘要/题录；全文未取得'
   if i=='CN020':row['resource_type']='技术/解剖文章';row['classification_note']='本轮可读摘要无原始研究样本与比较数据，原始类型保留待核'
   if i.startswith('NEW-LT-'):
    row['article_relations'].append({'type':x.get('relationship_type','新版指南关联'),'ids':row['linked_article_ids']});row['linked_article_ids']=[]
   if kind in ['video','course','portal','lead']:
    v=play.get(i,{});linked=next((notes[z] for z in row['linked_article_ids'] if z in notes),None)
    summary=(linked['question']+' '+linked['results'][0]) if linked and linked.get('results') else ('原站文字介绍的主题为“'+x.get('topic',x['title'])+'”。'+('这是分步骤课程模块，可从原站选择章节；本文库未观看全部片段。' if kind=='course' else '属于平台/专题目录入口，需要在原站选择具体资源。' if kind=='portal' else '演示、病例或讲座的具体范围见原始题名；本轮尚未完整观看。'))
    vt=x.get('video_type','未知')
    if i in ['VS016','VS010','VS009','VP005','VP009']:vt='讲座/会议报告'
    if kind=='course':vt='课程模块（分步骤）'
    if kind=='portal':vt='合集/目录入口'
    row['video']={'video_type':vt,'summary':summary,'description_original':p.get('description',''),'date':None,'duration':x.get('duration') or None,'access':x.get('access') or '未知；以原站提示为准','playback_status':v.get('playback_status','尚未实测；不保证当前网络可播放'),'playback_tested_at':v.get('checked_at'),'playback_evidence':v.get('evidence'),'embed_permission':v.get('embed_permission','unknown'),'embed_test':v.get('embed_test','not_tested'),'embed_status':v.get('embed_status','嵌入许可及实际播放未确认，仅提供原站入口'),'embed_url':v.get('embed_url',''),'watched':False}
    # Exact publication metadata, not guessed from HTTP modification times.
    md=p.get('metadata',{});row['video']['date']=md.get('article:published_time') or md.get('citation_publication_date') or None
    if i in summaries:
     row['video'].update({k:summaries[i][k] for k in ['summary','video_type']});row['video']['summary_source']={'url':summaries[i]['source_url'],'scope':summaries[i]['scope']}
   if override:
    row['metadata_corrections']={'changes':{k:{'before':original_fields.get(k),'after':v} for k,v in override.get('fields',{}).items()},'evidence':override.get('evidence',[]),'checked_at':override.get('checked_at')};row['old_links']=list(dict.fromkeys(row['old_links']+[u for u in [original_fields.get('url'),original_fields.get('fulltext_url')] if u]))
   prior=previous.get(i,{})
   row['verification_history']=copy.deepcopy(prior.get('verification_history',[]))
   prior_fulltext=prior.get('audit',{}).get('fulltext',{})
   for audit_key in ['link_checks','last_link_checked_at']:
    if audit_key in prior_fulltext:row['audit']['fulltext'][audit_key]=copy.deepcopy(prior_fulltext[audit_key])
   if prior.get('last_checked','')>row.get('last_checked',''):
    row['audit']['page']=prior['audit']['page'];row['last_checked']=prior['last_checked'];row['verification_history']=prior.get('verification_history',[])
    if prior.get('verification_status')=='题录变更待复核':row['verification_status']=prior['verification_status'];row['audit']['identity']['differences']=list(dict.fromkeys(row['audit']['identity']['differences']+prior['audit']['identity'].get('differences',[])))
   records.append(row);audit.append({'id':i,'kind':kind,'title':x['title'],'identity':row['audit']['identity'],'page':row['audit']['page'],'fulltext':ft,'content':content,'video':row.get('video'),'prior_check':{'date':row['history_date'],'text':row['history_text']},'metadata_corrections':row.get('metadata_corrections')})
 for oldid,newid in [('C022','NEW-LT-001'),('C023','NEW-LT-002')]:
  r=next((r for r in records if r['id']==oldid),None)
  if r and any(z['id']==newid for z in records):r['article_relations'].append({'type':'历史指南；存在新版/分领域更新，见对应新文说明','ids':[newid]});r['verification_status']='历史指南，需结合新版阅读'
 for own,other in [('P012','P013'),('P013','P012')]:
  r=next((r for r in records if r['id']==own),None)
  if r:r['article_relations'].append({'type':'原文—勘误关联；勘误具体内容仍待核对，不算独立研究','ids':[other]})
 # Named trial reports stay separate; only explicit names in the titles form relations.
 for trial in ['LIGRO','OSLO-COMET','ORANGE II PLUS','ORANGE Segments','LEOPARD-2','SECURE','PANTER','TENSION','POINTER','ESCAPE','DIPLOMA','PREOPANC','PANasta']:
  members=[r for r in records if trial.casefold() in r['title'].casefold()]
  if len(members)>1:
   for r in members:r['article_relations'].append({'type':'同名试验的报告；不得按独立试验重复计数','trial':trial,'ids':[z['id'] for z in members if z['id']!=r['id']]})
 write(ROOT/'data/records.json',records);write(ROOT/'data/reports/record-audit.json',{'audited_at':stamp,'records':audit,'unique_count':len(records),'dimensions':{k:dict(Counter(str(r[k].get('status')) for r in audit)) for k in ['identity','page','fulltext','content']}})
 original=read(ROOT/'data/inherited.json');oldids=[x['id'] for k in ['articles','videos','portals','pending_videos'] for x in original[k]]
 write(ROOT/'data/reports/preservation.json',{'original_ids':oldids,'current_ids':[r['id'] for r in records],'original_count':len(oldids),'lost_ids':list(set(oldids)-{r['id'] for r in records}),'added_ids':[r['id'] for r in records if r['id'] not in oldids],'source_snapshot':'data/inherited.json','private_manual_content':'Original snapshot kept locally. No raw user notes or attachments enter publication staging.','mappings_preserved':True,'generated_at':stamp})
 return records

def coverage(records,tax):
 runs=[]
 current=read(ROOT/'config/sources.json')['query_version']
 for p in sorted((ROOT/'data/runs').glob('*.json')):
  run=read(p,{})
  if run.get('query_version')==current and run.get('status')!='invalidated_query_scope':runs.extend(run.get('queries',[]))
 out=[]
 for topic in tax['topics']:
  for rt in ['指南/共识','综述','原始研究','技术/解剖文章','视频']:
   rows=[r for r in records if topic['id'] in r['topic_ids'] and (r['resource_type']==rt or rt=='视频' and r['kind']=='course')];verified=sum(r['verification_status']=='核心身份与所读内容已核对' for r in rows)
   searches=[]
   if rt in ['指南/共识','综述','原始研究']:
    for source in ['Europe PMC','PubMed']:
     matching=[r for r in runs if r.get('topic_id')==topic['id'] and r['source']==source]
     if matching:searches.append({k:matching[-1].get(k) for k in ['source','searched_at','query','complete','hit_count','returned']})
   complete=bool(searches) and all(s['complete'] for s in searches);zero=complete and all(s.get('hit_count')==0 for s in searches)
   state='已有题录/所读内容已核实资源' if verified else '发现但受限或待核对' if rows else '已检索未发现（本检索范围）' if zero else '已检索；本库尚无核实记录' if searches else '尚无本轮该类型系统补检'
   out.append({'organ':topic['organ'],'topic_id':topic['id'],'topic':topic['label'],'type':rt,'verified':verified,'restricted':len(rows)-verified,'state':state,'searches':searches,'gap':('原始研究历史检索以随机试验为重点，非随机原始研究仍有缺口。' if rt=='原始研究' else '综述历史检索以系统综述为重点。' if rt=='综述' else '视频数量表示页面/题录核对，不表示播放成功；目录巡查不构成该专题全覆盖。' if rt=='视频' else '技术/解剖原始研究未完成独立历史检索。' if rt=='技术/解剖文章' else '')+(' 部分查询分页或请求未完成。' if searches and not complete else '')})
 write(ROOT/'data/reports/coverage.json',out);return out

def build(migration=False,output=None):
 records=migrate() if migration or not (ROOT/'data/records.json').exists() else read(ROOT/'data/records.json');tax=read(ROOT/'config/taxonomy.json');stamp=now();cov=coverage(records,tax);state=read(ROOT/'data/state.json',{});dep=read(ROOT/'config/deployment.json',{});receipt=read(ROOT/'data/publication-status.json',{});runtime=read(ROOT/'data/runtime-status.json',{});dep.update({k:receipt[k] for k in ['deployed','schedule_enabled','observed_scheduled_run','verified_at','data_version'] if k in receipt})
 if receipt.get('deployed'):state['last_successful_publish']=receipt.get('verified_at')
 if runtime:state['cloud_runtime']=runtime
 # A reviewed addition replaces its candidate master record only when strong IDs agree.
 raw_candidates=read(ROOT/'data/candidates.json',{});promoted=[]
 for cid,c in list(raw_candidates.items()):
  matches=[r for r in records if (c.get('pmid') and c['pmid']==r.get('pmid')) or (c.get('doi') and doi(c['doi'])==doi(r.get('doi')))]
  if len(matches)==1 and not any(c.get(k) and matches[0].get(k) and (doi(c[k])!=doi(matches[0][k]) if k=='doi' else str(c[k])!=str(matches[0][k])) for k in ['pmid','doi']):
   promoted.append({'candidate_id':cid,'record_id':matches[0]['id'],'at':stamp});del raw_candidates[cid]
 if promoted:
  write(ROOT/'data/candidates.json',raw_candidates);write(ROOT/'data/reports/candidate-promotions.json',read(ROOT/'data/reports/candidate-promotions.json',[])+promoted)
 candidates=list(raw_candidates.values());state.setdefault('last_counts',{})['pending_total']=len(candidates)
 catalog=read(ROOT/'data/video_discovery.json',{});known_urls={r['url'] for r in records};video_candidates=[]
 for source,items in catalog.items():
  for x in items:
   if not x.get('url') or x['url'] in known_urls:continue
   # Scope the general surgical feed to HPB; excluded feed items remain in the catalog audit.
   if source=='SAGES RSS' and not re.search(r'liver|hepat|biliar|chole|pancrea|gallbladder|whipple|ALPPS',x.get('title',''),re.I):continue
   known_urls.add(x['url']);video_candidates.append({'id':'DISC-VIDEO-'+digest(x['url'])[:12],'title':x.get('title') or '官方目录视频入口（题名待核）','kind':'candidate','resource_type':'待核线索','source':source,'url':x['url'],'source_url':read(ROOT/'config/sources.json').get('sages_feed' if source=='SAGES RSS' else 'stanford_directory'),'organ':'待分类','topic_ids':[],'first_discovered':x.get('first_seen'),'last_seen':x.get('last_seen'),'identity':'官方目录返回的链接；具体视频身份待核','content_status':'视频候选；页面、播放、嵌入及内容均未实测。'})
 candidates+=video_candidates;state['last_counts']['pending_total']=len(candidates)
 # Keep all cumulative candidates, but do not ship opaque source hashes with every card.
 source_refs={};candidate_sources={}
 for c in candidates:
  c.pop('source_payload_hash',None);url=c.pop('source_url','')
  if url:
   if url not in source_refs:source_refs[url]='s'+str(len(source_refs));candidate_sources[source_refs[url]]=url
   c['source_ref']=source_refs[url]
 from ai_payload import build_ai
 ai=build_ai()
 stable={'domain_id':'hpb-surgery','schema_version':1,'records':records,'candidates':candidates,'candidate_sources':candidate_sources,'taxonomy':tax,'coverage':cov,'state':state,'deployment':dep,'methods':read(ROOT/'config/sources.json')}
 if ai:stable['ai']=ai
 version='hpb-'+digest(json.dumps(stable,ensure_ascii=False,sort_keys=True))[:16];payload={**stable,'data_version':version,'generated_at':stamp};raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'));envelope={'domain_id':'hpb-surgery','schema_version':1,'data_version':version,'sha256':digest(raw),'payload':raw};write(ROOT/'data/library-envelope.json',envelope)
 src=ROOT/'src';html=(src/'index.html').read_text().replace('/*__CSS__*/',(src/'style.css').read_text()).replace('/*__DATA__*/',raw.replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')).replace('/*__AI_JS__*/',(src/'ai.js').read_text() if (src/'ai.js').exists() else '').replace('/*__JS__*/',(src/'app.js').read_text());dest=Path(output) if output else ROOT/'肝胆胰外科_文献与视频笔记库.html';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(html)
 public=ROOT/'site';public.mkdir(exist_ok=True);(public/'index.html').write_text(html);write(public/'data.json',envelope);(public/'version.json').write_text(json.dumps({'domain_id':'hpb-surgery','data_version':version,'sha256':envelope['sha256'],'generated_at':stamp}));(public/'.nojekyll').write_text('')
 result={'data_version':version,'generated_at':stamp,'records':len(records),'candidates':len(candidates),'counts':dict(Counter(r['kind'] for r in records)),'reading':dict(Counter(r['note']['read_depth'] for r in records if r.get('note'))),'notes_complete':sum(r.get('note',{}).get('completion')=='substantive' for r in records if r.get('note')),'html_bytes':dest.stat().st_size,'cloud_deployed':bool(dep.get('deployed'))};write(ROOT/'data/reports/build.json',result)
 audit=[{'id':r['id'],'kind':r['kind'],'title':r['title'],**r['audit'],'video':r.get('video'),'prior_check':{'date':r.get('history_date'),'text':r.get('history_text')},'verification_history':r.get('verification_history',[]),'metadata_corrections':r.get('metadata_corrections')} for r in records]
 write(ROOT/'data/reports/record-audit.json',{'audited_at':stamp,'records':audit,'unique_count':len(audit),'dimensions':{k:dict(Counter(str(r[k].get('status')) for r in audit)) for k in ['identity','page','fulltext','content']}})
 from report import reports
 reports();print(json.dumps(result,ensure_ascii=False));return result
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--migrate',action='store_true');p.add_argument('--output');a=p.parse_args();build(a.migrate,a.output)
