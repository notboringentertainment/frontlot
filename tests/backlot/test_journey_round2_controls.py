"""Real board labels and the disabled controls for unrecorded reviews."""
import copy,os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_trimmed_trailer_delivery import trailer_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

def test_unrecorded_rough_blocks_later_controls_and_paper_record_waits(trailer_cut,monkeypatch):
    import backlot.server as server
    from lib import trailer_delivery as delivery,trailer_edit,trailer_paper_review
    root,calls,posts,*_=trailer_cut
    trailer_paper_review.request_review(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    recorded=set();paper_recorded=False;original=server.load_board_state
    def state(project):
        result=original(project)
        result['paper_cut']['whole_review_status']='Needs your signature'
        for stage in result['stages']:
            if stage['name']=='script' and not paper_recorded:stage['status']='in_progress'
            if stage['name'] in ('edit','colour_mix'):stage['status']='completed' if stage['name'] in recorded else 'in_progress'
        return result
    monkeypatch.setattr(server,'load_board_state',state)
    data={'number':1,'revision':'invented-revision','rough_status':'Signed by you','colour_status':'Signed by you',
          'exports':[{'id':'invented-current','status':'Needs your signature','samples':[],
             'review':{'issues_found':[],'checks':{'technical_probe':{'duration_seconds':3,'file_size_bytes':500000}},
                       'metadata':{'test_only':False,'boundary_samples':[],'measurements':{'integrated_lufs':-16,'true_peak_dbtp':-2}}}}]}
    older=copy.deepcopy(data['exports'][0]);older['id']='invented-earlier';data['exports'].append(older)
    monkeypatch.setattr(delivery,'snapshot',lambda *a:{**copy.deepcopy(data),
        'recorded_stages':['canon_ingest']+(['script'] if paper_recorded else [])+sorted(recorded)})
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def route(r):
                if r.request.url.startswith(url+'/'):r.continue_()
                else:outside.append(r.request.url);r.abort()
            page.route('**/*',route);page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery')
            expect(panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True)).to_be_disabled(timeout=120000)
            expect(panel.get_by_role('button',name='Review and sign colour and sound',exact=True)).to_be_disabled(timeout=120000)
            expect(panel.get_by_role('button',name='Make the final file',exact=True)).to_be_disabled()
            expect(panel.locator('[data-delivery]').first.get_by_role('button',name='Review and sign final approval',exact=True)).to_be_disabled()
            expect(page.get_by_role('button',name='Record the signed paper-cut review',exact=True)).to_be_disabled()
            expect(page.locator('body')).to_contain_text('Review the whole paper cut')
            capture=os.environ.get('JOURNEY_R2_CAPTURE')
            if capture:
                panel.scroll_into_view_if_needed();page.screenshot(path=str(Path(capture)/'r2-waiting-for-recording.png'))
            paper_recorded=True;page.reload()
            expect(panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True)).to_be_enabled(timeout=120000)
            recorded.add('edit');page.reload()
            expect(panel.get_by_role('button',name='Review and sign colour and sound',exact=True)).to_be_enabled(timeout=120000)
            expect(panel.get_by_role('button',name='Make the final file',exact=True)).to_be_disabled()
            recorded.add('colour_mix');page.reload()
            expect(panel.get_by_role('button',name='Make the final file',exact=True)).to_be_enabled(timeout=120000)
            panel.get_by_text('Earlier exports',exact=True).click()
            expect(panel.locator('[data-delivery="invented-earlier"]').get_by_role('heading',name='Earlier final trailer file',exact=True)).to_be_visible()
            if capture:page.screenshot(path=str(Path(capture)/'r2-earlier-export-named.png'),full_page=True)
            assert len(calls['posts'])==3 and len(posts)==1 and outside==[] and errors==[]
        finally:browser.close()
