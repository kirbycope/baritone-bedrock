"""The obstacle course that proves the executor (docs/baritone-port-plan.md, "Proving it"): seven pieces built by
command on quartz pads 200 blocks up in the current world, each with a start and an end marker, and a run that
teleports the player to each start and drives Navigator.go to the end, writing a report.

    python -m baritone_bedrock.course --build [--pieces 1,2] [--origin X Z] [--y 200]
    python -m baritone_bedrock.course --run   [--pieces 1,2] [--repeat 3] [--out captures/course] [--controller ftue|null] [--no-shots]
    python -m baritone_bedrock.course --clear

The pieces (the plan's list):
  1  100 blocks of flat ground (sprint and straight-line steering; sprint jumps)
  2  a staircase up 10 and down 10, one block steps (ascend and descend timing)
  3  a hill with a 3 block cliff and a wall round the easy way (the two-wall case: best-so-far and favoring)
  4  pits 1, 2 and 3 deep (the fall limits), each with steps out of its far side
  5  a river 6 wide (wade and swim) and a pond with a 1 block bank (bob out)
  6  a 1x2 tunnel through stone, cheaper to dig by hand than the 120 block way round
  7  a 6x6 dirt patch to gather from, a 4 block cliff needing a pillar, a 3 block gap over water needing a bridge
  8  parkour: four legs, gaps of 1, 2 and 3 over a 2 block drop and a gap of 1 onto a block one up (phase 3)
  9  a dark oak forest strip 40 long and 21 wide under a dense canopy, with no clear line through it (phase 3)

A piece with several legs (8) is run a leg at a time, each with its own start and end markers, and --repeat runs
every leg that many times, so the report can say "3 of 3" per gap.

The player is given nothing: piece 7 starts with Navigator.gather on its dirt patch, and any dirt the player
already carries is cleared before the run so pieces 1 to 6 are walked empty-handed. Everything is on the Rig pattern
(calibrate.Rig.tiled: chunk-sized fills, testforblock to verify, tp by command through the probe). --clear
teleports the player off the course first; a rig is never cleared under the player's feet.

The report goes to <out>/<time>/report.md with a row per piece (time, path length, planner nodes and milliseconds,
done or not) against the old navigator's figure (200 blocks in 4 to 8 minutes), every tick to ticks.jsonl (feet,
yaw, sticks, held, movement, status) so a failure can be read afterwards, and a screenshot at each end when the FTUE
skill's Client can be imported (course.py is the only module that knows it exists, and only for the pictures).
"""
import argparse
import datetime
import json
import math
import os
import sys
import time

from .calibrate import Rig, make_controller, yaw_to
from .navigator import Navigator
from .probe import Probe
from .settings import Settings

FLOOR = "quartz_block"
START, END = "gold_block", "emerald_block"
GAP = 6          # blocks of air between lanes


class Piece:
    """One lane along +x: x0..x0 + length, centred on z, half_width each side; feet on the pad at y + 1."""

    def __init__(self, number, name, length, half_width, start_dy=0, end_dy=0):
        self.number, self.name, self.length, self.half_width = number, name, length, half_width
        self.start_dy, self.end_dy = start_dy, end_dy
        self.x0 = self.z = self.y = None

    def place(self, x0, z, y):
        self.x0, self.z, self.y = x0, z, y
        return self

    @property
    def x1(self):
        return self.x0 + self.length

    @property
    def start(self):
        return (self.x0 + 1, self.y + 1 + self.start_dy, self.z)

    @property
    def end(self):
        return (self.x1 - 1, self.y + 1 + self.end_dy, self.z)

    def box(self):
        return (self.x0 - 1, self.y - 8, self.z - self.half_width - 1, self.x1 + 1, self.y + 12, self.z + self.half_width + 1)

    def pad(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        return (t(x0 - 1, y - 7, z - hw - 1, self.x1 + 1, y + 12, z + hw + 1, "air")
                + t(x0, y, z - hw, self.x1, y, z + hw, FLOOR))

    def legs(self):
        """(label, start, end) per leg: one for most pieces, several for a piece run a part at a time."""
        return [(self.name, self.start, self.end)]

    def markers(self):
        out = []
        for _label, (sx, sy, sz), (ex, ey, ez) in self.legs():
            out += [f"setblock {sx} {sy - 1} {sz} {START}", f"setblock {ex} {ey - 1} {ez} {END}"]
        return out

    def build(self, t):
        raise NotImplementedError

    def checks(self):
        """(x, y, z, block) triples testforblock verifies after the build."""
        out = []
        for _label, (sx, sy, sz), (ex, ey, ez) in self.legs():
            out += [(sx, sy - 1, sz, START), (ex, ey - 1, ez, END)]
        return out


class Flat(Piece):
    def __init__(self):
        super().__init__(1, "flat 100", 102, 3)

    def build(self, t):
        return self.pad(t)


class Stairs(Piece):
    """Up 10 one-block steps, 3 flat, down 10."""
    def __init__(self):
        super().__init__(2, "stairs up 10 and down 10", 33, 2)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        for i in range(10):                                       # x0+5 .. x0+14 rise to y+10 (feet y+11)
            out += t(x0 + 5 + i, y + 1, z - hw, x0 + 5 + i, y + 1 + i, z + hw, FLOOR)
        out += t(x0 + 15, y + 1, z - hw, x0 + 17, y + 10, z + hw, FLOOR)   # the top, 3 long
        for i in range(10):                                       # x0+18 .. x0+27 fall back to the pad
            out += t(x0 + 18 + i, y + 1, z - hw, x0 + 18 + i, y + 10 - i - 1, z + hw, FLOOR)
        return out

    def legs(self):
        """The whole staircase, then its descending half on its own (the sprint policy down one block steps, and
        the sprint jump refusing to hop down them: a hop lands three steps down, past SAFE_FALL)."""
        top = (self.x0 + 16, self.y + 11, self.z)
        return [(self.name, self.start, self.end), ("stairs down 10", top, self.end)]

    def checks(self):
        return super().checks() + [(self.x0 + 14, self.y + 10, self.z, FLOOR)]


class Hill(Piece):
    """A plateau 3 high across the lane with a 3 block cliff on the near side (no ascending it bare-handed), a
    walled pocket on the +z side the start is offset toward (the easy-looking way), the ramp up on the -z side, and
    a 3 block drop off the far side."""
    def __init__(self):
        super().__init__(3, "hill with a 3 block cliff", 44, 7)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        out += t(x0 + 18, y + 1, z - hw, x0 + 26, y + 3, z + hw, FLOOR)                 # the plateau, feet at y+4
        for i in range(3):                                                              # the ramp at z-7..z-6
            out += t(x0 + 15 + i, y + 1, z - hw, x0 + 15 + i, y + 1 + i, z - hw + 1, FLOOR)
        out += t(x0 + 8, y + 1, z + hw - 2, x0 + 17, y + 4, z + hw, FLOOR)              # the pocket wall on +z
        out += t(x0 + 8, y + 1, z + 1, x0 + 8, y + 4, z + hw - 3, FLOOR)                # its lip, so +z is a dead end
        out += t(x0 + 10, y + 1, z - 4, x0 + 14, y + 4, z - 2, FLOOR)                   # a boulder hiding the ramp
        return out

    @property
    def start(self):
        return (self.x0 + 1, self.y + 1, self.z + 4)

    def checks(self):
        return super().checks() + [(self.x0 + 22, self.y + 3, self.z, FLOOR), (self.x0 + 17, self.y + 3, self.z - self.half_width, FLOOR)]


class Pits(Piece):
    """Pits 1, 2 and 3 deep across the lane, each with steps out of its far side."""
    def __init__(self):
        super().__init__(4, "pits 1, 2 and 3 deep", 42, 2)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        # pit 1: x0+8..x0+9, floor at y-1 (feet y), the far wall one high
        out += t(x0 + 8, y, z - hw, x0 + 9, y, z + hw, "air") + t(x0 + 8, y - 1, z - hw, x0 + 9, y - 1, z + hw, FLOOR)
        # pit 2: x0+16..x0+18, floor at y-2 (feet y-1), a step at x0+18 (y-1) then the pad
        out += t(x0 + 16, y, z - hw, x0 + 18, y, z + hw, "air") + t(x0 + 16, y - 2, z - hw, x0 + 18, y - 2, z + hw, FLOOR)
        out += t(x0 + 18, y - 1, z - hw, x0 + 18, y - 1, z + hw, FLOOR)
        # pit 3: x0+26..x0+29, floor at y-3 (feet y-2), steps at x0+28 (y-2) and x0+29 (y-1)
        out += t(x0 + 26, y, z - hw, x0 + 29, y, z + hw, "air") + t(x0 + 26, y - 3, z - hw, x0 + 29, y - 3, z + hw, FLOOR)
        out += t(x0 + 28, y - 2, z - hw, x0 + 28, y - 2, z + hw, FLOOR) + t(x0 + 29, y - 2, z - hw, x0 + 29, y - 1, z + hw, FLOOR)
        # the pits' side walls, so nothing is walked round
        out += t(x0 + 7, y + 1, z - hw - 1, x0 + 30, y + 2, z - hw - 1, FLOOR) + t(x0 + 7, y + 1, z + hw + 1, x0 + 30, y + 2, z + hw + 1, FLOOR)
        return out

    def checks(self):
        return super().checks() + [(self.x0 + 27, self.y - 3, self.z, FLOOR), (self.x0 + 17, self.y - 2, self.z, FLOOR)]


class Water(Piece):
    """A river 6 wide, one deep (wade), then a pond 2 deep whose surface is a block under the pad (swim, bob out
    onto the 1 block bank)."""
    def __init__(self):
        super().__init__(5, "river 6 wide and a pond", 44, 3)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        # the river: a tub x0+7..x0+14 of quartz down to y-2, water at y over a floor at y-1
        out += t(x0 + 7, y - 2, z - hw - 1, x0 + 14, y, z + hw + 1, FLOOR)
        out += t(x0 + 8, y, z - hw, x0 + 13, y, z + hw, "water")
        # the pond: x0+24..x0+30, water at y-1..y over a floor at y-2, the pad round it one block above the surface
        out += t(x0 + 23, y - 3, z - hw - 1, x0 + 31, y, z + hw + 1, FLOOR)
        out += t(x0 + 24, y - 1, z - hw, x0 + 30, y, z + hw, "water")
        # banks: the pad beside the water stays at y; side walls so the water is the only way
        out += t(x0 + 6, y + 1, z - hw - 1, x0 + 32, y + 2, z - hw - 1, FLOOR) + t(x0 + 6, y + 1, z + hw + 1, x0 + 32, y + 2, z + hw + 1, FLOOR)
        return out

    def checks(self):
        return super().checks() + [(self.x0 + 10, self.y, self.z, "water"), (self.x0 + 27, self.y - 1, self.z, "water"), (self.x0 + 27, self.y, self.z, "water")]


class Tunnel(Piece):
    """A wall across a wide pad with a stone plug in the middle of it: two stone blocks by hand (about 300 ticks)
    against a 120 block walk round (about 430 ticks at a sprint)."""
    def __init__(self):
        super().__init__(6, "tunnel through stone", 50, 61)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        out += t(x0 + 25, y + 1, z - hw, x0 + 25, y + 4, z + hw, FLOOR)          # the wall, 4 high, the whole width
        out += t(x0 + 25, y + 1, z, x0 + 25, y + 2, z, "stone")                 # the plug: feet and head
        return out

    def checks(self):
        return super().checks() + [(self.x0 + 25, self.y + 1, self.z, "stone"), (self.x0 + 25, self.y + 2, self.z, "stone"),
                                   (self.x0 + 25, self.y + 3, self.z - self.half_width, FLOOR)]


class Cliff(Piece):
    """A 6x6 dirt patch at the start, a 4 block cliff with no way round (pillar up it), a 3 block gap over water
    (bridge it), and the end on the far plateau."""
    def __init__(self):
        super().__init__(7, "gather, pillar 4, bridge 3", 46, 3, start_dy=0, end_dy=4)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        # the dirt patch, 6x6, a bed one block high ON the pad (never the pad's own floor layer: a block dug from
        # under the feet would open the void); every block of it is side-exposed from the pad
        out += t(x0 + 2, y + 1, z - hw, x0 + 7, y + 1, z + 2, "dirt")
        out += t(x0 + 18, y + 1, z - hw, x0 + 28, y + 4, z + hw, FLOOR)              # the cliff, feet at y+5 on top
        out += t(x0 + 29, y - 7, z - hw - 1, x0 + 31, y - 7, z + hw + 1, FLOOR)      # under the gap: a pool
        out += t(x0 + 29, y - 6, z - hw, x0 + 31, y - 5, z + hw, "water")
        out += t(x0 + 29, y, z - hw, x0 + 31, y, z + hw, "air")                      # the gap: no pad under it
        out += t(x0 + 32, y + 1, z - hw, self.x1, y + 4, z + hw, FLOOR)              # the far plateau, feet at y+5
        return out

    def checks(self):
        return super().checks() + [(self.x0 + 4, self.y + 1, self.z, "dirt"), (self.x0 + 20, self.y + 4, self.z, FLOOR),
                                   (self.x0 + 30, self.y - 5, self.z, "water"), (self.x0 + 40, self.y + 4, self.z, FLOOR)]


class Parkour(Piece):
    """Four legs along the lane, each with its own start and end: a 7 block run-up, a gap over a quartz tub whose
    floor is 2 below the pad (a miss is a 2 block drop into a pit with no way out, which the next leg's teleport
    ends), and a landing platform. The gaps are 1, 2 and 3 blocks of air (Baritone's dist 2, 3 and 4, the last at a
    sprint: Park:104-113), then the ascend variant, a gap of 1 onto a platform one block up (Park:132-137)."""
    GAPS = ((2, False), (3, False), (4, False), (2, True))
    LEG = 20

    def __init__(self):
        super().__init__(8, "parkour", self.LEG * len(self.GAPS) + 2, 2)

    def leg_x(self, k):
        return self.x0 + 1 + k * self.LEG      # the leg's first pad block

    def legs(self):
        out = []
        for k, (dist, ascend) in enumerate(self.GAPS):
            lx = self.leg_x(k)
            start = (lx + 1, self.y + 1, self.z)
            end = (lx + self.LEG - 2, self.y + 2 if ascend else self.y + 1, self.z)
            out.append((f"parkour dist {dist}{' up 1' if ascend else ''}", start, end))
        return out

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        out = self.pad(t)
        for k, (dist, ascend) in enumerate(self.GAPS):
            lx = self.leg_x(k)
            gx0 = lx + 8                       # the gap's first air column: the run-up is lx .. lx + 7
            gx1 = gx0 + dist - 2               # dist - 1 air columns
            out += t(gx0 - 1, y - 3, z - hw - 1, gx1 + 1, y, z + hw + 1, FLOOR)      # the tub round the pit
            out += t(gx0, y - 1, z - hw, gx1, y, z + hw, "air")                       # the pit: its floor is y - 2, feet y - 1
            if ascend:
                out += t(gx1 + 1, y + 1, z - hw, lx + self.LEG - 1, y + 1, z + hw, FLOOR)   # the landing one up
            # walls along the gap so nothing is walked round, and one between the legs
            out += t(gx0 - 1, y + 1, z - hw - 1, gx1 + 1, y + 3, z - hw - 1, FLOOR)
            out += t(gx0 - 1, y + 1, z + hw + 1, gx1 + 1, y + 3, z + hw + 1, FLOOR)
            out += t(lx - 1, y + 1, z - hw - 1, lx - 1, y + 3, z + hw + 1, FLOOR)
        return out

    def checks(self):
        out = super().checks()
        for k, (dist, ascend) in enumerate(self.GAPS):
            gx0 = self.leg_x(k) + 8
            out += [(gx0, self.y - 2, self.z, FLOOR), (gx0 + dist - 1, self.y + (1 if ascend else 0), self.z, FLOOR)]
        return out


class Forest(Piece):
    """A dark oak strip 40 long and 21 wide walled on both sides: a canopy of leaves over all of it two deep, 2x2
    trunks six high on a jittered 5 block grid with leaves round their tops, and leaves at feet and head height in
    a fifth of the cells, so no straight line through it is clear and the navigator has to break leaves (natural,
    0.2 hardness) or weave between the trunks (the Saffron case from the plan's course list)."""
    LENGTH = 40

    def __init__(self):
        super().__init__(9, "dark oak forest 40 by 21", self.LENGTH + 6, 10)

    @property
    def strip(self):
        return (self.x0 + 3, self.x0 + 2 + self.LENGTH)

    def build(self, t):
        x0, y, z, hw = self.x0, self.y, self.z, self.half_width
        fx0, fx1 = self.strip
        out = self.pad(t)
        out += t(fx0, y + 6, z - hw, fx1, y + 7, z + hw, "dark_oak_leaves")           # the canopy
        for i, tx in enumerate(range(fx0 + 1, fx1 - 2, 5)):
            for j, tz in enumerate(range(z - hw + 1, z + hw - 1, 5)):
                bx, bz = tx + (i * 3 + j * 2) % 3 - 1, tz + (i * 2 + j * 5) % 3 - 1
                bx, bz = max(fx0, min(fx1 - 1, bx)), max(z - hw, min(z + hw - 1, bz))
                out += t(max(fx0, bx - 1), y + 4, max(z - hw, bz - 1), min(fx1, bx + 2), y + 5, min(z + hw, bz + 2), "dark_oak_leaves")   # the crown, trunk last
                out += t(bx, y + 1, bz, bx + 1, y + 5, bz + 1, "dark_oak_log")
        for x in range(fx0, fx1 + 1):                                                 # the undergrowth
            for zz in range(z - hw, z + hw + 1):
                if (x * 7 + zz * 13) % 5 == 0:
                    out += t(x, y + 1, zz, x, y + 2, zz, "dark_oak_leaves")
        out += t(fx0 - 1, y + 1, z - hw - 1, fx1 + 1, y + 8, z - hw - 1, FLOOR)       # the walls
        out += t(fx0 - 1, y + 1, z + hw + 1, fx1 + 1, y + 8, z + hw + 1, FLOOR)
        return out

    def checks(self):
        fx0, _fx1 = self.strip
        return super().checks() + [(fx0 + 10, self.y + 7, self.z, "dark_oak_leaves"), (fx0 + 1, self.y + 3, self.z - self.half_width + 1, "dark_oak_log")]


PIECES = {1: Flat, 2: Stairs, 3: Hill, 4: Pits, 5: Water, 6: Tunnel, 7: Cliff, 8: Parkour, 9: Forest}


def layout(ox, oz, y, numbers):
    """The pieces placed in a row along +z from (ox, oz), each lane as wide as it needs plus a gap. The row always
    holds all seven, so a piece sits at the same place whatever subset is built or run (a run of 2,3,4,5 laid out
    on its own once put piece 3's start in the gap between lanes)."""
    z = oz
    out = []
    for n in sorted(PIECES):
        piece = PIECES[n]()
        z += piece.half_width
        piece.place(ox, z, y)
        if n in numbers:
            out.append(piece)
        z += piece.half_width + GAP
    return out


class Course:
    """Builds, verifies, runs and clears the course through a Probe and a Controller."""

    def run(self, command, timeout=20.0):
        result = self.probe.command(command, timeout)
        changed_nothing = command.startswith(("fill", "setblock")) and not result.get("error")
        if not result.get("ok") and not changed_nothing:
            raise RuntimeError(f"{command}: {result.get('error') or 'no success'}")
        return result

    def player(self):
        return self.probe.player(self.who, fresh=5.0)

    def tick(self, n=1):
        p = None
        for _ in range(n):
            k = self.probe.ticks
            self.probe.wait_tick(k, timeout=0.5)
            p = self.player()
        return p

    def tp(self, x, y, z, yaw=None, pitch=0.0):
        rot = "" if yaw is None else f" {yaw:.1f} {pitch:.1f}"
        self.run(f'tp "{self.who}" {x:.2f} {y:.2f} {z:.2f}{rot}')
        for _ in range(60):
            p = self.tick()
            if p and math.hypot(p["x"] - x, p["z"] - z) < 1.0 and abs(p["y"] - y) < 1.5 and (p["ground"] or p["water"]):
                break
        return self.tick(10)

    # A fill or a setblock in a chunk the server has not loaded does nothing and reports no success (the first build
    # lost everything past about five chunks from the player), so the build walks the player along with it: the
    # commands go chunk by chunk, and before each chunk the player is put within NEAR chunks of it, on a floor tile
    # already laid, or on the ground below the course for the first one.
    NEAR = 2

    def __init__(self, probe, player, log=print):
        self.probe, self.who, self.log = probe, player, log
        self.floors = []     # (x, y, z) centres of floor tiles laid, to stand on

    @staticmethod
    def chunk_of(command):
        words = command.split()
        return (int(words[1]) >> 4, int(words[3]) >> 4)

    def ensure_loaded(self, x, z):
        """The player within NEAR chunks of (x, z): nothing if already there, else a tp onto the nearest laid floor
        tile that is near enough, else spreadplayers onto the ground under that spot."""
        p = self.player() or {}
        px, pz = p.get("x", 1e9), p.get("z", 1e9)
        if abs((math.floor(px) >> 4) - (x >> 4)) <= self.NEAR and abs((math.floor(pz) >> 4) - (z >> 4)) <= self.NEAR:
            return
        near = [f for f in self.floors if abs((f[0] >> 4) - (x >> 4)) <= self.NEAR and abs((f[2] >> 4) - (z >> 4)) <= self.NEAR]
        if near:
            fx, fy, fz = min(near, key=lambda f: abs(f[0] - x) + abs(f[2] - z))
            self.tp(fx + 0.5, fy + 1.0, fz + 0.5)
            return
        self.log(f"  loading chunk {x >> 4}, {z >> 4}: the player goes to the ground under it")
        self.run(f'spreadplayers {x} {z} 0 1 "{self.who}"')
        self.tick(30)

    def build(self, pieces):
        t = Rig.tiled
        for piece in pieces:
            self.log(f"building piece {piece.number} ({piece.name}) at x {piece.x0}..{piece.x1}, z {piece.z}, y {piece.y}")
            commands = sorted(piece.build(t) + piece.markers(), key=self.chunk_of)   # stable: air, floor, features per chunk
            for command in commands:
                words = command.split()
                self.ensure_loaded(int(words[1]), int(words[3]))
                self.run(command)
                if words[0] == "fill" and words[7] == FLOOR and words[2] == words[5] == str(piece.y):
                    self.floors.append(((int(words[1]) + int(words[4])) // 2, piece.y, (int(words[3]) + int(words[6])) // 2))
            self.tick(10)
            for x, y, z, block in piece.checks():
                self.ensure_loaded(x, z)
                r = self.probe.command(f"testforblock {x} {y} {z} {block}", 10)
                if not r.get("ok"):
                    raise RuntimeError(f"piece {piece.number}: no {block} at {x} {y} {z} ({r.get('error') or 'no success'})")
            self.log(f"  piece {piece.number} verified")

    def clear(self, pieces):
        """Air over every piece's box, chunk by chunk, the player on the ground under each chunk (never on a tile
        being cleared)."""
        self.floors = []
        for piece in pieces:
            x0, y0, z0, x1, y1, z1 = piece.box()
            for command in sorted(Rig.tiled(x0, y0, z0, x1, y1, z1, "air"), key=self.chunk_of):
                words = command.split()
                self.ensure_loaded(int(words[1]), int(words[3]))
                self.probe.command(command, 20.0)
            self.log(f"cleared piece {piece.number}")

    def alive(self):
        """The probe reports the player with health above zero; a missing report is waited for, not read as a
        death (the first run pressed A twice because the probe had not reported yet)."""
        p = self.player()
        for _ in range(20):
            if p is not None:
                break
            p = self.tick()
        if p is None:
            self.log("  no report on the player")
            return False
        return (p.get("health") or {}).get("current", 1) > 0

    def respawn(self, controller):
        """The death screen's Respawn is A; pressed until the probe shows the player alive again."""
        for _ in range(6):
            if self.alive():
                return True
            self.log("  the player is dead: respawning")
            controller.press("a")
            time.sleep(4)
        return self.alive()


def ftue_client(outdir):
    """The FTUE skill's Client for screenshots, when it can be imported; None otherwise."""
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skill = os.environ.get("BARITONE_FTUE_DIR") or os.path.join(os.path.dirname(here), ".claude", "skills", "ftue")
        if skill not in sys.path:
            sys.path.insert(0, skill)
        import ftue   # noqa: the FTUE skill, only for Client.shot
        return ftue.Run("course", outdir).client
    except Exception as error:   # no skill on this machine: the report says so
        print(f"no screenshots: {error!r}", flush=True)
        return None


def run_course(pieces, probe, controller, who, outdir, shots=True, timeout=300, log=print, repeat=1, settings=None):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(outdir, stamp)
    os.makedirs(outdir, exist_ok=True)
    course = Course(probe, who, log)
    client = ftue_client(outdir) if shots else None
    ticks_path = os.path.join(outdir, "ticks.jsonl")
    ticks_file = open(ticks_path, "w", encoding="utf-8")
    current = {"piece": None}

    def on_tick(record):
        record["piece"] = current["piece"]
        ticks_file.write(json.dumps(record) + "\n")

    def say(text):
        log(f"  say: {text}")
        try:
            probe.command(f'tellraw @a {json.dumps({"rawtext": [{"text": "§7[nav] §f" + text}]})}', 5)
        except Exception:
            pass

    nav = Navigator(controller, probe, who, settings=settings if settings is not None else Settings(),
                    log=lambda text: log(f"    {text}"), say=say, on_tick=on_tick)
    rows = []
    cleared = probe.command(f'clear "{who}" dirt', 10)
    log(f"dirt cleared from the player before the run: {cleared.get('successCount')}")
    legs = [(piece, k, label, start, end) for piece in pieces for k, (label, start, end) in enumerate(piece.legs())]
    try:
        for run in range(1, repeat + 1):
            for piece, k, label, start, end in legs:
                letter = chr(ord("a") + k) if len(piece.legs()) > 1 else ""
                tag = f"{piece.number}{letter}"
                current["piece"] = tag
                if not course.respawn(controller):
                    log("the player is dead and will not respawn; stopping")
                    break
                sx, sy, sz = start
                ex, ey, ez = end
                log(f"piece {tag} ({label}){f' run {run}' if repeat > 1 else ''}: {sx},{sy},{sz} -> {ex},{ey},{ez}")
                course.tp(sx + 0.5, sy, sz + 0.5, yaw=yaw_to(1, 0))
                nav.stand_up()
                if client and run == 1:
                    client.shot(f"p{tag}_start")
                t0 = time.time()
                gathered = None
                if piece.number == 7:
                    gathered = nav.gather("minecraft:dirt", 12, timeout=150)
                    log(f"  gathered {gathered} dirt in {time.time() - t0:.0f} s")
                # the end marker with its height (GoalNear 1.5): a GoalXZ was once reached by a 139 block fall into
                # the lake under the course and a climb up the hillside, which Baritone allows (no cap on water)
                scans_before = len(probe.scan_stats)
                left = nav.go((ex, ey, ez), within=1.5, timeout=timeout)
                elapsed = time.time() - t0
                p = course.player() or {}
                done = bool(nav.arrived)     # the goal's own is_in_goal, not a flat distance (a diagonal neighbour is 1.8 off)
                stats = nav.last_search_stats
                nodes = sum(s["nodes"] for s in stats)
                ms = sum(s["ms"] for s in stats)
                walked = sum(s["length"] for s in stats if s["type"].startswith("SUCCESS"))
                scans = list(probe.scan_stats)[scans_before:]
                parkour = list(nav.parkour)
                row = {"piece": tag, "name": label, "run": run, "blocks": ex - sx, "seconds": round(elapsed, 1), "done": done,
                       "left": None if left is None else round(left, 2), "path_positions": walked, "segments": len(stats),
                       "nodes": nodes, "planner_ms": round(ms), "gathered": gathered, "dead": not course.alive(),
                       "jumps": nav.flights, "parkour": parkour,
                       "parkour_ok": sum(1 for r in parkour if r["status"] == "SUCCESS"),
                       "bands": len(scans), "band_ms": round(sum((s[1] or 0) for s in scans)),
                       "end": [round(p.get("x", 0), 1), round(p.get("y", 0), 1), round(p.get("z", 0), 1)]}
                rows.append(row)
                log(f"  {'done' if done else 'NOT done'} in {elapsed:.0f} s, left {left}, {walked} positions, {nodes} nodes, "
                    f"{ms:.0f} ms planning, {nav.flights} sprint jumps, parkour {row['parkour_ok']}/{len(parkour)}, "
                    f"{len(scans)} bands in {row['band_ms']} ms of pack time")
                if client and run == 1:
                    client.shot(f"p{tag}_end")
                ticks_file.flush()
            else:
                continue
            break
    finally:
        nav.stop()
        ticks_file.close()
        write_report(outdir, rows, stamp)
    return outdir, rows


def write_report(outdir, rows, stamp):
    lines = [f"# Course run {stamp}", "",
             "The old navigator (the FTUE skill's nav.py) took 4 to 8 minutes per 200 blocks on the gym tour: 1.2 to 2.4 s a block.",
             "", "| piece | name | run | blocks | time s | old nav s (est.) | done | left | positions | segments | nodes | planner ms | bands (pack ms) | jumps | parkour | gathered | notes |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        old = f"{r['blocks'] * 1.2:.0f}-{r['blocks'] * 2.4:.0f}"
        notes = "died" if r.get("dead") else ""
        parkour = r.get("parkour") or []
        parkour_cell = f"{r.get('parkour_ok', 0)}/{len(parkour)}" if parkour else ""
        bands = f"{r.get('bands', 0)} ({r.get('band_ms', 0)})" if r.get("bands") else ""
        lines.append(f"| {r['piece']} | {r['name']} | {r.get('run', 1)} | {r['blocks']} | {r['seconds']} | {old} | {'yes' if r['done'] else 'no'} | {r['left']} | "
                     f"{r['path_positions']} | {r['segments']} | {r['nodes']} | {r['planner_ms']} | {bands} | {r.get('jumps', 0)} | {parkour_cell} | "
                     f"{r['gathered'] if r['gathered'] is not None else ''} | {notes} |")
    lines += ["", "jumps: sprint jumps taken (settings.sprintJumping); parkour: parkour movements landed / attempted; "
              "bands (pack ms): bands the pack read for the piece and the script time they cost it.",
              "", "Ticks: ticks.jsonl (one line per executor tick: feet, yaw, sticks, held, movement, status, path position, flight)."]
    with open(os.path.join(outdir, "report.md"), "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    with open(os.path.join(outdir, "rows.json"), "w", encoding="utf-8") as file:
        json.dump(rows, file, indent=1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="build, run or clear the obstacle course")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--clear", action="store_true")
    parser.add_argument("--pieces", default="1,2,3,4,5,6,7,8,9")
    parser.add_argument("--repeat", type=int, default=1, help="run every piece (every leg) this many times")
    parser.add_argument("--no-sprint-jump", action="store_true", help="run with settings.sprintJumping off")
    parser.add_argument("--no-parkour", action="store_true", help="run with settings.allowParkour off")
    parser.add_argument("--out", default="captures/course")
    parser.add_argument("--layout", default="captures/course/layout.json", help="where --build records the origin for --run and --clear")
    parser.add_argument("--origin", nargs=2, type=int, default=None, metavar=("X", "Z"))
    parser.add_argument("--y", type=int, default=200)
    parser.add_argument("--player", default=None)
    parser.add_argument("--controller", default="ftue", help="ftue (the FTUE skill's Pad) or null")
    parser.add_argument("--port", type=int, default=Probe.PORT)
    parser.add_argument("--timeout", type=int, default=300, help="seconds per piece")
    parser.add_argument("--no-shots", action="store_true")
    args = parser.parse_args(argv)
    numbers = sorted({int(n) for n in args.pieces.split(",") if n.strip()})
    probe = Probe(args.port)
    print(f"waiting for the probe pack on 127.0.0.1:{probe.port}", flush=True)
    if not probe.connected(60):
        raise SystemExit("the probe pack is not posting: is the pack in the world and the player in it?")
    who = args.player
    if who is None:
        with probe.cv:
            names = [p["name"] for p in probe.players.values()]
        if not names:
            raise SystemExit("no player in the probe's reports")
        who = names[0]
    saved = {}
    if os.path.exists(args.layout):
        with open(args.layout, encoding="utf-8") as file:
            saved = json.load(file)
    p = probe.player(who, fresh=5.0) or {}
    if args.origin:
        ox, oz = args.origin
    elif saved.get("origin") and not args.build:
        ox, oz = saved["origin"]
    else:
        ox, oz = (math.floor(p.get("x", 0)) >> 4) * 16 + 8, (math.floor(p.get("z", 0)) >> 4) * 16 + 8
    y = args.y if args.build or not saved.get("y") else saved["y"]
    pieces = layout(ox, oz, y, numbers)
    course = Course(probe, who)
    if args.build:
        home = saved.get("home") or [p.get("x"), p.get("y"), p.get("z"), p.get("yaw"), p.get("pitch")]
        os.makedirs(os.path.dirname(args.layout) or ".", exist_ok=True)
        with open(args.layout, "w", encoding="utf-8") as file:
            json.dump({"origin": [ox, oz], "y": y, "home": home, "pieces": {n: {"x0": pc.x0, "x1": pc.x1, "z": pc.z, "start": pc.start, "end": pc.end}
                                                                          for n, pc in zip(numbers, pieces)}}, file, indent=1)
        course.build(pieces)
        print(f"built pieces {numbers} at origin {ox}, {oz}, y {y}; layout in {args.layout}", flush=True)
    elif args.clear:
        home = saved.get("home")
        if home and home[0] is not None:
            course.tp(home[0], home[1], home[2], yaw=home[3] or 0.0, pitch=home[4] or 0.0)
        else:
            probe.command(f'spreadplayers {ox} {oz + 200} 0 1 "{who}"', 20)
            course.tick(20)
        q = probe.player(who, fresh=5.0) or {}
        if abs(q.get("y", 0) - y) < 20 and ox - 2 <= q.get("x", 0) <= ox + 130:
            raise SystemExit("the player is still on the course; not clearing under them")
        course.clear(pieces)
    else:
        controller = make_controller(args.controller)
        settings = Settings(sprintJumping=not args.no_sprint_jump, allowParkour=not args.no_parkour)
        outdir, rows = run_course(pieces, probe, controller, who, args.out, shots=not args.no_shots, timeout=args.timeout,
                                  repeat=max(1, args.repeat), settings=settings)
        print(f"report: {os.path.join(outdir, 'report.md')}", flush=True)
        for r in rows:
            print(f"  piece {r['piece']}: {'done' if r['done'] else 'not done'} in {r['seconds']} s", flush=True)
    probe.close()


if __name__ == "__main__":
    main()
