"""Check the browser's local video clock without any app or provider traffic."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect


def test_local_delivery_video_clock(tmp_path):
    from lib import programs
    supplied = os.environ.get('DELIVERY_MEDIA_SAMPLE')
    if supplied:
        movie = Path(supplied).resolve()
        assert movie.is_relative_to(Path(os.environ['TMPDIR']).resolve().parent)
    else:
        movie = tmp_path / 'invented-local.mp4'
        programs.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc=size=160x90:rate=24',
                      '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','0.5',
                      '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(movie)],check=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1440,'height':900})
            def local(route):
                if route.request.url.endswith('/movie.mp4'):
                    route.fulfill(body=movie.read_bytes(),content_type='video/mp4')
                else:
                    route.fulfill(body='<video controls preload="auto" src="/movie.mp4"></video>',content_type='text/html')
            page.route('**/*',local)
            page.goto('http://127.0.0.1/invented-local-player')
            video = page.locator('video')
            expect(video).to_have_js_property('readyState',4)
            # This isolates video decoding from the machine's audio hardware.
            # Audio samples and loudness are checked by the Delivery contract.
            video.evaluate('(v)=>{v.muted=true;return v.play();}')
            try:
                page.wait_for_function('(v)=>v.ended',arg=video.element_handle(),timeout=10000)
            except Exception:
                print('LOCAL VIDEO',video.evaluate('(v)=>({time:v.currentTime,duration:v.duration,rate:v.playbackRate,muted:v.muted,paused:v.paused,ready:v.readyState,seeking:v.seeking,visibility:document.visibilityState,error:v.error?.message})'))
                raise
            assert video.evaluate('(v)=>v.currentTime>=v.duration')
        finally:
            browser.close()
