"""Invented syllable-shaped sound with pauses; no speech service is used."""
import numpy as np

def paused_voice(case,rate=48000):
    t=np.arange(rate*3)/rate
    wave=.12*(np.sin(2*np.pi*127*t)+.5*np.sin(2*np.pi*223*t)+.25*np.sin(2*np.pi*761*t))
    if case=='syllable-gaps':ranges=[(s,s+.16) for s in np.arange(.05,2.8,.38)]
    elif case=='one-second-pause':ranges=[(.1,.3),(.4,.6),(.7,.9),(1.9,2.1),(2.2,2.4),(2.5,2.7)]
    elif case=='padding':ranges=[(.55,.78),(.9,1.13),(1.25,1.48),(1.6,1.83),(1.95,2.18),(2.3,2.45)]
    else:raise ValueError('Choose an invented paused-voice fixture.')
    envelope=np.zeros(len(t))
    for start,end in ranges:
        gate=(t>=start)&(t<end)
        envelope[gate]=np.minimum(1,np.minimum((t[gate]-start)/.005,(end-t[gate])/.005))
    return wave*envelope
