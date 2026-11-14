"""Fail-closed attribution for image, video and avatar generation entries.

Display identity is deliberately separate: an unverifiable older film can
keep its board, but unverifiable records never grant network permission.
"""
import contextvars
from pathlib import Path

CAPABILITIES = frozenset({'image_generation','video_generation','avatar'})
CLOSED = 'A matching signed footage request is required. Nothing was sent.'
UNVERIFIED = 'Generation needs one registered production with verifiable signed settings and history. Nothing was sent.'
BAD_PATH = 'Generation must stay inside one registered production, without linked or conflicting production paths. Nothing was sent.'
_CONTEXT = contextvars.ContextVar('verified_generation_production', default=None)


def applies(tool):
    capability=getattr(tool,'capability','')
    runtime=getattr(getattr(tool,'runtime',None),'value',getattr(tool,'runtime',None))
    return capability in CAPABILITIES or (capability in {'enhancement','clip_acquisition','corpus_population'} and runtime!='local')


def _identify(inputs, expected_root=None):
    from tools.audio._legacy_guard import _checked_path, _enclosing_root
    from lib.run_common import resolve_project_root
    if not isinstance(inputs,dict):raise ValueError(UNVERIFIED)
    explicit = _checked_path(inputs['project_dir']) if inputs.get('project_dir') else None
    expected = _checked_path(expected_root) if expected_root is not None else None
    inherited = _CONTEXT.get()
    if explicit is None and expected is None and inherited is not None:expected=inherited
    roots = set()
    paths = [Path.cwd()]
    if explicit is not None:paths.append(explicit)
    if expected is not None:paths.append(expected)
    for key,value in inputs.items():
        if not isinstance(key,str) or key=='project_dir':continue
        if key.endswith(('_path','_file','_dir')) and isinstance(value,(str,Path)):
            if not str(value).startswith(('https://','http://')):paths.append(_checked_path(value))
        elif key.endswith('_paths') and isinstance(value,(list,tuple)):
            paths.extend(_checked_path(v) for v in value if isinstance(v,(str,Path)) and not str(v).startswith(('https://','http://')))
    for path in paths:
        owner=_enclosing_root(_checked_path(path))
        if owner is not None:roots.add(owner)
    if len(roots)!=1:raise ValueError(BAD_PATH if roots else UNVERIFIED)
    root=roots.pop()
    if explicit is not None and explicit!=root:raise ValueError(BAD_PATH)
    if expected is not None and expected!=root:raise ValueError(BAD_PATH)
    if _enclosing_root(root.parent) is not None:raise ValueError(BAD_PATH)
    if _checked_path(resolve_project_root(root.name))!=root:raise ValueError(BAD_PATH)
    for key in ('project_id','production_id','project_name'):
        if inputs.get(key) and inputs[key]!=root.name:raise ValueError(BAD_PATH)
    for key in ('output_path','output_dir','output_file'):
        if inputs.get(key):_checked_path(inputs[key]).relative_to(root)
    return root


def refusal(inputs, expected_root=None, *, tool=None):
    """Return a plain refusal, or establish a positively verified old root."""
    try:root=_identify(inputs,expected_root)
    except ValueError as exc:return str(exc) if str(exc) in (BAD_PATH,UNVERIFIED) else BAD_PATH
    except Exception:return BAD_PATH
    try:
        from lib.supervised_production import is_trailer
        from lib.project_config import load_current_verified_project_config
        from lib.receipts import verified_approvals
        if is_trailer(root):
            # Only the operation that verified a saved signed request can
            # establish this scoped capability. Input flags never grant it.
            try:
                from lib.footage_requests import authorization
                authorization(root,inputs,tool=tool)
            except Exception:return CLOSED
            return None
        from tools.audio._legacy_guard import _checked_path
        for name in ('project.yaml','approvals.jsonl'):_checked_path(root/name)
        config=load_current_verified_project_config(root)
        if config.version not in ('1.0','1.1','1.2'):
            from lib.pipeline_pin import pinned_pipeline
            if not verified_approvals(root,'pipeline_migration'):return UNVERIFIED
            if pinned_pipeline(root,'authored-film').version=='1.6':return CLOSED
    except Exception:return UNVERIFIED
    return None


def protect(fn):
    """Guard execution before optional event instrumentation or provider code."""
    import functools
    import inspect
    if getattr(fn,'_generation_protected',False):return fn
    def enter(self,args,kwargs):
        if not applies(self):return None,None
        inputs=args[0] if args else kwargs.get('inputs')
        if not isinstance(inputs,dict) and _CONTEXT.get() is not None:
            inputs={}
        message=refusal(inputs,tool=getattr(self,'name',None))
        if message:return message,None
        return None,_CONTEXT.set(_identify(inputs))
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(self,*args,**kwargs):
            from tools.base_tool import ToolResult
            message,token=enter(self,args,kwargs)
            if message:return ToolResult(success=False,error=message)
            try:return await fn(self,*args,**kwargs)
            finally:
                if token is not None:_CONTEXT.reset(token)
    else:
        @functools.wraps(fn)
        def wrapper(self,*args,**kwargs):
            from tools.base_tool import ToolResult
            message,token=enter(self,args,kwargs)
            if message:return ToolResult(success=False,error=message)
            try:return fn(self,*args,**kwargs)
            finally:
                if token is not None:_CONTEXT.reset(token)
    wrapper._generation_protected=True
    return wrapper
