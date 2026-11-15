"""Icon-launched delivery uses installed programs, before creating export work."""
from pathlib import Path
import json,os,subprocess
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.lib.test_delivery_export import approve_rough
from tests.backlot.test_voice_panel_browser import board,short_signing_folder

@pytest.mark.parametrize('missing',['ffmpeg','ffprobe','node'])
def test_export_preflights_before_creating_work(tmp_path,monkeypatch,missing):
    from lib import trailer_delivery as d
    timeline={'cards':[{'id':'invented-card'}]}
    monkeypatch.setattr(d,'require_rough',lambda *a:({},timeline))
    monkeypatch.setenv('OPENMONTAGE_'+missing.upper(),'/nonexistent/invented-'+missing)
    calls=[]
    def forbidden(*args):
        calls.append(args)
        pytest.fail('An export started before its local program check.')
    monkeypatch.setattr(d,'_job',forbidden)
    monkeypatch.setattr(d,'render_final',forbidden)
    with pytest.raises(ValueError,match=missing+' is missing'):
        d.export(tmp_path,'invented-revision')
    assert calls==[] and list(tmp_path.iterdir())==[]

def test_reveal_uses_installed_program_and_exact_approved_path(tmp_path,monkeypatch):
    from lib import trailer_delivery as d,programs
    movie=tmp_path/'invented-approved.mp4';movie.write_bytes(b'invented local bytes')
    monkeypatch.setattr(d,'final_state',lambda *a:'Approved by you')
    monkeypatch.setattr(d,'playback',lambda *a:movie)
    if not programs.which('open'):pytest.skip('The local Finder program is not installed on this system.')
    launched=[]
    def run(args,*a,**kw):
        launched.append(args);return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    d.reveal(tmp_path,'invented-export')
    assert len(launched)==1 and Path(launched[0][0]).is_absolute()
    assert launched[0][1:]==['-R',str(movie)]

def test_actual_export_with_icon_path(delivery_cut,monkeypatch,short_signing_folder):
    from lib import trailer_delivery as d
    from lib.trailer_titles import renderer_configuration
    from lib.pathsafe import sha256_file
    try:renderer_configuration()
    except ValueError as error:pytest.skip(str(error))
    root,provider,posts=delivery_cut
    saved=approve_rough(root)
    before_posts=(len(provider['posts']),len(posts))
    sources={r['path']:sha256_file(root/r['path']) for r in saved['timeline']['picture']+saved['timeline']['audio']}
    launched=[];original=subprocess.Popen
    def spawn(args,*a,**kw):
        launched.append(str(args[0]))
        return original(args,*a,**kw)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    exported=[];original_export=d.export
    def export(*args):
        assert os.environ['PATH']=='/usr/bin:/bin:/usr/sbin:/sbin'
        item=original_export(*args);exported.append(item);return item
    monkeypatch.setattr(d,'export',export)
    with board(root,monkeypatch) as url:
        request=Request(url+f'/api/project/{root.name}/delivery/export',
            data=json.dumps({'revision':saved['edit_revision_sha256']}).encode(),
            headers={'Content-Type':'application/json','Origin':url,'X-Frontlot-Capability':'invented-browser-proof'})
        before_jobs=set((root/'artifacts/deliveries').glob('*'))
        with monkeypatch.context() as patch:
            patch.setenv('OPENMONTAGE_FFMPEG','/nonexistent/invented-ffmpeg')
            patch.setattr(d,'_job',lambda *a:pytest.fail('The board started export work before its program check.'))
            with pytest.raises(HTTPError) as refused:urlopen(request,timeout=60)
            assert refused.value.code==400 and 'ffmpeg is missing' in json.load(refused.value)['detail']
        assert exported==[] and set((root/'artifacts/deliveries').glob('*'))==before_jobs
        assert (len(provider['posts']),len(posts))==before_posts
        with urlopen(request,timeout=600) as response:
            result=json.load(response)
    assert len(exported)==1 and result['id']==exported[0]['id']
    item=exported[0]
    assert item['final_review']['status']=='pass',item['final_review']['issues_found']
    assert d._probe(d.playback(root,item['id']),72)['resolution']=='1920x1080'
    assert item['final_review']['metadata']['boundary_samples']
    assert {'ffmpeg','ffprobe','node'}<={Path(p).name for p in launched}
    assert all(Path(p).is_absolute() for p in launched)
    assert (len(provider['posts']),len(posts))==before_posts==(3,1)
    assert all(sha256_file(root/p)==digest for p,digest in sources.items())
