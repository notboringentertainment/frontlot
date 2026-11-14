"""Editing keeps invisible source bytes and spending repeats source warnings."""
import hashlib,json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,shot_picture,sign_audio
from tests.backlot.test_footage_controls_browser import browser_board

def paste(area,value):
    area.evaluate('''(e,value)=>{const data=new DataTransfer();data.setData('text/plain',value);e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}''',value)

@pytest.mark.parametrize('case',['paste-end','paste-middle','file-edit','old-mac','mixed','partial-paste'])
def test_edits_preserve_the_supplied_line_endings_or_refuse_before_save(root,tmp_path,monkeypatch,isolated_standins,case):
    ending='\r' if case=='old-mac' else '\r\n'
    original='\ufeff  A gallery waits.'+ending+'Keep the light on.'+ending+'A cafe\u0301 stays quiet.  '+ending
    if case=='mixed':original='\ufeff  A gallery waits.\r\nKeep the light on.\rA cafe\u0301 stays quiet.  \n'
    chosen=tmp_path/'Invented exact story.txt';chosen.write_bytes(original.encode())
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);panel=page.get_by_role('region',name='Trailer story',exact=True)
            area=panel.get_by_label('Exact story text',exact=True)
            if case=='file-edit':
                panel.get_by_label('Choose a text file',exact=True).set_input_files(str(chosen))
                expect(panel.get_by_role('alert')).to_contain_text('File chosen')
            else:paste(area,original)
            area.focus()
            if case in ('paste-middle','file-edit','mixed','partial-paste'):
                area.evaluate('(e)=>{const i=e.value.indexOf("gallery");e.setSelectionRange(i,i+7);}');expected=original.replace('gallery','hallway')
                if case=='partial-paste':paste(area,'hallway')
                else:page.keyboard.insert_text('hallway')
            else:
                area.evaluate('(e)=>e.setSelectionRange(e.value.length,e.value.length)');page.keyboard.insert_text('X');expected=original+'X'
            save=panel.get_by_role('button',name='Save story for review',exact=True)
            if case=='mixed':
                expect(panel.get_by_role('alert')).to_contain_text('mixed line endings')
                expect(save).to_be_disabled()
                assert not (root/'checkpoint_canon_ingest.json').exists()
                shot_picture(page,'mixed-edit-refused',panel.get_by_role('heading').first)
                replacement=tmp_path/'Invented edited mixed story.txt';replacement.write_bytes(expected.encode())
                panel.get_by_label('Choose a text file',exact=True).set_input_files(str(replacement))
                expect(save).to_be_enabled()
            save.click();expect(panel.get_by_role('status')).to_contain_text('Saved')
            sha=hashlib.sha256(expected.encode()).hexdigest()
            assert (root/'sources/story'/(sha+'.txt')).read_bytes()==expected.encode()
            assert chosen.read_bytes()==original.encode()
            panel.get_by_role('button',name='Review and sign this story',exact=True).click()
            review=page.get_by_role('region',name='Story approval',exact=True)
            assert review.locator('pre').text_content()==expected
            expect(review).to_contain_text('Mixed line endings' if case=='mixed' else 'old Mac carriage returns' if case=='old-mac' else 'Windows line endings')
            expect(review).to_contain_text('No protected wording or spoilers are designated; only the exact-passage warning applies.')
            shot_picture(page,case+'-signing',review.get_by_role('heading').first)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
            assert (root/'sources/story'/(sha+'.txt')).read_bytes()==expected.encode()
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()

@pytest.mark.parametrize('text',['\u00a0'*8,'\u2003\u2028\u2029','\u200b\u200c\u200d\u2060','\ufeff \u00ad','\ufe0e\ufe0f','\u2800\u3164\uffa0'])
def test_invisible_only_story_is_empty_and_creates_no_record(root,text):
    from lib.story_ingest import supply
    before=(root/'approvals.jsonl').read_bytes()
    with pytest.raises(ValueError,match='words|empty'):supply(root,text.encode())
    assert not (root/'checkpoint_canon_ingest.json').exists()
    assert (root/'approvals.jsonl').read_bytes()==before

def test_applying_unsigned_story_tells_the_true_next_step(root):
    from lib.story_ingest import supply,complete
    supply(root,b'An invented gallery waits.')
    with pytest.raises(ValueError,match='Nothing has been signed.*Review and sign'):complete(root)

def test_non_source_warning_repeats_at_voice_preparation_purchase_and_signing(root,monkeypatch,isolated_standins):
    from lib import story_ingest as story,trailer_edit as edit
    from tests.lib.test_trailer_edit import paper
    from backlot import voice_panel as voice
    gate=story.supply(root,b'The gallery is closed.')['gate'];approve_request(gate,root)
    value=paper();value['shots'][0]['duration_frames']=672
    value['lines']=[{'text':'An honest new sentence for this trailer.','start_frame':0,'duration_frames':72}]
    edit.save_revision(root,value,None)
    req=voice.prepare_prices(root,{'dollars_per_credit':.0002,'speech_credits_per_character':1,
        'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'effect_credits_per_second':40,'expires_at':4102444800})
    approve_request(req,root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);panel=page.get_by_role('region',name='Trailer audio',exact=True)
            form=panel.locator('[data-voice-line="0"]')
            expect(form).to_contain_text('not an exact passage')
            panel.get_by_role('button',name='Load account voices',exact=True).click()
            form.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
            form.get_by_label('I have permission to use this voice.').check()
            form.get_by_role('button',name='Prepare a spending card',exact=True).click()
            card=panel.locator('.voice-spend[data-kind="speech"]')
            expect(card).to_contain_text('not an exact passage')
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            review=page.get_by_role('heading',name='Review and sign audio',exact=True).locator('..')
            expect(review).to_contain_text('not an exact passage')
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled()
            shot_picture(page,'non-source-voice-signing',review.get_by_role('heading').first)
            assert not isolated_standins['audio_posts']
            sign_audio(page)
            card.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(panel.locator('.voice-take[data-kind="speech"]')).to_be_visible(timeout=20000)
            assert len(isolated_standins['audio_posts'])==1 and not page.errors
        finally:browser.close()
