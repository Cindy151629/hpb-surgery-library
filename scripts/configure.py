"""Build the reviewable HPB taxonomy; these are navigation/search terms, not clinical advice."""
from common import *
TOPICS=[
('liver-anatomy','肝脏外科','解剖与术前评估','肝脏解剖|解剖与命名|解剖性肝切除|Couinaud|Brisbane|future liver remnant','hepatectomy AND (anatomy OR assessment OR nomenclature)'),
('liver-resection','肝脏外科','肝切除术式与路径','肝切除|半肝|肝段|中央肝|左外叶|hepatectomy|liver resection','hepatectomy AND (laparoscopic OR robotic OR open OR segmentectomy)'),
('liver-growth','肝脏外科','余肝增容与分期切除','余肝增容|门静脉栓塞|分期肝切除|ALPPS|LIGRO|liver venous deprivation','ALPPS OR (portal vein embolization) OR (liver venous deprivation)'),
('liver-hcc','肝脏外科','肝细胞癌','肝细胞癌|原发性肝癌|HCC|hepatocellular carcinoma','hepatocellular carcinoma AND (resection OR surgery OR hepatectomy)'),
('liver-crlm','肝脏外科','结直肠癌肝转移','结直肠癌肝转移|肝转移|CRLM|colorectal liver metastases','colorectal liver metastases AND (resection OR ablation OR surgery)'),
('liver-benign','肝脏外科','良性肝病与囊肿','良性肝|肝囊肿|肝血管瘤|肝包虫|hemangioma|hydatid','(liver cyst OR liver hemangioma OR hepatic echinococcosis) AND surgery'),
('liver-vessel','肝脏外科','血管处理与肝动脉灌注','肝血流阻断|肝实质离断|肝动脉灌注|Pringle|hepatic artery infusion','hepatectomy AND (vascular reconstruction OR Pringle OR hepatic artery infusion)'),
('biliary-chole','胆囊与胆道外科','胆囊切除与适应证','胆囊切除|急性胆囊炎|胆囊息肉|胆石症|cholecystectomy|cholecystitis','cholecystectomy OR acute cholecystitis'),
('biliary-difficult','胆囊与胆道外科','困难胆囊与补救术式','困难胆囊|次全|Mirizzi|subtotal|critical view of safety|CVS','subtotal cholecystectomy OR critical view of safety OR Mirizzi'),
('biliary-stones','胆囊与胆道外科','胆管结石与探查','胆管结石|胆总管结石|胆管探查|胆道镜|LCBDE|choledocholithiasis|transcystic','common bile duct exploration OR choledocholithiasis OR hepatolithiasis'),
('biliary-repair','胆囊与胆道外科','胆管损伤与胆肠重建','胆管损伤|胆道损伤|胆肠|Hepp-Couinaud|hepaticojejunostomy|bile duct injury','bile duct injury OR hepaticojejunostomy'),
('biliary-gbc','胆囊与胆道外科','胆囊癌','胆囊癌|gallbladder cancer|radical cholecystectomy','gallbladder cancer AND (surgery OR resection)'),
('biliary-cca','胆囊与胆道外科','胆管癌与胆道肿瘤','胆管癌|胆道肿瘤|胆道恶性|cholangiocarcinoma|perihilar|Klatskin','cholangiocarcinoma AND (surgery OR resection OR drainage)'),
('pancreas-whipple','胰腺外科','胰十二指肠切除','胰十二指肠切除|胰头切除|Whipple|pancreaticoduodenectomy|pancreatoduodenectomy','pancreaticoduodenectomy OR pancreatoduodenectomy'),
('pancreas-distal','胰腺外科','远端胰切除与保脾','远端胰|胰体尾|保脾|RAMPS|Kimura|Warshaw|distal pancreatectomy','distal pancreatectomy OR RAMPS'),
('pancreas-local','胰腺外科','中段、全胰与局部切除','胰腺中段|中段胰|全胰|剜除|central pancreatectomy|total pancreatectomy|enucleation','central pancreatectomy OR total pancreatectomy OR pancreatic enucleation'),
('pancreas-anastomosis','胰腺外科','吻合重建与胰瘘','胰肠|胰胃|胰瘘|胰管支架|Blumgart|pancreaticojejunostomy|pancreaticogastrostomy','pancreaticojejunostomy OR pancreaticogastrostomy OR postoperative pancreatic fistula'),
('pancreas-vessel','胰腺外科','联合血管切除与清扫','胰腺癌淋巴结|复杂血管|门静脉切除|胰腺微创手术争议及血管|pancreatectomy vascular','pancreatectomy AND (vascular resection OR lymphadenectomy OR arterial resection)'),
('pancreas-cancer','胰腺外科','胰腺癌与手术时机','胰腺癌|pancreatic cancer|borderline resectable|neoadjuvant','pancreatic cancer AND (neoadjuvant OR resectable OR surgical)'),
('pancreas-cyst','胰腺外科','囊性病变与神经内分泌肿瘤','IPMN|胰腺囊性|胰腺神经内分泌|胰岛素瘤|pancreatic cyst|neuroendocrine','pancreatic AND (IPMN OR cystic neoplasm OR neuroendocrine) AND (guideline OR surgery OR resection)'),
('pancreas-inflammation','胰腺外科','胰腺炎与坏死处理','胰腺炎|胰腺坏死|坏死清创|Frey|Puestow|Beger|sinus tract|pancreatitis|necrosectomy','pancreatitis AND (necrosectomy OR drainage OR surgery OR step-up)'),
('common-navigation','共同技术与围手术期','超声、荧光与导航','术中超声|荧光|导航|ICG|indocyanine|intraoperative ultrasound','(hepatectomy OR cholecystectomy OR pancreatectomy) AND (indocyanine OR navigation OR intraoperative ultrasound)'),
('common-eras','共同技术与围手术期','营养、预康复与加速康复','ERAS|围术期营养|肝病营养|围手术期|预康复|enhanced recovery|nutrition','(hepatectomy OR pancreaticoduodenectomy OR liver surgery) AND (enhanced recovery OR nutrition OR prehabilitation)'),
('common-complications','共同技术与围手术期','并发症定义与防治','胆漏|肝衰竭|术后出血|并发症|胃排空延迟|乳糜漏|ISGLS|ISGPS|Clavien','(hepatectomy OR pancreatectomy) AND (complication OR ISGLS OR ISGPS OR postoperative hemorrhage)'),
('common-quality','共同技术与围手术期','学习曲线与质量评价','学习曲线|质量评价|风险模型|风险预测|learning curve|benchmark|quality','(hepatectomy OR pancreaticoduodenectomy OR cholecystectomy) AND (learning curve OR benchmark OR quality indicator)'),
('extended-liver-transplant','移植及扩展专题','肝移植与活体供肝','肝移植|供肝|供者|liver transplant|living donor|machine perfusion','liver transplantation OR living donor hepatectomy'),
('extended-pancreas-transplant','移植及扩展专题','胰腺与胰岛移植','胰腺移植|胰岛自体移植|pancreas transplant|islet transplant','pancreas transplantation OR islet autotransplantation'),
('extended-pediatric','移植及扩展专题','儿科与先天性疾病','先天性|儿科|胆道闭锁|choledochal|biliary atresia|pediatric','(biliary atresia OR choledochal cyst) AND surgery'),
('extended-intervention','移植及扩展专题','内镜、介入与消融','内镜|消融|ERCP|ablation|endoscopy','(biliary OR liver OR pancreatic) AND (ablation OR interventional OR endoscopic drainage)'),
('extended-trauma','移植及扩展专题','肝胆胰创伤','创伤|trauma|injury traumatic','(liver trauma OR pancreatic trauma OR duodenal trauma) AND surgery'),
]
if __name__=='__main__':
 write(ROOT/'config/taxonomy.json',{'version':'hpb-topics-1','organs':list(dict.fromkeys(x[1] for x in TOPICS)),'resource_types':['指南/共识','综述','原始研究','技术/解剖文章','学会专题','视频','课程模块','合集入口','待核线索'],'topics':[{'id':i,'organ':o,'label':l,'aliases':a.split('|'),'query':q} for i,o,l,a,q in TOPICS]})
 write(ROOT/'config/sources.json',{'query_version':'hpb-query-1','overlap_days':21,'monthly_lookback_days':90,'max_pages':30,'page_size':100,'epmc_endpoint':'https://www.ebi.ac.uk/europepmc/webservices/rest/search','pubmed_endpoint':'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi','sages_feed':'https://www.sages.org/video/feed/','stanford_directory':'https://med.stanford.edu/hpb-library.html','include':'与肝胆胰手术决策、操作或围手术期直接相关的人类研究；不限OA；教学病例与比较研究分开','exclude':'纯基础研究、无手术相关性、广告；疑似相关但证据不足留候选','historical_filter':'(PUB_TYPE:"guideline" OR PUB_TYPE:"randomized controlled trial" OR TITLE_ABS:consensus OR TITLE_ABS:"systematic review")','manual_sources':['中华医学会系列期刊/指南平台','国家卫生健康委','临床肝胆病杂志指南目录','JOMI官方视频目录','webop课程目录','Olympus中国专业教育'],'unsearched_subscription_sources':['Embase','Web of Science','CNKI付费检索'],'schedule':{'timezone':'Asia/Shanghai','local':'每周一09:17','utc_cron':'17 1 * * 1'}})
 if not (ROOT/'config/deployment.json').exists():write(ROOT/'config/deployment.json',{'domain_id':'hpb-surgery','repository':'','https_base_url':'','public_scope_approved':False,'schedule_enabled':False,'observed_scheduled_run':None,'status_url':'','runtime_status_url':''})
