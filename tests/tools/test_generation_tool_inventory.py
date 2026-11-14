"""Registry-wide coverage of the closed trailer generation boundary."""
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from tools.base_tool import ToolResult
from tools.tool_registry import ToolRegistry
from tests.tools.test_footage_generation_refusal import trailer, CLOSED  # noqa: F401


def inventory():
    registry=ToolRegistry()
    with patch.object(ToolRegistry,'_load_dotenv',staticmethod(lambda:None)):
        registry.discover()
    found=[]
    for name in registry.list_all():
        tool=registry.get(name)
        runtime=getattr(tool.runtime,'value',tool.runtime)
        if tool.capability in {'image_generation','video_generation','avatar'} or (
                tool.capability in {'enhancement','clip_acquisition','corpus_population'} and runtime!='local'):
            found.append(tool)
    return sorted(found,key=lambda t:t.name)


TOOLS=inventory()
COUNTS={}


def _provider_body(self,*args,**kwargs):
    _generation_body_calls.append(1)
    return None


@pytest.fixture(scope='session',autouse=True)
def inventory_result(request,tmp_path_factory):
    yield
    target=Path(os.environ.get('FRONTLOT_GENERATION_INVENTORY') or tmp_path_factory.mktemp('generation-inventory')/'counts.json')
    with target.open('x') as f:json.dump(COUNTS,f,indent=2,sort_keys=True)


@pytest.mark.parametrize('tool',TOOLS,ids=lambda t:t.name)
def test_every_registered_media_generator_stops_before_provider_body(trailer,monkeypatch,tool):
    _,inputs=trailer
    body=type(tool).execute
    while hasattr(body,'__wrapped__'):body=body.__wrapped__
    assert not body.__code__.co_freevars, 'Add a counting seam for this new provider closure.'
    body_calls=[]
    monkeypatch.setitem(body.__globals__,'_generation_body_calls',body_calls)
    monkeypatch.setattr(body,'__code__',_provider_body.__code__)
    network=[]
    import requests
    def blocked(*a,**k):network.append(1);raise AssertionError('A provider boundary was reached.')
    monkeypatch.setattr(requests.sessions.Session,'request',blocked)
    result=tool.execute(dict(inputs))
    assert isinstance(result,ToolResult) and not result.success and result.error==CLOSED
    assert body_calls==network==[]
    COUNTS[tool.name]={'provider_body_calls':len(body_calls),'network_calls':len(network)}


@pytest.mark.parametrize('entry',['_generate_openai','_generate_flux','_generate_local'])
def test_direct_picture_generation_methods_are_also_closed(trailer,monkeypatch,entry):
    from tools.graphics.image_gen import ImageGen
    _,inputs=trailer
    calls=[]
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:calls.append(1))
    result=getattr(ImageGen(),entry)(dict(inputs))
    assert not result.success and result.error==CLOSED
    assert calls==[]


@pytest.mark.parametrize('shape',['missing','subfolder','link','conflict','unregistered-copy','nested-older-copy'])
def test_missing_ambiguous_linked_and_copied_productions_never_reach_generation(trailer,tmp_path,monkeypatch,shape):
    import shutil
    from tests.tools._authored_film_helpers import make_verified_project
    from tools.video.kling_reference_video import KlingReferenceVideo
    root,inputs=trailer
    if shape=='missing':inputs={'prompt':'An invented shot.','output_path':str(tmp_path/'unknown.mp4')}
    elif shape=='subfolder':inputs={**inputs,'project_dir':str(root/'assets')}
    elif shape=='link':
        link=tmp_path/'linked';link.symlink_to(root,target_is_directory=True)
        inputs={**inputs,'project_dir':str(link)}
    elif shape=='conflict':inputs={**inputs,'project_id':'a-different-production'}
    elif shape=='unregistered-copy':
        copied=tmp_path/'copy'/root.name;shutil.copytree(root,copied)
        inputs={**inputs,'project_dir':str(copied),'output_path':str(copied/'out.mp4')}
    else:
        holder=tmp_path/'older-holder';holder.mkdir()
        older=make_verified_project(holder,monkeypatch,'old-film')
        parent=root/'inside';parent.mkdir();copied=parent/older.name;shutil.copytree(older,copied)
        monkeypatch.setattr('lib.paths.PROJECTS_DIR',parent)
        inputs={'prompt':'An invented shot.','project_dir':str(copied),'output_path':str(copied/'out.mp4')}
    calls=[]
    fn=KlingReferenceVideo.execute
    while hasattr(fn,'__wrapped__'):fn=fn.__wrapped__
    monkeypatch.setitem(fn.__globals__,'_generation_body_calls',calls)
    monkeypatch.setattr(fn,'__code__',_provider_body.__code__)
    result=KlingReferenceVideo().execute(inputs)
    assert not result.success and calls==[]
