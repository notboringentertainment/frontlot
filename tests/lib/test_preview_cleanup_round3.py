"""Cleanup needs verified authority and a matching local render record."""
import json,uuid
from datetime import datetime,timezone
from pathlib import Path
import pytest
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

@pytest.fixture(autouse=True)
def no_service_contact(media,monkeypatch):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**k:calls.append(1))
    yield
    assert calls==[]

def pair(directory,revision,words):
    from lib.pathsafe import sha256_file
    path=directory/(revision+'-'+uuid.uuid4().hex+'.mp4');path.write_bytes(words)
    report={'edit_revision_sha256':revision,'comparison':'after','output_sha256':sha256_file(path),'true_peak_dbtp':-5}
    path.with_suffix('.review.json').write_text(json.dumps(report))
    return path

def world(media):
    from lib import cutting_view as c,trailer_edit as e
    root,shot,*_=media;old=c.save_cut(root,cut(shot),e.read_revision(root)['edit_revision_sha256'])
    directory=root/'artifacts/cut-previews';directory.mkdir()
    prior=pair(directory,old['edit_revision_sha256'],b'An invented earlier local preview.')
    value=c.editable(old['timeline']);value['picture'][0]['colour']['warmth']=10
    saved=c.save_cut(root,value,old['edit_revision_sha256']);target=pair(directory,saved['edit_revision_sha256'],b'An invented current local preview.')
    mix=target.with_suffix('.wav');mix.write_bytes(b'Invented temporary sound.')
    parts=target.with_name(target.stem+'-parts');parts.mkdir();stem=parts/'speech.wav';stem.write_bytes(b'Invented temporary stem.')
    refs={'work_directory':str(parts),'temporary_files':[str(mix),str(stem)]}
    return root,prior,target,mix,refs

def approve_preview(root,path,monkeypatch,*,interrupt=False):
    from lib import gates,receipts
    from lib.pathsafe import sha256_file
    from lib.canonical_json import record_sha256
    record={'export_sha256':sha256_file(path)};digest=record_sha256(record)
    with gates.handler_context(),monkeypatch.context() as patch:
        token=gates.mint_gate_token(root.name,'compose','trailer-stage',digest,user_response='approve')
        if interrupt:
            def stopped(*a,**kw):raise OSError('Invented stop after consumption and before receipt commit.')
            patch.setattr(receipts,'_commit_approval',stopped)
        return receipts.record_human_approval(root,root.name,'compose','trailer-stage',record,token,'artifact_review',
            artifact={'artifact_type':'trailer_stage','artifact_version':'1.0','artifact_digest':digest,'migration_status':'reviewed'})

@pytest.mark.parametrize('fault',['approval-ledger','generation-ledger','consumed-approval'])
def test_unverifiable_or_pending_authority_clears_nothing(media,monkeypatch,fault):
    from lib import gates,receipts,cut_preview_files as cleanup
    from lib.pathsafe import sha256_file
    root,prior,target,mix,refs=world(media)
    if fault=='consumed-approval':
        with pytest.raises(OSError,match='Invented stop'):approve_preview(root,prior,monkeypatch,interrupt=True)
        assert gates.wal_entries()
    else:
        if fault=='approval-ledger':receipt=approve_preview(root,prior,monkeypatch);ledger=gates.ledger_path()
        else:
            now=datetime.now(timezone.utc).isoformat()
            receipt=receipts.record_generation(root,execution_id=uuid.uuid4().hex,tool='invented_local_preview',
                normalized_inputs_hash='a'*64,output_sha256=sha256_file(prior),cost_usd=0,started_at=now,finished_at=now,
                generator_kind='local',local_tool='ffmpeg',local_tool_version='invented',parameters_hash='b'*64)
            ledger=gates.generation_ledger_path()
        rows=[json.loads(s) for s in ledger.read_text().splitlines()]
        for row in rows:
            if row['receipt_id']==receipt['receipt_id']:row['record_sha256' if fault=='approval-ledger' else 'output_sha256']='0'*64
        ledger.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    directory=target.parent;before={str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='Nothing was cleared'):cleanup.finish_preview(root,target,mix,refs)
    assert {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}==before
    if fault=='consumed-approval':assert gates.wal_entries()

@pytest.mark.parametrize('fault',['foreign-revision','wrong-fingerprint','kept-take'])
def test_unknown_or_kept_material_under_preview_names_is_left_alone(media,fault):
    from lib import cut_preview_files as cleanup
    from lib.pathsafe import sha256_file
    root,prior,target,mix,refs=world(media)
    if fault=='foreign-revision':
        prior=pair(prior.parent,'f'*64,b'An invented foreign preview.')
    elif fault=='wrong-fingerprint':
        path=prior.with_suffix('.review.json');value=json.loads(path.read_text());value['output_sha256']='f'*64;path.write_text(json.dumps(value))
    else:
        prior.write_bytes((root/media[2]['path']).read_bytes());path=prior.with_suffix('.review.json')
        value=json.loads(path.read_text());value['output_sha256']=sha256_file(prior);path.write_text(json.dumps(value))
    report=prior.with_suffix('.review.json');before=(prior.read_bytes(),report.read_bytes())
    cleanup.finish_preview(root,target,mix,refs)
    assert prior.exists() and report.exists()
    assert (prior.read_bytes(),report.read_bytes())==before
