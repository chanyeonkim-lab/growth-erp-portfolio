"""Package the local app from an explicit allowlist; never include user data."""
from pathlib import Path
import hashlib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=('index.py','demo_cloud.py','demo_seed.py','ai_guide.py','guide_knowledge.json','ledger.py','ledger_replay.py','tax_evidence.py','document_templates.py','erp_documents.py','server.py','workflow.py','statement_workflow.py','tax_workflow.py','tax_provider.py','pdf_documents.py','quote_pdf.py','company_profile.py','requirements.txt','README.md','사용안내.txt','.env.example')

def main():
    destination=ROOT/'dist';destination.mkdir(exist_ok=True)
    archive=destination/'growth-erp-portfolio.zip'
    files=[ROOT/name for name in FILES]+sorted(p for p in (ROOT/'app').rglob('*') if p.is_file())+sorted(p for p in (ROOT/'assets').rglob('*') if p.is_file())
    if any(not p.is_file() for p in files):raise SystemExit('A required application file is missing.')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path,'growth-erp/'+path.relative_to(ROOT).as_posix())
        bundle.writestr('growth-erp/data/.gitkeep','')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination/'growth-erp-portfolio.zip.sha256').write_text(f'{digest}  growth-erp-portfolio.zip\n')
    print(f'Built {archive.name}: {len(files)} application files, no database or uploaded documents.')

if __name__=='__main__':main()
