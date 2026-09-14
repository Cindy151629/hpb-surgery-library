import sys,json,tempfile,unittest,copy
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import ROOT,digest,read,safe_url
from discovery import merge_candidate,epmc_search,pubmed_search,since
from query_config import compile_query
from deploy_check import verify
class Response:
 def __init__(self,data,status=200,headers=None,text=''):self.data=data;self.status_code=status;self.headers=headers or {};self.text=text
 def json(self):return self.data
def record(pid='1',doi='10.1/one'):
 return {'id':'DISC-MED-'+pid,'pmid':pid,'doi':doi,'title':'A clinical study','topic_ids':['topic'],'first_discovered':'2020-01-01','year':2020}
class PipelineTests(unittest.TestCase):
 def test_primary_id_and_manual_preservation(self):
  records=read(ROOT/'data/records.json');pres=read(ROOT/'data/reports/preservation.json');self.assertFalse(pres['lost_ids']);self.assertEqual(len({x['id'] for x in records}),len(records));self.assertTrue(set(pres['original_ids'])<={x['id'] for x in records})
  self.assertEqual(len(pres['original_ids']),365)
 def test_every_article_has_real_note_and_sources(self):
  for r in read(ROOT/'data/records.json'):
   if r['kind'] not in ['article','society']:continue
   with self.subTest(id=r['id']):
    n=r['note'];self.assertIn(n['completion'],['substantive','source_limited']);self.assertTrue(n['sources']);self.assertTrue(n['results']);self.assertTrue(n['limitations']);self.assertGreater(len(n['question']+n['design']+''.join(n['results'])),100);self.assertNotIn('尚未填入全文精读结论',json.dumps(n,ensure_ascii=False))
 def test_all_audits_have_current_attempt(self):
  a=read(ROOT/'data/reports/record-audit.json');self.assertEqual(a['unique_count'],len(a['records']))
  for r in a['records']:
   with self.subTest(id=r['id']):self.assertTrue(r['page']['checked_at']);self.assertIn('status',r['identity']);self.assertIn('status',r['fulltext']);self.assertIn('status',r['content'])
 def test_idempotent_merge_and_conflicts(self):
  d={};x=record();self.assertEqual(merge_candidate(d,x,{} )[0],'added');self.assertEqual(merge_candidate(d,copy.deepcopy(x),{})[0],'duplicate');self.assertEqual(len(d),1)
  before=copy.deepcopy(d);bad=record('2');self.assertEqual(merge_candidate(d,bad,{})[0],'conflict');self.assertEqual(d,before)
  bad=record('1','10.1/different');self.assertEqual(merge_candidate({},bad,{'OLD':x})[0],'conflict')
 def test_same_trial_different_publications_not_collapsed(self):
  d={};merge_candidate(d,record('1','10.1/first'),{});merge_candidate(d,record('2','10.1/followup'),{});self.assertEqual(len(d),2)
 def test_separate_article_video_relationships(self):
  rs={r['id']:r for r in read(ROOT/'data/records.json')}
  for r in rs.values():
   for v in r['linked_video_ids']:self.assertIn(v,rs);self.assertIn(rs[v]['kind'],['video','course'])
  self.assertTrue(rs['J017']['linked_video_ids'])
 def test_failed_source_watermark_and_overlap(self):
  p={'s':{'last_success':'2026-07-01T00:00:00+00:00','query_version':'v'}};self.assertEqual(since(p,'s','v',at=datetime(2026,9,1,tzinfo=timezone.utc)),'2026-06-10');self.assertIsNone(since(p,'s','changed'));self.assertEqual(since(p,'s','v',True),'2026-04-02')
 def test_cursor_resume_cumulative_no_loss(self):
  config={'max_pages':1,'page_size':1,'epmc_endpoint':'https://example.org/api'};calls=[]
  def req(url,params):
   calls.append(params['cursorMark']);c=params['cursorMark'];return Response({'hitCount':2,'resultList':{'result':[{'source':'MED','id':'1' if c=='*' else '2'}]},'nextCursorMark':'second' if c=='*' else 'end'}),{'status':200}
  with tempfile.TemporaryDirectory() as td:
   rows,logs,ok,total=epmc_search('a query',config,req,td);self.assertFalse(ok);self.assertEqual(len(rows),1)
   rows,logs,ok,total=epmc_search('a query',config,req,td);self.assertTrue(ok);self.assertEqual({x['id'] for x in rows},{'1','2'});self.assertEqual(calls,['*','second'])
 def test_http_failure_and_bad_json_not_zero_success(self):
  c={'max_pages':2,'page_size':10,'epmc_endpoint':'https://example.org'}
  for response in [Response({},403),Response({},429),Response({'not':'search'}),None]:
   with self.subTest(response=response):
    rows,logs,ok,total=epmc_search('test',c,lambda *x:(response,{'status':getattr(response,'status_code',None)}));self.assertFalse(ok);self.assertIsNone(total)
 def test_repeated_page_not_complete(self):
  c={'max_pages':2,'page_size':1,'epmc_endpoint':'https://example.org'}
  rows,logs,ok,total=epmc_search('test',c,lambda *x:(Response({'hitCount':2,'resultList':{'result':[{'id':'1','source':'MED'}]},'nextCursorMark':'*'}),{'status':200}));self.assertFalse(ok)
 def test_pubmed_guideline_book_not_lost(self):
  from harvest import parse_pubmed
  raw='<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>41021712</PMID><ArticleIdList><ArticleId IdType="bookaccession">NBK618150</ArticleId></ArticleIdList><Book><BookTitle>Gallstone disease: diagnosis and management</BookTitle><PubDate><Year>2014</Year></PubDate><Publisher><PublisherName>NICE</PublisherName></Publisher></Book></BookDocument></PubmedBookArticle></PubmedArticleSet>'
  m=parse_pubmed(raw);self.assertEqual(m['41021712']['book_accession'],'NBK618150');self.assertEqual(m['41021712']['authors'],['NICE'])
 def test_phrase_scope_matches_both_sources(self):
  groups=[['pancreas','pancreatic'],['distal pancreatectomy','RAMPS']]
  a=compile_query(groups,'Europe PMC');b=compile_query(groups,'PubMed');self.assertIn('TITLE_ABS:"distal pancreatectomy"',a);self.assertIn('"distal pancreatectomy"[Title/Abstract]',b);self.assertIn(' AND ',a)
 def test_crossref_separate_subtitle_and_missing_subtitle(self):
  from crossref_audit import title_identity
  old={'title':'The Southampton Consensus Guidelines for Laparoscopic Liver Surgery: From Indication to Implementation.','doi':'10.1097/sla.0000000000002524'}
  m={'title':['The Southampton Consensus Guidelines for Laparoscopic Liver Surgery'],'subtitle':['From Indication to Implementation'],'DOI':old['doi']}
  self.assertIs(title_identity(m,old)['identity_match'],True)
  m['subtitle']=[];self.assertIsNone(title_identity(m,old)['identity_match'])
  m['DOI']='10.1/wrong';self.assertIs(title_identity(m,old)['identity_match'],False)
 def test_publication_failure_keeps_receipt_absent(self):
  with self.assertRaises(ValueError):verify('https://example.org/','new',lambda *a,**k:Response({},200,{'Access-Control-Allow-Origin':'*'}))
  with self.assertRaises(ValueError):verify('https://example.org/','new',lambda *a,**k:Response({},200,{}))
 def test_real_snapshot_integrity(self):
  e=read(ROOT/'data/library-envelope.json');self.assertEqual(digest(e['payload']),e['sha256']);p=json.loads(e['payload']);self.assertEqual(p['data_version'],e['data_version']);self.assertEqual(p['domain_id'],'hpb-surgery')
 def test_no_private_or_invalid_fetch_destinations(self):
  for u in ['file:///etc/passwd','javascript:alert(1)','http://127.0.0.1','http://[::1]','http://user:pass@example.com']:
   with self.subTest(u=u),self.assertRaises(ValueError):safe_url(u)
 def test_unknown_playback_not_promoted(self):
  for r in read(ROOT/'data/records.json'):
   if not r.get('video'):continue
   v=r['video']
   if v.get('embed_test')=='passed':self.assertEqual(v.get('embed_permission'),'confirmed');self.assertTrue(v.get('playback_evidence'))
   self.assertFalse(v['watched'])
 def test_public_allowlist_excludes_private_material(self):
  from stage_public import FILES,GLOBS
  self.assertNotIn('data/inherited.json',FILES);self.assertFalse(any('manual' in p for p in FILES+GLOBS));self.assertFalse(any('work/' in p for p in FILES+GLOBS))
if __name__=='__main__':unittest.main()
