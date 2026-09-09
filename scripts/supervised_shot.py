"""Small CLI for a supervised shot. Only `generate` can submit paid work."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib import supervised_production as production


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('brief', type=Path)
    p.add_argument('--note', required=True)
    for command in ('inspect', 'stop', 'attach', 'select', 'propose', 'request', 'generate'):
        p = sub.add_parser(command)
        p.add_argument('shot_id')
        if command in ('stop', 'attach', 'select', 'propose'):
            p.add_argument('--note', required=True)
        if command == 'attach':
            p.add_argument('source', type=Path)
            p.add_argument('--cost', type=float)
            p.add_argument('--job-id')
            p.add_argument('--reservation-id')
            p.add_argument('--prompt')
        if command == 'select':
            p.add_argument('take_id')
            p.add_argument('--start', type=float)
            p.add_argument('--end', type=float)
        if command in ('request', 'generate'):
            p.add_argument('settings', type=Path, help='JSON with prompt, output_path and provider settings')
        if command == 'generate':
            p.add_argument('--tool', required=True, choices=sorted(production.SUPPORTED_TOOLS))
    args = parser.parse_args()
    root = args.project.resolve()
    if args.command == 'prepare':
        result = production.prepare(root, json.loads(args.brief.read_text()), user_note=args.note)
    elif args.command == 'inspect':
        result = production.inspect_shot(root, args.shot_id)
    elif args.command == 'stop':
        result = production.stop(root, args.shot_id, user_note=args.note)
    elif args.command == 'attach':
        result = production.attach(root, args.shot_id, args.source, user_note=args.note,
                    cost_usd=args.cost, provider_job_id=args.job_id, reservation_id=args.reservation_id,
                    prompt=args.prompt)
    elif args.command == 'select':
        result = production.select(root, args.shot_id, args.take_id, user_note=args.note,
                                   start=args.start, end=args.end)
    elif args.command == 'propose':
        result = production.propose_change(root, args.shot_id, args.note)
    else:
        settings = json.loads(args.settings.read_text())
        inputs = production.request(root, args.shot_id, **settings)
        if args.command == 'request':
            result = inputs
        else:
            from tools.tool_registry import registry
            from lib.shot_allowance import resolve
            registry.discover()
            tool = registry.get(args.tool)
            kind = 'image' if args.tool == 'seedream_image' else 'video'
            resolve(root, inputs, kind=kind).check(root, tool.estimate_cost(inputs), tool=args.tool)
            outcome = tool.execute(inputs)  # Exactly one call; indeterminate jobs are never retried here.
            if not outcome.success:
                raise RuntimeError(outcome.error)
            if kind == 'image':
                # Image outputs already have generation receipts; inspect them directly.
                result = outcome.data
            else:
                result = production.attach(root, args.shot_id, outcome.data['output_path'],
                    user_note='Generated from the saved supervised shot request.',
                    reservation_id=outcome.data['reservation_id'], prompt=inputs['prompt'],
                    references=outcome.metadata.get('references_applied'))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
