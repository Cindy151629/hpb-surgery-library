"""All-record bibliographic retrieval, with private raw evidence and resumable logs."""
from common import *
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from copy import deepcopy
from io import BytesIO

PAGE_PARSER_VERSION = 'main-title-v3'


def _title_matches(expected, actual, source):
    """Only punctuation/spacing differences and explicit browser-title suffixes."""
    wanted = norm(expected or '')
    if not wanted or not actual:
        return False
    if wanted == norm(actual):
        return True
    # A journal/site suffix is common in <title>, but an arbitrary substring (or
    # a high fuzzy score for a different edition/follow-up) is not identity.
    if source in ('title', 'og:title'):
        for suffix in (' from the SAGES Video Library', ' - A SAGES Publication'):
            if actual.rstrip().casefold().endswith(suffix.casefold()):
                return wanted == norm(actual.rstrip()[:-len(suffix)])
        parts = re.split(r'\s+[|–—-]\s+|\s*\|\s*', actual)
        # Remove a known site label from the END, never accept an arbitrary
        # suffix such as "five-year follow-up" as publisher branding.
        if len(parts) > 1 and re.fullmatch(
                r'PubMed|(?:Europe )?PMC|ScienceDirect(?:\.com)?|Wiley Online Library|'
                r'Springer(?:Link| Nature Link)?|Oxford Academic|OUP Academic|'
                r'Annals of Surgery|JAMA Network|AASLD|SAGES|IEG', parts[-1], re.I):
            return wanted == norm(' '.join(parts[:-1]))
    return False


def _excluded_heading(node):
    for ancestor in [node, *node.parents]:
        if getattr(ancestor, 'name', '') in ('nav', 'aside', 'footer'):
            return True
        attrs = getattr(ancestor, 'attrs', {}) or {}
        markers = ' '.join([str(attrs.get('id', '')),
                            ' '.join(attrs.get('class', [])),
                            str(attrs.get('role', ''))])
        if re.search(r'relat|recommend|reference|bibliograph|cited.by|citation.list|'
                     r'sidebar|navigation|search.result', markers, re.I):
            return True
        if attrs.get('hidden') is not None or attrs.get('aria-hidden') == 'true':
            return True
    return False


def _html_titles(soup, metas):
    """Select main-record titles; never pool every h2 into a candidate."""
    citations = list(dict.fromkeys(x.get('content', '').strip() for x in
                     soup.select('head meta[content]')
                     if (x.get('name') or x.get('property') or '').lower() == 'citation_title'
                     and x.get('content', '').strip()))
    headings = [x for x in soup.find_all('h1') if not _excluded_heading(x)]
    scoped = [x for x in headings if x.find_parent(['main', 'article'])]
    headings = scoped or headings
    primary = [x.get_text(' ', strip=True) for x in headings
               if x.get_text(' ', strip=True)]
    # citation_title and the visible primary title must not contradict one
    # another. Multiple different h1s usually indicate an article listing.
    if citations or primary:
        return ([('citation_title', x) for x in citations] +
                [('h1', x) for x in primary])
    if metas.get('og:title'):
        return [('og:title', metas['og:title'])]
    if soup.title and soup.title.get_text(' ', strip=True):
        return [('title', soup.title.get_text(' ', strip=True))]
    # h2 is a conservative fallback only when it is the first heading of the
    # single main/article container, not a link or a recommendation section.
    scopes = soup.find_all('main') or soup.find_all('article')
    if len(scopes) == 1:
        head = scopes[0].find(['h1', 'h2'])
        if (head is not None and head.name == 'h2' and
                not _excluded_heading(head) and not head.find('a') and
                not re.search(r'^(related|recommended|references|recommended articles|'
                              r'related articles|abstract|introduction)$',
                              head.get_text(' ', strip=True), re.I)):
            return [('h2', head.get_text(' ', strip=True))]
    return []


def _xml_main_record(root):
    """Return one primary record, its title and its own identifiers only."""
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = node.tag.rsplit('}', 1)[-1]
    if root.tag in ('pmc-articleset', 'article-set'):
        articles = root.findall('article')
        if len(articles) != 1:
            return None, '', []
        root = articles[0]
    if root.tag == 'article':
        meta = root.find('front/article-meta')
        group = root.find('front/article-meta/title-group')
        title = txt(group.find('article-title')) if group is not None else ''
        if title:
            subtitles = [txt(node) for node in group.findall('subtitle') if txt(node)]
            if subtitles:
                title = ': '.join([title.rstrip(':： '), *subtitles])
        title = title or txt(root.find('front/article-title'))
        ids = [txt(x) for x in meta.findall('article-id')] if meta is not None else []
        return root, title, ids
    if root.tag == 'PubmedArticleSet':
        articles = root.findall('PubmedArticle')
        if len(articles) != 1:
            return None, '', []
        root = articles[0]
    if root.tag == 'PubmedArticle':
        title = txt(root.find('MedlineCitation/Article/ArticleTitle'))
        ids = [txt(x) for x in root.findall('PubmedData/ArticleIdList/ArticleId')]
        return root, title, ids
    return None, '', []


def _pdf_title(reader):
    """Read the largest-type title block in the upper part of page one."""
    page = reader.pages[0]
    bottom, top = float(page.cropbox.bottom), float(page.cropbox.top)
    height = top - bottom
    fragments = []
    if page.rotation:
        return '', {'reason': 'rotated first page requires manual title review'}

    def visit(text, cm, tm, font_dict, font_size):
        text = text.strip()
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        size = abs(font_size) * (cm[0] ** 2 + cm[1] ** 2) ** .5
        if text and size > 0 and bottom + .55 * height <= y <= bottom + .95 * height:
            fragments.append((y, x, size, text))

    page.extract_text(visitor_text=visit)
    region = {'page': 1, 'bottom_fraction': .55, 'top_fraction': .95}
    if not fragments:
        return '', region
    largest = max(row[2] for row in fragments)
    lines = sorted((row for row in fragments if row[2] >= largest * .95),
                   key=lambda row: (-row[0], row[1]))
    block = [lines[0]]
    for row in lines[1:]:
        if block[-1][0] - row[0] > largest * 2.2:
            break
        block.append(row)
    region['font_size'] = largest
    return ' '.join(row[3] for row in block), region


def parse_page_content(content: bytes, title: str, log: dict, *,
                       extract_body: bool = True) -> tuple[dict, str]:
    """Reclassify cached response bytes without network or filesystem writes.

    Returns (fresh_log, extracted_text). Pass the original fetch log, including
    status/content_type/requested_url/final_url. Request provenance (including
    checked_at and sha256) is retained; old parsing conclusions are recomputed.
    With extract_body=False, text contains only the HTML challenge prefix or
    XML/PDF main title, and PDF text extraction touches only its first page.
    This confirms a page title only, not authors/DOI or a human reading depth.
    """
    result = deepcopy(log)
    for key in ('parse_error', 'pdf_pages', 'xml_title', 'body_present', 'body_chars',
                'ids', 'pdf_title_region'):
        result.pop(key, None)
    result.update(page_parser_version=PAGE_PARSER_VERSION, page_title='',
                  title_similarity=0, title_present=False, title_match_source='',
                  title_candidates=[], title_conflict=False, metadata={}, text_chars=0,
                  description='', video_tags=0, iframes=[], access_signals=[], content_format='',
                  text_extraction_scope='full' if extract_body else 'identity-only')
    if result.get('status') != 200:
        result['page_state'] = '受限或请求失败'
        return result, ''
    text = ''
    candidates = []
    challenge = False
    content_type = str(result.get('content_type') or '').lower()
    prefix = content.lstrip(b'\xef\xbb\xbf \r\n\t')
    is_pdf = prefix.startswith(b'%PDF') or 'pdf' in content_type
    is_xml = ('html' not in content_type and ('xml' in content_type or
              prefix.startswith(b'<?xml') or
              re.match(br'<(?:article|pmc-articleset|PubmedArticleSet)(?:\s|>)', prefix)))
    result['content_format'] = 'PDF' if is_pdf else 'XML' if is_xml else 'HTML'
    try:
        if is_pdf:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            primary, region = _pdf_title(reader)
            text = ('\n'.join(page.extract_text() or '' for page in reader.pages)
                    if extract_body else primary)
            candidates = [('pdf:first-page-title-region', primary)] if primary else []
            result.update(pdf_pages=len(reader.pages), pdf_title_region=region,
                          content_format='PDF', page_title=primary)
        elif is_xml:
            root = ET.fromstring(content)
            record, primary, ids = _xml_main_record(root)
            body = record.find('body') if record is not None else None
            text = ('\n'.join(txt(node) for node in root.iter()
                              if node.tag in ('article-title', 'title', 'p', 'caption', 'table'))
                    if extract_body else primary)
            candidates = [('xml:main-record-title', primary)] if primary else []
            result.update(content_format='XML', page_title=primary, xml_title=primary,
                          ids=ids, body_present=body is not None,
                          body_chars=len(txt(body)) if extract_body else None)
        else:
            soup = BeautifulSoup(content, 'html.parser')
            metas = {(x.get('name') or x.get('property') or '').lower(): x.get('content', '')
                     for x in soup.select('head meta[content]')}
            page_title = soup.title.get_text(' ', strip=True) if soup.title else ''
            candidates = _html_titles(soup, metas)
            for node in soup(['script', 'style', 'noscript', 'nav', 'footer', 'header']):
                node.decompose()
            if extract_body:
                text = soup.get_text('\n', strip=True)
            else:
                pieces, count = [], 0
                for piece in soup.stripped_strings:
                    pieces.append(piece[:600 - count])
                    count += len(piece) + 1
                    if count >= 600:
                        break
                text = '\n'.join(pieces)[:600]
            challenge = bool(re.search(
                r'just a moment|verify you are human|checking your browser|access denied|'
                r'enable javascript and cookies|captcha verification|robot check',
                page_title + ' ' + text[:600], re.I))
            challenge = challenge or bool(re.match(
                r'^(?:sign in|log in|login|authentication required)(?:\s*[|:–—-]|\s*$)',
                page_title.strip(), re.I))
            result.update(content_format='HTML', page_title=page_title, metadata=metas,
                          description=metas.get('description') or metas.get('og:description', ''),
                          video_tags=len(soup.find_all('video')),
                          iframes=[x.get('src') for x in soup.find_all('iframe')],
                          access_signals=sorted(set(re.findall(
                              'subscribe|subscription|log in|sign in|register|purchase|login',
                              text, re.I)))[:12])
        matches = [_title_matches(title, value, source) for source, value in candidates]
        citations = [i for i, (source, _) in enumerate(candidates) if source == 'citation_title']
        h1s = [i for i, (source, _) in enumerate(candidates) if source == 'h1']
        if citations and all(matches[i] for i in citations) and len(h1s) == 1:
            # Publishers sometimes put the subtitle outside h1. The COMPLETE
            # citation_title must already match, and the visible h1 must equal
            # the entire sufficiently specific main title at a colon boundary.
            parts = re.split(r'[:：]', title, maxsplit=1)
            if (len(parts) == 2 and norm(parts[1]) and len(norm(parts[0])) >= 40 and
                    len(norm(parts[0])) >= .4 * len(norm(title))):
                index = h1s[0]
                matches[index] = matches[index] or norm(candidates[index][1]) == norm(parts[0])
        ratio = max((SequenceMatcher(None, norm(title), norm(value)).ratio()
                     for _, value in candidates), default=0)
        match = bool(matches) and all(matches) and not challenge
        result.update(title_candidates=[{'source': source, 'title': value}
                                        for source, value in candidates],
                      title_similarity=round(ratio, 3), title_present=match,
                      title_match_source=', '.join(source for source, _ in candidates) if match else '',
                      title_conflict=bool(matches) and any(matches) and not all(matches),
                      text_chars=len(text),
                      page_state='访问验证或限制页面' if challenge else
                      ('目标题名在页面中确认' if match else
                       '取得PDF；身份待核对' if is_pdf else '页面取得；身份待核对'))
    except Exception as exc:
        result.update(parse_error=str(exc), page_state='页面解析失败；身份待核对')
    return result, text
def inputs():
 d=read(ROOT/'data/inherited.json');d['articles']+=read(ROOT/'data/additions.json',[]);return d
def txt(x):return ''.join(x.itertext()).strip() if x is not None else ''
def parse_pubmed(raw):
 root=ET.fromstring(raw);out={}
 for a in root.findall('.//PubmedArticle'):
  art=a.find('./MedlineCitation/Article');pid=txt(a.find('./MedlineCitation/PMID'));ids={x.get('IdType'):txt(x) for x in a.findall('./PubmedData/ArticleIdList/ArticleId')};ab=[{'label':x.get('Label',''),'text':txt(x)} for x in art.findall('./Abstract/AbstractText')];date=art.find('./Journal/JournalIssue/PubDate')
  out[pid]={'pmid':pid,'title':txt(art.find('ArticleTitle')),'doi':ids.get('doi',''),'pmcid':ids.get('pmc',''),'journal':txt(art.find('./Journal/Title')),'year':txt(date.find('Year')) if date is not None else '', 'authors':[txt(x.find('LastName'))+' '+txt(x.find('ForeName')) for x in art.findall('./AuthorList/Author')],'abstract':ab,'article_types':[txt(x) for x in art.findall('./PublicationTypeList/PublicationType')],'corrections':[{'type':x.get('RefType'), 'pmid':txt(x.find('PMID')),'citation':txt(x.find('RefSource')),'note':txt(x.find('Note'))} for x in a.findall('./MedlineCitation/CommentsCorrectionsList/CommentsCorrections')],'dates':{x.get('PubStatus'): '-'.join(txt(x.find(k)) for k in ['Year','Month','Day']) for x in a.findall('./PubmedData/History/PubMedPubDate')},'source':'PubMed EFetch XML','checked_at':now()}
 for a in root.findall('.//PubmedBookArticle'):
  b=a.find('BookDocument');pid=txt(b.find('PMID'));book=b.find('Book');ids={x.get('IdType'):txt(x) for x in b.findall('./ArticleIdList/ArticleId')};title=txt(b.find('ArticleTitle')) or txt(book.find('BookTitle'));authors=[txt(x.find('CollectiveName')) or (txt(x.find('LastName'))+' '+txt(x.find('ForeName'))).strip() for x in b.findall('.//AuthorList/Author')]
  out[pid]={'pmid':pid,'title':title,'doi':ids.get('doi',''),'pmcid':'','journal':txt(book.find('CollectionTitle')) or txt(book.find('Publisher/PublisherName')),'year':txt(book.find('PubDate/Year')),'authors':list(dict.fromkeys(authors)) or [txt(book.find('Publisher/PublisherName'))],'abstract':[{'label':x.get('Label',''),'text':txt(x)} for x in b.findall('./Abstract/AbstractText')],'article_types':['Book/Guideline']+[txt(x) for x in b.findall('PublicationType')],'corrections':[],'dates':{},'book_accession':ids.get('bookaccession'),'source':'PubMed EFetch BookArticle XML','checked_at':now()}
 return out
def metadata():
 data=inputs();pm=read(WORK/'pubmed.json',{});ep=read(WORK/'epmc.json',{});logs=read(WORK/'metadata_log.json',[]);ids=[str(x['pmid']) for x in data['articles'] if x.get('pmid')]
 for i in range(0,len(ids),70):
  batch=ids[i:i+70]
  if all(x in pm for x in batch):continue
  r,log=fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',{'db':'pubmed','id':','.join(batch),'retmode':'xml','tool':'HPBResearchLibrary'})
  if r is not None and r.status_code==200:
   try:pm.update(parse_pubmed(r.content));(WORK/f'pubmed_batch_{i}.xml').write_bytes(r.content)
   except Exception as e:log['parse_error']=str(e)
  logs.append(log);write(WORK/'pubmed.json',pm);write(WORK/'metadata_log.json',logs);print('PubMed',i,len(pm),flush=True)
 for i in range(0,len(ids),60):
  batch=ids[i:i+60]
  if all(x in ep for x in batch):continue
  q='EXT_ID:('+ ' OR '.join(batch)+') AND SRC:MED'
  r,log=fetch('https://www.ebi.ac.uk/europepmc/webservices/rest/search',{'query':q,'format':'json','resultType':'core','pageSize':100})
  if r is not None and r.status_code==200:
   try:
    dat=r.json();ep.update({x['id']:x for x in dat['resultList']['result']});log.update(hitCount=dat.get('hitCount'),returned=len(dat['resultList']['result']));write(WORK/f'epmc_batch_{i}.json',dat)
   except Exception as e:log['parse_error']=str(e)
  logs.append(log);write(WORK/'epmc.json',ep);write(WORK/'metadata_log.json',logs);print('EPMC',i,len(ep),flush=True)
 return pm,ep
def page_check(item):
 rid,url,title=item;r,log=fetch(url,retries=1)
 if r is None:return rid,log
 log,text=parse_page_content(r.content,title,log)
 if r.status_code!=200:return rid,log
 suffix='.pdf' if log.get('content_format')=='PDF' else '.html'
 path=WORK/'pages'/f'{rid}{suffix}';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(r.content)
 (WORK/'pages'/f'{rid}.txt').write_text(text)
 return rid,log
def pages():
 d=inputs();tasks=[]
 for k in ['articles','videos','portals','pending_videos']:
  for x in d[k]:
   if x.get('url'):tasks.append((x['id'],x['url'],x['title']))
   if k=='articles' and x.get('fulltext_url') and x['fulltext_url']!=x.get('url'):tasks.append((x['id']+'_full',x['fulltext_url'],x['title']))
 logs=read(WORK/'page_checks.json',{});tasks=[x for x in tasks if x[0] not in logs]
 with ThreadPoolExecutor(max_workers=6) as pool:
  for f in as_completed([pool.submit(page_check,x) for x in tasks]):
   rid,log=f.result();logs[rid]=log;write(WORK/'page_checks.json',logs)
   if len(logs)%20==0:print('page checks',len(logs),flush=True)
def fulltexts():
 d=inputs();ep=read(WORK/'epmc.json',{});pm=read(WORK/'pubmed.json',{});logs=read(WORK/'fulltext_checks.json',{})
 def one(x):
  pid=str(x.get('pmid',''));pmc=pm.get(pid,{}).get('pmcid') or ep.get(pid,{}).get('pmcid') or x.get('pmcid');url=f'https://www.ebi.ac.uk/europepmc/webservices/rest/{pmc}/fullTextXML';r,log=fetch(url,retries=1)
  if r is not None and r.status_code==200:
   try:
    root=ET.fromstring(r.content);record,title,ids=_xml_main_record(root);body=record.find('body') if record is not None else None;match=(bool(pid) and pid in ids or bool(doi(x.get('doi'))) and doi(x.get('doi')) in [doi(s) for s in ids]) and _title_matches(x['title'],title,'xml:main-record-title')
    log.update(identity_match=match,body_present=body is not None,xml_title=title,body_chars=len(txt(body)),ids=ids)
    if body is not None and match:
     dest=WORK/'fulltext';dest.mkdir(exist_ok=True);(dest/f'{x["id"]}.xml').write_bytes(r.content);(dest/f'{x["id"]}.txt').write_text('\n\n'.join(txt(p) for p in body.iter() if p.tag in ['title','p','caption','table']));log['state']='已取得XML正文并匹配身份；阅读范围另记'
   except Exception as e:log['parse_error']=str(e)
  return x['id'],log
 tasks=[x for x in d['articles'] if x['id'] not in logs and (pm.get(str(x.get('pmid')),{}).get('pmcid') or x.get('pmcid'))]
 with ThreadPoolExecutor(max_workers=3) as pool:
  for f in as_completed([pool.submit(one,x) for x in tasks]):
   k,v=f.result();logs[k]=v;write(WORK/'fulltext_checks.json',logs)
   if len(logs)%15==0:print('fulltexts',len(logs),sum(x.get('identity_match',False) for x in logs.values()),flush=True)
if __name__=='__main__':
 import sys
 mode=sys.argv[1] if len(sys.argv)>1 else 'metadata'
 {'metadata':metadata,'pages':pages,'fulltexts':fulltexts}[mode]()
