"""Read-only verification of pinned source and plugin checkouts; no config output."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import argparse
import json
import re
import subprocess


def git(root: Path, *args: str) -> str:
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    if result.returncode:raise ValueError('Git inspection failed for '+str(root))
    return result.stdout.strip()


def verify_checkout(root: Path, expected: str) -> str:
    if re.fullmatch('[0-9a-f]{40}',expected) is None:
        raise ValueError('Expected revision must be a full commit SHA')
    actual=git(root,'rev-parse','HEAD')
    if actual!=expected:raise ValueError('Source revision differs from the release lock')
    if git(root,'status','--porcelain','--untracked-files=all'):
        raise ValueError('Source checkout has uncommitted files')
    return actual


def resolve_plugin_path(instance: Path, folder: str) -> Path:
    if not folder or Path(folder).name!=folder or folder in {'.','..'}:
        raise ValueError('Invalid plugin folder')
    return instance/'plugins'/folder


def verify_release(source_root: Path, instance: Path, expected_main: str) -> dict[str, Any]:
    main_sha=verify_checkout(source_root,expected_main)
    lock=json.loads((source_root/'deployment/plugin-lock.json').read_text())
    if lock.get('schema_version')!=1:raise ValueError('Unsupported plugin lock schema')
    plugins=[]
    for item in lock['plugins']:
        sha=verify_checkout(resolve_plugin_path(instance,item['folder']),item['source']['commit'])
        plugins.append({'id':item['id'],'commit':sha})
    return {'main_commit':main_sha,'plugins':plugins,'source_clean':True}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--instance-dir',type=Path,required=True)
    parser.add_argument('--expected-main',help='Full pushed main commit; defaults to private deployment record')
    args=parser.parse_args();expected=args.expected_main
    if not expected:
        record=args.instance_dir/'data/deployment/current.json'
        if not record.is_file():parser.error('No deployment record; supply --expected-main')
        expected=json.loads(record.read_text())['main_commit']
    try:print(json.dumps(verify_release(args.source_root,args.instance_dir,expected),indent=2))
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as exc:
        print(json.dumps({'source_clean':False,'error':str(exc)}));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
