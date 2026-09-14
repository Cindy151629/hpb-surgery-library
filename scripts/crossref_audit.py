from common import *
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import quote
from difflib import SequenceMatcher
def title_identity(message,record):
 title=' '.join(message.get('title',[]));subtitle=' '.join(message.get('subtitle',[]));full=': '.join(x for x in [title,subtitle] if x);a=norm(re.sub('<[^>]+>','',full));b=norm(record['title']);same=doi(message.get('DOI'))==doi(record['doi']);score=SequenceMatcher(None,a,b).ratio()
 # Publishers often separate subtitles. A missing subtitle is uncertainty, not a proven conflict.
 partial=same and len(a)>20 and b.startswith(a) and not subtitle
 return {'title':title,'subtitle':subtitle,'full_title':full,'title_similarity':score,'identity_match':True if same and score>.8 else None if partial else False,'match_scope':'main title only; subtitle unavailable' if partial and score<=.8 else 'full returned title and DOI'}
def run():
 d=read(ROOT/'data/inherited.json');logs=read(WORK/'crossref_checks.json',{})
 def one(x):
  r,l=fetch('https://api.crossref.org/works/'+quote(doi(x['doi']),safe=''),retries=1)
  if r is not None and r.status_code==200:
   try:
    m=r.json()['message'];l.update(**title_identity(m,x),doi=m.get('DOI'),authors=m.get('author',[]),published=m.get('published'),journal=m.get('container-title'),updates=m.get('update-to',[]),relations=m.get('relation',{}),indexed=m.get('indexed'),publisher=m.get('publisher'))
   except Exception as e:l['parse_error']=str(e)
  return x['id'],l
 with ThreadPoolExecutor(max_workers=3) as pool:
  for f in as_completed([pool.submit(one,x) for x in d['articles'] if x.get('doi') and x['id'] not in logs]):
   k,v=f.result();logs[k]=v;write(WORK/'crossref_checks.json',logs)
   if len(logs)%40==0:print('Crossref checked',len(logs),flush=True)
if __name__=='__main__':run()
