"""Bounded local SVG renderer; launched in an independently killable process."""
import sys

if sys.platform.startswith('linux'):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024,) * 2)

import resvg_py

svg = sys.stdin.buffer.read(50001)
if len(svg) > 50000:
    raise ValueError('SVG input too large')
png = resvg_py.svg_to_bytes(svg_string=svg.decode('utf-8'), background=sys.argv[1] or None)
if len(png) > 8 * 1024 * 1024:
    raise ValueError('PNG output too large')
sys.stdout.buffer.write(png)
