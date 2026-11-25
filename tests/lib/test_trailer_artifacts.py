"""The optional trailer envelope is strict without changing old artifacts."""
import copy
import pytest
import jsonschema
from schemas.artifacts import load_schema

NAMES = ['script', 'scene_plan', 'edit_decisions', 'render_report', 'final_review']

@pytest.mark.parametrize('name', NAMES)
def test_trailer_envelope_exists_and_rejects_long_target(name):
    schema = load_schema(name)
    branch = schema.get('$defs', {}).get('v1_1', schema)
    trailer = branch['properties']['trailer']
    # Envelope validation is independent of each artifact's older required body.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'kind': 'trailer', 'target_duration_seconds': 121,
            'fps': 24, 'approval_points': {'edit': 'rough_cut', 'compose': 'final_export'}}, trailer)

@pytest.mark.parametrize('name,required', [
    ('edit_decisions', ['edit_revision_sha256']),
    ('render_report', ['edit_revision_sha256', 'export_sha256']),
    ('final_review', ['edit_revision_sha256', 'export_sha256']),
])
def test_trailer_finishing_requires_content_bindings(name, required):
    schema = load_schema(name)
    assert schema['allOf'][0]['then']['required'] == required

def test_trailer_script_cannot_exceed_two_minutes():
    from schemas.artifacts import validate_artifact
    with pytest.raises(jsonschema.ValidationError, match='120'):
        validate_artifact('script', {'version':'1.0','title':'Night Window',
            'sections':[{'id':'s1','text':'Wait.','start_seconds':0,'end_seconds':28}], 'total_duration_seconds':121,
            'trailer': {'kind':'trailer','target_duration_seconds':28,'fps':24,
                'approval_points':{'edit':'rough_cut','compose':'final_export'}}})
