"""Create the exact reviewable public package. Never recursively copy the workspace."""
from common import *
import shutil
FILES=['README.md','PUBLICATION_SCOPE.md','requirements.txt','.gitignore','.github/workflows/weekly.yml','config/taxonomy.json','config/sources.json','config/deployment.json','data/records.json','data/state.json','data/candidates.json','data/additions.json','data/curated_overrides.json','data/video_discovery.json','data/publication-status.json','data/runtime-status.json','data/pending_changes.json']
FILES += ['data/ai_records.json', 'data/ai_aliases.json', 'data/ai_state.json', 'data/ai_candidates.json', 'data/ai_pending_changes.json', 'data/ai_asset_checks.json', 'config/ai_taxonomy.json', 'config/ai_sources.json', 'PATCH_REPORT.md']
GLOBS=['scripts/*.py','src/*','tests/*.py','tests/*.cjs','data/reports/*.json','data/reports/*.md','data/reports/*.txt','data/runs/*.json','data/checkpoints/*.json']
def stage():
 dest=ROOT/'public-review';dest.mkdir(exist_ok=True);paths=[ROOT/f for f in FILES if (ROOT/f).is_file()]
 for pattern in GLOBS:paths.extend(ROOT.glob(pattern))
 expected={str(p.relative_to(ROOT)) for p in paths}
 previous={f['path']:f for f in read(ROOT/'PUBLICATION_MANIFEST.json',{}).get('files',[])}
 unexpected=[]
 for p in dest.rglob('*'):
  if not p.is_file():continue
  relative=str(p.relative_to(dest))
  if relative in expected:continue
  # Remove only an unchanged copy listed in the previous approved staging manifest.
  if relative in previous and not (ROOT/relative).exists() and digest(p.read_bytes())==previous[relative]['sha256']:
   p.unlink()
  else:unexpected.append(relative)
 if unexpected:raise ValueError('Public staging contains unreviewed files: '+str(unexpected))
 manifest=[]
 for src in paths:
  if src.is_symlink():raise ValueError('symlink disallowed')
  relative=src.relative_to(ROOT);target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target);manifest.append({'path':str(relative),'bytes':src.stat().st_size,'sha256':digest(src.read_bytes())})
 # Inherited snapshot, manual notes, raw evidence and original attachments are deliberately absent.
 write(ROOT/'PUBLICATION_MANIFEST.json',{'generated_at':now(),'scope':'Generated reading notes + public citation/video metadata + code/config/audit; excludes private notes and full texts.','files':sorted(manifest,key=lambda x:x['path']),'cloud_destination':read(ROOT/'config/deployment.json')});print('Public review files',len(manifest));return manifest
if __name__=='__main__':stage()
