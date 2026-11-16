"""Save the actual browser actions and their waits for a visible journey."""
import json
import zipfile
from pathlib import Path


def save_action_log(trace, destination):
    calls={}
    with zipfile.ZipFile(trace) as archive:
        for name in archive.namelist():
            if not name.endswith('.trace'):continue
            for line in archive.read(name).decode().splitlines():
                row=json.loads(line)
                identity=row.get('callId')
                if row.get('type')=='before':
                    calls[identity]={'action':row.get('method'),'parameters':row.get('params'),
                                     'started_ms':row.get('startTime'),'waits':[]}
                elif identity in calls:
                    if row.get('type')=='log':calls[identity]['waits'].append(row.get('message'))
                    elif row.get('type')=='after':
                        calls[identity]['finished_ms']=row.get('endTime')
                        calls[identity]['error']=row.get('error')
    with Path(destination).open('x') as output:
        json.dump(list(calls.values()),output,indent=2)
