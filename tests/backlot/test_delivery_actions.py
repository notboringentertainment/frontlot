"""Delivery presents one next action and never prints Finder's machine path.

The real board renders controlled local delivery responses. These are
presentation tests, not stand-ins for signature or export verification.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced, http


def snapshot(rough, final=None):
    rows=[]
    if final:
        rows=[{'id':'invented-final','status':final,'path':'artifacts/delivery/trailer.mp4','samples':[],
               'review':{'status':'pass','issues_found':[],
                         'checks':{'technical_probe':{'duration_seconds':3,'file_size_bytes':491460}},
                         'metadata':{'test_only':True,'boundary_samples':[],
                                     'measurements':{'integrated_lufs':-16,'true_peak_dbtp':-1.5}}}}]
    return {'number':1,'revision':'invented-saved-revision','rough_status':rough,'exports':rows}


def setup_delivery(voice_world,monkeypatch):
    from lib import trailer_delivery as delivery
    from tests.tools.test_signed_footage import clip_bytes
    root,posts,gets=voice_world
    served={'value':snapshot('Needs your signature')}
    monkeypatch.setattr(delivery,'snapshot',lambda _:served['value'])
    movie=root/'assets/invented-delivery.mp4';movie.write_bytes(clip_bytes())
    monkeypatch.setattr(delivery,'playback',lambda _,identity:movie)
    return root,posts,gets,served


def test_delivery_buttons_are_spaced_and_next_action_follows_the_main_file(voice_world,monkeypatch):
    root,posts,gets,served=setup_delivery(voice_world,monkeypatch)
    cases=[('Needs your signature',None,'Review and sign the saved rough cut'),
           ('Signed by you',None,'Make the final file'),
           ('Signed by you','Needs your signature','Review and sign final approval'),
           ('Signed by you','Approved by you','Show approved file in Finder'),
           ('Needs a fresh look','Needs a fresh look','Review and sign the saved rough cut')]
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def route(request):
                if request.request.url.startswith(url+'/'):request.continue_()
                else:outside.append(request.request.url);request.abort()
            page.route('**/*',route)
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            endpoint=url+f'/api/project/{root.name}/delivery'
            for index,(rough,final,next_label) in enumerate(cases):
                served['value']=snapshot(rough,final)
                with page.expect_response(lambda response:response.url==endpoint and response.request.method=='GET'):
                    panel.get_by_role('button',name='Refresh delivery',exact=True).click()
                expect(panel.get_by_role('status')).to_contain_text('Rough cut: '+rough+'.')
                if not final:
                    expect(panel.get_by_role('status')).to_contain_text('Make a final file from the signed saved cut.' if rough=='Signed by you' else 'Review and sign the saved rough cut before making a final file.')
                for width in (1440,900):
                    page.set_viewport_size({'width':width,'height':1000})
                    a=panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).bounding_box()
                    b=panel.get_by_role('button',name='Make the final file',exact=True).bounding_box()
                    assert max(b['x']-a['x']-a['width'],b['y']-a['y']-a['height'])>=10,'Delivery buttons touch instead of leaving space.'
                    primary=panel.locator('button[aria-current="step"]')
                    expect(primary).to_have_count(1)
                    expect(primary).to_have_text(next_label)
                    expect(primary).to_be_enabled()
                    assert primary.evaluate('(button)=>getComputedStyle(button).backgroundColor')!=panel.get_by_role('button',name='Refresh delivery',exact=True).evaluate('(button)=>getComputedStyle(button).backgroundColor'),'The next step looks the same as a secondary button.'
                    if final:
                        buttons=panel.locator('[data-delivery]').first.get_by_role('button').all()
                        for left,right in zip(buttons,buttons[1:]):
                            a,b=left.bounding_box(),right.bounding_box()
                            assert max(b['x']-a['x']-a['width'],b['y']-a['y']-a['height'])>=10,'Final-file buttons touch.'
                    folder=os.environ.get('DELIVERY_ACTIONS_CAPTURE')
                    if folder and width==1440:
                        panel.evaluate('(node)=>node.scrollIntoView({block:"start",behavior:"instant"})')
                        path=Path(folder)/f'buttons-next-step-{index}.png';assert not path.exists()
                        page.screenshot(path=str(path))
            assert posts==[] and gets==[] and outside==[] and not errors
        finally:browser.close()


def test_finder_feedback_keeps_the_plain_sentence_and_omits_the_computer_path(voice_world,monkeypatch):
    from lib import trailer_delivery as delivery
    root,posts,gets,served=setup_delivery(voice_world,monkeypatch)
    served['value']=snapshot('Signed by you','Approved by you');revealed=[]
    words='This approved file has been shown in Finder.'
    def reveal(project,identity):
        assert project==root and identity=='invented-final';revealed.append(identity)
        return {'message':words,'path':str(root/'artifacts/delivery/trailer.mp4')}
    monkeypatch.setattr(delivery,'reveal',reveal)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def route(request):
                if request.request.url.startswith(url+'/'):request.continue_()
                else:outside.append(request.request.url);request.abort()
            page.route('**/*',route);page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            panel.get_by_role('button',name='Show approved file in Finder',exact=True).click()
            expect(panel.locator('[data-delivery]').first.get_by_role('note')).to_have_text(words)
            expect(panel.locator('[data-delivery]').first.get_by_role('note')).not_to_contain_text(str(root))
            expect(panel.locator(':scope > [role="note"]')).to_be_empty()
            expect(panel.get_by_role('status')).to_contain_text('Current final file: Approved by you.')
            assert revealed==['invented-final'] and posts==[] and gets==[] and outside==[] and not errors
        finally:browser.close()
