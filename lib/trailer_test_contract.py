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
    # Closed until each distinct signed review is connected below. A new
    # manifest must never fall through to bare-flag stage completion.
    raise ValueError('This trailer-test review needs its verified signed stage record before continuing.')
