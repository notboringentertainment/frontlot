"""Independent browser checks for the narrow-window repair."""
import json
import os
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.backlot.signing_outcome_helpers import figures
from tests.backlot.test_first_hands_icon import icon_board
from tests.backlot.test_voice_panel import http, options, priced, project, voice_world


@pytest.fixture(autouse=True)
def short_gates(monkeypatch):
    from tests.backlot.isolation_helpers import signing_directory
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR', str(signing_directory('g')))
    monkeypatch.setenv('FRONTLOT_CLAUDE', '/nonexistent/invented-no-assistant')


def _shot(page, name):
    folder = os.environ.get('GROK_POLISH2_PICTURES')
    if not folder:
        return
    path = Path(folder) / name
    assert not path.exists(), name
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path))


def _metrics(page):
    return page.evaluate("""() => {
      const viewer = document.querySelector('#viewer');
      const narrow = matchMedia('(max-width: 760px)').matches;
      const warning = document.querySelector('#writer-warning');
      const bar = document.querySelector('.bar');
      return {
        narrow,
        scrollY: window.scrollY,
        viewerTop: viewer ? viewer.scrollTop : null,
        docHeight: document.documentElement.scrollHeight,
        innerHeight: window.innerHeight,
        innerWidth: window.innerWidth,
        viewerScroll: viewer ? viewer.scrollHeight : null,
        viewerClient: viewer ? viewer.clientHeight : null,
        bodyOverflow: getComputedStyle(document.body).overflowY,
        viewerOverflow: viewer ? getComputedStyle(viewer).overflowY : null,
        position: narrow ? window.scrollY : (viewer ? viewer.scrollTop : 0),
        room: narrow
          ? document.documentElement.scrollHeight - window.innerHeight
          : (viewer ? viewer.scrollHeight - viewer.clientHeight : 0),
        warning: !!warning,
        barTop: bar ? bar.getBoundingClientRect().top : null,
        warningBottom: warning ? warning.getBoundingClientRect().bottom : null,
        active: document.activeElement ? document.activeElement.tagName + ':' + (document.activeElement.getAttribute('aria-label') || document.activeElement.textContent || '').trim().slice(0, 40) : null,
        trailer: document.querySelector('.table')?.classList.contains('trailer-board') || false
      };
    }""")


def _reset(page):
    page.evaluate("""() => {
      window.scrollTo(0, 0);
      const viewer = document.querySelector('#viewer');
      if (viewer) viewer.scrollTop = 0;
      const active = document.activeElement;
      if (active && active !== document.body) active.blur();
    }""")
    page.wait_for_timeout(50)


def _aim(page):
    box = page.locator('#viewer').bounding_box()
    x = box['x'] + min(140, max(24, box['width'] / 3))
    y = min(box['y'] + 160, box['y'] + box['height'] - 24)
    for _ in range(8):
        kind = page.evaluate("""([x, y]) => {
          const el = document.elementFromPoint(x, y);
          if (!el) return 'none';
          if (el.closest('button,a,input,textarea,select,summary')) return 'control';
          return 'text';
        }""", [x, y])
        if kind == 'text':
            break
        y = min(y + 36, box['y'] + box['height'] - 12)
    page.mouse.move(x, y)
    return x, y


def _position(page):
    return _metrics(page)['position']


def _gesture(page, kind):
    _reset(page)
    x, y = _aim(page)
    if kind == 'wheel':
        page.mouse.wheel(0, 900)
    elif kind == 'trackpad':
        for _ in range(10):
            page.mouse.wheel(0, 40)
            page.wait_for_timeout(16)
        chain = page.evaluate("""([x, y]) => {
          const node = document.elementFromPoint(x, y);
          const rows = [];
          for (let el = node; el; el = el.parentElement) {
            const style = getComputedStyle(el);
            rows.push({tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 40),
              overflow: style.overflowY, top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight});
          }
          return {hit: node ? node.tagName + '.' + String(node.className).slice(0, 40) : null, rows: rows.slice(0, 8)};
        }""", [x, y])
    else:
        page.mouse.click(x, y)
        page.evaluate("""() => { const active = document.activeElement; if (active && active !== document.body) active.blur(); }""")
        page.keyboard.press(kind)
    page.wait_for_timeout(350)
    moved = _position(page)
    row = {'kind': kind, 'position': moved, 'active': _metrics(page)['active'], 'aim': [x, y]}
    if kind == 'trackpad':
        row['chain'] = chain
    return row


def _keys_from_body(page):
    found = {}
    for key in ('PageDown', 'End', 'ArrowDown', 'Space'):
        row = _gesture(page, key)
        found[key] = row['position']
    down = _gesture(page, 'wheel')['position']
    _reset(page)
    page.evaluate("() => { const narrow = matchMedia('(max-width: 760px)').matches; if (narrow) window.scrollTo(0, 500); else document.querySelector('#viewer').scrollTop = 500; }")
    before = _position(page)
    page.evaluate("""() => { const active = document.activeElement; if (active && active !== document.body) active.blur(); }""")
    _aim(page)
    page.keyboard.press('ArrowUp')
    page.wait_for_timeout(350)
    after = _position(page)
    found['ArrowUp_before'] = before
    found['ArrowUp_after'] = after
    found['wheel_for_arrow'] = down
    return found


def _keys_with_viewer_focused(page):
    found = {}
    for key in ('PageDown', 'End', 'ArrowDown', 'Space'):
        _reset(page)
        page.locator('#viewer').focus()
        page.keyboard.press(key)
        page.wait_for_timeout(350)
        found[key] = _position(page)
    _reset(page)
    page.evaluate("() => { const narrow = matchMedia('(max-width: 760px)').matches; if (narrow) window.scrollTo(0, 500); else document.querySelector('#viewer').scrollTop = 500; }")
    before = _position(page)
    page.locator('#viewer').focus()
    page.keyboard.press('ArrowUp')
    page.wait_for_timeout(350)
    found['ArrowUp_before'] = before
    found['ArrowUp_after'] = _position(page)
    return found


def _scroll_suite(page, label):
    opened = _metrics(page)
    wheel = _gesture(page, 'wheel')
    trackpad = _gesture(page, 'trackpad')
    body_keys = _keys_from_body(page)
    viewer_keys = _keys_with_viewer_focused(page)
    _gesture(page, 'wheel')
    _shot(page, f"grok-polish2c-{label}-after-wheel.png")
    return {'opened': opened, 'wheel': wheel, 'trackpad': trackpad, 'body_keys': body_keys, 'viewer_keys': viewer_keys}


def _moved(row, key):
    return row.get(key, 0) > 0


def _shell(page):
    return page.evaluate("""() => {
      const shell = document.querySelector('#gate-terminal-shell');
      const panel = document.querySelector('#gate-sign-panel');
      const box = shell ? shell.getBoundingClientRect() : null;
      const text = shell ? shell.textContent : '';
      const visible = !!(shell && !shell.hidden && box && box.height > 2 && box.bottom > 0 && box.top < innerHeight);
      const start = document.querySelector('.gate-primary');
      const startBox = start ? start.getBoundingClientRect() : null;
      const right = [...document.querySelectorAll('p')].filter(p => p.textContent.includes('panel on the right')).map(p => {
        const b = p.getBoundingClientRect();
        return {top: b.top, bottom: b.bottom, inView: b.top < innerHeight && b.bottom > 0, text: p.textContent.trim()};
      });
      const approve = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Approve and sign');
      const approveBox = approve ? approve.getBoundingClientRect() : null;
      const hit = (rect, node) => {
        if (!rect || !node) return false;
        const el = document.elementFromPoint(rect.x + rect.width / 2, Math.min(Math.max(rect.y + 8, 4), innerHeight - 4));
        return !!(el && (el === node || node.contains(el)));
      };
      return {
        hidden: !shell || shell.hidden,
        visible, text: (text || '').slice(0, 500),
        both: text.includes('Your approval is recorded') && text.includes('Getting the question ready'),
        top: box ? box.top : null, bottom: box ? box.bottom : null, left: box ? box.left : null,
        startInView: !!(startBox && startBox.top < innerHeight && startBox.bottom > 0),
        startTop: startBox ? startBox.top : null,
        approveInView: !!(approveBox && approveBox.top >= 0 && approveBox.bottom <= innerHeight),
        approveHit: hit(approveBox, approve),
        right, scrollY: window.scrollY, viewerTop: document.querySelector('#viewer')?.scrollTop || 0,
        innerHeight, innerWidth
      };
    }""")


def _ordinary(projects):
    film = projects / 'ordinary-gallery'
    shot = film / 'production' / 'shots' / 'shot-a'
    shot.mkdir(parents=True)
    (film / 'project.json').write_text(json.dumps({'title': 'Ordinary invented gallery'}))
    direction = ' '.join(
        f'Invented gallery sentence {number} stays on this ordinary film so the page has room to scroll.'
        for number in range(220))
    row = {
        'event_id': 'rev1', 'at': '2026-01-02T10:00:00+00:00', 'kind': 'brief', 'user_note': 'ok',
        'brief': {'shot_id': 'shot-a', 'direction': direction, 'spend_allowance_usd': 15.0,
                  'max_video_takes': 2, 'allowed_tools': ['kling_reference_video']}}
    (shot / 'history.jsonl').write_text(json.dumps(row) + '\n')


def _open(page, ready, token, project_id):
    page.goto(ready['url'] + 'p/' + project_id + '#k=' + token)
    expect(page.locator('#viewer')).to_be_visible()


@pytest.mark.parametrize('width', [735, 1440])
def test_grok_polish2_walk(tmp_path, voice_world, monkeypatch, width):
    root, posts, _gets = voice_world
    from backlot import voice_panel as voice
    voice.catalogue(root)
    prepared = voice.prepare_voice(root, options())
    assert prepared['request']['prompt'] == 'The gallery is closed.'
    assert posts == []
    pictures = os.environ.get('GROK_POLISH2_PICTURES')
    out = Path(os.environ.get('GROK_POLISH2_OUT') or tmp_path/'observations')
    notes = {'width': width, 'problems': []}
    with icon_board(tmp_path, failed_filing=True) as (projects, counter, start, stop), sync_playwright() as pw:
        live = projects / root.name
        shutil.copytree(root, live)
        _ordinary(projects)
        ready = start(1)
        notes['port_url'] = ready['url']
        notes['board_path'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        browser = pw.chromium.launch(headless=True)
        errors = []
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(ready['url']) else route.abort())
            token = ready['token']
            _open(page, ready, token, root.name)
            expect(page.get_by_role('region', name='Trailer paper cut')).to_be_visible(timeout=15000)
            notes['trailer_clear'] = _scroll_suite(page, f'{width}-trailer-clear')
            _open(page, ready, token, 'ordinary-gallery')
            expect(page.get_by_text('Invented gallery sentence 40', exact=False)).to_be_visible(timeout=15000)
            expect(page.locator('#writer-warning')).to_have_count(0)
            notes['ordinary_clear'] = _scroll_suite(page, f'{width}-ordinary-clear')
            _open(page, ready, token, root.name)
            audio = page.get_by_role('region', name='Trailer audio', exact=True)
            review = audio.get_by_role('button', name='Review and sign this request', exact=True)
            expect(review).to_be_visible(timeout=15000)
            review.click()
            expect(page.get_by_text('The signing panel asks one question at a time',exact=False)).to_be_visible()
            before_start = _shell(page)
            notes['before_start'] = before_start
            _shot(page, f'grok-polish2c-{width}-signing-instruction.png')
            page.get_by_role('button', name='Start signing', exact=True).click()
            expect(page.get_by_role('button', name='Approve and sign', exact=True)).to_be_visible(timeout=20000)
            after_start = _shell(page)
            notes['after_start'] = after_start
            _shot(page, f'grok-polish2c-{width}-signing-question.png')
            notes['approve_reach'] = page.evaluate("""() => {
              const button = [...document.querySelectorAll('button')].find(node => node.textContent.trim() === 'Approve and sign');
              const shell = document.querySelector('#gate-terminal-shell');
              const see = node => {
                if (!node) return null;
                const r = node.getBoundingClientRect();
                return {top: r.top, bottom: r.bottom, inView: r.top >= 0 && r.bottom <= innerHeight};
              };
              const room = () => ({
                scrollY: window.scrollY,
                scrollHeight: document.documentElement.scrollHeight,
                clientHeight: document.documentElement.clientHeight,
                shellScroll: shell ? shell.scrollTop : null,
                shellClient: shell ? shell.clientHeight : null,
                shellHeight: shell ? shell.scrollHeight : null,
                approve: see(button),
                shell: see(shell)
              });
              const natural = room();
              window.scrollBy(0, 500);
              const afterWheel = room();
              if (button) button.scrollIntoView({block: 'center', behavior: 'instant'});
              return {natural, afterWheel, afterCenter: room()};
            }""")
            page.get_by_role('button', name='Approve and sign', exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded', timeout=20000)
            page.evaluate("""() => {
              window.__grokSamples = [];
              const shell = document.querySelector('#gate-terminal-shell');
              const look = () => {
                const box = shell.getBoundingClientRect();
                const text = shell.textContent || '';
                const visible = !shell.hidden && box.height > 2 && box.bottom > 0 && box.top < innerHeight;
                window.__grokSamples.push({
                  visible, hidden: shell.hidden,
                  both: text.includes('Your approval is recorded') && text.includes('Getting the question ready'),
                  waiting: text.includes('Getting the question ready'),
                  recorded: text.includes('Your approval is recorded'),
                  text: text.slice(0, 300)
                });
              };
              new MutationObserver(look).observe(shell, {subtree: true, childList: true, characterData: true, attributes: true});
              window.__grokTimer = setInterval(look, 40);
              look();
            }""")
            page.route('**/gate-sessions', lambda route: route.fulfill(json=[{'request_id': 'invented-ended-session'}]))
            with page.expect_response('**/gate-sessions'):
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            page.get_by_role('button', name='Return to audio', exact=True).click()
            page.wait_for_timeout(6000)
            samples = page.evaluate("() => { clearInterval(window.__grokTimer); return window.__grokSamples; }")
            notes['return_samples'] = {
                'count': len(samples),
                'visible_both': [sample['text'] for sample in samples if sample['visible'] and sample['both']],
                'any_both': any(sample['both'] for sample in samples),
                'visible_waiting': any(sample['visible'] and sample['waiting'] for sample in samples),
            }
            _shot(page, f'grok-polish2c-{width}-after-purchase-return.png')
            page.unroute('**/gate-sessions')
            make = audio.get_by_role('button', name='Make this approved take', exact=True)
            expect(make).to_be_visible(timeout=15000)
            make.click()
            card = audio.locator('[data-audio-request]')
            expect(card.get_by_text('could not be filed')).to_be_visible(timeout=15000)
            page.wait_for_timeout(800)
            alert = card.get_by_role('alert').filter(has_text='could not be filed')
            held_button = card.get_by_role('button', name='Review this held charge', exact=True)
            expect(alert).to_be_visible()
            expect(held_button).to_be_visible()
            boxes = page.evaluate("""() => {
              const card = document.querySelector('[data-audio-request]');
              const alert = card && [...card.querySelectorAll('[role="alert"]')].find(node => node.textContent.includes('could not be filed'));
              const button = [...card.querySelectorAll('button')].find(b => b.textContent.trim() === 'Review this held charge');
              const see = node => {
                if (!node) return null;
                const r = node.getBoundingClientRect();
                const x = r.x + Math.min(r.width / 2, 80);
                const y = r.y + r.height / 2;
                const top = document.elementFromPoint(x, y);
                return {top: r.top, bottom: r.bottom, inView: r.top >= 0 && r.bottom <= innerHeight,
                  hit: !!(top && (top === node || node.contains(top))), text: node.textContent.trim().slice(0, 180)};
              };
              const feedback = document.querySelector('.voice-panel .action-feedback, .action-feedback');
              return {alert: see(alert), button: see(button), feedback: see(feedback),
                scrollY: window.scrollY, scrollHeight: document.documentElement.scrollHeight,
                clientHeight: document.documentElement.clientHeight,
                alerts: card ? [...card.querySelectorAll('[role="alert"]')].map(node => ({top: node.getBoundingClientRect().top, text: node.textContent.trim().slice(0, 80)})) : []};
            }""")
            notes['filing'] = boxes
            _shot(page, f'grok-polish2c-{width}-filing-beside-take.png')
            notes['filing_probe'] = page.evaluate("""() => {
              const card = document.querySelector('[data-audio-request]');
              const alert = card && [...card.querySelectorAll('[role="alert"]')].find(node => node.textContent.includes('could not be filed'));
              if (!alert) return null;
              const before = alert.getBoundingClientRect().top;
              alert.scrollIntoView({block: 'center', behavior: 'instant'});
              const after = alert.getBoundingClientRect();
              return {before, afterTop: after.top, afterBottom: after.bottom, inView: after.top >= 0 && after.bottom <= innerHeight, scrollY: window.scrollY};
            }""")
            held_button.click()
            form_alert = audio.locator('[data-account-review] [role=alert]')
            expect(form_alert).to_be_visible()
            notes['review_click'] = form_alert.evaluate("""node => {
              const r = node.getBoundingClientRect();
              const top = document.elementFromPoint(r.x + 40, r.y + r.height / 2);
              return {top: r.top, bottom: r.bottom, inView: r.top >= 0 && r.bottom <= innerHeight,
                hit: !!(top && (top === node || node.contains(top)))};
            }""")
            ledger = figures(live, 1, fresh=True)
            notes['held_ledger'] = ledger
            amount = min(float(ledger[1]), 0.0002)
            held_button.scroll_into_view_if_needed()
            held_button.evaluate("node => node.dataset.pressMark = 'held'")
            box = held_button.bounding_box()
            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.mouse.down()
            def changed(route):
                if route.request.method != 'GET' or not route.request.url.split('?')[0].endswith('/voice'):
                    route.fallback()
                    return
                data = route.fetch().json()
                data['record_warnings'] = ['Invented changed information during the press.']
                route.fulfill(json=data)
            page.route('**/voice', changed)
            with page.expect_response(lambda response: response.request.method == 'GET' and response.url.split('?')[0].endswith('/voice')):
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(400)
            while_held = page.evaluate("""() => {
              const marked = document.querySelector('[data-press-mark="held"]');
              return {stillThere: !!marked, text: marked ? marked.textContent.trim() : ''};
            }""")
            page.mouse.up()
            page.unroute('**/voice', changed)
            page.wait_for_timeout(300)
            review_clicks = 1
            notes['press'] = {'button_survived': while_held['stillThere'], 'label': while_held['text'], 'clicks': review_clicks}
            expect(form_alert).to_be_visible(timeout=10000)
            audio.get_by_label('The account was charged.', exact=True).check()
            audio.get_by_label('Actual credit replacement dollars', exact=True).fill(f'{amount:.4f}')
            audio.get_by_label('What I checked in the account', exact=True).fill('I checked the invented account and saw this held charge.')
            audio.get_by_role('button', name='Review and sign this account outcome', exact=True).click()
            expect(page.get_by_role('button', name='Start signing', exact=True)).to_be_visible(timeout=15000)
            applies = []
            def fail_apply(route):
                applies.append(route.request.method)
                route.fulfill(status=500, json={'detail': 'Invented apply failure.'})
            page.route('**/voice/resolution/*/apply', fail_apply)
            page.get_by_role('button', name='Start signing', exact=True).click()
            page.get_by_role('button', name='Approve and sign', exact=True).click(timeout=20000)
            expect(page.locator('#gate-terminal-shell')).to_contain_text('automatic follow-up did not finish', timeout=15000)
            page.wait_for_timeout(3000)
            shell_text = page.locator('#gate-terminal-shell').inner_text()
            notes['failed_apply'] = {'attempts': len(applies), 'text': shell_text[:500]}
            page.unroute('**/voice/resolution/*/apply', fail_apply)
            page.get_by_role('button', name='Return to audio', exact=True).click()
            audio.get_by_role('button', name='Apply the signed account outcome', exact=True).click()
            settled = None
            for _ in range(24):
                page.wait_for_timeout(250)
                settled = figures(live, 1, fresh=True)
                if settled[1] == 0 and abs(float(settled[0]) - amount) < 0.0000001:
                    break
            notes['after_apply'] = settled
            notes['calls'] = [json.loads(line)['kind'] for line in counter.read_text().splitlines()] if counter.exists() else []
            stop()
            ready2 = start(2)
            notes['restarted'] = ready2['token'] != token
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            expect(page.locator('#writer-warning')).to_be_visible(timeout=15000)
            notes['trailer_warning'] = _scroll_suite(page, f'{width}-trailer-warning')
            page.get_by_role('button', name='Paper cut workspace', exact=True).click()
            page.wait_for_timeout(300)
            bar = page.locator('.bar').bounding_box()
            warning = page.locator('#writer-warning').bounding_box()
            notes['bar_under_warning'] = {'bar_top': bar['y'], 'warning_bottom': warning['y'] + warning['height']}
            hook = page.get_by_label('Hook', exact=True)
            hook.fill('Unfinished invented hook that must stay.')
            saves = []
            page.on('request', lambda request: saves.append(request.url) if request.method == 'POST' and 'paper-cut' in request.url else None)
            page.get_by_role('button', name='Save a new version', exact=True).click()
            note = page.locator('.action-feedback').filter(has_text='Copy your unfinished words')
            expect(note).to_be_visible(timeout=10000)
            notes['refused_save'] = note.inner_text()
            expect(hook).to_have_value('Unfinished invented hook that must stay.')
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(200)
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            notes['save_posts'] = len(saves)
            _open(page, ready2, token, 'ordinary-gallery')
            expect(page.locator('#writer-warning')).to_be_visible(timeout=15000)
            expect(page.get_by_text('Invented gallery sentence 40', exact=False)).to_be_visible(timeout=15000)
            notes['ordinary_warning'] = _scroll_suite(page, f'{width}-ordinary-warning')
            notes['errors'] = errors
            _judge(notes, width, amount)
        finally:
            notes['errors'] = errors
            out.mkdir(parents=True, exist_ok=True)
            (out / f'walk-{width}.json').write_text(json.dumps(notes, indent=2))
            browser.close()
    assert notes['problems'] == [], '\n'.join(notes['problems'])


def _judge(notes, width, amount):
    for name in ('trailer_clear', 'ordinary_clear', 'trailer_warning', 'ordinary_warning'):
        suite = notes[name]
        opened = suite['opened']
        if opened['room'] < 400:
            notes['problems'].append(f'{name} has too little to scroll: {opened["room"]}')
        if width <= 760 and opened['bodyOverflow'] == 'hidden':
            notes['problems'].append(f'{name} locks the page at {width}')
        if width >= 761 and opened['bodyOverflow'] != 'hidden':
            notes['problems'].append(f'{name} does not keep the wide page in the main column')
        if suite['wheel']['position'] <= 0:
            notes['problems'].append(f'{name} wheel did not scroll')
        # The review explicitly deferred ordinary-film smooth trackpad motion
        # at wide widths; keep its measurements without making it this fix.
        if suite['trackpad']['position'] <= 0 and not (width>=761 and name.startswith('ordinary_')):
            notes['problems'].append(f'{name} trackpad did not scroll')
        for key in ('PageDown', 'End', 'ArrowDown', 'Space'):
            if not _moved(suite['body_keys'], key) and not _moved(suite['viewer_keys'], key):
                notes['problems'].append(f'{name} {key} did not scroll')
            elif not _moved(suite['body_keys'], key) and _moved(suite['viewer_keys'], key):
                notes.setdefault('keys_need_viewer', []).append(f'{name} {key}')
        if suite['body_keys']['ArrowUp_after'] >= suite['body_keys']['ArrowUp_before'] and suite['viewer_keys']['ArrowUp_after'] >= suite['viewer_keys']['ArrowUp_before']:
            notes['problems'].append(f'{name} ArrowUp did not scroll')
    filing = notes.get('filing') or {}
    if not filing.get('alert', {}).get('inView') or not filing.get('alert', {}).get('hit'):
        notes['problems'].append(f'filing explanation is not visible beside the take: {filing.get("alert")}')
    if not filing.get('button', {}).get('inView') or not filing.get('button', {}).get('hit'):
        notes['problems'].append(f'Review this held charge is not visible beside the take: {filing.get("button")}')
    if not notes.get('review_click', {}).get('inView'):
        notes['problems'].append('Review this held charge did not bring the form into view')
    if notes.get('return_samples', {}).get('visible_both'):
        notes['problems'].append('approval and waiting question showed together')
    failed = notes.get('failed_apply') or {}
    if failed.get('attempts') != 1:
        notes['problems'].append(f'automatic apply attempts: {failed.get("attempts")}')
    text = failed.get('text') or ''
    if "Couldn't sign" in text or 'Try signing again' in text:
        notes['problems'].append('failed automatic step offered signing again')
    if 'automatic follow-up did not finish' not in text:
        notes['problems'].append('failed automatic step hid the recorded signature')
    after = notes.get('after_apply') or []
    expected = [amount, 0, round(1 - amount, 10)]
    if len(after) != 3 or any(abs(float(got) - want) > 0.0000001 for got, want in zip(after, expected)):
        notes['problems'].append(f'money after one apply: {after}, expected {expected}')
    if notes.get('calls') != ['speech']:
        notes['problems'].append(f'service calls: {notes.get("calls")}')
    if 'Reload' in (notes.get('refused_save') or ''):
        notes['problems'].append('refused save still says reload')
    if 'Copy your unfinished words' not in (notes.get('refused_save') or ''):
        notes['problems'].append('refused save lost the copy instruction')
    if notes.get('save_posts') != 1:
        notes['problems'].append(f'refused save was sent {notes.get("save_posts")} times')
    bar = notes.get('bar_under_warning') or {}
    if bar.get('bar_top') is None or bar['bar_top'] < bar['warning_bottom'] - 1:
        notes['problems'].append(f'top bar slid under the warning: {bar}')
    press = notes.get('press') or {}
    if not press.get('button_survived'):
        notes['problems'].append(f'a press was lost when the audio changed: {press}')
    reach = notes.get('approve_reach') or {}
    after_center = (reach.get('afterCenter') or {}).get('approve') or {}
    if not after_center.get('inView'):
        notes['problems'].append(f'Approve and sign cannot be scrolled into view: {reach}')
    if notes.get('errors'):
        notes['problems'].append(f'page errors: {notes["errors"]}')
    notes['amount'] = amount
