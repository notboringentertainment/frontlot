"""The trailer test's stage checks. Individual purchase guards stay separate."""


def enforce(root,stage,status,artifacts):
    if status not in ('completed','awaiting_human'):return
    if stage=='canon_ingest':
        from lib.story_ingest import material
        material(root,artifacts['canon_packet'])
        return
    # Closed until each distinct signed review is connected below. A new
    # manifest must never fall through to bare-flag stage completion.
    raise ValueError('This trailer-test review needs its verified signed stage record before continuing.')
