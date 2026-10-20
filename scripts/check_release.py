"""Check tracked release files without printing credential values or deleting local data."""
import argparse
import json
import re
import subprocess
from pathlib import Path

SECRET=re.compile(rb'AIza[0-9A-Za-z_-]{30,}|hf_[0-9A-Za-z]{20,}')


def check(root):
    tracked=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
    findings=[]
    for name in filter(None,tracked):
        path=root/name
        if not path.is_file(): continue
        if (path.name.startswith('.env') and path.name!='.env.example') or 'backup' in name.lower() or path.suffix in {'.sqlite3','.log','.pdf'}:
            findings.append({'file':name,'problem':'private_or_generated_file_tracked'})
        if path.stat().st_size>50*1024*1024:
            findings.append({'file':name,'problem':'file_larger_than_50mb'})
        elif path.suffix.lower() in {'.py','.md','.txt','.json','.toml','.yml','.yaml','.example'} or path.name.startswith('.env'):
            if SECRET.search(path.read_bytes()): findings.append({'file':name,'problem':'credential_pattern'})
    for name in ('loaddocs/document.py','loaddocs/embedding.py'):
        if name not in tracked: findings.append({'file':name,'problem':'git_filename_case_mismatch'})
    history=[]
    commits=subprocess.check_output(['git','log','--all','--format=%H','--','.env copy'],cwd=root).decode().splitlines()
    for commit in commits:
        previous=subprocess.run(['git','show',f'{commit}:.env copy'],cwd=root,capture_output=True)
        if previous.returncode==0 and SECRET.search(previous.stdout): history.append(commit[:12])
    return {'working_tree_checks_passed':not findings,'findings':findings,
            'credential_history_commits':history,
            'publication_file_gate_passed':not findings and not history,
            'key_revocation':'requires_owner_confirmation' if history else 'not_checked_with_providers'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=check(Path(__file__).resolve().parents[1])
    text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,encoding='utf-8')
    print(text)
    return 1 if report['findings'] else 2 if report['credential_history_commits'] else 0


if __name__=='__main__': raise SystemExit(main())
