"""Measures the numbers costs.py and control.py read, in a quartz rig built by command in the current test world,
and writes calibration.json (docs/baritone-port-plan.md, "Calibration").

    python -m baritone_bedrock.calibrate --controller ftue --out calibration.json [--player NAME] [--only speeds,jump]

The controller is the caller's (controller.py): --controller ftue imports the FTUE skill's Pad, which connects to the
pad host already running on this machine. The probe is this package's (probe.py): the pack must be in the world and
the player in it. Everything the rig needs is done by commands through the probe (fill, setblock, tp, a give of dirt
and survival mode while breaking and pillaring), the one place a give or a gamemode is allowed because it is a rig,
and it is all cleared again afterwards.

Every wait is counted in probe ticks rather than seconds, so the same code runs against the FakeProbe in
tests/test_calibrate.py, which advances its simulation a tick per wait_tick and answers at once. Wall-clock time is
read only for the millisecond columns of the latency section.
"""
import argparse
import datetime
import json
import math
import os
import platform
import statistics
import sys
import time

from .controller import NullController
from .costs import DEFAULT_SPEEDS
from .probe import Probe

EYE = 1.62   # the eyes above the feet (nav.py:489)


def yaw_to(dx, dz):
    """Bedrock yaw facing a direction: 0 is +z, -90 is +x (nav.py:491)."""
    return math.degrees(math.atan2(-dx, dz))


def wrap(degrees):
    return (degrees + 180) % 360 - 180


class Rig:
    """The platform and its pieces, placed relative to where the player stood. The platform is one layer at y, so
    the player's feet are at y + 1 on it: a flat lane along +x for the speed, jump, turn and latency runs; a step lane
    with a 1 block rise at x = ox; a 2 block wall across each lane's end; a quartz tub holding water 3 deep, 6 wide
    and 14 long for the swims; a spot for the blocks broken and a spot for the pillar."""

    HALF = 24

    def __init__(self, ox, oz, y=200):
        self.ox, self.oz, self.y = ox, oz, y
        f = self.feet = y + 1
        self.flat_start = (ox - 20 + 0.5, f, oz + 0.5)
        self.flat_stop = ox + 22
        self.step_edge = ox
        self.step_start = (ox - 6 + 0.5, f, oz + 8.5)
        self.pool = (ox - 3, ox + 2, oz - 22, oz - 9)      # x0, x1, z0, z1
        self.pool_start = (ox + 0.5, f, oz - 21.5)
        self.break_spots = {"dirt": (ox + 6, f, oz + 16), "stone": (ox + 6, f, oz + 18)}
        self.pillar_spot = (ox - 10 + 0.5, f, oz + 16.5)

    def box(self):
        """x0, y0, z0, x1, y1, z1 of everything the rig touches."""
        return (self.ox - self.HALF, self.y - 4, self.oz - self.HALF, self.ox + self.HALF - 1, self.y + 15, self.oz + self.HALF - 1)

    @staticmethod
    def tiled(x0, y0, z0, x1, y1, z1, block):
        """fill commands for a box, one per chunk column and eight levels: a fill through the pack's runCommand
        silently does nothing past about three chunks of extent (48x48 at one level came back with no success and
        no error while 46x46 and 16x16x8 worked), so the rig is laid in chunk-sized tiles."""
        out = []
        for cx in range(x0 >> 4, (x1 >> 4) + 1):
            for cz in range(z0 >> 4, (z1 >> 4) + 1):
                ax, bx = max(x0, cx * 16), min(x1, cx * 16 + 15)
                az, bz = max(z0, cz * 16), min(z1, cz * 16 + 15)
                for ay in range(y0, y1 + 1, 8):
                    out.append(f"fill {ax} {ay} {az} {bx} {min(ay + 7, y1)} {bz} {block}")
        return out

    def build(self):
        """The commands that make it, in chunk-sized tiles (see tiled)."""
        ox, oz, y = self.ox, self.oz, self.y
        x0, _, z0, x1, _, z1 = self.box()
        px0, px1, pz0, pz1 = self.pool
        t = self.tiled
        return (t(x0, y + 1, z0, x1, y + 15, z1, "air") + t(x0, y, z0, x1, y, z1, "quartz_block")
                + t(ox, y + 1, oz + 6, ox + 19, y + 1, oz + 10, "quartz_block")                      # the step
                + t(ox + 20, y + 2, oz + 6, ox + 20, y + 3, oz + 10, "quartz_block")                 # its end wall
                + t(self.flat_stop, y + 1, oz - 3, self.flat_stop, y + 2, oz + 3, "quartz_block")   # the flat lane's wall
                + t(px0 - 1, y - 3, pz0 - 1, px1 + 1, y, pz1 + 1, "quartz_block")                   # the tub
                + t(px0, y - 2, pz0, px1, y, pz1, "water"))

    def clear(self):
        """The commands that take it away again: air over the whole box, five levels a fill."""
        x0, y0, z0, x1, y1, z1 = self.box()
        return self.tiled(x0, y0, z0, x1, y1, z1, "air")


class Calibrator:
    """The measurements, one method per section of calibration.json."""

    SECTIONS = ("speeds", "jump", "step_up", "turn", "latency", "break", "pillar")

    def __init__(self, controller, probe, player, log=print, trials=3, rounds=4):
        self.pad, self.probe, self.who, self.log = controller, probe, player, log
        self.trials, self.rounds = trials, rounds
        self.notes, self.rig, self.home, self.mode = [], None, None, None
        self.latency_ticks = 0

    # -------------------------------------------------------------------------------------------- waiting, in ticks

    def tick(self):
        """The next report on the player: one probe tick."""
        n = self.probe.ticks
        for _ in range(20):
            if self.probe.wait_tick(n, timeout=0.5) > n:
                break
        else:
            raise RuntimeError("the probe stopped reporting")
        p = self.probe.player(self.who, fresh=5.0)
        if p is None:
            raise RuntimeError(f"no report on {self.who!r}")
        return p

    def ticks(self, n):
        p = None
        for _ in range(n):
            p = self.tick()
        return p

    def settle(self, n=10):
        """Let everything go and wait n ticks."""
        self.pad.state()
        return self.ticks(n)

    def stand_up(self):
        """Out of a crouch. Crouch is a toggle on this client: a B held for the sneak speed left the player
        crouched through the jump and step-up sections of the first run (jump lengths of 0.795 at sneak speed), and
        the options file's togglecrouch_gamepad:0 is not what the game does. The probe's sneak says."""
        for _ in range(3):
            p = self.settle(3)
            if not p.get("sneak"):
                return p
            self.pad.press("b")
            self.ticks(5)
        raise RuntimeError("the player stays crouched after three B taps")

    # ------------------------------------------------------------------------------------------------- the world

    def run(self, command, timeout=10.0):
        result = self.probe.command(command, timeout)
        # a fill that changes nothing (air over air, quartz over a tile a previous run left) is reported by the game
        # as no success with no error; the rig is verified afterwards (verify_rig), so that is let through
        changed_nothing = command.startswith(("fill", "setblock")) and not result.get("error")
        if not result.get("ok") and not changed_nothing:
            raise RuntimeError(f"{command}: {result.get('error') or 'no success'}")
        return result

    def verify_rig(self):
        """The rig's floor, step, walls and water are there: testforblock on one block of each, since a fill that
        did nothing and a fill that could not run both come back as no success."""
        rig, y = self.rig, self.rig.y
        px0, px1, pz0, pz1 = rig.pool
        for x, by, z, block in ((rig.ox, y, rig.oz, "quartz_block"), (rig.ox - 20, y, rig.oz + 20, "quartz_block"),
                                (rig.ox + 19, y + 1, rig.oz + 8, "quartz_block"), (rig.flat_stop, y + 2, rig.oz, "quartz_block"),
                                ((px0 + px1) // 2, y, (pz0 + pz1) // 2, "water")):
            r = self.probe.command(f"testforblock {x} {by} {z} {block}", 10)
            if not r.get("ok"):
                raise RuntimeError(f"the rig is not there: no {block} at {x} {by} {z} ({r.get('error') or 'no success'})")

    def tp(self, x, y, z, yaw=None, pitch=None, timeout_ticks=60):
        """Teleport the player, facing yaw and pitch when given, and wait until the probe shows it there and settled."""
        rot = "" if yaw is None else f" {yaw:.1f} {0.0 if pitch is None else pitch:.1f}"
        self.run(f'tp "{self.who}" {x:.2f} {y:.2f} {z:.2f}{rot}')
        for _ in range(timeout_ticks):
            p = self.tick()
            if math.hypot(p["x"] - x, p["z"] - z) < 1.0 and abs(p["y"] - y) < 1.5 and (p["ground"] or p["water"]):
                break
        return self.settle(10)

    def look_at(self, point, block=None, timeout_ticks=120, tolerance=2.0):
        """Turn the camera with the right stick until the eyes point at point, or, with block (x, y, z), until the
        probe says the crosshair is on that block: the game's own answer (nav.py:469-531, without the entity case)."""
        try:
            for _ in range(timeout_ticks):
                p = self.tick()
                dx, dy, dz = point[0] - p["x"], point[1] - (p["y"] + EYE), point[2] - p["z"]
                yaw_error = wrap(yaw_to(dx, dz) - p["yaw"])
                pitch_error = -math.degrees(math.atan2(dy, math.hypot(dx, dz))) - p["pitch"]   # down is positive
                if block is not None:
                    b = p.get("block") or {}
                    on = (b.get("x"), b.get("y"), b.get("z")) == tuple(block)
                else:
                    on = abs(yaw_error) <= tolerance and abs(pitch_error) <= tolerance
                if on:
                    self.pad.state()
                    return True
                self.pad.state(right=(self.stick(yaw_error, tolerance), -self.stick(pitch_error, tolerance)))
            return False
        finally:
            self.pad.state()

    @staticmethod
    def stick(error, tolerance):
        """Right stick deflection for an angular error (nav.py:523-526)."""
        if abs(error) <= tolerance * 0.5:
            return 0.0
        v = max(-1.0, min(1.0, error / 30))
        return math.copysign(max(abs(v), 0.15), v)

    def select_hand(self, wanted, tries=9):
        """RB until the held item satisfies wanted(item) (item is None for an empty hand); False if no slot does."""
        for _ in range(tries):
            if wanted(self.probe.hand(self.who)):
                return True
            self.pad.press("rb")
            self.ticks(4)
        return bool(wanted(self.probe.hand(self.who)))

    def survival(self):
        """Survival for the measurements that break and place, remembering the mode to put back."""
        if self.mode is None:
            for mode in ("survival", "creative", "adventure", "spectator"):
                if self.probe.command(f'testfor @a[name="{self.who}",m={mode}]').get("ok"):
                    self.mode = mode
                    break
            else:
                self.mode = "unknown"
        if self.mode != "survival":
            self.run(f'gamemode survival "{self.who}"')
            self.ticks(5)
            if not any(n.startswith("gamemode:") for n in self.notes):
                self.notes.append(f"gamemode: the player was in {self.mode}; survival for breaking and pillaring, {self.mode} again afterwards")

    def restore_mode(self):
        if self.mode and self.mode not in ("survival", "unknown"):
            self.probe.command(f'gamemode {self.mode} "{self.who}"')

    # ------------------------------------------------------------------------------------------------- the runs

    def speed_run(self, start, yaw, held=(), sprint=False, warm=10, ticks=60):
        """Blocks a second along the left stick's forward over ticks, after warm ticks to get up to speed."""
        self.tp(*start, yaw=yaw, pitch=0.0)
        if sprint:   # the sprint click: LS held a moment with forward, then forward alone
            self.pad.state(left=(0.0, 1.0), held=("ls",) + tuple(held))
            self.ticks(4)
        self.pad.state(left=(0.0, 1.0), held=tuple(held))
        self.ticks(warm)
        p0 = self.tick()
        p1 = self.ticks(ticks)
        self.pad.state()
        game_ticks = (p1.get("tick") or 0) - (p0.get("tick") or 0)
        dt = game_ticks / 20.0 if game_ticks > 0 else ticks / 20.0
        return math.hypot(p1["x"] - p0["x"], p1["z"] - p0["z"]) / dt, p1

    def speeds(self):
        rig, east, south, out = self.rig, yaw_to(1, 0), yaw_to(0, 1), {}
        out["walk"], _ = self.speed_run(rig.flat_start, east)
        out["sprint"], p = self.speed_run(rig.flat_start, east, sprint=True)
        if not p.get("sprint"):
            self.notes.append("sprint: isSprinting was false at the end of the sprint run (the LS click may not have taken)")
        out["sneak"], _ = self.speed_run(rig.flat_start, east, held=("b",))
        self.stand_up()   # B is a toggle here: the run above left the crouch on
        out["walk_in_water"], _ = self.speed_run(rig.pool_start, south, warm=15)
        out["swim"], _ = self.speed_run(rig.pool_start, south, sprint=True, warm=15)
        return {k: round(v, 3) for k, v in out.items()}

    def jump(self):
        rig, east = self.rig, yaw_to(1, 0)
        apexes, airs, vys = [], [], []
        for _ in range(self.trials):
            p = self.tp(*rig.flat_start, yaw=east, pitch=0.0)
            y0 = p["y"]
            self.pad.state(held=("a",))
            self.ticks(2)
            self.pad.state()
            top, vy, air, left = y0, 0.0, 0, False
            for _ in range(40):
                p = self.tick()
                top, vy = max(top, p["y"]), max(vy, p["vy"])
                if not p["ground"]:
                    left, air = True, air + 1
                elif left:
                    break
            apexes.append(top - y0)
            airs.append(air)
            vys.append(vy)
        out = {"apex": round(statistics.median(apexes), 3), "air_ticks": int(statistics.median(airs)), "max_vy": round(statistics.median(vys), 4)}
        for name, sprint in (("sprint_jump_length", True), ("walk_jump_length", False)):
            lengths = []
            for _ in range(self.trials):
                self.tp(*rig.flat_start, yaw=east, pitch=0.0)
                if sprint:
                    self.pad.state(left=(0.0, 1.0), held=("ls",))
                    self.ticks(4)
                self.pad.state(left=(0.0, 1.0))
                self.ticks(12)
                self.pad.state(left=(0.0, 1.0), held=("a",))
                self.ticks(2)
                self.pad.state(left=(0.0, 1.0))
                takeoff, land, last_ground = None, None, self.tick()
                for _ in range(40):
                    p = self.tick()
                    if takeoff is None:
                        if not p["ground"]:
                            takeoff = last_ground
                        else:
                            last_ground = p
                    elif p["ground"]:
                        land = p
                        break
                self.pad.state()
                if takeoff and land:
                    lengths.append(math.hypot(land["x"] - takeoff["x"], land["z"] - takeoff["z"]))
            out[name] = round(statistics.median(lengths), 3) if lengths else None
        return out

    def jump_trace(self, start, yaw, sprint=True, trials=None):
        """A sprint (or walk) jump on open flat ground at start (x, y, z) facing yaw, traced tick by tick from the
        probe: x, y, z, vx, vy, vz, ground and sprint for every report from the A tap until three reports back on
        the ground. The length is from the last report on the ground before the flight to the first on it after
        (the probe's ground flag can lag the game a tick, so this is a lower bound and the trace is the measurement),
        with the per-tick horizontal steps of the flight beside it: SprintJump.java's numbers (sprintjump.py) say
        0.48 on the jump tick and about 0.29 a tick after it for a sprint, 2.33 and 0.20 for a walk, 12 ticks in the
        air. `velocity_is_displacement` says whether the probe's vx is the tick's displacement (the executor's
        assumption) or something else. Returns the median trial's numbers and every trace."""
        traces = []
        for _ in range(trials or self.trials):
            self.tp(start[0], start[1], start[2], yaw=yaw, pitch=0.0)
            self.stand_up()
            if sprint:
                self.pad.state(left=(0.0, 1.0), held=("ls",))
                self.ticks(4)
            self.pad.state(left=(0.0, 1.0))
            self.ticks(16)
            self.pad.state(left=(0.0, 1.0), held=("a",))
            trace, grounded = [], 0
            for i in range(60):
                p = self.tick()
                if i == 1:
                    self.pad.state(left=(0.0, 1.0))
                trace.append({"tick": p["tick"], "x": round(p["x"], 4), "y": round(p["y"], 4), "z": round(p["z"], 4),
                              "vx": round(p["vx"], 4), "vy": round(p["vy"], 4), "vz": round(p["vz"], 4),
                              "ground": bool(p["ground"]), "sprint": bool(p["sprint"])})
                if any(not r["ground"] for r in trace):
                    grounded = grounded + 1 if p["ground"] else 0
                    if grounded >= 3:
                        break
            self.pad.state()
            traces.append(trace)
        out = {"trials": []}
        for trace in traces:
            air = [i for i, r in enumerate(trace) if not r["ground"]]
            if not air:
                out["trials"].append({"length": None, "air_ticks": 0})
                continue
            k0, k1 = air[0], air[-1]
            before, after = trace[max(0, k0 - 1)], trace[min(len(trace) - 1, k1 + 1)]
            steps = [round(math.hypot(trace[i]["x"] - trace[i - 1]["x"], trace[i]["z"] - trace[i - 1]["z"]), 4) for i in range(k0, k1 + 2) if i < len(trace) and i > 0]
            out["trials"].append({"length": round(math.hypot(after["x"] - before["x"], after["z"] - before["z"]), 3),
                                  "air_ticks": k1 - k0 + 1, "apex": round(max(r["y"] for r in trace) - before["y"], 3),
                                  "steps": steps, "takeoff_step": steps[0] if steps else None,
                                  "air_step": round(statistics.median(steps[1:-1]), 4) if len(steps) > 3 else None})
        good = [t for t in out["trials"] if t["length"] is not None]
        if good:
            median = sorted(good, key=lambda t: t["length"])[len(good) // 2]
            out["length"] = median["length"]
            out["air_ticks"] = median["air_ticks"]
            out["apex"] = median["apex"]
            out["takeoff_step"] = median["takeoff_step"]
            out["air_step"] = median["air_step"]
            trace = traces[out["trials"].index(median)]
            moving = [r for r in trace[1:] if abs(r["vx"]) + abs(r["vz"]) > 0.05]
            if moving:
                i = trace.index(moving[0])
                disp = math.hypot(trace[i]["x"] - trace[i - 1]["x"], trace[i]["z"] - trace[i - 1]["z"])
                out["velocity_is_displacement"] = abs(math.hypot(trace[i]["vx"], trace[i]["vz"]) - disp) < 0.03
            out["trace"] = trace
        return out

    def step_trial(self, distance, sprint):
        """Walk (or sprint) at the step and tap A when the probe puts the player distance blocks short of the edge;
        True if the player is standing on the step afterwards."""
        rig = self.rig
        self.tp(*rig.step_start, yaw=yaw_to(1, 0), pitch=0.0)
        if sprint:
            self.pad.state(left=(0.0, 1.0), held=("ls",))
            self.ticks(4)
        self.pad.state(left=(0.0, 1.0))
        tapped, since = False, 0
        try:
            for _ in range(100):
                p = self.tick()
                if not tapped:
                    if p["x"] >= rig.step_edge - distance:
                        self.pad.state(left=(0.0, 1.0), held=("a",))
                        self.ticks(2)
                        self.pad.state(left=(0.0, 1.0))
                        tapped = True
                else:
                    since += 1
                    if p["ground"] and since > 4:
                        return p["y"] >= rig.feet + 0.9 and p["x"] >= rig.step_edge
            return False
        finally:
            self.pad.state()

    def step_up(self):
        """The largest distance from the step at which an A tap still lands it: bisection over 0.4..1.6, the trials
        at each midpoint deciding by majority."""
        out, trials = {}, {}
        for name, sprint in (("walk", False), ("sprint", True)):
            lo, hi, log = 0.4, 1.6, []
            for _ in range(self.rounds):
                mid = round((lo + hi) / 2, 3)
                wins = sum(self.step_trial(mid, sprint) for _ in range(self.trials))
                log.append([mid, wins, self.trials])
                if wins * 2 > self.trials:
                    lo = mid
                else:
                    hi = mid
            out[name], trials[name] = lo, log
            if not any(w * 2 > t for _, w, t in log):
                self.notes.append(f"step_up {name}: no midpoint landed the step; {lo} is the untested lower bound")
        out["trials"] = trials
        return out

    def turn(self):
        """Degrees a second at three right stick deflections, each held a second, and the ticks from the first
        movement to nine tenths of the steady rate."""
        out = {}
        for deflection in (0.25, 0.5, 1.0):
            p = self.tp(*self.rig.flat_start, yaw=yaw_to(1, 0), pitch=0.0)
            yaws, ticks = [p["yaw"]], [p.get("tick") or 0]
            self.pad.state(right=(deflection, 0.0))
            for _ in range(20):
                p = self.tick()
                yaws.append(p["yaw"])
                ticks.append(p.get("tick") or ticks[-1] + 1)
            self.settle(10)
            deltas = [wrap(b - a) / max(1, tb - ta) for a, b, ta, tb in zip(yaws, yaws[1:], ticks, ticks[1:])]   # degrees a game tick
            steady = statistics.median(deltas[-8:])
            moving = [i for i, d in enumerate(deltas) if abs(d) > 0.05]
            first = moving[0] if moving else None
            full = next((i for i, d in enumerate(deltas) if abs(d) >= 0.9 * abs(steady)), None) if steady else None
            out[str(deflection)] = {"deg_per_s": round(abs(steady) * 20, 2),
                                    "ramp_ticks": (full - first) if first is not None and full is not None else None,
                                    "total_deg": round(abs(sum(wrap(b - a) for a, b in zip(yaws, yaws[1:]))), 1)}
        return out

    def latency(self):
        """Probe ticks (and milliseconds) from a state() call to the first report showing it: a stick move and a
        jump, ten each."""
        rig, east, stick, jump = self.rig, yaw_to(1, 0), [], []
        for i in range(10):
            if i % 5 == 0:
                self.tp(*rig.flat_start, yaw=east, pitch=0.0)
            p0 = self.settle(8)
            n0, t0 = self.probe.ticks, time.time()
            self.pad.state(left=(0.0, 1.0))
            for _ in range(40):
                p = self.tick()
                if math.hypot(p["x"] - p0["x"], p["z"] - p0["z"]) > 0.02 or math.hypot(p["vx"], p["vz"]) > 0.02:
                    stick.append([p["n"] - n0, round((p["at"] - t0) * 1000, 1)])
                    break
            self.pad.state()
        for _ in range(10):
            self.settle(8)
            n0, t0 = self.probe.ticks, time.time()
            self.pad.state(held=("a",))
            for _ in range(40):
                p = self.tick()
                if not p["ground"] or p["vy"] > 0.05:
                    jump.append([p["n"] - n0, round((p["at"] - t0) * 1000, 1)])
                    break
            self.pad.state()
            for _ in range(30):
                if self.tick()["ground"]:
                    break

        def median(samples, k):
            return round(statistics.median(s[k] for s in samples), 1) if samples else None

        self.latency_ticks = int(median(stick, 0) or 0)
        return {"stick_ticks": median(stick, 0), "stick_ms": median(stick, 1), "jump_ticks": median(jump, 0), "jump_ms": median(jump, 1),
                "samples": {"stick": stick, "jump": jump}}

    def breaking(self):
        """Ticks to break dirt and stone by hand, from the probe's break event, with the measured latency taken off."""
        rig, out = self.rig, {}
        self.survival()
        if not self.select_hand(lambda item: item is None):
            self.notes.append("break: no empty hotbar slot, so dug with the held item")
        for name in ("dirt", "stone"):
            bx, by, bz = rig.break_spots[name]
            self.run(f"setblock {bx} {by} {bz} {name}")
            self.tp(bx - 2.5, by, bz + 0.5, yaw=yaw_to(1, 0), pitch=20.0)
            if not self.look_at((bx + 0.5, by + 0.5, bz + 0.5), block=(bx, by, bz)):
                self.notes.append(f"break {name}: the crosshair never settled on the block")
                out[name] = None
                continue
            self.probe.events()
            p0, n0, event = self.tick(), self.probe.ticks, None
            self.pad.state(held=("rt",))
            for _ in range(20 * 40):   # stone by hand is about 150 ticks
                self.tick()
                for e in self.probe.events():
                    if e.get("type") == "break" and (e.get("x"), e.get("y"), e.get("z")) == (bx, by, bz):
                        event = e
                if event:
                    break
            self.pad.state()
            if event is None:
                self.notes.append(f"break {name}: no break event within 40 s")
                out[name] = None
                continue
            raw = int(event.get("tick", 0) - (p0.get("tick") or 0))
            out[name] = {"ticks": max(0, raw - self.latency_ticks), "raw_ticks": raw, "probe_ticks": self.probe.ticks - n0}
        return out

    def pillar(self):
        """The delay from an A tap to the LT tap that places a dirt block under the feet: two trials at each of one
        to eight ticks, the window that always worked and the middle of it."""
        rig = self.rig
        x, y, z = rig.pillar_spot
        bx, bz = math.floor(x), math.floor(z)
        self.survival()
        self.run(f'give "{self.who}" dirt 16')
        self.ticks(10)
        if not self.select_hand(lambda item: bool(item) and item.get("type") == "minecraft:dirt"):
            self.notes.append("pillar: no dirt in the hotbar after the give")
            return None
        results = {}
        for delay in range(1, 9):
            wins = []
            for _ in range(2):
                self.run(f"fill {bx} {y} {bz} {bx} {y + 14} {bz} air")
                self.tp(x, y, z, yaw=yaw_to(1, 0), pitch=90.0)
                b = self.tick().get("block") or {}
                if (b.get("x"), b.get("y"), b.get("z")) != (bx, y - 1, bz):
                    self.look_at((bx + 0.5, y - 0.5, bz + 0.5), block=(bx, y - 1, bz))
                self.probe.events()
                self.pad.state(held=("a",))
                self.ticks(2)
                self.pad.state()
                if delay > 2:
                    self.ticks(delay - 2)
                self.pad.state(held=("lt",))
                self.ticks(2)
                self.pad.state()
                placed, p = False, None
                for _ in range(20):
                    p = self.tick()
                    for e in self.probe.events():
                        if e.get("type") == "place" and (e.get("x"), e.get("y"), e.get("z")) == (bx, y, bz):
                            placed = True
                    if placed and p["ground"]:
                        break
                wins.append(bool(placed and p and p["y"] >= y + 0.9))
            results[delay] = wins
        self.run(f"fill {bx} {y} {bz} {bx} {y + 14} {bz} air")
        good = [d for d, w in results.items() if all(w)]
        best = good[len(good) // 2] if good else None
        return {"delay_ticks": best, "delay_ms": best * 50 if best else None,
                "window_ticks": [min(good), max(good)] if good else None, "trials": {str(d): w for d, w in results.items()}}

    # -------------------------------------------------------------------------------------------------- all of it

    def run_all(self, only=None):
        p = self.tick()
        self.home = (p["x"], p["y"], p["z"], p["yaw"], p["pitch"])
        y = 200 if p["y"] < 180 else min(300, int(p["y"]) + 40)
        self.rig = Rig(math.floor(p["x"]), math.floor(p["z"]), y)
        out = {"client": {"platform": platform.platform(), "controller": type(self.pad).__name__, "player": self.who,
                          "probe_tick_rate": round(self.probe.tick_rate(), 1)},
               "date": datetime.datetime.now().isoformat(timespec="seconds"),
               "rig": {"origin": [self.rig.ox, self.rig.oz], "y": y}}
        try:
            self.log(f"building the rig at {self.rig.ox}, {y}, {self.rig.oz}")
            for command in self.rig.build():
                self.run(command, timeout=20.0)
            self.ticks(20)
            self.verify_rig()
            for name in self.SECTIONS:
                if only and name not in only:
                    continue
                self.log(name)
                try:
                    self.stand_up()
                    out[name] = getattr(self, "breaking" if name == "break" else name)()
                    self.log(f"  {json.dumps(out[name])}")
                except Exception as error:   # one section failing must not lose the others
                    self.notes.append(f"{name}: {error!r}")
                    out[name] = None
                    self.log(f"  failed: {error!r}")
        finally:
            self.pad.state()
            self.cleanup()
        out["notes"] = list(self.notes)
        return out

    def cleanup(self):
        """The world as it was: the player's mode and place, the dirt given taken back, the rig filled with air."""
        try:
            self.restore_mode()
            self.probe.command(f'clear "{self.who}" dirt 0 16')
            x, y, z, yaw, pitch = self.home
            self.probe.command(f'tp "{self.who}" {x:.2f} {y:.2f} {z:.2f} {yaw:.1f} {pitch:.1f}')
            # home may have been water (the player drowned there after the first run on this PC): the surface then
            p = self.ticks(10)
            if p.get("water"):
                self.notes.append("cleanup: home was in water, the player was put on the surface nearby instead")
                self.probe.command(f'spreadplayers {x:.1f} {z:.1f} 0 1 "{self.who}"')
                self.ticks(20)
            for command in self.rig.clear():
                self.probe.command(command, timeout=20.0)
        except Exception as error:
            self.notes.append(f"cleanup: {error!r}")


def make_controller(name):
    """The caller's controller by name: "ftue" is the FTUE skill's Pad, connected to the pad host already running on
    this machine; "null" moves nothing."""
    if name == "null":
        return NullController()
    if name == "ftue":
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skill = os.environ.get("BARITONE_FTUE_DIR") or os.path.join(os.path.dirname(here), ".claude", "skills", "ftue")
        sys.path.insert(0, skill)
        from ftue import Pad   # noqa: the FTUE skill's controller, see controller.py
        return Pad()
    raise SystemExit(f"unknown controller {name!r}")


def write(result, path, only=None):
    """Write the calibration; with only, the sections measured replace those in the file already there, so a
    section can be run again on its own."""
    if only and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            previous = json.load(file)
        merged = dict(previous)
        for key in list(only) + ["client", "date", "rig"]:
            if key in result:
                merged[key] = result[key]
        about = lambda note, keys: any(note.startswith(k + ":") or note.startswith(k + ".") for k in keys)
        merged["notes"] = ([n for n in previous.get("notes", []) if not about(n, only)]
                           + [n for n in result.get("notes", []) if about(n, only) or n.startswith("cleanup")])
        result = merged
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=1)
        file.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="measure the client's speeds, jump, step-up, turn, latency, break and pillar numbers in a rig built by command")
    parser.add_argument("--controller", default="ftue", help="ftue (the FTUE skill's Pad) or null")
    parser.add_argument("--out", default="calibration.json")
    parser.add_argument("--player", default=None, help="the player's name; the first one the probe reports when left out")
    parser.add_argument("--only", default=None, help="comma separated sections: " + ",".join(Calibrator.SECTIONS))
    parser.add_argument("--port", type=int, default=Probe.PORT)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--trace-jump", nargs=3, type=float, default=None, metavar=("X", "Y", "Z"),
                        help="only the per-tick jump trace, from this open flat spot (no rig): written under jump as "
                             "sprint_jump_length_measured, walk_jump_length_measured and sprint_jump_trace")
    parser.add_argument("--yaw", type=float, default=-90.0, help="the facing for --trace-jump (-90 is +x)")
    args = parser.parse_args(argv)
    controller = make_controller(args.controller)
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
    print(f"calibrating {who!r} with {type(controller).__name__}, probe at {probe.tick_rate():.1f} reports a second", flush=True)
    if args.trace_jump:
        calibrator = Calibrator(controller, probe, who, trials=args.trials)
        sprint = calibrator.jump_trace(args.trace_jump, args.yaw, sprint=True)
        walk = calibrator.jump_trace(args.trace_jump, args.yaw, sprint=False)
        calibrator.pad.state()
        previous = {}
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as file:
                previous = json.load(file)
        jump = dict(previous.get("jump") or {})
        jump["sprint_jump_length_measured"] = sprint.get("length")
        jump["walk_jump_length_measured"] = walk.get("length")
        jump["sprint_jump_trace"] = {k: v for k, v in sprint.items() if k != "trials"}
        jump["walk_jump_trace"] = {k: v for k, v in walk.items() if k != "trials"}
        jump["jump_trace_trials"] = {"sprint": sprint["trials"], "walk": walk["trials"]}
        previous["jump"] = jump
        with open(args.out, "w", encoding="utf-8") as file:
            json.dump(previous, file, indent=1)
            file.write("\n")
        print(f"sprint jump {sprint.get('length')} blocks in {sprint.get('air_ticks')} air ticks (takeoff step {sprint.get('takeoff_step')}, "
              f"air step {sprint.get('air_step')}, velocity is displacement: {sprint.get('velocity_is_displacement')}); "
              f"walk jump {walk.get('length')}; wrote {args.out}", flush=True)
        probe.close()
        return previous
    only = set(args.only.split(",")) if args.only else None
    result = Calibrator(controller, probe, who, trials=args.trials).run_all(only)
    for key in DEFAULT_SPEEDS:
        if key not in (result.get("speeds") or {}):
            result.setdefault("notes", []).append(f"speeds.{key}: not measured, costs.py keeps Baritone's {DEFAULT_SPEEDS[key]}")
    write(result, args.out, only)
    print(f"wrote {args.out}", flush=True)
    probe.close()
    return result


if __name__ == "__main__":
    main()
