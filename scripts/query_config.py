from common import *
# Each outer group is AND; terms within each group are OR. All terms are scoped to title/abstract.
GROUPS=[
[['hepatectomy','liver resection'],['anatomy','assessment','nomenclature']],
[['hepatectomy','liver resection','liver surgery','hepatic surgery'],['laparoscopic','robotic','open','segmentectomy']],
[['ALPPS','portal vein embolization','portal venous embolization','liver venous deprivation']],
[['hepatocellular carcinoma'],['resection','surgery','hepatectomy']],
[['colorectal liver metastases','colorectal liver metastasis'],['resection','ablation','surgery']],
[['liver cyst','hepatic cyst','liver hemangioma','hepatic hemangioma','hepatic echinococcosis'],['surgery','surgical','resection']],
[['hepatectomy','liver resection'],['vascular reconstruction','Pringle','hepatic artery infusion']],
[['cholecystectomy','cholecystitis','gallbladder polyp','gallstones','gallstone disease']],
[['subtotal cholecystectomy','critical view of safety','Mirizzi']],
[['common bile duct exploration','choledocholithiasis','hepatolithiasis']],
[['bile duct injury','hepaticojejunostomy']],
[['gallbladder cancer','gallbladder carcinoma'],['surgery','surgical','resection']],
[['cholangiocarcinoma'],['surgery','surgical','resection','drainage']],
[['pancreaticoduodenectomy','pancreatoduodenectomy']],
[['pancreas','pancreatic','pancreatectomy'],['distal pancreatectomy','RAMPS','spleen preservation']],
[['central pancreatectomy','total pancreatectomy','pancreatic enucleation']],
[['pancreaticojejunostomy','pancreatojejunostomy','pancreaticogastrostomy','postoperative pancreatic fistula']],
[['pancreatectomy','pancreatic cancer'],['vascular resection','lymphadenectomy','arterial resection']],
[['pancreatic cancer','pancreatic adenocarcinoma'],['neoadjuvant','resectable','surgical']],
[['pancreatic'],['IPMN','cystic neoplasm','neuroendocrine'],['guideline','surgery','resection']],
[['pancreatitis'],['necrosectomy','drainage','surgery','step-up']],
[['hepatectomy','cholecystectomy','pancreatectomy'],['indocyanine','navigation','intraoperative ultrasound']],
[['hepatectomy','pancreaticoduodenectomy','liver surgery'],['enhanced recovery','nutrition','prehabilitation']],
[['hepatectomy','pancreatectomy','postpancreatectomy','posthepatectomy'],['complication','ISGLS','ISGPS','postoperative hemorrhage','hemorrhage','definition']],
[['hepatectomy','pancreaticoduodenectomy','cholecystectomy'],['learning curve','benchmark','quality indicator']],
[['liver transplantation','liver transplant','living donor hepatectomy']],
[['pancreas transplantation','pancreatic transplantation','islet autotransplantation']],
[['biliary atresia','choledochal cyst'],['surgery','surgical']],
[['biliary','liver','pancreatic'],['ablation','interventional','endoscopic drainage']],
[['liver trauma','pancreatic trauma','duodenal trauma','pancreatic injury'],['surgery','surgical']],
[['pancreatic surgery','pancreas surgery'],['minimally invasive','guideline','consensus','laparoscopic','robotic']],
]
def compile_query(groups,source):
 def term(t):
  if any(c in t for c in ['"','[',']',':']):raise ValueError('query terms must be plain reviewed phrases')
  return 'TITLE_ABS:"'+t+'"' if source=='Europe PMC' else '"'+t+'"[Title/Abstract]'
 return ' AND '.join('('+' OR '.join(term(t) for t in group)+')' for group in groups)
if __name__=='__main__':
 tax=read(ROOT/'config/taxonomy.json')
 if not any(t['id']=='pancreas-approach' for t in tax['topics']):tax['topics'].append({'id':'pancreas-approach','organ':'胰腺外科','label':'胰腺外科总论与手术路径','aliases':['微创胰腺外科','微创胰腺手术','EGUMIPS','Brescia','Miami guidelines','pancreatic surgery','pancreas surgery']})
 assert len(tax['topics'])==len(GROUPS)
 for t,groups in zip(tax['topics'],GROUPS):t['query_groups']=groups;t['query']=' AND '.join('('+' OR '.join('"'+v+'"' for v in g)+')' for g in groups)
 tax['version']='hpb-topics-3';write(ROOT/'config/taxonomy.json',tax);c=read(ROOT/'config/sources.json');c['query_version']='hpb-query-3-guideline-recall';c['max_pages']=120;c['historical_filter']='(PUB_TYPE:"guideline" OR PUB_TYPE:"randomized controlled trial" OR TITLE_ABS:"consensus" OR TITLE_ABS:"systematic review" OR TITLE_ABS:"guideline" OR TITLE_ABS:"guidelines" OR TITLE_ABS:"definition" OR TITLE_ABS:"recommendations")';write(ROOT/'config/sources.json',c)
