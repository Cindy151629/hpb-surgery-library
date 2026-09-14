"""Local-only audit of saved response bytes; never represents a new network request."""
from common import *
from harvest import parse_page_content, PAGE_PARSER_VERSION
from update import page_audit
from copy import deepcopy

def main():
    stamp=now(); records=read(ROOT/'data/records.json'); byid={r['id']:r for r in records}
    ids=sorted(byid,key=len,reverse=True); pages=read(WORK/'page_checks.json',{})
    sweep=read(ROOT/'data/reports/fulltext-link-sweep.json',{}); changes=[]; skipped=[]
    for name,value in [('page_checks_before_strict_parser',pages),('records_before_strict_parser',records),('fulltext_sweep_before_strict_parser',sweep)]:
        path=WORK/(name+'.json')
        if not path.exists():write(path,value)
    original_records={r['id']:r for r in read(WORK/'records_before_strict_parser.json')}
    def parse(taskid,title,log):
        if log.get('status') != 200:
            parsed,_=parse_page_content(b'',title,log,extract_body=False)
            return parsed
        folder=WORK/'pages';paths=[folder/(taskid+s) for s in ['.pdf','.html']]
        found=next((p for p in paths if p.exists() and digest(p.read_bytes())==log.get('sha256')),None)
        if found is None:
            found=next((p for p in folder.glob(taskid+'*') if p.suffix in ['.pdf','.html'] and p.is_file() and digest(p.read_bytes())==log.get('sha256')),None)
        if found is None:
            skipped.append({'task_id':taskid,'reason':'No cached response with matching original SHA256; existing evidence retained.'})
            return log
        parsed,_=parse_page_content(found.read_bytes(),title,log,extract_body=False)
        parsed['reclassified_at']=stamp
        if parsed.get('title_present')!=log.get('title_present'):
            changes.append({'task_id':taskid,'before':log.get('title_present'),'after':parsed.get('title_present'),'request_checked_at':log.get('checked_at'),'reclassified_at':stamp,'title_candidates':parsed.get('title_candidates')})
        return parsed
    for taskid,log in list(pages.items()):
        rid=next((rid for rid in ids if taskid==rid or taskid.startswith(rid+'_')),None)
        if rid:pages[taskid]=parse(taskid,byid[rid]['title'],log)
    write(WORK/'page_checks.json',pages)
    for record in records:
        rid=record['id'];old=record['audit']['page'];initial=pages.get(rid,{})
        if initial.get('checked_at')==old.get('checked_at'):
            parsed=initial
        else:
            log={**old,'status':old.get('http_status'),'page_state':old.get('status'),'title_present':old.get('title_confirmed'),'requested_url':record['url']}
            parsed=parse(rid,record['title'],log)
        if parsed.get('page_parser_version'):
            task={'url':record['url']};updated=page_audit(parsed,task)
            updated.update(page_parser_version=parsed['page_parser_version'],reclassified_at=stamp,title_candidates=parsed.get('title_candidates',[]))
            record.setdefault('verification_history',[]).append({**old,'history_reason':'Cached bytes reclassified using main title evidence; not a new request.'})
            record['audit']['page']=updated
            if old.get('title_confirmed') and not updated['title_confirmed'] and record['verification_status']=='核心身份与所读内容已核对':
                record['verification_status']='主页面题名待复核；保留已有内容笔记'
            elif updated['title_confirmed'] and record['verification_status']=='主页面题名待复核；保留已有内容笔记' and original_records[rid]['verification_status']=='核心身份与所读内容已核对':
                record['verification_status']='核心身份与所读内容已核对'
            for source in record['audit']['identity'].get('sources',[]):
                if source.get('name')=='原站' and source.get('checked_at')==old.get('checked_at'):
                    source.update(title_present=updated['title_confirmed'],reclassified_at=stamp)
        ft=record['audit']['fulltext']
        if ft.get('format')=='PDF':
            old_log=pages.get(rid+'_full',{}) or pages.get(rid,{})
            if old_log.get('sha256')==ft.get('sha256') and old_log.get('page_parser_version') and not old_log.get('title_present'):
                ft['previous_retrieval_status']=ft['status'];ft['status']='已取得PDF；自动主标题匹配不足，需结合笔记来源复核'
                record['fulltext_status']='已取得PDF；题名待复核'
            elif old_log.get('sha256')==ft.get('sha256') and old_log.get('title_present') and ft.get('previous_retrieval_status'):
                ft['status']=ft.pop('previous_retrieval_status');record['fulltext_status']='已取得并匹配全文'
    for taskid,log in list(sweep.get('checks',{}).items()):
        rid=log['record_id'];record=byid[rid];parsed=parse(taskid,record['title'],log);sweep['checks'][taskid]=parsed
        url=log['requested_url'];old=record['audit']['fulltext'].get('link_checks',{}).get(url,{})
        if parsed.get('page_parser_version'):
            updated=page_audit(parsed,{'url':url});updated.update(page_parser_version=parsed['page_parser_version'],reclassified_at=stamp,title_candidates=parsed.get('title_candidates',[]),history=old.get('history',[]))
            if old:updated['history'].append({k:v for k,v in old.items() if k!='history'})
            record['audit']['fulltext'].setdefault('link_checks',{})[url]=updated
    sweep['previous_title_matched']=sweep.get('title_matched');sweep['title_matched']=sum(bool(l.get('title_present')) for l in sweep['checks'].values());sweep['unconfirmed']=len(sweep['checks'])-sweep['title_matched'];sweep['reclassified_at']=stamp;sweep['parser_version']=PAGE_PARSER_VERSION
    state=read(ROOT/'data/state.json');state['last_link_audit']={k:v for k,v in sweep.items() if k not in ['checks','revised_ids']}
    write(ROOT/'data/state.json',state);write(ROOT/'data/records.json',records);write(ROOT/'data/reports/fulltext-link-sweep.json',sweep)
    report={'reclassified_at':stamp,'parser_version':PAGE_PARSER_VERSION,'network_requests':0,'scope':'Saved response bytes with original matching SHA256; request timestamps preserved. HTML primary title, XML main record and PDF first-page title region only. Prior parsing retained privately and record history retained.','changed':changes,'skipped':skipped,'fulltext_link_counts':{'attempted':sweep['attempted'],'title_matched':sweep['title_matched'],'unconfirmed':sweep['unconfirmed']},'note_hashes_unchanged':all(digest(json.dumps(r.get('note'),sort_keys=True))==digest(json.dumps(byid[r['id']].get('note'),sort_keys=True)) for r in read(WORK/'records_before_strict_parser.json'))}
    write(ROOT/'data/reports/page-identity-reclassification.json',report)
    print({'changes':len(changes),'skipped':len(skipped),'fulltext_title_matches':sweep['title_matched'],'notes_unchanged':report['note_hashes_unchanged']})
if __name__=='__main__':main()
