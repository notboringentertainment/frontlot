"""Free local paper-cut operation; no generation or approval commands."""
import argparse
import json
from pathlib import Path
from lib.run_common import resolve_project_root
from lib.trailer_edit import save_revision, paper_state


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--project',required=True)
    p.add_argument('--paper')
    p.add_argument('--expected-revision')
    args=p.parse_args()
    root=resolve_project_root(args.project)
    result=save_revision(root,json.loads(Path(args.paper).read_text()),args.expected_revision) if args.paper else paper_state(root)
    print(json.dumps(result))


if __name__=='__main__': main()
