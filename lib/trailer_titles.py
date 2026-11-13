"""Exact, locally typeset cards using the existing signed gate and edit history."""
import copy
import io
import hashlib
import re
import unicodedata
from pathlib import Path

from lib import trailer_edit,receipts
from lib.canonical_json import record_sha256
from lib.paths import REPO_ROOT
from lib.pathsafe import resolve_input,sha256_file,PathSafetyError

FONT_PATH='ink-theater/assets/patrickhand.ttf'
FONT_SHA='0f173b3e6cb6d1af25babf7f0057c5ac4ee11f9992b0469bb817e967ef4ad0fc'
CARD_FIELDS={'id','text','font_path','font_sha256','font_size','start_frame','duration_frames','background','role'}

def fonts():
    return [{'name':'Patrick Hand — licensed for film lettering','path':FONT_PATH,'sha256':FONT_SHA}]

def _font(card):
    if card.get('font_path')!=FONT_PATH or card.get('font_sha256')!=FONT_SHA:
        raise ValueError('This card’s font is missing or changed. Choose the supplied licensed font and review the card again.')
    try:
        path=resolve_input(FONT_PATH,REPO_ROOT)
        if sha256_file(path)!=FONT_SHA:raise ValueError()
        return path
    except (PathSafetyError,OSError,ValueError):
        raise ValueError('The supplied font is missing or changed. Restore the licensed font before signing or playing cards.') from None

def card_png(card):
    """Reuse the title tool's literal PIL renderer, with no model or fallback font."""
    from PIL import Image,ImageFont
    from tools.graphics.title_card import TitleCard
    text=card.get('text')
    if not isinstance(text,str) or not text.strip():raise ValueError('Write the card’s exact words before saving.')
    if len(text)>4000:raise ValueError('This text leaves the safe margins. Add your own line breaks or choose a smaller letter size; nothing was shortened.')
    if any(unicodedata.category(c).startswith('C') and c!='\n' for c in text):
        raise ValueError('The card contains a control character that cannot be shown. Remove that character yourself; the words have not been changed.')
    size=card.get('font_size')
    if type(size) is not int or not 12<=size<=72:raise ValueError('Choose a whole-number letter size from twelve to seventy-two pixels.')
    path=_font(card);font=ImageFont.truetype(str(path),size)
    missing=bytes(font.getmask('\U0010ffff'))
    if any(c not in ' \n' and bytes(font.getmask(c))==missing for c in text):
        raise ValueError('This font cannot show every letter or symbol in the card. Choose different wording yourself or wait for another licensed font; nothing was substituted.')
    params={'text':text,'font_size':size,'color':'#FFFFFF','width':640,'height':360,'alignment':'center','line_spacing':8}
    png=TitleCard()._render(params,path)
    # Check both layout bounds and pixels: a clipped glyph cannot hide beyond
    # the canvas and fool the alpha bounding box.
    from PIL import ImageDraw
    draw=ImageDraw.Draw(Image.new('RGBA',(640,360)))
    bounds=draw.multiline_textbbox((320,180),text,font=font,anchor='mm',align='center',spacing=8)
    if bounds[0]<64 or bounds[1]<36 or bounds[2]>576 or bounds[3]>324:
        raise ValueError('This text leaves the safe margins. Add your own line breaks or choose a smaller letter size; nothing was shortened.')
    return png

def _record(root,card):
    exact=copy.deepcopy(card)
    exact['text_utf8_sha256']=hashlib.sha256(card['text'].encode('utf-8')).hexdigest()
    return {'project_id':Path(root).name,'card':exact,'story_inputs':trailer_edit._inputs(Path(root))}

def status(root,card):
    rows=receipts.verified_approvals(root,'trailer_card',entity_id=card['id'])
    if not rows:return 'Needs your signature'
    latest=rows[-1]
    if latest['record']!=_record(root,card):return 'Needs a fresh look'
    response=latest.get('user_response')
    if response!='approve' and not (isinstance(response,dict) and response.get('answer')=='approved'):
        return 'Needs your signature'
    return 'Signed by you'

def validate_cards(root,cards,end,*,require_approval=True):
    if not isinstance(cards,list) or len(cards)>12:raise ValueError('Use up to twelve text cards in this trailer.')
    seen=set();result=[];intervals=[]
    for row in cards:
        if not isinstance(row,dict) or set(row) not in (CARD_FIELDS,CARD_FIELDS|{'text_utf8_sha256'}):raise ValueError('Save each card’s exact words, font and timing together.')
        if not isinstance(row['id'],str) or not re.fullmatch('[a-z0-9-]{1,80}',row['id']) or row['id'] in seen:
            raise ValueError('Each card needs its own identity. Reopen the saved cut before adding it again.')
        seen.add(row['id'])
        if any(type(row[k]) is not int or row[k]<(1 if k=='duration_frames' else 0) for k in ('start_frame','duration_frames')):
            raise ValueError('Card timing needs whole frames and at least one frame on screen.')
        stop=row['start_frame']+row['duration_frames']
        if stop>end:raise ValueError('This card ends after the cut. Shorten it or move it earlier before saving.')
        if row['background'] not in ('black','transparent') or row['role'] not in ('intertitle','end'):
            raise ValueError('Choose an intertitle or end card, on black or over the picture.')
        if row['role']=='end' and stop!=end:raise ValueError('An end card must finish on the cut’s last frame. Change its timing before saving.')
        if any(row['start_frame']<b and stop>a for a,b in intervals):raise ValueError('Text cards cannot overlap. Move or trim one so both can be read.')
        intervals.append((row['start_frame'],stop));card_png(row)
        if require_approval:
            state=status(root,row)
            if state!='Signed by you':raise ValueError('This card needs a fresh look and your signature before playback. Review its exact words, font and timing.' if state=='Needs a fresh look' else 'This card needs your signature before playback. Review its exact words, font and timing.')
        checked=copy.deepcopy(row)
        checked['text_utf8_sha256']=hashlib.sha256(row['text'].encode('utf-8')).hexdigest()
        result.append(checked)
    if result:
        saved=trailer_edit.read_revision(root)
        if not saved:raise ValueError('Save a checked paper cut before adding cards.')
        paper=copy.deepcopy(saved['paper_cut']);paper['cards']=result
        check=trailer_edit._story_check(root,paper)
        if check['status']!='checked':raise ValueError('Check these card words against the approved story before signing or playback. '+ ' '.join(check['errors']))
    return result

def approval_record(root,identity):
    from lib.footage_requests import require_trailer
    try:require_trailer(root)
    except ValueError:
        raise ValueError('This trailer’s creation record cannot be verified. Restore its signed record before reviewing cards.') from None
    saved=trailer_edit.read_revision(root)
    if not saved or saved.get('inputs')!=trailer_edit._inputs(Path(root)):
        raise ValueError('The story or trailer settings changed. Review the paper cut before signing this card.')
    timeline=saved['timeline'];end=max(x['start_frame']+x['duration_frames'] for x in timeline['picture'])
    rows=validate_cards(root,timeline.get('cards',[]),end,require_approval=False)
    card=next((x for x in rows if x['id']==identity),None)
    if card is None:raise ValueError('This card changed or was removed. Save the cut and prepare a new review.')
    return _record(root,card)

def request_approval(root,identity):
    from lib.media_spending import gate_request
    return gate_request(root,'trailer_card',identity,approval_record(root,identity),
        'Review these exact card words, font and timing. This signature permits no spending and approves no stage.',stage='edit')

def render_layer(root,cards,end,directory):
    """Remotion places literal title-tool PNGs; never downloads a runtime."""
    import base64,json,os,shutil,subprocess,uuid
    modules=Path(os.environ.get('FRONTLOT_REMOTION_MODULES',REPO_ROOT/'remotion-composer'/'node_modules'))
    browser=os.environ.get('FRONTLOT_REMOTION_BROWSER')
    if not browser:
        candidates=[Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
        cache=Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',Path.home()/'Library/Caches/ms-playwright'))
        candidates+=sorted(cache.glob('chromium-*/chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
        browser=next((str(p) for p in candidates if p.is_file()),None)
    if not shutil.which('node') or not (modules/'@remotion'/'renderer').is_dir() or not browser or not Path(browser).is_file():
        raise ValueError('Local title playback needs the installed Remotion tools and browser. Nothing was sent or spent; restore the local renderer before preparing playback.')
    job=directory/('titles-'+uuid.uuid4().hex);job.mkdir()
    props={'end':end,'cards':[{**row,'image':'data:image/png;base64,'+base64.b64encode(card_png(row)).decode()} for row in cards]}
    request=job/'props.json'
    with request.open('x') as f:json.dump(props,f,ensure_ascii=False)
    output=job/'cards.mov'
    args=['node',str(REPO_ROOT/'remotion-composer'/'render-trailer-cards.mjs'),str(modules),str(browser),str(request),str(output),str(job/'bundle')]
    try:subprocess.run(args,capture_output=True,check=True,timeout=240)
    except (OSError,subprocess.SubprocessError) as error:
        with (job/'renderer-diagnostic.txt').open('x') as f:f.write(str(error)+'\n'+str(getattr(error,'stderr',b'').decode(errors='replace')))
        raise ValueError('The local title renderer could not prepare these cards. Nothing was sent or spent. Keep the saved cut and check the local renderer before trying again.') from None
    return output
