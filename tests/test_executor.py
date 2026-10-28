"""The executor on a simulated player: a small kinematic model stepping Java's physics (walk 0.216 blocks a tick,
sprint 0.281, jump 0.42 with gravity 0.08 and drag 0.98, a right stick through the calibrated turn curve, one tick
of input latency between the pad and the report) on DictWorld fixtures, driven through the real Body, PathExecutor
and PathingBehavior. Plus the sprint policy decisions, Body's stick mapping and the sneak toggle logic."""
import math
import os
import sys
import time
import unittest
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.astar import AStarPathFinder, PathCalculationResult                # noqa: E402
from baritone_bedrock.context import CalculationContext                                 # noqa: E402
from baritone_bedrock.control import Body, Player, MovementState, TurnCurve, yaw_to, wrap, EYE   # noqa: E402
from baritone_bedrock.controller import NullController                                  # noqa: E402
from baritone_bedrock.executor import PathExecutor, PathingBehavior, PathEvent, TickState   # noqa: E402
from baritone_bedrock.goals import GoalXZ, GoalBlock                                    # noqa: E402
from baritone_bedrock.moves import (MovementStatus, MovementTraverse, MovementAscend, MovementDescend,
                                    MovementDiagonal, MovementPillar, MovementParkour)  # noqa: E402
from baritone_bedrock.settings import Settings                                          # noqa: E402
from baritone_bedrock.world import WALK_THROUGH, classify                               # noqa: E402
from tests.fixtures import flat, clear, wall, cliff, STONE, DIRT                        # noqa: E402

Y = 64   # the feet on the flat fixture

CALIBRATION = {"turn": {"0.25": {"deg_per_s": 0.0}, "0.5": {"deg_per_s": 25.6}, "1.0": {"deg_per_s": 150.0}},
               "latency": {"stick_ticks": 1, "jump_ticks": 2}, "jump": {"air_ticks": 9, "apex": 1.252},
               "step_up": {"walk": 1.525, "sprint": 1.525}, "pillar": {"delay_ticks": 5},
               "speeds": {"walk": 4.317, "sprint": 5.612}}


class SimPlayer:
    """The player under a NullController's sticks, one step a tick: Java's physics in the small, on a DictWorld.
    Latency: the report of tick n shows the controller as it was LATENCY ticks before, so a state set after report
    n first shows in report n + 2 (the CALIBRATION above says jump_ticks 2 for that reason; the client measured 1)."""

    LATENCY = 1
    WALK, SPRINT, SNEAK = 4.317 / 20, 5.612 / 20, 1.3 / 20
    JUMP, GRAVITY, DRAG, AIR_DRAG = 0.42, 0.08, 0.98, 0.91
    GROUND_FRICTION = 0.6 * 0.91                 # LivingEntity.travel's horizontal friction on plain ground
    AIR_ACCEL_SPRINT, AIR_ACCEL_WALK = 0.026 * 0.98, 0.02 * 0.98   # Player.getFlyingSpeed times aiStep's 0.98
    SPRINT_JUMP_BOOST = 0.2                      # jumpFromGround, along the facing
    HALF = 0.3
    BREAK = {"minecraft:dirt": 15, "minecraft:stone": 150, "minecraft:grass_block": 18}

    def __init__(self, world, pad, at, yaw=0.0, hotbar=None, name="Sim"):
        self.world, self.pad, self.name = world, pad, name
        self.x, self.y, self.z, self.yaw, self.pitch = float(at[0]), float(at[1]), float(at[2]), float(yaw), 0.0
        self.vx = self.vz = self.vy = 0.0
        self.mx = self.mz = 0.0      # Java's deltaMovement after a tick: the displacement times the tick's friction
        self.ground, self.sneak, self.sprinting = True, False, False
        self.slots = list(hotbar) if hotbar else [None] * 9
        self.slot = 0
        self.turn = TurnCurve(CALIBRATION["turn"])
        self.history = deque()
        self.prev_held = frozenset()
        self.breaking = None
        self.frozen = False
        self.ticks, self.game_tick = 0, 1000
        self._events, self.report, self.health = [], None, 20
        self.collided = False
        self._tick()

    # ---- the world ----

    def solid(self, bx, by, bz):
        return not (self.world.kind(bx, by, bz).flags & WALK_THROUGH)

    def corners(self, x, z):
        h = self.HALF
        return ((x - h, z - h), (x + h, z - h), (x - h, z + h), (x + h, z + h))

    def blocked(self, x, y, z):
        """The player's box at (x, y, z) meets a solid block at feet or head level."""
        fy = math.floor(y + 1e-6)
        for cx, cz in self.corners(x, z):
            if self.solid(math.floor(cx), fy, math.floor(cz)) or self.solid(math.floor(cx), fy + 1, math.floor(cz)):
                return True
        return False

    def floor_below(self, x, z, y):
        """The highest solid top at or under the feet across the box's corners."""
        best = -64.0
        start = math.floor(y + 1e-6)
        for cx, cz in self.corners(x, z):
            bx, bz = math.floor(cx), math.floor(cz)
            for by in range(start, start - 12, -1):
                if self.solid(bx, by, bz) and by + 1 <= y + 1e-6:
                    best = max(best, by + 1.0)
                    break
        return best

    def supported(self, x, z, y):
        """Some corner stands over a block top at the feet height (the sneak edge rule)."""
        for cx, cz in self.corners(x, z):
            if self.solid(math.floor(cx), math.floor(y + 1e-6) - 1, math.floor(cz)):
                return True
        return False

    def crosshair(self):
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        dx, dy, dz = -math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)
        ex, ey, ez = self.x, self.y + EYE, self.z
        prev = (math.floor(ex), math.floor(ey), math.floor(ez))
        t = 0.02
        while t <= 6.0:
            cell = (math.floor(ex + dx * t), math.floor(ey + dy * t), math.floor(ez + dz * t))
            if cell != prev and self.solid(*cell):
                if cell[1] != prev[1]:
                    face = "Up" if prev[1] > cell[1] else "Down"
                elif cell[0] != prev[0]:
                    face = "West" if prev[0] < cell[0] else "East"
                else:
                    face = "North" if prev[2] < cell[2] else "South"
                return {"type": self.world.kind(*cell).type_id, "x": cell[0], "y": cell[1], "z": cell[2], "face": face}
            prev = cell
            t += 0.02
        return None

    # ---- a tick ----

    def _tick(self):
        self.ticks += 1
        self.game_tick += 1
        self.history.append((self.pad.left, self.pad.right, frozenset(self.pad.held)))
        left, right, held = self.history[0] if len(self.history) <= self.LATENCY else self.history[-1 - self.LATENCY]
        while len(self.history) > self.LATENCY + 1:
            self.history.popleft()
        rising = held - self.prev_held
        self.prev_held = held
        # the camera: a rate through the curve
        rx, ry = right
        self.yaw = wrap(self.yaw + math.copysign(self.turn.rate(rx), rx) / 20.0) if rx else self.yaw
        self.pitch = max(-90.0, min(90.0, self.pitch - math.copysign(self.turn.rate(ry), ry) / 20.0)) if ry else self.pitch
        # toggles and taps
        if "b" in rising:
            self.sneak = not self.sneak
        if "rb" in rising:
            self.slot = (self.slot + 1) % 9
        forward = left[1]
        if "ls" in held and forward >= 0.8 and self.ground:
            self.sprinting = True
        if forward < 0.8 or self.sneak:
            self.sprinting = False
        jumped = False
        if "a" in rising and self.ground and not self.frozen:
            self.vy, self.ground, jumped = self.JUMP, False, True
        x0, z0 = self.x, self.z
        # horizontal input in the camera frame
        mag = math.hypot(left[0], left[1])
        if mag > 1e-6 and not self.frozen:
            speed = self.SNEAK if self.sneak else self.SPRINT if self.sprinting else self.WALK
            yaw = math.radians(self.yaw)
            fx, fz = -math.sin(yaw), math.cos(yaw)       # forward
            rxv, rzv = -math.cos(yaw), -math.sin(yaw)    # right
            ux, uz = (fx * left[1] + rxv * left[0]) / max(1.0, mag), (fz * left[1] + rzv * left[0]) / max(1.0, mag)
        else:
            speed, ux, uz = 0.0, 0.0, 0.0
        if self.ground:
            # LivingEntity.travel on the ground: displacement = momentum + the input acceleration, momentum = the
            # displacement times the ground friction (SprintJump.java:44, 205-206); the steady state is the speed,
            # reached in half a dozen ticks, and a landing's momentum carries into the next hop
            accel = speed * (1 - self.GROUND_FRICTION)
            self.vx, self.vz = self.mx + ux * accel, self.mz + uz * accel
            if self.sneak and math.hypot(self.vx, self.vz) > speed:   # a crouch is a brake, not a slide
                self.vx, self.vz = ux * speed, uz * speed
            self.mx, self.mz = self.vx * self.GROUND_FRICTION, self.vz * self.GROUND_FRICTION
        else:
            # LivingEntity.travel: displacement = momentum + the input acceleration, momentum = displacement times
            # the friction. On the jump tick onGround is still true, so the ground acceleration and friction apply,
            # and a sprinting jump adds 0.2 along the facing (jumpFromGround): SprintJump.java:223-248
            if jumped:
                accel, friction = speed * (1 - self.GROUND_FRICTION), self.GROUND_FRICTION
            else:
                accel, friction = (self.AIR_ACCEL_SPRINT if self.sprinting else self.AIR_ACCEL_WALK), self.AIR_DRAG
            boost = self.SPRINT_JUMP_BOOST if jumped and self.sprinting else 0.0
            yaw = math.radians(self.yaw)
            self.vx = self.mx + ux * accel - math.sin(yaw) * boost
            self.vz = self.mz + uz * accel + math.cos(yaw) * boost
            self.mx, self.mz = self.vx * friction, self.vz * friction
        self.collided = False
        for axis in ("x", "z"):
            nx = self.x + (self.vx if axis == "x" else 0.0)
            nz = self.z + (self.vz if axis == "z" else 0.0)
            if self.blocked(nx, self.y, nz):
                self.collided = True
                if axis == "x":
                    self.vx = self.mx = 0.0
                else:
                    self.vz = self.mz = 0.0
                continue
            if self.sneak and self.ground and not self.supported(nx, nz, self.y):
                continue
            self.x, self.z = nx, nz
        if self.collided and self.sprinting:
            self.sprinting = False
        # vertical
        if not self.ground:
            ny = self.y + self.vy
            floor = self.floor_below(self.x, self.z, self.y)
            if self.vy <= 0 and ny <= floor:
                self.y, self.vy, self.ground = floor, 0.0, True
            else:
                if self.vy > 0 and self.blocked(self.x, ny + 0.8, self.z) and not self.blocked(self.x, self.y + 0.8, self.z):
                    self.vy = 0.0   # a head bonk
                else:
                    self.y = ny
                self.vy = (self.vy - self.GRAVITY) * self.DRAG
        elif self.floor_below(self.x, self.z, self.y) < self.y - 1e-6:
            self.ground = False
        # breaking and placing
        block = self.crosshair()
        if "rt" in held and block and block["type"] in self.BREAK:
            pos = (block["x"], block["y"], block["z"])
            self.breaking = (pos, self.breaking[1] + 1) if self.breaking and self.breaking[0] == pos else (pos, 1)
            if self.breaking[1] >= self.BREAK[block["type"]]:
                self.world.blocks.pop(pos, None)
                self._events.append({"type": "break", "tick": self.game_tick, "dim": "overworld", "x": pos[0], "y": pos[1], "z": pos[2],
                                     "block": block["type"], "player": "1", "damage": None, "cause": None})
                self.breaking = None
        else:
            self.breaking = None
        hand = self.slots[self.slot]
        if "lt" in rising and hand and hand["type"] == "minecraft:dirt" and block:
            face = {"Up": (0, 1, 0), "Down": (0, -1, 0), "North": (0, 0, -1), "South": (0, 0, 1), "East": (1, 0, 0), "West": (-1, 0, 0)}[block["face"]]
            pos = (block["x"] + face[0], block["y"] + face[1], block["z"] + face[2])
            # no "place ahead from the top face of the block under the feet": this client placed nothing from 22
            # such taps, so a bridge has to be the backplace from over the edge, which is what this sim exercises
            if not self.solid(*pos) and not self.intersects(pos):
                self.world.set(pos[0], pos[1], pos[2], DIRT)
                hand["amount"] -= 1
                if hand["amount"] <= 0:
                    self.slots[self.slot] = None
                self._events.append({"type": "place", "tick": self.game_tick, "dim": "overworld", "x": pos[0], "y": pos[1], "z": pos[2],
                                     "block": "minecraft:dirt", "player": "1", "damage": None, "cause": None})
        hand = self.slots[self.slot]
        self.report = {"id": "1", "name": self.name, "dim": "overworld", "x": self.x, "y": self.y, "z": self.z, "yaw": self.yaw,
                       "pitch": self.pitch, "vx": self.x - x0, "vy": self.vy, "vz": self.z - z0, "ground": self.ground, "water": False,
                       "sneak": self.sneak, "sprint": self.sprinting, "jump": "a" in held, "climbing": False, "swimming": False,
                       "falling": self.vy < 0, "fall": 0.0, "slot": self.slot, "hand": hand, "hotbar": list(self.slots),
                       "input": {"move": {"x": left[0], "y": left[1]}, "jump": "a" in held, "sneak": "b" in held},
                       "health": {"current": self.health, "max": 20}, "food": {"current": 20, "max": 20}, "look": None,
                       "block": block, "at": time.time(), "n": self.ticks, "tick": self.game_tick}

    def intersects(self, pos):
        """The player's box overlaps a block cell (a block cannot be placed there)."""
        if not (pos[1] < self.y + 1.8 and pos[1] + 1 > self.y + 1e-6):
            return False
        return pos[0] < self.x + self.HALF and pos[0] + 1 > self.x - self.HALF and pos[2] < self.z + self.HALF and pos[2] + 1 > self.z - self.HALF

    def teleport(self, x, y, z, yaw=None):
        self.x, self.y, self.z = x, y, z
        if yaw is not None:
            self.yaw = yaw
        self.vx = self.vz = self.vy = self.mx = self.mz = 0.0
        self.ground = self.floor_below(x, z, y) >= y - 1e-6


class SimProbe:
    """The Probe surface the Body and the executor use, over a SimPlayer: wait_tick steps the simulation."""

    def __init__(self, sim):
        self.sim = sim
        self.bands = {}

    @property
    def ticks(self):
        return self.sim.ticks

    def wait_tick(self, after, timeout=0.2):
        self.sim._tick()
        return self.sim.ticks

    def player(self, who, fresh=1.0):
        return self.sim.report

    def hand(self, who, fresh=2.0):
        return self.sim.slots[self.sim.slot]

    def hotbar(self, who, fresh=2.0):
        return list(self.sim.slots)

    def events(self):
        out, self.sim._events = self.sim._events, []
        return out

    def forget(self, key):
        pass


def plan(world, start, goal, hotbar=(), settings=None):
    ctx = CalculationContext(world, settings=settings or Settings(), hotbar=list(hotbar))
    finder = AStarPathFinder.create(start, goal, ctx)
    result = finder.calculate(2000, 5000)
    assert result.type in (PathCalculationResult.SUCCESS_TO_GOAL, PathCalculationResult.SUCCESS_SEGMENT), result
    return result.path


class Harness:
    """A world, a simulated player, a Body and a PathingBehavior wired together, driven a tick at a time."""

    def __init__(self, world, at, yaw=0.0, hotbar=None, settings=None):
        self.world = world
        self.pad = NullController()
        self.sim = SimPlayer(world, self.pad, at, yaw, hotbar)
        self.probe = SimProbe(self.sim)
        self.settings = settings or Settings()
        self.body = Body(self.pad, self.probe, "Sim", calibration=CALIBRATION)
        self.events = []
        self.behavior = PathingBehavior(self.context, self.settings, log=lambda text: None, on_event=self.events.append)
        self.behavior.context = self.context()

    def context(self):
        return CalculationContext(self.world, settings=self.settings, hotbar=list(self.sim.slots))

    def state(self):
        pl = Player(self.probe.player("Sim"))
        return pl, TickState(pl, self.behavior.context, self.body, self.settings, events=self.probe.events())

    def execute(self, path):
        ex = PathExecutor(self.behavior, path)
        self.behavior.current = ex
        return ex

    def tick(self):
        """One executor tick: the simulation steps, the report becomes a TickState, the behavior ticks it."""
        self.probe.wait_tick(self.probe.ticks)
        pl, s = self.state()
        self.body.begin(pl)
        self.behavior.on_tick(s)
        return pl

    def run(self, ticks, until=None):
        for _ in range(ticks):
            pl = self.tick()
            if until is not None and until(pl):
                return pl
        return pl


# ---------------------------------------------------------------------------------------------------------------

class TraverseRunTest(unittest.TestCase):

    def test_walks_a_flat_path_to_the_end(self):
        world = flat(radius=30)
        path = plan(world, (0, Y, 0), GoalXZ(12, 0))
        self.assertEqual(path.length(), 13)
        h = Harness(world, (0.5, Y, 0.5), yaw=180.0)        # facing -z: the first job is a 90 degree turn to +x
        ex = h.execute(path)
        pl = h.run(400, until=lambda p: ex.finished())
        self.assertTrue(ex.finished(), f"position {ex.path_position} feet {pl.feet}")
        self.assertFalse(ex.failed())
        self.assertEqual(pl.feet[0], 12)
        self.assertEqual(pl.feet[2], 0)
        self.assertLess(h.sim.ticks, 120, "12 blocks at a sprint is about 45 ticks plus the turn")
        held = [c for c in h.pad.calls if c[0] == "state" and "ls" in c[3]]
        self.assertTrue(held, "the sprint click went out")
        self.assertTrue(any(c[0] == "state" and c[2][0] > 0.9 for c in h.pad.calls), "a full right-stick turn (to the right: +x is right of -z)")
        self.assertIn(PathEvent.AT_GOAL, h.events)

    def test_skips_forward_when_teleported_ahead(self):
        world = flat(radius=30)
        path = plan(world, (0, Y, 0), GoalXZ(12, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=False))   # the walk, not the hops
        ex = h.execute(path)
        h.run(3)
        self.assertLessEqual(ex.path_position, 1)
        h.sim.teleport(8.5, Y, 0.5)
        h.run(3)
        self.assertGreaterEqual(ex.path_position, 7)            # PE:118-129 then the overshoot successes
        pl = h.run(200, until=lambda p: ex.finished())
        self.assertTrue(ex.finished())
        self.assertEqual(pl.feet[0], 12)

    def test_cancels_three_blocks_off_the_path(self):
        world = flat(radius=30)
        path = plan(world, (0, Y, 0), GoalXZ(12, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=False))
        ex = h.execute(path)
        h.run(3)
        h.sim.teleport(3.5, Y, 4.5)                             # 4 blocks beside the line
        h.run(2)
        self.assertTrue(ex.failed())                             # PE:144-148
        self.assertIsNone(h.behavior.current)                    # PB:154-155: the behavior drops it

    def test_two_blocks_off_is_tolerated_for_200_ticks(self):
        world = flat(radius=30)
        path = plan(world, (0, Y, 0), GoalXZ(12, 0))
        # the movement timeout (PE:246) would fire first on a frozen player; this test is about PE:133-143
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(movementTimeoutTicks=100000))
        ex = h.execute(path)
        h.run(2)
        h.sim.frozen = True
        h.sim.teleport(2.5, Y, 2.9)                             # about 2.5 from the nearest centre
        h.run(150)
        self.assertFalse(ex.failed())
        self.assertGreater(ex.ticks_away, 100)
        h.run(60)
        self.assertTrue(ex.failed())                             # PE:133-140

    def test_times_a_movement_out(self):
        world = flat(radius=30)
        path = plan(world, (0, Y, 0), GoalXZ(12, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=False))
        ex = h.execute(path)
        h.sim.frozen = True
        h.run(95)
        self.assertFalse(ex.failed())
        h.run(15)                                                # PE:246: 3.56 + 100 ticks
        self.assertTrue(ex.failed())

    def test_splices_a_next_segment(self):
        world = flat(radius=40)
        first = plan(world, (0, Y, 0), GoalXZ(10, 0))
        second = plan(world, (10, Y, 0), GoalXZ(20, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0)
        ex = h.execute(first)
        nxt = PathExecutor(h.behavior, second)
        ex.path_position = 3
        ex.ticks_on_current = 7
        spliced = ex.try_splice(nxt)
        self.assertIsNot(spliced, ex)
        self.assertEqual(spliced.path.length(), 21)
        self.assertEqual(spliced.path.get_dest(), (20, Y, 0))
        self.assertEqual(spliced.path_position, 3)
        self.assertEqual(spliced.ticks_on_current, 7)
        self.assertIs(ex.try_splice(None), ex)                   # PE:737-739: nothing to splice, not long enough to cut
        # and through the behavior: a planned next is spliced on every tick (PB:206-208)
        h.behavior.current, h.behavior.next = ex, nxt
        h.run(2)
        self.assertEqual(h.behavior.current.path.length(), 21)
        self.assertIsNone(h.behavior.next)


class AscendDescendRunTest(unittest.TestCase):

    def test_steps_up_and_down(self):
        world = flat(radius=30)
        cliff(world, 5, -3, 9, 3, Y - 4, 4)                      # a plateau one block up from x 5 to 9
        path = plan(world, (0, Y, 0), GoalXZ(14, 0))
        kinds = [type(m).__name__ for m in path.movements()]
        self.assertIn("MovementAscend", kinds)
        self.assertIn("MovementDescend", kinds)
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0)
        ex = h.execute(path)
        pl = h.run(400, until=lambda p: ex.finished() or ex.failed())
        self.assertTrue(ex.finished(), f"position {ex.path_position} of {path.length()} feet {pl.feet} at {pl.x:.2f} {pl.y:.2f}")
        self.assertEqual(pl.feet[0], 14)
        jumps = [c for c in h.pad.calls if c[0] == "state" and "a" in c[3]]
        self.assertTrue(jumps, "the ascend tapped A")

    def test_falls_three_and_walks_on(self):
        """A 3 block fall followed by traverses in the same direction goes through PE:468-500 overrideFall, which
        clears the keys and must steer the fall itself; the first course run stood at the edge until the timeout."""
        world = flat(radius=30)
        cliff(world, -6, -3, 5, 3, Y - 4, 6)                     # a plateau three blocks up from x -6 to 5
        path = plan(world, (2, Y + 3, 0), GoalXZ(12, 0))
        kinds = [type(m).__name__ for m in path.movements()]
        self.assertIn("MovementFall", kinds)
        h = Harness(world, (2.5, Y + 3, 0.5), yaw=-90.0)
        ex = h.execute(path)
        pl = h.run(300, until=lambda p: ex.finished() or ex.failed())
        self.assertTrue(ex.finished(), f"position {ex.path_position} of {path.length()} feet {pl.feet} at {pl.x:.2f} {pl.y:.2f}")
        self.assertEqual(pl.feet, (12, Y, 0))
        self.assertLess(h.sim.ticks, 150)

    def test_bridges_a_gap_with_dirt(self):
        """A traverse whose dest floor is missing places it the Bedrock way: stop on src, look down at the src
        floor's far edge, LT; the place event puts the block in the terrain and the walk goes on."""
        world = flat(radius=20)
        clear(world, 3, Y - 4, -20, 5, Y - 1, 20)                # a gap three wide across the whole world, nothing under it
        hotbar = [None, {"type": "minecraft:dirt", "amount": 8}] + [None] * 7
        settings = Settings(allowParkour=False)                  # the bridge, not the jump the planner now prefers
        path = plan(world, (0, Y, 0), GoalXZ(8, 0), hotbar=hotbar, settings=settings)
        self.assertEqual(path.get_dest(), (8, Y, 0))
        self.assertTrue(all(isinstance(m, MovementTraverse) for m in path.movements()))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, hotbar=hotbar, settings=settings)
        ex = h.execute(path)
        pl = h.run(500, until=lambda p: ex.finished() or ex.failed())
        self.assertTrue(ex.finished(), f"position {ex.path_position} of {path.length()} feet {pl.feet} at {pl.x:.2f} {pl.y:.2f}")
        self.assertEqual(pl.feet, (8, Y, 0))
        self.assertEqual(h.sim.slots[1]["amount"], 5)
        self.assertTrue(all(world.kind(x, Y - 1, 0).type_id == "minecraft:dirt" for x in (3, 4, 5)))

    def test_pillars_with_dirt(self):
        world = flat(radius=20)
        hotbar = [None, {"type": "minecraft:dirt", "amount": 8}] + [None] * 7
        path = plan(world, (0, Y, 0), GoalBlock(0, Y + 2, 0), hotbar=hotbar)
        self.assertTrue(all(isinstance(m, MovementPillar) for m in path.movements()), path.movements())
        h = Harness(world, (0.5, Y, 0.5), yaw=0.0, hotbar=hotbar)
        ex = h.execute(path)
        pl = h.run(300, until=lambda p: ex.finished() or ex.failed())
        self.assertTrue(ex.finished(), f"position {ex.path_position} feet {pl.feet} y {pl.y:.2f} blocks {sorted(k for k in world.blocks if k[1] >= Y)}")
        self.assertEqual(pl.feet, (0, Y + 2, 0))
        self.assertEqual(h.sim.slots[1]["amount"], 6)


class SprintPolicyTest(unittest.TestCase):

    def executor_at(self, world, start, goal, feet, hotbar=()):
        path = plan(world, start, goal, hotbar)
        h = Harness(world, (feet[0] + 0.5, feet[1], feet[2] + 0.5), yaw=-90.0)
        ex = h.execute(path)
        pl, s = h.state()
        h.body.begin(pl)
        return h, ex, path, s

    def test_traverse_into_ascend_skips_and_jumps(self):
        world = flat(radius=30)
        cliff(world, 5, -3, 9, 3, Y - 4, 4)
        h, ex, path, s = self.executor_at(world, (0, Y, 0), GoalXZ(9, 0), (3, Y, 0))
        ex.path_position = 3
        self.assertIsInstance(path.movements()[3], MovementTraverse)
        self.assertIsInstance(path.movements()[4], MovementAscend)
        self.assertTrue(PathExecutor.sprintable_ascend(s.ctx, path.movements()[3], path.movements()[4], path.movements()[5]))
        self.assertTrue(PathExecutor.skip_now(s.ctx, s.player, path.movements()[3]))
        ex.pending = path.movements()[3].current
        self.assertTrue(ex.should_sprint_next_tick(s))
        self.assertEqual(ex.path_position, 4)                    # PE:372-378: skipped to the ascend
        self.assertTrue(ex.jump_override)                        # PE:377: JUMP forced on
        # not sprintable with a block to break over the step
        world.set(5, Y + 2, 0, STONE)
        s.ctx.block_changed()
        path.movements()[4].reset_block_cache()
        self.assertFalse(PathExecutor.sprintable_ascend(s.ctx, path.movements()[3], path.movements()[4], path.movements()[5]))

    def test_descend_into_a_straight_traverse_keeps_sprinting(self):
        world = flat(radius=30)
        clear(world, 5, Y - 1, -5, 30, Y - 1, 5)                  # the ground drops one block from x 5 on
        h, ex, path, s = self.executor_at(world, (0, Y, 0), GoalXZ(10, 0), (4, Y, 0))
        i = next(k for k, m in enumerate(path.movements()) if isinstance(m, MovementDescend))
        ex.path_position = i
        current, nxt = path.movements()[i], path.movements()[i + 1]
        self.assertIsInstance(nxt, MovementTraverse)
        self.assertTrue(PathExecutor.can_sprint_from_descend_into(s.ctx, current, nxt))
        self.assertFalse(current.safe_mode(s.ctx))
        ex.pending = current.current
        self.assertTrue(ex.should_sprint_next_tick(s))           # PE:432-447

    def test_descend_into_a_turn_does_not_sprint(self):
        world = flat(radius=30)
        clear(world, 5, Y - 1, -30, 30, Y - 1, 30)               # the ground drops one block from x 5 on
        ctx = CalculationContext(world)
        descend = MovementDescend((4, Y, 0), (5, Y - 1, 0))
        straight = MovementTraverse((5, Y - 1, 0), (6, Y - 1, 0))
        turn = MovementTraverse((5, Y - 1, 0), (5, Y - 1, 1))
        diagonal = MovementDiagonal((5, Y - 1, 0), 1, 1)
        again = MovementDescend((5, Y - 1, 0), (6, Y - 2, 0))
        sideways = MovementDescend((5, Y - 1, 0), (5, Y - 2, 1))
        self.assertTrue(PathExecutor.can_sprint_from_descend_into(ctx, descend, straight))    # PE:708-710
        self.assertFalse(PathExecutor.can_sprint_from_descend_into(ctx, descend, turn))
        self.assertTrue(PathExecutor.can_sprint_from_descend_into(ctx, descend, diagonal))    # PE:711
        self.assertTrue(PathExecutor.can_sprint_from_descend_into(ctx, descend, again))       # PE:702: the same direction
        self.assertFalse(PathExecutor.can_sprint_from_descend_into(ctx, descend, sideways))   # PE:702: not the same direction
        clear(world, 6, Y - 2, 0, 6, Y - 2, 0)                   # nothing to land on beyond dest
        ctx.block_changed()
        self.assertFalse(PathExecutor.can_sprint_from_descend_into(ctx, descend, straight))   # PE:705-707

    def test_no_sprint_when_hungry(self):
        world = flat(radius=30)
        h, ex, path, s = self.executor_at(world, (0, Y, 0), GoalXZ(10, 0), (2, Y, 0))
        s.player.food = 6
        ms = path.movements()[2].current
        ms.set_input("sprint", True)
        ex.path_position, ex.pending = 2, ms
        self.assertFalse(ex.should_sprint_next_tick(s))          # PE:357-359
        self.assertFalse(ms.is_down("sprint"))                   # PE:352: consumed
        s.player.food = 20
        ms.set_input("sprint", True)
        self.assertTrue(ex.should_sprint_next_tick(s))           # PE:386-391: the traverse asked


class BodyTest(unittest.TestCase):

    def player(self, x=0.5, y=Y, z=0.5, yaw=0.0, pitch=0.0, **more):
        p = {"id": "1", "name": "Sim", "x": x, "y": y, "z": z, "yaw": yaw, "pitch": pitch, "vx": 0, "vy": 0, "vz": 0,
             "ground": True, "water": False, "sneak": False, "sprint": False, "tick": 1, "n": 1, "at": time.time()}
        p.update(more)
        return Player(p)

    def test_a_target_ninety_degrees_left_turns_full_and_strafes_left(self):
        pad = NullController()
        body = Body(pad, None, "Sim", calibration=CALIBRATION)
        pl = self.player(yaw=0.0)                                # facing +z; east (+x) is on the left
        body.begin(pl)
        ms = MovementState("RUNNING")
        ms.move_to = (10.5, Y, 0.5)
        ms.set_input("forward", True)
        body.apply(pl, ms)
        left, right = pad.left, pad.right
        self.assertAlmostEqual(right[0], -1.0, places=3)         # a full turn to the left
        self.assertAlmostEqual(left[0], -1.0, places=3)          # the body strafes left, toward the point
        self.assertAlmostEqual(left[1], 0.0, places=3)
        self.assertEqual(wrap(yaw_to(10, 0) - 0.0), -90.0)

    def test_small_errors_sit_in_the_deadband_and_the_curve_is_inverted(self):
        pad = NullController()
        body = Body(pad, None, "Sim", calibration=CALIBRATION)
        pl = self.player(yaw=-88.0)
        body.begin(pl)
        ms = MovementState("RUNNING")
        ms.move_to = (10.5, Y, 0.5)                              # wanted yaw -90: 2 degrees off
        ms.set_input("forward", True)
        body.apply(pl, ms)
        self.assertEqual(pad.right[0], 0.0)
        self.assertGreater(pad.left[1], 0.99)
        curve = body.turn
        self.assertAlmostEqual(curve.deflection(25.6), 0.5, places=3)
        self.assertAlmostEqual(curve.deflection(150.0), 1.0, places=3)
        self.assertAlmostEqual(curve.rate(0.75), (25.6 + 150.0) / 2, places=3)
        self.assertEqual(curve.deflection(0.0), 0.0)
        pl2 = self.player(yaw=-60.0)                             # 30 degrees off: 300 deg/s wanted, saturates
        body.begin(pl2)
        body.apply(pl2, ms)
        self.assertAlmostEqual(pad.right[0], -1.0, places=3)

    def test_forced_target_uses_the_keys_in_the_camera_frame(self):
        pad = NullController()
        body = Body(pad, None, "Sim", calibration=CALIBRATION)
        pl = self.player(yaw=0.0)
        body.begin(pl)
        ms = MovementState("RUNNING")
        ms.set_target(type(pl.rotation)(0.0, 45.0), True)
        ms.set_input("back", True)
        ms.set_input("click_left", True)
        body.apply(pl, ms)
        self.assertEqual(pad.left, (0.0, -1.0))
        self.assertIn("rt", pad.held)
        self.assertLess(pad.right[1], 0.0)                      # pitch down: the stick goes down

    def test_taps_are_timed_and_sneak_is_a_toggle(self):
        pad = NullController()
        body = Body(pad, None, "Sim", calibration=CALIBRATION)
        pl = self.player(sneak=False)
        ms = MovementState("RUNNING")
        ms.set_input("jump", True)
        ms.set_input("sneak", True)
        body.begin(pl)
        body.apply(pl, ms)
        self.assertEqual(set(pad.held), {"a", "b"})
        body.begin(pl)
        body.apply(pl, ms)
        self.assertEqual(set(pad.held), {"a", "b"})              # two ticks of A, three of B
        body.begin(pl)
        body.apply(pl, ms)
        self.assertEqual(set(pad.held), {"b"})
        body.begin(pl)
        body.apply(pl, ms)
        self.assertNotIn("b", pad.held)                          # no second B while the first has not shown
        pl2 = self.player(sneak=True)
        for _ in range(10):
            body.begin(pl2)
            body.apply(pl2, ms)
        self.assertNotIn("b", pad.held)                          # the probe agrees: sneaking, wanted sneaking
        ms.set_input("sneak", False)
        body.begin(pl2)
        body.apply(pl2, ms)
        self.assertIn("b", pad.held)                             # wanted up while the probe says crouched: a tap


class FakeSneakProbe:
    """A probe whose player reports sneak as a toggle flipped by a B rising edge on the pad, one tick late."""

    def __init__(self, pad, sneak):
        self.pad, self.sneak, self.ticks, self.was_down = pad, sneak, 0, False
        self.b_presses = 0

    def wait_tick(self, after, timeout=0.2):
        self.ticks += 1
        down = "b" in self.pad.held
        if down and not self.was_down:
            self.sneak = not self.sneak
            self.b_presses += 1
        self.was_down = down
        return self.ticks

    def player(self, who, fresh=1.0):
        return {"id": "1", "name": who, "x": 0.5, "y": 64.0, "z": 0.5, "yaw": 0.0, "pitch": 0.0, "vx": 0, "vy": 0, "vz": 0,
                "ground": True, "water": False, "sneak": self.sneak, "sprint": False, "tick": self.ticks, "n": self.ticks, "at": time.time()}


class StandUpTest(unittest.TestCase):

    def test_stand_up_taps_b_only_while_crouched(self):
        pad = NullController()
        probe = FakeSneakProbe(pad, sneak=True)
        body = Body(pad, probe, "Sim", calibration=CALIBRATION)
        self.assertTrue(body.stand_up())
        self.assertEqual(probe.b_presses, 1)
        self.assertFalse(probe.sneak)
        self.assertTrue(body.stand_up())                         # already up: no tap
        self.assertEqual(probe.b_presses, 1)
        self.assertTrue(body.sneak_on())
        self.assertEqual(probe.b_presses, 2)
        self.assertTrue(probe.sneak)
        self.assertEqual(pad.held, frozenset(), "everything let go afterwards")


class SprintJumpRunTest(unittest.TestCase):
    """PE:94-96, 362-366, 507-535 on the simulated player: settings.sprintJumping hops along a flat path, every hop
    lands back on it, and the walk is faster than the sprint."""

    def run_flat(self, sprint_jumping, blocks=30):
        world = flat(radius=40)
        path = plan(world, (0, Y, 0), GoalXZ(blocks, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=sprint_jumping))
        ex = h.execute(path)
        seen = {"low": Y, "flights": 0, "high": Y, "ids": set()}

        def step(pl):
            seen["low"] = min(seen["low"], pl.y)
            seen["high"] = max(seen["high"], pl.y)
            if ex.flight is not None and id(ex.flight) not in seen["ids"]:   # a landing plans the next hop in the same tick
                seen["ids"].add(id(ex.flight))
                seen["flights"] += 1
            return ex.finished() or ex.failed()
        pl = h.run(600, until=step)
        return h, ex, pl, seen

    def test_hops_along_a_flat_path_and_lands_on_it(self):
        h, ex, pl, seen = self.run_flat(True)
        self.assertTrue(ex.finished(), f"position {ex.path_position} feet {pl.feet} at {pl.x:.2f} {pl.y:.2f}")
        self.assertFalse(ex.failed())
        self.assertEqual(pl.feet[0], 30)
        self.assertGreaterEqual(seen["flights"], 3, "several hops over 30 blocks")
        self.assertGreater(seen["high"], Y + 1.0, "it did leave the ground")
        self.assertGreaterEqual(seen["low"], Y - 1e-6, "never below the runway")
        self.assertIsNone(ex.flight, "down by the end")
        taps = [c for c in h.pad.calls if c[0] == "state" and "a" in c[3]]
        self.assertTrue(taps)
        h2, ex2, pl2, seen2 = self.run_flat(False)
        self.assertTrue(ex2.finished())
        self.assertEqual(seen2["flights"], 0)
        self.assertLess(h.sim.ticks, h2.sim.ticks, f"hops {h.sim.ticks} ticks, sprint {h2.sim.ticks}")

    def test_no_hop_near_the_end_or_off_the_line(self):
        world = flat(radius=40)
        path = plan(world, (0, Y, 0), GoalXZ(4, 0))                 # SJ:114: a runway under 3 cells
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=True))
        ex = h.execute(path)
        flights = 0
        for _ in range(200):
            h.tick()
            flights += ex.flight is not None
            if ex.finished():
                break
        self.assertTrue(ex.finished())
        self.assertEqual(flights, 0)

    def test_flight_survives_a_splice(self):
        """PE:751: a new path showing up does not make us any less airborne."""
        world = flat(radius=40)
        first = plan(world, (0, Y, 0), GoalXZ(12, 0))
        second = plan(world, (12, Y, 0), GoalXZ(24, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=Settings(sprintJumping=True))
        ex = h.execute(first)
        pl = h.run(100, until=lambda p: ex.flight is not None)
        self.assertIsNotNone(ex.flight)
        spliced = ex.try_splice(PathExecutor(h.behavior, second))
        self.assertIsNot(spliced, ex)
        self.assertIs(spliced.flight, ex.flight)
        self.assertEqual(spliced.path.length(), 25)


class ParkourRunTest(unittest.TestCase):
    """Park:252-315 on the simulated player: a pit too deep to fall into is jumped at dist 2, 3 and 4 and up one,
    from a straight run-up at the traverses' pace, and the jump lands on the far side."""

    def gap_world(self, width, ascend=False):
        world = flat(radius=30)
        clear(world, 1, Y - 4, -6, width, Y - 1, 6)                 # a pit `width` wide and 4 deep: no way but over
        if ascend:
            world.fill(width + 1, Y, -6, 20, Y, 6, STONE)           # the far side one block up
        return world

    def jump_over(self, width, ascend=False):
        world = self.gap_world(width, ascend)
        settings = Settings(allowParkour=True)
        path = plan(world, (-4, Y, 0), GoalXZ(8, 0), settings=settings)
        parkour = [m for m in path.movements() if isinstance(m, MovementParkour)]
        self.assertEqual(len(parkour), 1, [type(m).__name__ for m in path.movements()])
        move = parkour[0]
        self.assertEqual((move.src, move.dist, move.ascend), ((0, Y, 0), width + 1, ascend))
        h = Harness(world, (-3.5, Y, 0.5), yaw=-90.0, settings=settings)
        ex = h.execute(path)
        seen = {"low": Y}

        def step(pl):
            seen["low"] = min(seen["low"], pl.y)
            return ex.finished() or ex.failed()
        pl = h.run(400, until=step)
        self.assertTrue(ex.finished(), f"position {ex.path_position} of {path.length()} feet {pl.feet} at {pl.x:.2f} {pl.y:.2f}, {move.status}")
        self.assertFalse(ex.failed())
        self.assertEqual(move.status, MovementStatus.SUCCESS)
        self.assertGreaterEqual(seen["low"], Y - 1e-6, "never down in the pit")
        self.assertEqual(pl.feet[0], 8)
        return h, move

    def test_dist_2_over_a_one_block_gap(self):
        self.jump_over(1)

    def test_dist_3_over_a_two_block_gap(self):
        self.jump_over(2)

    def test_dist_4_over_a_three_block_gap_at_a_sprint(self):
        h, move = self.jump_over(3)
        self.assertTrue(any(c[0] == "state" and "ls" in c[3] for c in h.pad.calls), "Park:265-267: dist 4 sprints")

    def test_ascend_over_a_one_block_gap(self):
        self.jump_over(1, ascend=True)

    def test_run_up_walks_back_to_src(self):
        """Park:305-312: on the ground somewhere other than src, the takeoff or the landing, walk to src - dir
        (then src) without sprinting."""
        world = self.gap_world(2)
        settings = Settings(allowParkour=True)
        path = plan(world, (0, Y, 0), GoalXZ(8, 0), settings=settings)
        move = path.movements()[0]
        self.assertIsInstance(move, MovementParkour)
        h = Harness(world, (-0.5, Y, 0.5), yaw=-90.0, settings=settings)   # on src - dir, the block behind src
        pl, s = h.state()
        h.body.begin(pl)
        move.tick(s)
        move.tick(s)                                                  # PREPPING, WAITING, then RUNNING
        status = move.tick(s)
        self.assertEqual(status, MovementStatus.RUNNING)
        self.assertEqual(move.current.move_to, (0.5, Y + 0.5, 0.5), "at src - dir: toward src")
        self.assertFalse(move.current.is_down("sprint"))
        self.assertFalse(move.current.is_down("jump"))
        h.sim.teleport(-1.5, Y, 0.5)                                  # two behind
        h.probe.wait_tick(0)                                          # the report follows the sim a tick later
        pl, s = h.state()
        move.tick(s)
        self.assertEqual(move.current.move_to, (-0.5, Y + 0.5, 0.5), "elsewhere: toward src - dir first")
        h.sim.teleport(1.5, Y - 2, 0.5)                               # Park:257-261: fallen
        h.probe.wait_tick(0)
        pl, s = h.state()
        self.assertEqual(move.tick(s), MovementStatus.UNREACHABLE)

    def test_one_past_dest_on_the_ground_is_the_landing(self):
        """departs: a hop that comes down a block past dest succeeds there instead of walking back across dest."""
        world = self.gap_world(1)
        settings = Settings(allowParkour=True)
        path = plan(world, (0, Y, 0), GoalXZ(8, 0), settings=settings)
        move = path.movements()[0]
        self.assertEqual(move.dest, (2, Y, 0))
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=settings)
        pl, s = h.state()
        h.body.begin(pl)
        for _ in range(3):
            move.tick(s)
        h.sim.teleport(3.5, Y, 0.5)                                   # dest + dir, on the ground
        h.probe.wait_tick(0)
        pl, s = h.state()
        self.assertEqual(move.tick(s), MovementStatus.SUCCESS)

    def test_dist_3_waits_for_point_seven(self):
        """Park:294-302: a 2 block gap is not jumped before the player is 0.7 from the src centre (a latency ahead)."""
        world = self.gap_world(2)
        settings = Settings(allowParkour=True)
        path = plan(world, (0, Y, 0), GoalXZ(8, 0), settings=settings)
        move = path.movements()[0]
        h = Harness(world, (0.5, Y, 0.5), yaw=-90.0, settings=settings)
        pl, s = h.state()
        h.body.begin(pl)
        for _ in range(3):
            move.tick(s)
        h.sim.teleport(1.05, Y, 0.5)                                  # feet in src + dir, 0.55 from the centre, still
        h.probe.wait_tick(0)                                          # the report follows the sim a tick later
        pl, s = h.state()
        self.assertEqual(pl.feet, (1, Y, 0))
        move.tick(s)
        self.assertFalse(move.current.is_down("jump"))
        h.sim.teleport(1.25, Y, 0.5)                                  # 0.75: jump
        h.probe.wait_tick(0)
        pl, s = h.state()
        move.tick(s)
        self.assertTrue(move.current.is_down("jump"))


if __name__ == "__main__":
    unittest.main()
