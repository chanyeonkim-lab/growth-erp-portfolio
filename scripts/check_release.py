"""Check a source index or release archive without printing credential contents."""
import argparse,hashlib,re,subprocess,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SECRET=re.compile(rb'(?:sb_secret_[A-Za-z0-9_-]{20,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')

# User-approved source fixtures only; never permit them in the release archive.
APPROVED_FIXTURES={}

def check(entries,allow_test_fixtures=False):
    problems=[]
    for name,content in entries:
        path=Path(name)
        if name.startswith('growth-erp/'):path=Path(name[len('growth-erp/'):])
        parts=path.parts
        if ('data' in parts and path.as_posix()!='data/.gitkeep') or any(p in parts for p in ('.git','.vercel','.venv','node_modules','__pycache__','private-samples','backups')):
            problems.append(f'{name}: excluded directory')
        if path.name.startswith('.env') and path.name!='.env.example':problems.append(f'{name}: environment file')
        approved=(allow_test_fixtures and APPROVED_FIXTURES.get(path.as_posix())==hashlib.sha256(content).hexdigest())
        if path.suffix.lower() in ('.pdf','.xlsx','.xls','.csv','.sqlite3','.db','.pem','.key','.pyc') and not approved:
            problems.append(f'{name}: private data or runtime file')
        if SECRET.search(content):problems.append(f'{name}: possible credential')
    if problems:raise SystemExit('\n'.join(problems))
    print('PASS: no excluded data files or credential patterns.')

def main():
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--git',action='store_true');group.add_argument('--zip',type=Path)
    args=parser.parse_args()
    if args.git:
        names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        entries=[(name,subprocess.check_output(['git','show',':'+name],cwd=ROOT)) for name in names if name]
        check(entries,allow_test_fixtures=True)
    else:
        with zipfile.ZipFile(args.zip) as archive:check((name,archive.read(name)) for name in archive.namelist() if not name.endswith('/'))

if __name__=='__main__':main()
