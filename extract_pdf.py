#!/usr/bin/env python3
"""Extract the 290 melodic dictations of qweeee.pdf (Finale/Maestro typesetting) into JSON.

Pitch   : glyph origin y vs. the five staff lines (vector drawings).
Rhythm  : notehead glyph (w ˙ œ) + dot glyph + flag glyphs (j J) + beam polygons (filled paths).
Rests   : Œ (quarter) Ó (half) ‰ (eighth).
Bars    : vertical 0.36pt lines spanning the staff.  Ties: filled bezier paths.  Tuplets: bold-italic '3'.
"""
import pymupdf, json, re, sys, os, collections

PDF = sys.argv[1] if len(sys.argv) > 1 else '/Users/nicolas/dictee/qweeee.pdf'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'out'
SP = 4.306            # staff space (pt)
HS = SP / 2           # half space = one diatonic step
Q = 12                # ticks per quarter note (1/16 = 3, triplet eighth = 4)

HEAD = {'w': 48, '˙': 24, 'œ': 12}          # notehead glyph -> base ticks (quarter head refined by beams/flags)
REST = {'Œ': 12, 'Ó': 24, '‰': 6}
ACC = {'#': 1, 'b': -1, 'n': 0, 'N': 0, 'a': 1}     # 'N' = (natural), 'a' = (sharp): cautionary accidentals
CLEF = {'&': ('treble', 4 * 7 + 2), '?': ('bass', 2 * 7 + 4)}   # bottom line diatonic index (C4 = 28)
LETTERS = 'CDEFGAB'
SHARPS = 'FCGDAEB'
FLATS = 'BEADGCF'
MAJOR_OF_SIG = {0: 'C', 1: 'G', 2: 'D', 3: 'A', 4: 'E', -1: 'F', -2: 'Bb', -3: 'Eb', -4: 'Ab'}
MINOR_OF_SIG = {0: 'Am', 1: 'Em', 2: 'Bm', 3: 'F#m', 4: 'C#m', -1: 'Dm', -2: 'Gm', -3: 'Cm', -4: 'Fm'}
VALUE = {48: 'whole', 24: 'half', 12: 'quarter', 6: 'eighth', 3: 'sixteenth'}

diag = collections.Counter()
warnings = []


def warn(msg):
    warnings.append(msg)


def group_staves(ys):
    """Cluster horizontal long-line y's into staves of 5 lines."""
    ys = sorted(ys)
    staves, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] < SP * 1.5:
            cur.append(y)
        else:
            staves.append(cur); cur = [y]
    staves.append(cur)
    out = []
    for s in staves:
        if len(s) != 5:
            warn(f'staff with {len(s)} lines at y={s[0]:.1f}')
        out.append({'top': s[0], 'bottom': s[-1], 'cy': (s[0] + s[-1]) / 2})
    return out


def nearest_staff(staves, y):
    best = min(staves, key=lambda s: abs(s['cy'] - y))
    return best if abs(best['cy'] - y) < 40 else None


def parse_page(page, pno):
    drawings = page.get_drawings()
    hlines, vlines, beams, ties = [], [], [], []
    for dr in drawings:
        fill = dr.get('fill') is not None
        w = dr.get('width') or 0
        items = dr['items']
        if fill:
            xs, ys = [], []
            has_curve = False
            for it in items:
                if it[0] == 'c':
                    has_curve = True
                    for p in (it[1], it[2], it[3], it[4]):
                        xs.append(p.x); ys.append(p.y)
                elif it[0] == 'l':
                    for p in (it[1], it[2]):
                        xs.append(p.x); ys.append(p.y)
                elif it[0] == 'qu':
                    for p in (it[1].ul, it[1].ur, it[1].ll, it[1].lr):
                        xs.append(p.x); ys.append(p.y)
                elif it[0] == 're':
                    xs += [it[1].x0, it[1].x1]; ys += [it[1].y0, it[1].y1]
            if not xs:
                continue
            box = (min(xs), min(ys), max(xs), max(ys))
            (ties if has_curve else beams).append(box)
            continue
        for it in items:
            if it[0] != 'l':
                continue
            a, b = it[1], it[2]
            if abs(a.y - b.y) < 0.5:
                hlines.append((min(a.x, b.x), max(a.x, b.x), a.y, w))
            elif abs(a.x - b.x) < 0.5:
                vlines.append((a.x, min(a.y, b.y), max(a.y, b.y), w))
    staff_line_ys = sorted(set(round(h[2], 2) for h in hlines if h[1] - h[0] > 100))
    staves = group_staves(staff_line_ys)
    for s in staves:
        s.update(page=pno, chars=[], bars=[], stems=[], beams=[], ties=[], labels=[], tup=[])
    # vertical lines -> barlines / stems
    for x, y0, y1, w in vlines:
        st = nearest_staff(staves, (y0 + y1) / 2)
        if st is None:
            continue
        if abs(y0 - st['top']) < 0.7 and abs(y1 - st['bottom']) < 0.7 and w >= 0.34:
            st['bars'].append((x, w))
        elif w < 0.34:
            st['stems'].append((x, y0, y1))
    for box in beams:
        st = nearest_staff(staves, (box[1] + box[3]) / 2)
        if st is not None and box[2] - box[0] > 3:
            st['beams'].append(box)
    for box in ties:
        st = nearest_staff(staves, (box[1] + box[3]) / 2)
        if st is not None:
            st['ties'].append(box)
    # text
    headings = collections.defaultdict(list)
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                font = s['font']
                text = ''.join(c['c'] for c in s['chars'])
                bbox = s['bbox']
                if 'Maestro' in font:
                    for c in s['chars']:
                        if c['c'] == ' ':
                            continue
                        st = nearest_staff(staves, c['origin'][1])
                        if st is None or (c['c'] in ACC and abs(st['cy'] - c['origin'][1]) > 28):
                            if c['c'] in ACC:     # accidental glyph embedded in a section heading
                                headings[round(c['origin'][1])].append((c['bbox'][0], {'#': '♯', 'b': '♭'}.get(c['c'], '♮')))
                            else:
                                warn(f'p{pno} glyph {c["c"]!r} at {c["origin"]} not near a staff')
                            continue
                        st['chars'].append({'c': c['c'], 'x0': c['bbox'][0], 'x1': c['bbox'][2],
                                            'xc': (c['bbox'][0] + c['bbox'][2]) / 2, 'y': c['origin'][1]})
                elif 'BoldItal' in font:
                    for c in s['chars']:
                        st = nearest_staff(staves, c['origin'][1])
                        if st is not None and c['c'].isdigit():
                            st['tup'].append({'n': int(c['c']), 'x': (c['bbox'][0] + c['bbox'][2]) / 2})
                else:
                    t = text.strip()
                    m = re.fullmatch(r'(\d+)\s*([ab])?\)?', t)
                    if m and bbox[0] < 90:
                        yc = (bbox[1] + bbox[3]) / 2
                        st = nearest_staff(staves, yc)
                        if st is None or abs(st['cy'] - yc) > 14:
                            if yc > 110:      # otherwise it is the page number
                                warn(f'p{pno} label {t!r} not matched to a staff')
                        else:
                            st['labels'].append((int(m.group(1)), m.group(2) or ''))
                    elif bbox[0] > 90 and bbox[0] < 500 and 'DICTÉES' not in t and not re.fullmatch(r'\d+', t) and s['chars']:
                        headings[round(s['chars'][0]['origin'][1])].append((bbox[0], text))
    heads = []
    for y, parts in sorted(headings.items()):
        if heads and y - heads[-1][0] < 3:
            heads[-1][1].extend(parts)
        else:
            heads.append([y, list(parts)])
    headings = [(y, re.sub(r'\s+', ' ', ''.join(t for _, t in sorted(p))).replace('NU LL', 'NULL').replace('NULL', '♪').strip()) for y, p in heads]
    headings = [(y, t) for y, t in headings if len(t) > 3]
    return staves, headings


def parse_staff(st):
    """Turn one staff's glyphs/drawings into a list of events with x positions, plus barline xs."""
    chars = sorted(st['chars'], key=lambda c: c['x0'])
    bottom = st['bottom']
    step_of = lambda y: round((bottom - y) / HS)
    clef = next((c for c in chars if c['c'] in CLEF), None)
    if clef is None:
        warn(f'p{st["page"]} staff y={st["top"]:.0f}: no clef'); return None
    clef_name, d0 = CLEF[clef['c']]
    digits = [c for c in chars if c['c'].isdigit()]
    heads = [c for c in chars if c['c'] in HEAD or c['c'] in REST]
    first_x = min([c['x0'] for c in heads] + [c['x0'] for c in digits] + [1e9])
    # key signature: accidentals between clef and first digit/note
    keysig = [c for c in chars if c['c'] in ('#', 'b') and clef['x0'] < c['x0'] < first_x - 7]
    sig = 0
    if keysig:
        kinds = set(c['c'] for c in keysig)
        if len(kinds) > 1:
            warn(f'p{st["page"]} mixed key signature')
        sig = len(keysig) * (1 if '#' in kinds else -1)
    meter = None
    if digits:
        top = [c for c in digits if c['y'] < bottom - SP * 1.5]
        bot = [c for c in digits if c['y'] >= bottom - SP * 1.5]
        if top and bot:
            meter = (int(''.join(c['c'] for c in sorted(top, key=lambda c: c['x0']))),
                     int(''.join(c['c'] for c in sorted(bot, key=lambda c: c['x0']))))
        else:
            warn(f'p{st["page"]} odd time signature digits')
    # events
    events = []
    for c in chars:
        if c['c'] in HEAD:
            events.append({'kind': 'note', 'x0': c['x0'], 'x1': c['x1'], 'xc': c['xc'], 'y': c['y'],
                           'step': step_of(c['y']), 'base': HEAD[c['c']], 'glyph': c['c'], 'dots': 0,
                           'beams': 0, 'flags': 0, 'acc': None, 'tie': False, 'tuplet': None})
        elif c['c'] in REST:
            events.append({'kind': 'rest', 'x0': c['x0'], 'x1': c['x1'], 'xc': c['xc'], 'y': c['y'],
                           'base': REST[c['c']], 'glyph': c['c'], 'dots': 0, 'beams': 0, 'flags': 0,
                           'tuplet': None})
    events.sort(key=lambda e: e['x0'])
    notes = [e for e in events if e['kind'] == 'note']
    # dots: nearest event to the left within 10pt
    for c in chars:
        if c['c'] != '.':
            continue
        cands = [e for e in events if -1 < c['x0'] - e['x1'] < 10 and abs(e['y'] - c['y']) < SP * 1.6]
        if not cands:
            warn(f'p{st["page"]} unattached dot at x={c["x0"]:.1f}'); continue
        e = min(cands, key=lambda e: c['x0'] - e['x1'])
        e['dots'] += 1
        diag['dot_dx', round(c['x0'] - e['x1'])] += 1
    # flags (eighth note flags, stem up 'j' / stem down 'J')
    for c in chars:
        if c['c'] not in ('j', 'J'):
            continue
        cands = [e for e in notes if abs(c['xc'] - e['xc']) < 9]
        if not cands:
            warn(f'p{st["page"]} unattached flag at x={c["x0"]:.1f}'); continue
        e = min(cands, key=lambda e: abs(c['xc'] - e['xc']))
        e['flags'] += 1
        diag['flag_dx', round(c['xc'] - e['xc'])] += 1
    # accidentals (not key signature)
    ks_ids = set(id(c) for c in keysig)
    for c in chars:
        if c['c'] not in ACC or id(c) in ks_ids:
            continue
        cands = [e for e in notes if 0 < e['x0'] - c['x0'] < 14 and abs(e['step'] - step_of(c['y'])) <= 1]
        if not cands:
            warn(f'p{st["page"]} unattached accidental {c["c"]} at x={c["x0"]:.1f} y={c["y"]:.1f}'); continue
        e = min(cands, key=lambda e: e['x0'] - c['x0'])
        e['acc'] = ACC[c['c']]
        diag['acc_dx', round(e['x0'] - c['x0'])] += 1
    # stems & beams
    for e in notes:
        stem = None
        for sx, sy0, sy1 in st['stems']:
            if (abs(sx - e['x0']) < 1.0 or abs(sx - e['x1']) < 1.0) and (abs(sy0 - e['y']) < 2.5 or abs(sy1 - e['y']) < 2.5):
                stem = (sx, sy0, sy1); break
        e['stem'] = stem
        xs = stem[0] if stem else None
        n = 0
        for bx0, by0, bx1, by1 in st['beams']:
            if xs is not None:
                hit = bx0 - 0.8 <= xs <= bx1 + 0.8
            else:
                hit = bx0 < e['x1'] + 0.5 and bx1 > e['x0'] - 0.5
            if hit:
                # beam must be at the far end of the stem, not near the notehead
                if stem is None or min(abs(by0 - stem[1]), abs(by1 - stem[2]), abs(by0 - stem[2]), abs(by1 - stem[1])) < 6:
                    n += 1
        e['beams'] = n
        if e['glyph'] == 'œ' and stem is None:
            warn(f'p{st["page"]} quarter head without stem at x={e["x0"]:.1f}')
    # beam groups (connected via shared beam polygons)
    for i, e in enumerate(notes):
        e['group'] = i
    for bx0, by0, bx1, by1 in st['beams']:
        members = [e for e in notes if e['stem'] and bx0 - 0.8 <= e['stem'][0] <= bx1 + 0.8]
        if len(members) >= 2:
            g = min(m['group'] for m in members)
            old = set(m['group'] for m in members)
            for e in notes:
                if e['group'] in old:
                    e['group'] = g
    # ties
    for tx0, ty0, tx1, ty1 in st['ties']:
        a = min(notes, key=lambda e: abs(e['xc'] - tx0), default=None)
        b = min(notes, key=lambda e: abs(e['xc'] - tx1), default=None)
        if a is None or b is None or abs(a['xc'] - tx0) > 7 or abs(b['xc'] - tx1) > 7 or a is b:
            warn(f'p{st["page"]} unmatched tie/slur {tx0:.1f}-{tx1:.1f} y={ty0:.1f}'); continue
        if a['step'] != b['step']:
            warn(f'p{st["page"]} slur (different pitches) {tx0:.1f}-{tx1:.1f}: steps {a["step"]}/{b["step"]}')
            a['slur_to'] = notes.index(b)
            continue
        a['tie'] = True
    # tuplets
    for t in st['tup']:
        groups = collections.defaultdict(list)
        for e in notes:
            groups[e['group']].append(e)
        hit = None
        for g, mem in groups.items():
            if len(mem) >= 2 and min(m['x0'] for m in mem) - 3 <= t['x'] <= max(m['x1'] for m in mem) + 3:
                hit = mem; break
        if hit is None:
            hit = sorted(events, key=lambda e: abs(e['xc'] - t['x']))[:t['n']]
            warn(f'p{st["page"]} tuplet {t["n"]} at x={t["x"]:.1f}: no beam group, took {len(hit)} nearest events')
        for e in hit:
            e['tuplet'] = t['n']
    # durations
    for e in events:
        ticks = e['base']
        if e['kind'] == 'note' and e['glyph'] == 'œ':
            ticks = 12 >> max(e['beams'], e['flags'])
        base = ticks
        for d in range(e['dots']):
            ticks += base >> (d + 1)
        e['ticks_nominal'] = ticks
        if e['tuplet'] == 3:
            e['ticks'] = ticks * 2 / 3
        else:
            e['ticks'] = ticks
    bars = sorted(x for x, w in st['bars'])
    return {'clef': clef_name, 'd0': d0, 'sig': sig, 'meter': meter, 'events': events, 'bars': bars,
            'labels': st['labels'], 'page': st['page'], 'top': st['top']}


def alter_from_sig(sig, letter):
    if sig > 0 and letter in SHARPS[:sig]:
        return 1
    if sig < 0 and letter in FLATS[:-sig]:
        return -1
    return 0


def abc_pitch(letter, octave):
    s = letter
    if octave >= 5:
        s = letter.lower() + "'" * (octave - 5)
    elif octave < 4:
        s = letter + ',' * (4 - octave)
    return s


ACC_ABC = {-2: '__', -1: '_', 0: '=', 1: '^', 2: '^^'}


def build_melody(staffs, number, variant, heading):
    first = staffs[0]
    clef, d0, sig, meter = first['clef'], first['d0'], first['sig'], first['meter']
    for s in staffs[1:]:
        if s['clef'] != clef or s['sig'] != sig:
            warn(f'melody {number}{variant}: clef/key change on continuation staff ({s["clef"]},{s["sig"]})')
    measures = []
    for s in staffs:
        bars = s['bars']
        cur = []
        bi = 0
        for e in s['events']:
            while bi < len(bars) and e['x0'] > bars[bi]:
                measures.append(cur); cur = []; bi += 1
            cur.append(e)
        if cur:
            measures.append(cur)
            if bi >= len(bars):
                warn(f'melody {number}{variant}: staff p{s["page"]} does not end with a barline')
    # drop empty measures created by double barlines (thin+thick final)
    measures = [m for m in measures if m]
    # pitches
    out_measures = []
    tie_pending = None
    for m in measures:
        state = {L: alter_from_sig(sig, L) for L in LETTERS}
        om = []
        for e in m:
            ev = {'type': e['kind']}
            if e['kind'] == 'note':
                d = d0 + e['step']
                letter, octave = LETTERS[d % 7], d // 7
                if tie_pending and tie_pending[0] == (letter, octave):
                    alter = tie_pending[1]
                elif e['acc'] is not None:
                    alter = e['acc']; state[letter] = alter
                else:
                    alter = state[letter]
                tie_pending = None
                midi = 12 * (octave + 1) + [0, 2, 4, 5, 7, 9, 11][LETTERS.index(letter)] + alter
                ev.update(pitch=letter + ({1: '#', -1: 'b', 0: ''}[alter]) + str(octave), step=letter, octave=octave,
                          alter=alter, midi=midi)
                if e['acc'] is not None:
                    ev['accidental'] = {1: 'sharp', -1: 'flat', 0: 'natural'}[e['acc']]
                if e['tie']:
                    ev['tie'] = True; tie_pending = ((letter, octave), alter)
                if e.get('slur_to') is not None:
                    ev['slur'] = True
            base = 12 >> max(e['beams'], e['flags']) if e['glyph'] == 'œ' else e['base']
            ev['value'] = VALUE[base]
            if e['dots']:
                ev['dots'] = e['dots']
            if e['tuplet']:
                ev['tuplet'] = e['tuplet']
            ev['ticks'] = int(e['ticks']) if float(e['ticks']).is_integer() else e['ticks']   # quarter = 12
            ev['_group'] = e.get('group', None)
            om.append(ev)
        out_measures.append(om)
    # validation
    meter_ticks = meter[0] * 48 // meter[1] if meter else None
    sums = [sum(ev['ticks'] for ev in m) for m in out_measures]
    issues = []
    anacrusis = False
    if meter_ticks:
        for i, sm in enumerate(sums):
            if abs(sm - meter_ticks) > 0.01:
                if i == 0 and sm < meter_ticks:
                    anacrusis = True
                elif i == len(sums) - 1 and anacrusis and abs(sums[0] + sm - meter_ticks) < 0.01:
                    pass
                else:
                    issues.append(f'measure {i + 1}: {sm:g} ticks vs {meter_ticks}')
    # ABC
    abc_body = []
    for m in out_measures:
        state = {L: alter_from_sig(sig, L) for L in LETTERS}
        toks = []
        prev_group = object()
        i = 0
        while i < len(m):
            ev = m[i]
            tok = ''
            if ev.get('tuplet') and (i == 0 or not m[i - 1].get('tuplet')):
                tok += f"({ev['tuplet']}"
            if ev['type'] == 'rest':
                tok += 'z'
            else:
                if ev['alter'] != state[ev['step']]:
                    tok += ACC_ABC[ev['alter']]; state[ev['step']] = ev['alter']
                tok += abc_pitch(ev['step'], ev['octave'])
            ticks = ev['ticks'] * (3 / 2 if ev.get('tuplet') == 3 else 1)
            u = ticks / 3
            if u != 1:
                tok += ('%g' % u) if u == int(u) else f'{int(round(u * 2))}/2'
            if ev.get('tie'):
                tok += '-'
            if toks and ev['_group'] is not None and ev['_group'] == prev_group:
                toks[-1] += tok
            else:
                toks.append(tok)
            prev_group = ev['_group'] if ev['type'] == 'note' else object()
            i += 1
        abc_body.append(' '.join(toks))
    for m in out_measures:
        for ev in m:
            ev.pop('_group', None)
    # key guess
    last_note = next((ev for m in reversed(out_measures) for ev in reversed(m) if ev['type'] == 'note'), None)
    major, minor = MAJOR_OF_SIG[sig], MINOR_OF_SIG[sig]
    mode = 'major'
    if last_note and last_note['step'] == minor[0] and (last_note['alter'] == (1 if '#' in minor else 0)):
        mode = 'minor'
    key = minor if mode == 'minor' else major
    ident = f'{number}{variant}'
    abc_key = key + (' clef=bass' if clef == 'bass' else '')
    abc = (f"X:{ident}\nT:Dictée {number}{(' ' + variant + ')') if variant else ''}\n"
           f"M:{meter[0]}/{meter[1] if meter else ''}\nL:1/16\nK:{abc_key}\n" if meter else
           f"X:{ident}\nT:Dictée {number}{(' ' + variant + ')') if variant else ''}\nM:none\nL:1/16\nK:{abc_key}\n")
    abc += ' | '.join(abc_body) + ' |]\n'
    return {
        'id': ident, 'number': number, 'variant': variant or None, 'page': first['page'],
        'section': heading, 'clef': clef, 'keySignature': sig, 'key': key, 'mode': mode,
        'meter': f'{meter[0]}/{meter[1]}' if meter else None, 'anacrusis': anacrusis,
        'staves': len(staffs), 'ticksPerQuarter': Q, 'measures': out_measures, 'abc': abc,
        'issues': issues,
    }


def main():
    doc = pymupdf.open(PDF)
    melodies = []
    for pno, page in enumerate(doc, 1):
        staves, headings = parse_page(page, pno)
        staves.sort(key=lambda s: s['top'])
        parsed = [parse_staff(s) for s in staves]
        groups = []
        for p in parsed:
            if p is None:
                continue
            if p['labels']:
                if len(p['labels']) > 1:
                    warn(f'p{pno} staff with several labels {p["labels"]}')
                groups.append([p])
            elif groups:
                groups[-1].append(p)
            else:
                warn(f'p{pno} first staff has no label')
                groups.append([p])
        for g in groups:
            num, var = g[0]['labels'][0] if g[0]['labels'] else (0, '')
            heading = max([h for h in headings if h[0] < g[0]['top']], key=lambda h: h[0], default=(0, ''))[1]
            melodies.append(build_melody(g, num, var, heading))
    os.makedirs(OUT, exist_ok=True)
    for m in melodies:
        with open(os.path.join(OUT, f'{int(m["number"]):03d}{m["variant"] or ""}.json'), 'w') as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, 'all.json'), 'w') as f:
        json.dump(melodies, f, ensure_ascii=False)
    with open(os.path.join(OUT, 'all.abc'), 'w') as f:
        f.write('\n'.join(m['abc'] for m in melodies))
    print('melodies', len(melodies))
    bad = [m for m in melodies if m['issues']]
    print('melodies with measure-length issues', len(bad))
    for m in bad:
        print(' ', m['id'], 'p', m['page'], m['meter'], m['issues'])
    print('diag', sorted(diag.items()))
    print('warnings', len(warnings))
    for w in warnings:
        print(' ', w)


if __name__ == '__main__':
    main()
