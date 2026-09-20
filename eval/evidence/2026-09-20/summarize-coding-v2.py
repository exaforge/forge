"""Recompute this fixed 10-trial snapshot; reads metadata and makes no model calls."""

import json
import statistics
from collections import defaultdict, Counter
from pathlib import Path

root = Path(__file__).resolve().parent / 'coding-v2'
runs = [(p, json.loads(p.read_text())) for p in root.glob('*/result.json')]
assert len(runs) == 10, len(runs)
groups = defaultdict(list)
for p, r in runs:
    groups[r['profile']].append((p, r))

def med(values):
    values = [x for x in values if x is not None]
    return statistics.median(values) if values else None

def full_input(r):
    u = r['protocol']['usage']
    if 'uncached_input' in r['protocol']['usage_scope']:
        return u['input_tokens'] + u['cached_input_tokens'] + u['cache_creation_input_tokens']
    return u['input_tokens']

out = {}
for profile in ['forge-optimized', 'forge-coding-v2']:
    rows = groups[profile]
    result = {
        'attempts': len(rows), 'passes': sum(r['successful_verified_run'] for _, r in rows),
        'median_wall_seconds': med([r['execution']['duration_ms']/1000 for _, r in rows]),
        'total_wall_seconds': sum(r['execution']['duration_ms']/1000 for _, r in rows),
        'median_successful_wall_seconds': med([r['execution']['duration_ms']/1000 for _, r in rows if r['successful_verified_run']]),
        'total_input_tokens_including_cache': sum(full_input(r) for _, r in rows),
        'total_cached_input_tokens': sum(r['protocol']['usage']['cached_input_tokens'] for _, r in rows),
        'total_output_tokens': sum(r['protocol']['usage']['output_tokens'] for _, r in rows),
        'median_requests': med([r['native']['request_count'] for _, r in rows]),
        'native_usage_coverage': dict(Counter(r['native']['usage_coverage'] for _, r in rows)),
        'per_task': {},
    }
    for task in sorted({r['task'] for _, r in rows}):
        task_rows = [r for _, r in rows if r['task'] == task]
        result['per_task'][task] = {
            'passes': sum(r['successful_verified_run'] for r in task_rows),
            'wall_seconds': sorted(r['execution']['duration_ms']/1000 for r in task_rows),
            'median_wall_seconds': med([r['execution']['duration_ms']/1000 for r in task_rows]),
            'median_input_tokens': med([full_input(r) for r in task_rows]),
        }
    if profile.startswith('forge'):
        first_input, tools, request_fractions, builds, prepares = [], [], [], [], []
        all_attempts = []
        for p, r in rows:
            es = [json.loads(line) for line in p.with_name('events.jsonl').read_text().splitlines()]
            requests = [e for e in es if e['event'] == 'sampler_request_started']
            attempts = [e for e in es if e['event'] == 'sampler_attempt_finished']
            first_input.append(attempts[0]['provider_usage']['input_tokens'])
            tools.append(requests[0]['requested_config']['tool_count'])
            all_attempts.extend(attempts)
            pids = {e['process_id'] for e in requests}
            assert len(pids) == 1
            starts = {(e['process_id'], e['request_id']): e['elapsed_ms'] for e in requests}
            intervals = sorted((starts[(e['process_id'], e['request_id'])], e['elapsed_ms'])
                               for e in es if e['event'] == 'sampler_request_finished')
            total, start, end = 0, *intervals[0]
            for a,b in intervals[1:]:
                if a <= end: end=max(end,b)
                else: total += end-start; start,end = a,b
            total += end-start
            request_fractions.append(total/r['execution']['duration_ms'])
            builds.append(r['native']['phase_summary']['request_build']['median_duration_ms'])
            prepares.append(r['native']['phase_summary']['sampler_prepare']['median_duration_ms'])
        result.update({
            'total_requests': sum(r['native']['request_count'] for _, r in rows),
            'median_first_request_input_tokens': med(first_input),
            'advertised_tool_counts': sorted(set(tools)),
            'median_request_interval_union_fraction_of_wall': med(request_fractions),
            'median_run_median_request_build_ms': med(builds),
            'median_run_median_sampler_prepare_ms': med(prepares),
            'pooled_text_ttft_ms': med([a.get('first_text_ms') for a in all_attempts]),
            'text_ttft_samples': sum(a.get('first_text_ms') is not None for a in all_attempts),
            'pooled_first_generation_ms': med([a.get('first_generation_event_ms') for a in all_attempts]),
            'pooled_request_output_tokens_per_second': med([a.get('end_to_end_output_tokens_per_second') for a in all_attempts]),
            'pooled_generation_window_tokens_per_second': med([a.get('observed_output_tokens_per_second') for a in all_attempts]),
        })
    context_rows = [json.loads(line) for p, _ in rows for line in p.with_name('events.jsonl').read_text().splitlines() if json.loads(line).get('event') == 'request_context']
    result['tool_description_bytes'] = sorted({e['tool_description_bytes'] for e in context_rows})
    result['max_reduced_read_results_in_a_request'] = max((e['reduced_read_results'] for e in context_rows), default=None)
    out[profile] = result
print(json.dumps(out, indent=2))
