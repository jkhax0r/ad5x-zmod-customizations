# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Orca toolpath fallback when an aggregate brim box hides free edges.

Pure geometry/parser functions also run offline. Coordinates are millimetres.
This extra never edits the print file and dry runs never move or heat anything.
"""
import json
import hashlib
import math
import os
import re

MAX_BYTES = 16 * 1024 * 1024
MAX_SEQUENTIAL_BYTES = 256 * 1024 * 1024
MAX_SEGMENTS = 300000
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
WORDS = re.compile(r'([XYZEF])\s*(' + NUMBER + r')', re.I)
SAFE_COMMANDS = {
    'SET_VELOCITY_LIMIT', 'SET_PRINT_STATS_INFO', 'SET_PRESSURE_ADVANCE',
    'EXCLUDE_OBJECT_START', 'EXCLUDE_OBJECT_END', 'M73', 'M104', 'M109',
    'M140', 'M190', 'M106', 'M107', 'M400', 'M204', 'M205', 'M220', 'M221',
    'G4', 'G10', 'G11', 'G21',
}

# Only absolute XY / relative E moves at a known safe Z may take this shortcut.
# Noncapturing runs avoid costly capture bookkeeping on the native CPU.
# Final modal coordinates are recovered separately from the validated span.
_FAST_NUMBER = rb'[-+]?(?:\d{1,5}(?:\.\d{0,12})?|\.\d{1,12})'
_HIGH_RUN_XY = re.compile(
    rb'(?:G0?[01](?:[ \t]+(?:X(?:' + _FAST_NUMBER +
    rb')|Y(?:' + _FAST_NUMBER + rb')|E(?:' + _FAST_NUMBER + rb')|F' + _FAST_NUMBER +
    rb'))*[ \t]*(?:;[^\r\n]*)?\r?\n|'
    rb'(?:SET_VELOCITY_LIMIT|M73|M106)[ \t][^\r\n]*\r?\n|'
    rb';(?!LAYER_CHANGE|Z:|WIDTH:)[^\r\n]*\r?\n|[ \t]*\r?\n)+'
)
# Z >= 2 is a safe bulk range for the usual 0.8 mm bead + 0.5 mm scan height.
# For taller purge beads the reader uses the XY-only shortcut above.
_HIGH_RUN = re.compile(
    rb'(?:G0?[01][ \t]+[XYEF0-9.+\t -]*(?:Z(?:[2-9]\d*|1\d+)(?:\.\d*)?[XYEF0-9.+\t -]*)?(?:;[^\r\n]*)?\r?\n|'
    rb';WIDTH:(?:[1-4](?:\.\d{0,12})?|5(?:\.0{0,12})?|0?\.0{0,11}[1-9]\d{0,11})[ \t]*\r?\n|'
    rb'(?:SET_VELOCITY_LIMIT|M73|M106)[ \t][^\r\n]*\r?\n|'
    rb';(?!LAYER_CHANGE|Z:|WIDTH:)[^\r\n]*\r?\n|[ \t]*\r?\n)+'
)


WIDTH_LINES = re.compile(rb'^;WIDTH:([^\r\n]+)', re.M)
MOTION_NUMBER = re.compile(NUMBER.encode())


def last_motion_word(block, start, end, letter):
    """Find the last actual move word, ignoring words embedded in comments."""
    while True:
        at = block.rfind(letter, start, end)
        if at < 0:
            return None
        line = max(start, block.rfind(b'\n', start, at) + 1)
        if (block.startswith((b'G1 ', b'G0 ', b'G01 ', b'G00 ',
                              b'G1\t', b'G0\t', b'G01\t', b'G00\t'), line) and
                block.find(b';', line, at) < 0):
            match = MOTION_NUMBER.match(block, at + 1)
            if not match:
                raise PlanError('invalid coordinate in high move')
            return match.group()
        end = at


class PathReader:
    """Bounded chunks permit C-level skipping without hiding state changes."""
    def __init__(self, stream, sequential, low_z, cooperate):
        self.stream, self.sequential = stream, sequential
        self.low_z, self.cooperate = low_z, cooperate
        self.consumed = 0
        self.e_invalid = False
        self.width = 1.

    def lines(self, state):
        block, index, eof = b'', 0, False
        while True:
            if index == len(block) or block.find(b'\n', index) < 0:
                rest = block[index:]
                if len(rest) > 1024 * 1024:
                    raise PlanError('G-code line exceeds 1 MiB limit')
                extra = self.stream.read(65536)
                block, index, eof = rest + extra, 0, not extra
                if self.sequential and self.consumed + len(block) > MAX_SEQUENTIAL_BYTES:
                    raise PlanError('sequential scan exceeds 256 MiB limit')
                self.cooperate()
                if not block:
                    return
            absolute, absolute_e, pos = state()
            if (self.sequential and absolute and not absolute_e and
                    pos[2] is not None and pos[2] > self.low_z):
                pattern = _HIGH_RUN if self.low_z < 2. else _HIGH_RUN_XY
                match = pattern.match(block, index)
                if match:
                    end = match.end()
                    if last_motion_word(block, index, end, b'E') is not None:
                        self.e_invalid = True
                    for axis, letter in enumerate((b'X', b'Y', b'Z')):
                        value = last_motion_word(block, index, end, letter)
                        if value is not None:
                            pos[axis] = finite(value)
                    widths = WIDTH_LINES.findall(block, index, end)
                    if widths:
                        self.width = finite(widths[-1])
                    self.consumed += match.end() - index
                    index = match.end()
                    continue
            end = block.find(b'\n', index)
            if end < 0 and not eof:
                continue
            end = len(block) if end < 0 else end + 1
            raw = block[index:end]
            self.consumed += len(raw)
            index = end
            yield raw


def print_sequence(stream):
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(max(0, size - 512 * 1024))
    tail = stream.read().decode('utf-8', errors='replace')
    modes = re.findall(r'^; print_sequence = ([^\r\n]+)', tail, re.M)
    if len(modes) != 1 or modes[0].strip() not in ('by layer', 'by object'):
        raise PlanError('edge fallback requires Orca print-sequence metadata')
    sequential = modes[0].strip() == 'by object'
    if sequential and size > MAX_SEQUENTIAL_BYTES:
        raise PlanError('sequential scan exceeds 256 MiB limit')
    stream.seek(0)
    return sequential


class PlanError(ValueError):
    pass


def finite(value):
    value = float(value)
    if not math.isfinite(value) or abs(value) > 100000:
        raise PlanError('invalid coordinate')
    return value


def parse_paths(stream, low_z, park=None, cooperate=lambda: None,
                toolchange=False):
    """Read complete low layers, including brim, travels, and frame parking.

    Require Orca metadata and layer markers; unsupported coordinate
    transforms/macros/arcs fail closed. Include a conservative 0.5 mm path radius.
    Real object polygons protect interiors, but synthetic BORDER boxes do not
    substitute for the actual brim paths extracted from this same file.
    """
    sequential = print_sequence(stream)
    pos = [None, None, None]
    absolute, absolute_e, epos = True, True, 0.
    seen_xy_mode = seen_e_mode = False
    polygons, segments = [], []
    layer, previous_layer_z, layer_z = 0, None, None
    done, generated, extruded = False, False, 0
    width = 1.0
    park = dict(park or {})
    consumed = 0
    names, visited, low_objects = set(), set(), set()
    current_object = active_object = None
    need_new_object = ended = False
    low_layers = 0
    reader = PathReader(stream, sequential, low_z, cooperate)
    for index, raw in enumerate(reader.lines(lambda: (absolute and not ended, absolute_e, pos))):
        width = reader.width
        consumed += len(raw)
        if consumed > MAX_BYTES:
            raise PlanError('detailed path scan exceeds 16 MiB limit')
        if index % 256 == 0:
            cooperate()
        line = raw.decode('utf-8', errors='strict').strip()
        if line.startswith('; generated by OrcaSlicer '):
            generated = True
        if line == ';LAYER_CHANGE':
            layer += 1
        if line.startswith(';Z:') and layer:
            layer_z = finite(line[3:])
            if previous_layer_z is not None and layer_z <= previous_layer_z:
                if not sequential or active_object is not None:
                    raise PlanError('layer heights are not increasing within an object')
                need_new_object = True
            previous_layer_z = layer_z
            if layer_z <= low_z:
                low_layers += 1
            if not sequential and layer > 1 and layer_z > low_z:
                done = True
                break
        if line.startswith(';WIDTH:'):
            width = finite(line[7:])
            if not 0 < width <= 5:
                raise PlanError('invalid extrusion width')
            reader.width = width
        code = line.split(';', 1)[0].strip()
        if not code:
            continue
        command = code.split()[0].upper()
        if ended:
            if command != 'M73':
                raise PlanError('unexpected command after END_PRINT: ' + command)
            continue
        if command == 'EXCLUDE_OBJECT_DEFINE' and 'POLYGON=' in code:
            name = re.search(r'\bNAME=([^ ]+)', code)
            # The Z-Mod brim rectangle is an aggregate bound, not a solid shape.
            if name and re.fullmatch(r'BORDER\d+', name.group(1), re.I):
                continue
            if sequential:
                if layer or not name or name.group(1) in names:
                    raise PlanError('invalid sequential object definitions')
                names.add(name.group(1))
            points = json.loads(code.split('POLYGON=', 1)[1])
            if len(points) < 3 or len(points) > 10000:
                raise PlanError('invalid object polygon')
            polygons.append([(finite(p[0]), finite(p[1])) for p in points])
            continue
        if command in ('G90', 'G91'):
            absolute = command == 'G90'
            seen_xy_mode = True
            continue
        if command in ('M82', 'M83'):
            absolute_e = command == 'M82'
            seen_e_mode = True
            continue
        words = {k.upper(): finite(v) for k, v in WORDS.findall(code)}
        if command == 'G92':
            if any(k in words for k in 'XYZ'):
                raise PlanError('XYZ coordinate resets are unsupported')
            epos = words.get('E', epos)
            if 'E' in words:
                reader.e_invalid = False
            continue
        if not layer:
            # Startup is executed separately by Z-Mod. The first XY travel is
            # from the purge's raised exit; require explicit slicer modes below.
            if command in ('G0', 'G1', 'G00', 'G01'):
                pos = [None, None, None]
            continue
        if not generated or not seen_xy_mode or not seen_e_mode or layer_z is None:
            raise PlanError('missing Orca layer or coordinate-mode metadata')
        if sequential and command in ('EXCLUDE_OBJECT_START', 'EXCLUDE_OBJECT_END'):
            name = re.fullmatch(r'EXCLUDE_OBJECT_(?:START|END)\s+NAME=([^ ]+)', code)
            if not name or name.group(1) not in names:
                raise PlanError('unknown sequential object')
            name = name.group(1)
            if command == 'EXCLUDE_OBJECT_END':
                if active_object != name:
                    raise PlanError('mismatched sequential object end')
                active_object = None
            else:
                if active_object is not None:
                    raise PlanError('overlapping sequential objects')
                if name != current_object:
                    if name in visited or layer_z > low_z:
                        raise PlanError('sequential object missing its initial low layers')
                    visited.add(name)
                    current_object = name
                elif need_new_object:
                    raise PlanError('layer heights decrease within one object')
                active_object = name
                need_new_object = False
            continue
        if sequential and command == 'END_PRINT':
            if active_object is not None or need_new_object or visited != names or low_objects != names:
                raise PlanError('incomplete sequential object geometry')
            ended = done = True
            continue
        if sequential and command.startswith('T') and re.fullmatch(r'T[0-3]', code):
            if (not toolchange or not absolute or absolute_e or pos[2] is None or
                    pos[2] < max(5., low_z + 1.) or active_object is not None):
                raise PlanError('tool change requires reviewed AD5X restore mode above purge height')
            continue
        if command in ('G0', 'G1', 'G00', 'G01'):
            new = list(pos)
            for i, axis in enumerate('XYZ'):
                if axis in words:
                    if not absolute and pos[i] is None:
                        raise PlanError('relative move before position is known')
                    new[i] = words[axis] if absolute else pos[i] + words[axis]
            e_absolute = absolute and absolute_e
            if e_absolute and 'E' in words and reader.e_invalid:
                raise PlanError('absolute E after fast relative-E scan requires G92 E reset')
            de = (words['E'] - epos if e_absolute else words['E']) if 'E' in words else 0.
            if 'E' in words:
                epos = words['E'] if e_absolute else epos + words['E']
            xy_change = new[:2] != pos[:2]
            if de > 0 and xy_change:
                if None in pos or None in new:
                    raise PlanError('extrusion before XYZ is known')
                extruded += 1
                if sequential and min(pos[2], new[2]) <= low_z and active_object:
                    low_objects.add(active_object)
            if None not in pos and None not in new:
                if min(pos[2], new[2]) <= low_z and (xy_change or de > 0):
                    segments.append((tuple(pos[:2]), tuple(new[:2]), max(.5, width/2) if de > 0 else .5))
            pos = new
        elif command == '_SET_TIMELAPSE_SETUP':
            # Orca selects parking per job. Runtime provides the named park XY.
            args = dict(re.findall(r'(\w+)=([^ ]+)', code))
            if set(args) - {'PARK_ENABLE', 'ENABLE'}:
                raise PlanError('custom timelapse changes inside low layers')
            for key, dest in [('PARK_ENABLE', 'park'), ('ENABLE', 'enabled')]:
                if key in args:
                    if args[key].lower() not in ('true', 'false'):
                        raise PlanError('invalid timelapse setting')
                    park[dest] = args[key].lower() == 'true'
        elif command == 'TIMELAPSE_TAKE_FRAME':
            if park.get('enabled') and park.get('park') and None not in pos:
                # Protect the whole XY path even if a park lift was requested;
                # this also protects older macros that lift during XY travel.
                if pos[2] <= low_z:
                    dest = (park.get('x', pos[0]), park.get('y', pos[1]))
                    if None in dest:
                        raise PlanError('unknown timelapse parking location')
                    segments.append((tuple(pos[:2]), dest, .5))
        elif command not in SAFE_COMMANDS:
            raise PlanError('unsupported low-layer command: ' + command)
        if len(segments) > MAX_SEGMENTS:
            raise PlanError('too many low-layer paths')
    if not done or not polygons or not segments or not extruded:
        raise PlanError('incomplete low-layer geometry; no unchecked purge')
    return {'polygons': polygons, 'segments': segments, 'layers': low_layers,
            'bytes': reader.consumed, 'objects': len(visited) if sequential else len(polygons),
            'sequence': 'by object' if sequential else 'by layer'}


def clip_polygon(poly, axis, lo, hi):
    for bound, sign in [(lo, 1), (hi, -1)]:
        out = []
        if not poly:
            break
        a = poly[-1]
        for b in poly:
            ina, inb = sign * (a[axis]-bound) >= 0, sign * (b[axis]-bound) >= 0
            if ina != inb:
                t = (bound-a[axis]) / (b[axis]-a[axis])
                out.append((a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])))
            if inb:
                out.append(b)
            a = b
        poly = out
    return poly


def merge_interval(intervals, lo, hi):
    """Maintain the sorted union; overlapping paths need only one interval."""
    start = 0
    while start < len(intervals) and intervals[start][1] < lo:
        start += 1
    end = start
    while end < len(intervals) and intervals[end][0] <= hi:
        lo = min(lo, intervals[end][0])
        hi = max(hi, intervals[end][1])
        end += 1
    intervals[start:end] = [(lo, hi)]


def blocked_intervals(geometry, axis, fixed, radius, cooperate=lambda: None,
                      window=None):
    """Conservative square buffers, with redundant projections skipped.

    A segment's full projected buffer bounds its clipped projection. If that
    bound is already covered, it cannot remove any further free space. When a
    requested window has no long-enough gap, further obstacles cannot open one.
    Neither optimization changes an accepted placement or its clearance.
    """
    other = 1 - axis
    intervals = []
    for poly in geometry['polygons']:
        clipped = clip_polygon(poly, axis, fixed-radius, fixed+radius)
        if clipped:
            merge_interval(intervals, min(p[other] for p in clipped)-radius,
                           max(p[other] for p in clipped)+radius)
    if window is not None and not free_intervals(intervals, *window):
        return intervals
    for i, (a, b, width) in enumerate(geometry['segments']):
        if i % 2048 == 0:
            cooperate()
        r = radius + width
        # Most low-layer segments are deep inside the bed. Reject them before
        # division/clipping, which is expensive on the printer's small CPU.
        if ((a[axis] < fixed-r and b[axis] < fixed-r) or
                (a[axis] > fixed+r and b[axis] > fixed+r)):
            continue
        lo = min(a[other], b[other])-r
        hi = max(a[other], b[other])+r
        if window is not None and (hi < window[0] or lo > window[1]):
            continue
        covered = False
        for lower, upper in intervals:
            if lower > lo:
                break
            if upper >= hi:
                covered = True
                break
        if covered:
            continue
        d = b[axis] - a[axis]
        if abs(d) < 1.e-12:
            if abs(a[axis] - fixed) > r:
                continue
            t0, t1 = 0., 1.
        else:
            t0, t1 = sorted(((fixed-r-a[axis])/d, (fixed+r-a[axis])/d))
            t0, t1 = max(0., t0), min(1., t1)
            if t0 > t1:
                continue
        u, v = a[other]+t0*(b[other]-a[other]), a[other]+t1*(b[other]-a[other])
        merge_interval(intervals, min(u,v)-r, max(u,v)+r)
        if window is not None and not free_intervals(intervals, *window):
            return intervals
    return intervals


def free_intervals(blocked, lo, hi, length):
    free = []
    cursor = lo
    for a,b in blocked:
        if a - cursor >= length:
            free.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
        if cursor >= hi:
            break
    if hi - cursor >= length:
        free.append((cursor, hi))
    return [(a,b) for a,b in free if b-a >= length]


def choose_edge(geometry, bed, amount, height, margin, diameter=1.75,
                cooperate=lambda: None):
    if min(amount, height, diameter) <= 0 or margin < 0:
        raise PlanError('invalid purge dimensions')
    bead_width = math.pi*diameter**2/4/height + height*(1-math.pi/4)
    padding = max(3., bead_width/2+1.)
    required = max(margin, padding)
    x0,y0,x1,y1 = bed
    span = amount + 10.
    sides = [('front', 1, y0+padding, x0+padding, x1-padding),
             ('left', 0, x0+padding, y0+padding, y1-padding),
             ('right', 0, x1-padding, y0+padding, y1-padding),
             ('back', 1, y1-padding, x0+padding, x1-padding)]
    best = None
    for name, axis, fixed, lo, hi in sides:
        if x1-x0 < 2*padding or y1-y0 < 2*padding:
            continue
        def gaps(radius):
            return free_intervals(blocked_intervals(geometry, axis, fixed,
                                                   radius, cooperate,
                                                   window=(lo, hi, span)), lo, hi, span)
        free = gaps(required + .02)  # avoid rounded coordinates touching a boundary
        if not free:
            continue
        low, high = required + .02, max(x1-x0,y1-y0)
        # Select the edge segment with the greatest guaranteed clearance.
        for _ in range(10):
            mid = (low + high)/2
            trial = gaps(mid)
            if trial:
                low, free = mid, trial
            else:
                high = mid
        a,b = max(free, key=lambda p:p[1]-p[0])
        start = (a+b-span)/2
        candidate = {'side':name, 'clearance':low, 'padding':padding,
                     'x':fixed if axis == 0 else start,
                     'y':start if axis == 0 else fixed,
                     'dx':0 if axis == 0 else 1, 'dy':1 if axis == 0 else 0,
                     'bead_width':bead_width}
        if best is None or low > best['clearance']:
            best = candidate
    if best is None:
        raise PlanError('no clear edge segment in the actual brim/travel paths; '
                        'need %.2f mm clearance and %.2f mm length' % (required,span))
    return best


class AD5XPurge:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.cache = None
        self.gcode.register_command('PLAN_EDGE_PURGE', self.cmd_edge,
                                   desc='Plan a brim-aware edge purge; DRY_RUN=1 never moves')

    def status(self, name):
        obj = self.printer.lookup_object(name, None)
        return obj.get_status(self.reactor.monotonic()) if obj is not None else {}

    def toolchange_supported(self):
        # These inspected Z-Mod macros lift, change at the maintenance area,
        # then restore XYZ and extrusion state. Trash mode 2 skips restoration.
        expected = {
            '_A_CHANGE_FILAMENT': '9e1279edbbfdcc2dce801d4c534fe7494a4684148f5c260acd359d6a6369a74a',
            '_RESTORE_POSITION_AFTER_FILAMENT_CHANGE': '8a9f8ead7334a99bed5f881b518e92db537f4a6265273f41acf3080a40501332',
            'END_CHANGE_FILAMENT': '6cefbe411d12e95200a7d44187b0e0309df61914e7c9f2d7c28d3db4ed4fd7ff',
            '_MODIFY_END_CHANGE_FILAMENT_DATA': '444ba7e749c8ff89d8eab1fc47b73bacb7ff070e3b5ee00c093e1dd8a6c0219a',
        }
        config = self.status('configfile').get('config', {})
        return (self.status('gcode_macro _CLIENT_VARIABLE').get('ad5x', False) and
                not self.status('gcode_macro _SCREEN').get('screen', True) and
                self.status('save_variables').get('variables', {}).get('use_trash_on_print', 1) != 2 and
                self.status('gcode_macro _A_CHANGE_FILAMENT').get('purge', 0) == 0 and
                all(hashlib.sha256(config.get('gcode_macro ' + name, {}).get('gcode', '').encode()).hexdigest() == digest
                    for name, digest in expected.items()))

    def cmd_edge(self, gcmd):
        dry = gcmd.get_int('DRY_RUN', 0, minval=0, maxval=1)
        filename = gcmd.get('FILE', None)
        if filename and not dry:
            raise gcmd.error('FILE is only allowed for a nonmoving DRY_RUN=1')
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is None:
            raise gcmd.error('LINE_PURGE: no safe outer side and no printer file to inspect')
        path = filename or self.status('virtual_sdcard').get('file_path')
        if not path:
            raise gcmd.error('LINE_PURGE: no safe outer side; load a printer file or use PLAN_EDGE_PURGE DRY_RUN=1 FILE="..."')
        root = os.path.realpath(sd.sdcard_dirname)
        path = os.path.realpath(os.path.join(root, path))
        if os.path.commonpath([root,path]) != root or not path.lower().endswith(('.gcode','.gco','.g')):
            raise gcmd.error('LINE_PURGE: file must be G-code within the printer file directory')
        if not dry and (not self.status('virtual_sdcard').get('is_active') or
                        self.status('virtual_sdcard').get('progress',1) > .01):
            raise gcmd.error('Edge purge is only allowed during printer-file startup')
        k = self.status('gcode_macro _KAMP_Settings')
        client = self.status('gcode_macro _CLIENT_VARIABLE')
        toolhead = self.status('toolhead')
        settings = self.status('configfile')['settings']['extruder']
        if float(k['tip_distance']) != 0:
            raise gcmd.error('Brim-aware fallback requires tip_distance=0; stationary blobs have unknown size')
        tl = self.status('gcode_macro TIMELAPSE_TAKE_FRAME')
        parkstate = tl.get('park', {})
        park = {'enabled':tl.get('enable',False), 'park':parkstate.get('enable',False)}
        coord = parkstate.get('coord', {})
        for key in ('x','y'):
            if coord.get(key) != 'none':
                park[key] = coord.get(key)
        origin = self.status('gcode_move').get('homing_origin', [0,0,0,0])
        if abs(origin[0]) > 1.e-6 or abs(origin[1]) > 1.e-6:
            raise gcmd.error('Edge purge requires zero XY G-code offsets')
        last_yield = [self.reactor.monotonic()]
        budget = 40.
        def cooperate():
            now = self.reactor.monotonic()
            if now - start > budget:
                raise PlanError('planning exceeded %d seconds' % budget)
            if now - last_yield[0] >= .02:
                self.reactor.pause(now + .001)
                last_yield[0] = self.reactor.monotonic()
        start = self.reactor.monotonic()
        bed = (max(float(client['min_x']),toolhead['axis_minimum'][0]),
               max(float(client['min_y']),toolhead['axis_minimum'][1]),
               min(float(client['max_x']),toolhead['axis_maximum'][0]),
               min(float(client['max_y']),toolhead['axis_maximum'][1]))
        params = (float(k['purge_amount']), float(k['purge_height']),
                  float(k['purge_margin']), float(settings['filament_diameter']))
        toolchange = self.toolchange_supported()
        key = (path, bed, params, json.dumps(park, sort_keys=True), toolchange)
        def signature(stream, length):
            # Revalidate every byte used in the cached decision, including
            # footer metadata. A reused filename or preserved mtime is not enough.
            digest = hashlib.sha256()
            stream.seek(0)
            remaining = length
            while remaining:
                block = stream.read(min(65536,remaining))
                if not block:
                    return None
                digest.update(block)
                remaining -= len(block)
                cooperate()
            stream.seek(max(0,os.fstat(stream.fileno()).st_size-512*1024))
            digest.update(stream.read())
            return digest.digest()
        cached = False
        try:
            with open(path, 'rb') as stream:
                before = os.fstat(stream.fileno())
                sequential = print_sequence(stream)
                if sequential:
                    budget = 240.
                    gcmd.respond_info('Sequential purge: scanning every object and transition in the file')
                entry = self.cache
                if (entry is not None and entry['key'] == key and
                        entry['size'] == before.st_size and
                        signature(stream,entry['bytes']) == entry['digest']):
                    p, count, layers = entry['plan'],entry['count'],entry['layers']
                    objects = entry['objects']
                    cached = True
                else:
                    geometry = parse_paths(stream, params[1]+.5, park, cooperate, toolchange)
                    p = choose_edge(geometry, bed, *params, cooperate=cooperate)
                    count, layers = len(geometry['segments']),geometry['layers']
                    objects = geometry['objects']
                    entry = {'key':key,'size':before.st_size,'bytes':geometry['bytes'],
                             'digest':signature(stream,geometry['bytes']),
                             'plan':p,'count':count,'layers':layers,'objects':objects}
                after = os.fstat(stream.fileno())
                if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
                    raise PlanError('print file changed during planning')
            self.cache = entry
        except (ValueError, OSError, UnicodeError, KeyError, TypeError, IndexError) as exc:
            raise gcmd.error('LINE_PURGE geometry: ' + str(exc))
        gcmd.respond_info('Purge placement: side=%s clearance=%.3f start=%.3f,%.3f '
                          'actual_paths=%d low_layers=%d objects=%d scan=%.3fs cached=%d dry_run=%d' %
                          (p['side'],p['clearance'],p['x'],p['y'],count,
                           layers,objects,self.reactor.monotonic()-start,cached,dry))
        if not dry:
            if not self.status('virtual_sdcard').get('is_active'):
                raise gcmd.error('Print stopped during purge planning')
            self.gcode.run_script_from_command('_RUN_VALIDATED_PURGE X=%.4f Y=%.4f DX=%d DY=%d' %
                                               (p['x'],p['y'],p['dx'],p['dy']))


def load_config(config):
    return AD5XPurge(config)
