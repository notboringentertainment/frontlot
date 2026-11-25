"""Small local checks of every final picture frame against the saved cut.

This detects black and unchanged picture, not faces, story or taste. The
full-size boundary stills remain the writer's evidence for those judgements.
"""
import io
from pathlib import Path
import numpy as np
from PIL import Image
from lib import programs
from lib.pathsafe import resolve_input
from lib.trailer_finishing import time_words

WIDTH,HEIGHT=96,54

def _decode(path,grade=None):
    filters=f'fps=24,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2'
    if grade:filters+=','+grade
    result=programs.run(['ffmpeg','-v','error','-i',str(path),'-an','-vf',
        filters+',format=gray',
        '-f','rawvideo','pipe:1'],capture_output=True,timeout=180)
    size=WIDTH*HEIGHT
    if result.returncode or not result.stdout or len(result.stdout)%size:
        raise ValueError('The final picture could not be examined. Keep the saved cut and make the file again.')
    return np.frombuffer(result.stdout,dtype=np.uint8).reshape(-1,HEIGHT,WIDTH).copy()

def _black(frame):
    return bool(np.mean(frame<12)>.99)

def _difference(a,b):
    return float(np.mean(np.abs(a.astype(float)-b.astype(float))))

def _runs(mask):
    result=[];start=None
    for index,value in enumerate([*mask,False]):
        if value and start is None:start=index
        if not value and start is not None:result.append((start,index));start=None
    return result

def review_picture(root,path,timeline,boundaries):
    root=Path(root);actual=_decode(path)
    end=max(p['start_frame']+p.get('duration_frames',p['source_out_frame']-p['source_in_frame']) for p in timeline['picture'])
    if len(actual)!=end:raise ValueError('The measured picture length differs from the saved cut. Make the file again.')
    sources={};cards={};card_matches={};expected={}
    from lib.trailer_titles import card_png
    def card_at(frame):
        return next((r for r in timeline.get('cards',[]) if r['background']=='black' and r['start_frame']<=frame<r['start_frame']+r['duration_frames']),None)
    def card_image(row):
        if row['id'] not in cards:
            lettering=Image.open(io.BytesIO(card_png(row,resolution=(1920,1080))))
            image=Image.new('RGBA',lettering.size,'black');image.alpha_composite(lettering)
            cards[row['id']]=np.array(image.convert('L').resize((WIDTH,HEIGHT),Image.Resampling.BILINEAR))
        return cards[row['id']]
    def matching_card(frame):
        if frame not in card_matches:
            row=card_at(frame)
            if row is None:card_matches[frame]=False
            else:
                image=card_image(row);ink=image>40
                # A black card is deliberate only when its lettering is still
                # there. A missing card must not excuse an all-black export.
                card_matches[frame]=bool(ink.any() and np.mean(actual[frame][ink])>=np.mean(image[ink])*.6 and _difference(actual[frame],image)<8)
        return card_matches[frame]
    def wanted(frame):
        if frame in expected:return expected[frame]
        row=card_at(frame)
        if row is not None:value=card_image(row)
        else:
            shot=next((p for p in timeline['picture'] if p['start_frame']<=frame<p['start_frame']+p.get('duration_frames',p['source_out_frame']-p['source_in_frame'])),None)
            if shot is None:value=np.zeros((HEIGHT,WIDTH),dtype=np.uint8)
            else:
                source=resolve_input(shot['path'],root)
                from lib.trailer_finishing import NEUTRAL
                from tools.enhancement.color_grade import ColorGrade
                colour=shot.get('colour',NEUTRAL);key=(source,tuple(colour[k] for k in sorted(colour)))
                if key not in sources:
                    import json
                    probe=json.loads(programs.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-of','json',str(source)],timeout=30))['streams'][0]
                    grade=ColorGrade()._build_filter({'trailer_adjustments':colour,'source_colour':probe})
                    sources[key]=_decode(source,grade)
                index=shot['source_in_frame']+frame-shot['start_frame']
                if index>=len(sources[key]):raise ValueError('A saved source range could not be examined. Choose a valid kept take and save the cut again.')
                value=sources[key][index]
        expected[frame]=value
        return value
    black=[_black(f) for f in actual]
    unexpected_black=[yes and not matching_card(i) and not _black(wanted(i)) for i,yes in enumerate(black)]
    if all(black) and not all(matching_card(i) for i in range(end)):unexpected_black=[True]*end
    unchanged=[False]+[_difference(actual[i-1],actual[i])<.25 for i in range(1,end)]
    unexpected_freeze=[False]*end
    runs=[];first=0
    for frame in range(1,end+1):
        if frame==end or _difference(actual[first],actual[frame])>.35:
            if frame-first>=6:runs.append((first,frame))
            first=frame
    for first,stop in runs:
        if stop-first<6:continue
        # A source hold or a saved static card is legitimate inside a moving
        # cut. A renderer holding a moving source or crossing a new shot is not.
        for frame in range(max(1,first+1),stop):
            if matching_card(frame) and matching_card(frame-1):continue
            if _difference(wanted(first),wanted(frame))>.6:
                for k in range(first,stop):unexpected_freeze[k]=True
                break
    for frame in boundaries:
        if frame and unchanged[frame] and not (matching_card(frame) and matching_card(frame-1)) and _difference(wanted(frame-1),wanted(frame))>.6:
            unexpected_freeze[frame-1]=unexpected_freeze[frame]=True
    if runs==[(0,end)] and not all(matching_card(i) for i in range(end)):
        unexpected_freeze=[True]*end
    issues=[];intervals=[]
    for kind,mask in [('black',unexpected_black),('frozen',unexpected_freeze if not all(unexpected_black) else [False]*end)]:
        for start,stop in _runs(mask):
            intervals.append({'kind':kind,'start_frame':start,'end_frame':stop})
            issues.append(f'The final picture is {kind} from {time_words(start)} to {time_words(stop)}, where the saved cut does not call for it. Check those pictures and make the final file again.')
    boundary_checks=[{'frame':f,'seconds':f/24,'examined':True,'black':black[f],
                      'frozen_from_previous':unchanged[f],'matches_saved_black_card':matching_card(f)} for f in boundaries]
    return {'status':'revise' if issues else 'pass','frames_examined':end,
            'black_frames_measured':sum(black),'unchanged_frame_pairs':sum(unchanged),
            'measurement_size':[WIDTH,HEIGHT],'intervals':intervals,'boundary_checks':boundary_checks,'issues':issues,
            'scope':'Every output frame was checked locally for black and unchanged picture, against the saved source ranges, gaps and black cards. These checks do not certify story, faces or exact lettering.'}
