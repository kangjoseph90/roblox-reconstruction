"""Candidate correspondence is diagnostic only; it never grants certification."""

import collections
import math

CATEGORIES = [
    'actual_control_flow_mismatch',
    'register_reallocation',
    'compiler_optimization',
    'basic_block_split_merge',
    'conditional_expression',
    'loop_structuring',
    'upvalue_closure_environment',
    'constant_operation_side_effect',
    'unsupported_pattern',
]


def interface(p):
    return (p['params'], p['upvalues'], p['vararg'])


def features(p):
    ops = collections.Counter()
    for i in p['instructions']:
        o = i['op']
        if o in {'MOVE', 'NOP', 'COVERAGE', 'PREPVARARGS', 'LOADN', 'LOADK', 'LOADKX', 'LOADB', 'LOADNIL'}:
            continue
        if o == 'CALLFB':
            o = 'CALL'
        if o.startswith('JUMP'):
            o = 'branch'
        ops[o] += 1
    constants = {
        str(c.get('stringBytes', c.get('importPath', c['text'])))
        for c in p['constants']
        if c['kind'] in {'string', 'import'}
    }
    return ops, constants


def score(a, b):
    x, xc = features(a)
    y, yc = features(b)
    union = sum((x | y).values())
    op = sum((x & y).values()) / union if union else 1
    const = len(xc & yc) / len(xc | yc) if xc | yc else 1
    length = min(len(a['instructions']), len(b['instructions'])) / max(
        1, len(a['instructions']), len(b['instructions'])
    )
    return 0.55 * op + 0.3 * const + 0.15 * length


def candidates(original, recovered, index, limit=3):
    a = original['protos'][index]
    if index == original['mainProto']:
        return [(recovered['mainProto'], 1.0, 'module_entry')]
    options = [p for p in recovered['protos'] if interface(a) == interface(p)]
    named = [p for p in options if a['name'] and p['name'] == a['name']]
    if named:
        options = named
        basis = 'debug_name_plus_interface'
    else:
        basis = 'diagnostic_similarity_only'
    ranked = sorted(((p['index'], score(a, p), basis) for p in options), key=lambda x: (-x[1], x[0]))
    return [r for r in ranked[:limit] if r[1] >= 0.3]


def classify(original, recovered, proof, accepted=False):
    reason = proof.get('reason', '')
    functions = proof.get('functions', [])
    detail = next((f for f in reversed(functions) if f['status'] != 'CFG_VALIDATED'), {})
    rules = collections.Counter()
    for f in functions:
        rules.update(f.get('rules', {}))
    categories = []
    certainty = 'candidate_difference'
    if proof['status'] == 'CFG_MISMATCH':
        categories = ['actual_control_flow_mismatch']
        certainty = 'counterexample'
    elif accepted:
        certainty = 'proved_under_rules'
        if rules['register_copy_propagation']:
            categories.append('register_reallocation')
        if rules['literal_load_unification'] or rules['exact_integer_constant_fold']:
            categories.append('compiler_optimization')
        if rules['effect_free_branch_partition']:
            categories.extend(['basic_block_split_merge', 'conditional_expression'])
        # Revisited product states also occur at acyclic joins. A reused state
        # alone is not evidence that the function contains a loop.
        has_loop = any(
            f.get(side, {}).get('backedges') for f in functions for side in ('original_analysis', 'recompiled_analysis')
        )
        if rules['relational_loop_invariant_closure'] and has_loop:
            categories.append('loop_structuring')
        if not categories:
            categories = ['basic_block_split_merge']
    elif any(s in reason.lower() for s in ['ref closure', 'capture', 'upvalue', 'prototype', 'cache']):
        categories = ['upvalue_closure_environment']
    elif any(
        s in reason.lower()
        for s in ['unsupported', 'unavailable', 'budget', 'boundary', 'metadata', 'without a supported', 'multret']
    ):
        categories = ['unsupported_pattern']
    else:
        la = detail.get('original_action')
        ra = detail.get('recompiled_action')
        if la and ra:
            if la[0] == 'compare' or ra[0] == 'compare':
                categories.append('conditional_expression')
            if str(la).find('FOR') >= 0 or str(ra).find('FOR') >= 0:
                categories.append('loop_structuring')
            if str(la).find('upvalue') >= 0 or str(ra).find('upvalue') >= 0:
                categories.append('upvalue_closure_environment')
            categories.append('constant_operation_side_effect')
        else:
            categories = ['unsupported_pattern']
    return {
        'categories': list(dict.fromkeys(categories)),
        'certainty': certainty,
        'reason': reason or 'normalization rules discharged',
        'location': proof.get('location'),
        'original_action': detail.get('original_action'),
        'recompiled_action': detail.get('recompiled_action'),
        'counterexample': proof.get('counterexample'),
        'rules': dict(rules),
        'actual_decompiler_error': (
            'confirmed_context_free_counterexample' if proof['status'] == 'CFG_MISMATCH' else 'not_established'
        ),
    }
