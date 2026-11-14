"""Discard only disposable files created by local preview preparation."""
import json
import re
from pathlib import Path
from lib import gates,receipts,trailer_edit
from lib.pathsafe import resolve_input,sha256_file
from lib.canonical_json import record_sha256

def _hashes(value):
    if isinstance(value,dict):
        for v in value.values():yield from _hashes(v)
    elif isinstance(value,list):
        for v in value:yield from _hashes(v)
    elif isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value):yield value

def _protected(root):
    rows=receipts.chained_rows(root,'approval')
    rows=[r for r in rows if gates.verify_receipt(r)]
    revisions={r.get('record',{}).get('edit_revision_sha256') for r in rows}
    hashes=set(_hashes(rows))|{r['output_sha256'] for r in receipts.verified_generation_receipts(root)}
    return revisions,hashes

def _discard_tree(root,directory,protected):
    """The caller owns this one exclusive render job, never a source folder."""
    if directory.is_symlink() or not directory.is_dir():return
    entries=list(directory.rglob('*'))
    if any(p.is_symlink() for p in entries):return
    files=[p for p in entries if p.is_file()]
    if any(sha256_file(resolve_input(str(p.relative_to(root)),root)) in protected for p in files):return
    for p in files:p.unlink()
    for p in sorted((p for p in entries if p.is_dir()),key=lambda p:len(p.parts),reverse=True):p.rmdir()
    directory.rmdir()

def finish_preview(root,target,mixfile,refs,title_layer=None,*,rejected=False):
    root=Path(root);directory=root/'artifacts/cut-previews'
    # A human signature and cleanup cannot race: the same approval lock
    # protects the inventory and the bounded removals. Broken history never
    # becomes permission to clear files.
    with gates.receipt_lock(root.name,'approval'):
        revisions,protected=_protected(root)
        owned=Path(refs['work_directory'])
        if owned==mixfile.parent/(mixfile.stem+'-parts'):
            expected={Path(p) for p in refs['temporary_files']}
            if expected==set(owned.iterdir())|{mixfile} and all(p==mixfile or p.parent==owned for p in expected):
                if not any(p.is_symlink() or sha256_file(resolve_input(str(p.relative_to(root)),root)) in protected for p in expected):
                    for p in expected:p.unlink()
                    owned.rmdir()
        if title_layer is not None and title_layer.parent.parent==directory and re.fullmatch('titles-[a-f0-9]{32}',title_layer.parent.name):
            _discard_tree(root,title_layer.parent,protected)
        if rejected:
            report=target.with_suffix('.review.json')
            if (target.parent==directory and re.fullmatch('[a-f0-9]{64}-[a-f0-9]{32}\\.mp4',target.name)
                and not target.is_symlink() and sha256_file(target) not in protected
                and not report.is_symlink() and sha256_file(report) not in protected
                and record_sha256(json.loads(report.read_text())) not in protected):
                target.unlink();report.unlink()
            return
        current=trailer_edit.read_revision(root)
        if not current or not target.name.startswith(current['edit_revision_sha256']):return
        candidates=[]
        for report in directory.iterdir():
            match=re.fullmatch('([a-f0-9]{64})-[a-f0-9]{32}\\.review\\.json',report.name)
            if not match or report.is_symlink() or not report.is_file():continue
            path=report.with_name(report.name.removesuffix('.review.json')+'.mp4')
            if path.is_symlink() or not path.is_file():continue
            try:review=json.loads(report.read_text())
            except (ValueError,OSError):continue
            if review.get('comparison') not in ('before','after') or review.get('edit_revision_sha256')!=match[1]:continue
            candidates.append((path,report,review))
        keep={target}
        for comparison in ('before','after'):
            same=[p for p,r,v in candidates if p.name.startswith(current['edit_revision_sha256']) and v['comparison']==comparison
                  and not (comparison=='after' and v.get('true_peak_dbtp') is not None and v['true_peak_dbtp']>-1)]
            if same:keep.add(max(same,key=lambda p:p.stat().st_mtime_ns))
        for path,report,review in candidates:
            # Older completed jobs have the same exclusive stem, even when
            # their report predates the explicit temporary-files inventory.
            parts=path.with_name(path.stem+'-parts')
            if parts.is_dir() and not parts.is_symlink():
                children=list(parts.iterdir())
                if all(p.is_file() and not p.is_symlink() and re.fullmatch('(speech-source|other-source|music-source|voice-[0-9]+-source|speech|other|music|effect-[0-9]+(-source)?|summed)\\.wav',p.name) for p in children):
                    _discard_tree(root,parts,protected)
            wave=path.with_suffix('.wav')
            if wave.is_file() and not wave.is_symlink() and sha256_file(wave) not in protected:wave.unlink()
            if (path in keep or review['edit_revision_sha256'] in revisions or sha256_file(path) in protected
                or sha256_file(report) in protected or record_sha256(review) in protected):continue
            path.unlink();report.unlink()
