"""Read-only release/index audit. Reports paths and metadata, never secret values."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SECRET=re.compile(r'AIza[A-Za-z0-9_-]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
DENIED={'.db','.sqlite','.sqlite3','.wav','.mp3','.mp4','.mkv','.mov','.webm','.jpg','.jpeg','.png','.pdf','.docx','.zip','.key','.pem','.p12','.pfx'}


def audit(root=ROOT,check_index=False):
    root=Path(root).resolve();issues=[]
    include=json.loads((root/'GIT_INCLUDE_FILES.json').read_text('utf-8'))['files']
    entries=json.loads((root/'CLOUD_FILE_MANIFEST.json').read_text('utf-8'))['files']
    if len(include)!=len(set(include)):issues.append('Duplicate include paths')
    expected={entry['path']:entry for entry in entries}
    if set(include)!=set(expected)|{'CLOUD_FILE_MANIFEST.json'}:issues.append('Manifest/include differ')
    for relative in include:
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or '\\' in relative or ':' in relative:
            issues.append('Unsafe include path');continue
        path=root/p
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or any(part in {'.git','.test-data','test_artifacts','rc_data','media','backups','exports','.venv'} for part in p.parts)):
            issues.append('Redirected/runtime include: '+relative);continue
        if p.suffix.lower() in DENIED or p.name.startswith(('.env','XAGON_')):
            issues.append('Private file type/name: '+relative);continue
        if not path.is_file():issues.append('Missing: '+relative);continue
        if path.stat().st_size>4*1024*1024:issues.append('Oversized source candidate: '+relative);continue
        raw=path.read_bytes()
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:issues.append('Non UTF-8 source: '+relative);continue
        if SECRET.search(text):issues.append('Known secret pattern: '+relative)
        entry=expected.get(relative)
        if entry and (hashlib.sha256(raw).hexdigest()!=entry['sha256'] or len(raw)!=entry['bytes']):issues.append('Checksum mismatch: '+relative)
        if p.suffix in ('.py','.sh','.js') and b'\r' in raw:issues.append('Non-LF source: '+relative)
    unknown=[]
    for path in root.rglob('*'):
        relative=path.relative_to(root).as_posix()
        if path.is_symlink():issues.append('Unexpected symlink: '+relative);continue
        if not path.is_file() or set(path.relative_to(root).parts)&{'.git','__pycache__','test_artifacts','.test-data','rc_data','.venv'}:continue
        if relative not in include:unknown.append(relative)
    if unknown:issues.append('Unlisted files: '+', '.join(sorted(unknown)))
    indexed=None
    if check_index:
        # Inspect the complete index, including unchanged files after a commit.
        proc=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,check=True)
        indexed=set(proc.stdout.decode('utf-8').strip('\x00').split('\x00')) if proc.stdout else set()
        if indexed!=set(include):issues.append('Staged index differs from allowlist')
        # Compare the actual staged blobs, not only filenames, to audited files.
        rows=subprocess.run(['git','ls-files','--stage','-z'],cwd=root,capture_output=True,check=True).stdout
        objects=[]
        for row in rows.split(b'\x00'):
            if not row:continue
            metadata,name=row.split(b'\t',1);mode,oid,stage=metadata.split()
            if mode not in (b'100644',b'100755') or stage!=b'0':issues.append('Non-regular/conflicted index entry');continue
            objects.append((oid,name.decode('utf-8')))
        request=b''.join(oid+b'\n' for oid,_ in objects)
        blobs=subprocess.run(['git','cat-file','--batch'],input=request,cwd=root,capture_output=True,check=True).stdout
        position=0
        for _,name in objects:
            end=blobs.index(b'\n',position);header=blobs[position:end].split();size=int(header[-1])
            position=end+1;content=blobs[position:position+size];position+=size+1
            if header[1]!=b'blob' or name not in include or content!=(root/name).read_bytes():issues.append('Staged content differs: '+name)
    return {'success':not issues,'release_files':len(include),'issues':issues,'staged_files':len(indexed) if indexed is not None else None,
            'scope':'release allowlist/known secret patterns; not a guarantee against every possible secret'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--index',action='store_true');args=parser.parse_args()
    result=audit(check_index=args.index);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(not result['success'])
