"""Animation selection planning: what a build/export actually costs.

HANGAR used to reject any selection above a fixed count.  Measurements on the
real local library show why a count is the wrong unit:

    1 clip      41 frames    6.4 s    191 MB peak    5.4 MB GLB
    25 clips  1423 frames   30.8 s    469 MB peak   25.8 MB GLB
    50 clips  2203 frames   47.6 s    612 MB peak   40.1 MB GLB
    75 clips  3916 frames   83.5 s    942 MB peak   64.2 MB GLB
    123 clips 10707 frames 222.7 s   2130 MB peak  145.2 MB GLB

Cost scales with decoded keyframes and final GLB bytes, not with the number of
files, and the isolated worker peaks at roughly 14.5x the GLB it writes.  The
planner below splits a selection by a measured byte budget so that no single
worker has to hold the whole library in memory at once, and it reports the
estimate to the UI before anything is submitted.  Nothing here rejects an
animation because of how many other animations were selected.
"""

MiB = 1024 * 1024
KiB = 1024

# Measured on the local AC6 library with the bundled H02 engine; see
# docs/H04-LARGE-ANIMATION.md.  Both constants round in the safe direction.
BASE_BYTES = 9 * MiB                 # rig + mesh + materials + readback
BYTES_PER_FRAME = 15 * KiB           # measured 13.3 KiB per native keyframe
WORKER_BYTES_PER_OUTPUT_BYTE = 16    # measured 14.5x peak working set

# Largest single deliverable GLB the product will build in one worker.  This is
# a memory/size budget, not a clip count: today's entire library (123 entries,
# 10,707 keyframes) estimates ~145 MB and fits comfortably.
DEFAULT_SINGLE_FILE_CEILING = 192 * MiB

# One worker may not exceed this estimated peak working set.  It is the only
# hard resource ceiling in the product, and it exists because the conversion is
# one in-process NumPy pipeline.
DEFAULT_WORKER_CEILING = 3 * 1024 * MiB

# Interactive preview packets stay small: the viewer keeps only the animations
# from a packet, so a packet's mesh is pure loading latency.
DEFAULT_PREVIEW_PACKET = 48 * MiB

# Request-body guard against absurd payloads (e.g. a corrupted client).  It is
# far above any real library and is not a user-facing animation limit.
REQUEST_ID_CEILING = 4096


class Budget:
    """A byte budget checked against the planner's cost model."""

    def __init__(self, max_bytes, label='packet', texture_bytes=0):
        self.max_bytes = int(max_bytes)
        self.label = label
        self.texture_bytes = max(0, int(texture_bytes))

    def estimated_bytes(self, clips):
        return BASE_BYTES + self.texture_bytes + BYTES_PER_FRAME * frames_of(clips)

    def estimated_peak(self, clips):
        return int(self.estimated_bytes(clips) * WORKER_BYTES_PER_OUTPUT_BYTE)

    def fits(self, clips):
        return self.estimated_bytes(clips) <= self.max_bytes

    def room(self):
        return max(0, self.max_bytes - BASE_BYTES - self.texture_bytes)

    def as_dict(self):
        return {'max_bytes': self.max_bytes, 'label': self.label,
                'max_mb': round(self.max_bytes / MiB, 1)}


def frames_of(clips):
    total = 0
    for clip in clips or []:
        try:
            value = int(clip.get('frames') or 0)
        except (TypeError, ValueError):
            value = 0
        total += max(value, 1)
    return total


def clip_cost(clip):
    """Marginal cost estimate for one clip, in bytes."""
    return BYTES_PER_FRAME * frames_of([clip])


def estimate(clips, packets=1, texture_bytes=0):
    """Estimate reported to the UI for a selection."""
    clips = [c for c in (clips or []) if c]
    frames = frames_of(clips)
    texture_bytes = max(0, int(texture_bytes))
    fixed = BASE_BYTES + texture_bytes
    single = fixed + BYTES_PER_FRAME * frames
    packets = max(1, int(packets or 1))
    per_packet = single if packets == 1 else fixed + (single - fixed) / packets
    return {
        'clips': len(clips),
        'frames': frames,
        'estimated_bytes': int(single),
        'estimated_packet_bytes': int(per_packet),
        'estimated_peak_bytes': int(per_packet * WORKER_BYTES_PER_OUTPUT_BYTE),
        'base_bytes': fixed,
        'texture_bytes_upper_bound': texture_bytes,
        'bytes_per_frame': BYTES_PER_FRAME,
        'measurement_note': ('按本机真实资源实测的 13.3 KiB/原生关键帧 与 14.5 倍峰值内存取整估算；'
                             '估算用于分批与提示，不改变实际解码或导出内容。'),
    }


def plan_packets(clips, budget):
    """Split an ordered clip list into ordered packets that respect *budget*.

    Order is preserved, so packet N always holds the user's earlier choices.  A
    single clip larger than the budget still gets its own packet: the planner
    never drops or truncates an animation to satisfy a size target.
    """
    clips = [c for c in (clips or []) if c]
    if not clips:
        return []
    packets = []
    current = []
    for clip in clips:
        if current and not budget.fits(current + [clip]):
            packets.append(current)
            current = [clip]
        else:
            current.append(clip)
    if current:
        packets.append(current)
    return packets


def summarize_packets(packets, budget):
    rows = []
    for index, packet in enumerate(packets, 1):
        size = budget.estimated_bytes(packet)
        rows.append({
            'index': index,
            'clips': len(packet),
            'frames': frames_of(packet),
            'estimated_bytes': int(size),
            'estimated_mb': round(size / MiB, 1),
            'estimated_peak_bytes': int(size * WORKER_BYTES_PER_OUTPUT_BYTE),
            'overflow': not budget.fits(packet),
        })
    return rows


def select_mode(clips, requested=None, ceiling=None):
    """Choose between one GLB and a batch unless the caller decided."""
    clips = [c for c in (clips or []) if c]
    if not clips:
        return 'single'
    if requested in ('single', 'batch'):
        return requested
    budget = Budget(ceiling or DEFAULT_SINGLE_FILE_CEILING, 'single')
    return 'single' if budget.fits(clips) else 'batch'


def export_plan(clips, mode='auto', single_ceiling=None, worker_ceiling=None, texture_bytes=0):
    """Plan a user-visible export: one file, or verified batch files."""
    clips = [c for c in (clips or []) if c]
    ceiling = Budget(single_ceiling or DEFAULT_SINGLE_FILE_CEILING, 'single', texture_bytes)
    worker = Budget(worker_ceiling or DEFAULT_WORKER_CEILING, 'worker', texture_bytes)
    if not clips:
        return {'mode': 'single', 'budget': ceiling.as_dict(), 'plan': [],
                'packets': 0, 'estimate': estimate([], 1, texture_bytes),
                'fits': True, 'reason': 'bind_pose'}
    # A batch file must satisfy both ceilings: the worker must be able to build
    # it and the result must remain a usable single GLB.
    packet_budget = Budget(min(ceiling.max_bytes, worker.max_bytes), 'packet', texture_bytes)
    chosen = select_mode(clips, None if mode in (None, '', 'auto') else mode,
                         ceiling.max_bytes)
    if chosen == 'single':
        fits = ceiling.fits(clips) and worker.fits(clips)
        if fits:
            return {'mode': 'single', 'budget': ceiling.as_dict(),
                    'plan': summarize_packets([clips], ceiling), 'packets': 1,
                    'estimate': estimate(clips, 1, texture_bytes), 'fits': True,
                    'reason': 'within_single_file_budget'}
        packets = plan_packets(clips, packet_budget)
        return {'mode': 'batch', 'budget': packet_budget.as_dict(),
                'plan': summarize_packets(packets, packet_budget),
                'packets': len(packets),
                'estimate': estimate(clips, len(packets), texture_bytes), 'fits': False,
                'reason': 'single_file_over_budget',
                'explanation': over_budget_text(clips, ceiling)}
    packets = plan_packets(clips, packet_budget)
    return {'mode': 'batch', 'budget': packet_budget.as_dict(),
            'packets': len(packets),
            'plan': summarize_packets(packets, packet_budget),
            'estimate': estimate(clips, len(packets), texture_bytes), 'fits': True,
            'reason': 'batch_requested'}


def over_budget_text(clips, ceiling):
    return (f'所选 {len(clips)} 段动画单文件预计 '
            f'{ceiling.estimated_bytes(clips) / MiB:.0f} MB，超过单文件预算 '
            f'{ceiling.max_bytes / MiB:.0f} MB；将改为分批导出，'
            '每段动画、骨骼绑定与关键帧都保持不变。')


def bytes_human(value):
    value = float(value or 0)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if value < 1024 or unit == 'GiB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024.0
    return f'{value:.1f} GiB'


def clamp_requested_ids(ids):
    """Trim a pathological request body without inventing a product limit."""
    if len(ids) <= REQUEST_ID_CEILING:
        return ids, 0
    return ids[:REQUEST_ID_CEILING], len(ids) - REQUEST_ID_CEILING
