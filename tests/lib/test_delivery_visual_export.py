"""A measured visual fault must block the real export's signing route."""
import subprocess
import pytest
from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.lib.test_delivery_export import approve_rough
from tests.lib.test_delivery_visual_review import encode
from tests.title_renderer import require_title_renderer
from tests.backlot.test_voice_panel_browser import board,short_signing_folder

@pytest.mark.parametrize('kind',['black-cut','freeze-cut','all-black'])
def test_real_export_with_normal_sound_refuses_bad_picture(delivery_cut,monkeypatch,kind):
    from lib import trailer_delivery as d
    root,calls,audio=delivery_cut;saved=approve_rough(root);render=d.render_final
    def damaged(*args):
        original,review,refs=render(*args)
        silent=encode(original,original.parent/'damaged-picture.mp4',kind)
        target=original.parent/'damaged-with-original-sound.mp4'
        subprocess.run(['ffmpeg','-v','error','-n','-i',str(silent),'-i',str(original),'-map','0:v:0','-map','1:a:0','-c','copy',str(target)],check=True)
        return target,review,refs
    monkeypatch.setattr(d,'render_final',damaged)
    item=d.export(root,saved['edit_revision_sha256']);review=item['final_review']
    assert review['metadata']['measurements']['on_target']
    assert review['status']=='revise' and review['checks']['visual_spotcheck']['issues']
    assert review['metadata']['visual_measurements']['frames_examined']==72
    assert d.final_state(root,item['id'])=='Final file needs changes'
    assert d.SPEECH_NOTES not in review['issues_found']
    with pytest.raises(ValueError,match='picture or sound check needs changes'):d.request_final(root,item['id'])
    if kind=='all-black':
        from playwright.sync_api import sync_playwright,expect
        with board(root,monkeypatch) as url,sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
                page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
                panel=page.get_by_role('region',name='Trailer delivery',exact=True)
                expect(panel).to_contain_text('Final file needs changes',timeout=30000)
                expect(panel.get_by_role('button',name='Review and sign final approval',exact=True)).to_be_disabled()
                gallery=panel.get_by_role('region',name='Pictures from the final file')
                expect(gallery.locator('img')).to_have_count(16)
                expect(gallery.locator('img').first).to_have_js_property('naturalWidth',1920,timeout=30000)
                assert page.request.get(url+f'/api/project/{root.name}/delivery/{item["id"]}/frame/-1').status==400
                import os
                if os.environ.get('DELIVERY_CAPTURE'):
                    from pathlib import Path
                    gallery.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                    page.screenshot(path=str(Path(os.environ['DELIVERY_CAPTURE'])/'round2-black-file-refused.png'))
                assert not errors
            finally:browser.close()
    assert len(calls['posts'])==3 and len(audio)==1
