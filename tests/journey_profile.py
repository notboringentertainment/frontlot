"""Opt-in local diagnosis of a retained invented journey, under pytest's guard.

Run this explicitly with JOURNEY_DIAGNOSTIC naming a stop record inside the
isolated run parent. Its original temporary signing history must still exist;
missing keys are a refusal, never permission to bypass verification. This
utility is outside pytest's normal test-file pattern and sends nothing.
"""
import cProfile
import json
import os
import pstats
import uuid
from pathlib import Path


def test_read_saved_invented_journey(monkeypatch):
    from tests import conftest as guard
    parent=Path(os.environ['TMPDIR']).resolve().parent
    diagnostic=Path(os.environ['JOURNEY_DIAGNOSTIC']).resolve()
    assert diagnostic.is_relative_to(parent)
    evidence=json.loads(diagnostic.read_text());root=Path(evidence['root']).resolve()
    assert root.is_relative_to(parent)
    locations=evidence['board_locations'][-1]
    for key,label in [('HOME','home'),('OPENMONTAGE_HOME','home'),('OPENMONTAGE_GATES_DIR','signing'),('OPENMONTAGE_METADATA_PATH','metadata')]:
        value=Path(locations[label]).resolve();assert value.is_relative_to(parent)
        monkeypatch.setenv(key,str(value))
    assert not guard._unsafe_locations(os.environ)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    calls=[]
    def refuse(*args,**kwargs):calls.append(1);raise AssertionError('A local review read must never contact a service.')
    monkeypatch.setattr('requests.sessions.Session.request',refuse)
    from lib import cutting_view,trailer_edit
    from backlot.cutting import snapshot
    for label,operation in [('snapshot',lambda:snapshot(root)),('validate',lambda:cutting_view.validate_cut(root,cutting_view.editable(trailer_edit.read_revision(root)['timeline']),verify_files=True))]:
        profile=cProfile.Profile();profile.runcall(operation)
        profile.dump_stats(str(parent/('journey-'+label+'-'+uuid.uuid4().hex+'.profile')))
        print(label);pstats.Stats(profile).sort_stats('cumulative').print_stats(18)
    assert calls==[]
