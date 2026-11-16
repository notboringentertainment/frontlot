import React from 'react';
import {AbsoluteFill,Composition,Img,Sequence,registerRoot} from 'remotion';

type Card={start_frame:number;duration_frames:number;background:'black'|'transparent';image:string};
type Props={end:number;cards:Card[];width:number;height:number};
const Cards=({cards}:Props)=><AbsoluteFill>{cards.map((c,i)=><Sequence key={i} from={c.start_frame} durationInFrames={c.duration_frames}>
 <AbsoluteFill style={{backgroundColor:c.background==='black'?'black':'transparent'}}>
  <Img src={c.image} style={{width:'100%',height:'100%'}}/>
 </AbsoluteFill>
</Sequence>)}</AbsoluteFill>;
const Root=()=> <Composition id="TrailerCards" component={Cards} width={640} height={360} fps={24} durationInFrames={24}
 defaultProps={{end:24,cards:[],width:640,height:360}} calculateMetadata={({props})=>({durationInFrames:props.end,width:props.width,height:props.height})}/>;
registerRoot(Root);
