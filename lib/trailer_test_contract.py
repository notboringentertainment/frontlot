"""The trailer test's stage checks. Individual purchase guards stay separate."""


def enforce(root,stage,status,artifacts):
    if status not in ('completed','awaiting_human'):return
    if stage=='canon_ingest':
        from lib.story_ingest import material
        material(root,artifacts['canon_packet'])
        return
    if stage=='script':
        from lib.trailer_paper_review import require_approval
        from lib.trailer_records import paper_artifacts
        require_approval(root)
        if artifacts!=paper_artifacts(root):raise ValueError('The saved paper-cut records changed. Save and review that version again.')
        return
    if stage in ('edit','colour_mix'):
        from lib import trailer_delivery as delivery
        from lib.trailer_contract import validate_stage_contract,require_contract
        (delivery.require_rough if stage=='edit' else delivery.require_colour)(root)
        validate_stage_contract(require_contract(root),'edit',artifacts)
        return
    if stage=='compose':
        from lib import trailer_delivery as delivery
        identity=artifacts['render_report']['metadata']['delivery_id']
        if artifacts!=delivery._artifacts(root,identity):
            raise ValueError('The final stage must use this production’s exact measured export record. Make and review the file in Delivery workspace.')
        return
    raise ValueError('Choose one of the five signed trailer-test reviews.')
