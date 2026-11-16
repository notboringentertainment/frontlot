"""Compare retained pytest XML by case name without needing Git history."""
import argparse
import collections
import json
import xml.etree.ElementTree as xml
from pathlib import Path


def read(path):
    cases=collections.defaultdict(list)
    for row in xml.parse(path).iter('testcase'):
        status=next((name for name in ('failure','error','skipped') if row.find(name) is not None),'passed')
        cases[(row.get('classname','')+'::'+row.get('name',''))].append(status)
    return {key:sorted(value) for key,value in cases.items()}


def compare(base,head):
    return {'base_xml_entries':dict(collections.Counter(s for rows in base.values() for s in rows)),
            'head_xml_entries':dict(collections.Counter(s for rows in head.values() for s in rows)),
            'added':{key:head[key] for key in head.keys()-base.keys()},
            'removed':{key:base[key] for key in base.keys()-head.keys()},
            'changed':{key:{'base':base[key],'head':head[key]} for key in base.keys()&head.keys() if base[key]!=head[key]},
            'head_failures':{key:rows for key,rows in head.items() if any(s in ('failure','error') for s in rows)}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('base');parser.add_argument('head');parser.add_argument('output')
    args=parser.parse_args()
    with Path(args.output).open('x') as stream:json.dump(compare(read(args.base),read(args.head)),stream,sort_keys=True,indent=2)
