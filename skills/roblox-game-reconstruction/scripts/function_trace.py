"""Recorded function analysis checks, separate from CFG or behavior certificates."""


def validate_trace(fn, evidence, error, location):
    """Check a trace's shape and own-body citations using the caller's evidence checker.

    Neither prose entailment nor exhaustive branch coverage can be checked here.
    A rationale, linked subject, or module assignment is not a function trace.
    """
    if fn.get('status') not in ('TRACED', 'REVIEWED'):
        return
    fid = fn.get('function_id')
    trace = fn.get('trace')
    if not isinstance(trace, dict):
        error('function_trace_required', location)
        return
    for key in ('inputs', 'outputs', 'limits'):
        value = trace.get(key)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            error('function_trace_text_array_required', location + '.trace.' + key)
    steps = trace.get('steps')
    if not isinstance(steps, list) or not steps:
        error('function_trace_steps_required', location + '.trace.steps')
        return
    for i, step in enumerate(steps):
        pos = f'{location}.trace.steps[{i}]'
        if not isinstance(step, dict):
            error('function_trace_step_invalid', pos)
            continue
        if not isinstance(step.get('action'), str) or not step['action'].strip():
            error('function_trace_action_required', pos)
        evs = step.get('evidence')
        if not isinstance(evs, list) or not evs:
            error('function_trace_evidence_required', pos)
            continue
        # An unrelated function or null mapping must not support this body's status.
        for ev in evs:
            if not isinstance(ev, dict) or ev.get('function_id') != fid:
                error('function_trace_wrong_body', pos)
        evidence(step, pos)
