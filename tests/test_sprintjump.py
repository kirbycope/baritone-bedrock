"""The SprintJump simulation (baritone_bedrock/sprintjump.py) against the Java's constants and SprintJump.java's
rules: a flat runway jump lands on the runway, a step ahead gets the chained hop, a hill drop over 3 is refused,
the runway's end is a wall, the latency allowance, and plan() through a real path and player report."""
import math
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.astar import AStarPathFinder, PathCalculationResult                # noqa: E402
from baritone_bedrock.context import CalculationContext                                 # noqa: E402
from baritone_bedrock.control import Player                                             # noqa: E402
from baritone_bedrock.goals import GoalXZ                                               # noqa: E402
from baritone_bedrock.settings import Settings                                          # noqa: E402
from baritone_bedrock.sprintjump import (SprintJump, Landing, solid_floor, FULL_SEND, GROUND_FRICTION, AIR_FRICTION,
                                         AIR_ACCEL, JUMP_BOOST, JUMP, GRAVITY, SAFE_FALL, MAX_RUNWAY, MAX_TICKS,
                                         MARGINS, SPRINT_GROUND_ACCEL)                  # noqa: E402
from tests.fixtures import flat, clear, cliff, wall, STONE, ICE, BOTTOM_SLAB            # noqa: E402

Y = 64
SPRINT = 5.612 / 20                 # the steady sprint displacement a tick
V = SPRINT * GROUND_FRICTION        # Java's deltaMovement after a ground tick at that pace


def runway(floors, leads=(0,)):
    return SprintJump([float(f) for f in floors], 1.0, SPRINT_GROUND_ACCEL, leads=leads)


def report(x=0.5, y=Y, z=0.5, vx=SPRINT, vz=0.0, **more):
    p = {"id": "1", "name": "Sim", "x": x, "y": float(y), "z": z, "yaw": -90.0, "pitch": 0.0, "vx": vx, "vy": 0.0, "vz": vz,
         "ground": True, "water": False, "sneak": False, "sprint": True, "climbing": False, "tick": 1, "n": 1, "at": time.time()}
    p.update(more)
    return Player(p)


def plan_path(world, start, goal):
    ctx = CalculationContext(world, settings=Settings())
    result = AStarPathFinder.create(start, goal, ctx).calculate(2000, 5000)
    assert result.type in (PathCalculationResult.SUCCESS_TO_GOAL, PathCalculationResult.SUCCESS_SEGMENT), result
    return result.path, ctx


class ConstantsTest(unittest.TestCase):

    def test_the_java_numbers(self):
        """SJ:44-50, and the apex the calibration measured (1.252) falls out of them."""
        self.assertAlmostEqual(GROUND_FRICTION, 0.546)
        self.assertEqual(AIR_FRICTION, 0.91)
        self.assertAlmostEqual(AIR_ACCEL, 0.02548)
        self.assertEqual((JUMP_BOOST, JUMP, GRAVITY, SAFE_FALL), (0.2, 0.42, 0.08, 3))
        self.assertEqual((MAX_RUNWAY, MAX_TICKS), (10, 40))
        self.assertEqual(MARGINS, (0.85, 1.0, 1.15))
        y, vy, apex = 0.0, JUMP, 0.0
        while vy > 0:
            y += vy
            apex = max(apex, y)
            vy = (vy - GRAVITY) * 0.98
        self.assertAlmostEqual(apex, 1.252, places=3)
        # the sprint ground acceleration gives the sprint pace back at steady state (SJ:104-105)
        self.assertAlmostEqual(SPRINT_GROUND_ACCEL / (1 - GROUND_FRICTION), SPRINT, places=3)


class FlightTest(unittest.TestCase):

    def test_flat_runway_hop_lands_on_the_runway(self):
        """SJ:220-251: from a steady sprint the hop is about 3.6 blocks in 12 ticks with the vanilla apex."""
        sj = runway([0] * 11)
        landing = sj.jump(0.0, V)
        self.assertIsInstance(landing, Landing)
        self.assertEqual(landing.ticks, 12)
        self.assertAlmostEqual(landing.y, 0.0)
        self.assertAlmostEqual(landing.apex, 1.252, places=3)
        self.assertTrue(3.4 < landing.s < 3.9, landing)
        self.assertTrue(sj.safe(landing))
        self.assertTrue(math.isnan(sj.choose_target(0.0, V)))           # SJ:166-168: full send
        # the pace after landing: the slide ends over the runway (SJ:271-274)
        self.assertLess(landing.s + landing.v / (1 - GROUND_FRICTION), 10.5)

    def test_a_step_ahead_gets_the_chained_hop(self):
        """SJ:156-198: with a step on the runway the hop chosen is one the next hop can pop the step from; a step
        too close for any hop is left to the ascend (no jump)."""
        sj = runway([0] * 7 + [1] * 4)
        self.assertTrue(sj.step_up_ahead(0))
        target = sj.choose_target(0.0, V)
        self.assertTrue(math.isnan(target), "a full send that sets the pop up")
        landing = sj.jump(0.0, V)
        self.assertTrue(sj.chains(landing))
        self.assertEqual(int(round(landing.s)), 4)
        # from the landing the next hop (after at most two ground ticks) comes down on the step
        rest = SprintJump([f - landing.y for f in sj.floors[4:]], 1.0, SPRINT_GROUND_ACCEL)
        self.assertTrue(rest.can_hop(landing.s - 4, landing.v, (1.0,), False, 0))
        close = runway([0] * 4 + [1] * 7)                                  # the step at cell 4: face first (SJ:241-243)
        self.assertIsNone(close.jump(0.0, V))
        self.assertIsNone(close.choose_target(0.0, V))

    def test_a_hill_drop_over_three_is_refused(self):
        """SJ:266-269: apex minus landing must stay under SAFE_FALL, so a drop of 1 is a small hill and a drop of 2
        or more is not (1.25 up plus 2 down is 3.25)."""
        self.assertTrue(math.isnan(runway([0] * 3 + [-1] * 8).choose_target(0.0, V)))
        for drop in (2, 3, 4):
            sj = runway([0] * 3 + [-drop] * 8)
            landing = sj.jump(0.0, V)
            self.assertIsNotNone(landing)
            self.assertAlmostEqual(landing.y, -drop)
            self.assertGreaterEqual(landing.apex - landing.y, SAFE_FALL)
            self.assertFalse(sj.safe(landing))
            self.assertIsNone(sj.choose_target(0.0, V), f"a drop of {drop}")

    def test_the_runway_end_is_a_wall(self):
        """SJ:277-290: past the last checked cell is a wall, so a hop that would fly off the runway is refused."""
        self.assertIsNone(runway([0] * 4).jump(0.0, V))
        self.assertIsNone(runway([0] * 4).choose_target(0.0, V))
        self.assertEqual(runway([0] * 4).highest(4.0), math.inf)
        self.assertEqual(runway([0, 0, 1, 1]).highest(1.3), 1.0)        # the box reaches into cell 2
        self.assertEqual(runway([0, 0, 1, 1]).highest(1.1), 0.0)
        self.assertEqual(runway([0, 0, 1, 1]).highest(-3.0), 0.0)       # behind the start is where we came from

    def test_latency_allowance(self):
        """departs: the hop must also work from the ground ticks the tap takes to land. Somewhere along a short
        runway a plan that works now stops working two ticks later, and the allowance refuses it there."""
        differ = None
        for i in range(0, 60):
            s = i * 0.05
            now = runway([0] * 6, leads=(0,)).choose_target(s, V)
            later = runway([0] * 6, leads=(0, 1, 2)).choose_target(s, V)
            if now is not None and later is None:
                differ = s
                break
            self.assertFalse(now is None and later is not None, "the allowance can only refuse")
        self.assertIsNotNone(differ, "no position where the two ticks of allowance change the answer")
        s, v = runway([0] * 6).advance(0.0, V, 2)                         # SJ:205-206: two ticks of ground sprint
        self.assertAlmostEqual(s, 2 * SPRINT, places=3, msg="steady sprint: a pace a tick")
        self.assertAlmostEqual(v, ((V + SPRINT_GROUND_ACCEL) * GROUND_FRICTION + SPRINT_GROUND_ACCEL) * GROUND_FRICTION, places=6)

    def test_steer_kills_sideways_drift(self):
        """SJ:292-313: forward on takeoff, then the push tilts back toward the line by the offset and the drift."""
        sj = runway([0] * 11)
        sj.origin_x, sj.origin_z, sj.ux, sj.uz = 0.5, 0.5, 1.0, 0.0
        push = sj.steer(report(x=0.5, z=0.5))
        self.assertAlmostEqual(push[0], 1.0)
        self.assertAlmostEqual(push[1], 0.0)
        push = sj.steer(report(x=1.5, z=0.8, ground=False))                 # drifted +z: push -z
        self.assertLess(push[1], -0.05)
        self.assertGreater(push[0], 0.85)
        self.assertAlmostEqual(math.hypot(*push), 1.0)
        push = sj.steer(report(x=2.0, z=0.5, vz=-0.05, ground=False))        # on the line but drifting -z: push +z
        self.assertGreater(push[1], 0.0)
        look = sj.look_point(report(x=2.0, z=0.7))
        self.assertAlmostEqual(look[0], 5.0)
        self.assertAlmostEqual(look[2], 0.5)


class PlanTest(unittest.TestCase):

    def test_plans_along_a_flat_path(self):
        """SJ:68-123 through a real path: the runway is the straight run of traverses, capped at MAX_RUNWAY."""
        world = flat(radius=30)
        path, ctx = plan_path(world, (0, Y, 0), GoalXZ(20, 0))
        jump = SprintJump.plan(report(), ctx, path, 0, Settings())
        self.assertIsNotNone(jump)
        self.assertEqual(len(jump.floors), MAX_RUNWAY + 1)
        self.assertEqual(jump.floors, [0.0] * 11)
        self.assertEqual((jump.ux, jump.uz), (1.0, 0.0))
        self.assertTrue(math.isnan(jump.target))
        self.assertEqual(jump.leads, (0, 1))
        # SJ:75-81 preconditions
        self.assertIsNone(SprintJump.plan(report(sprint=False), ctx, path, 0, Settings()))
        self.assertIsNone(SprintJump.plan(report(ground=False), ctx, path, 0, Settings()))
        self.assertIsNone(SprintJump.plan(report(water=True), ctx, path, 0, Settings()))
        self.assertIsNone(SprintJump.plan(report(x=1.5), ctx, path, 0, Settings()), "feet not on the path node")
        self.assertIsNone(SprintJump.plan(report(y=Y + 0.01), ctx, path, 0, Settings()))
        self.assertIsNone(SprintJump.plan(report(z=0.85), ctx, path, 0, Settings()), "SJ:114: 0.35 off the line")
        self.assertIsNone(SprintJump.plan(report(vz=0.15), ctx, path, 0, Settings()), "SJ:114: drifting sideways (0.15 a tick is 0.082 after friction)")
        # near the end of the path the runway is too short (SJ:114: n < 3)
        self.assertIsNone(SprintJump.plan(report(x=19.5), ctx, path, 19, Settings()))
        world.set(0, Y + 3, 0, STONE)                                        # SJ:81: y + 3 must be clear
        ctx.block_changed()
        self.assertIsNone(SprintJump.plan(report(), ctx, path, 0, Settings()))

    def test_runway_stops_at_a_turn_a_slippery_floor_or_a_block_to_break(self):
        world = flat(radius=30)
        path, ctx = plan_path(world, (0, Y, 0), GoalXZ(20, 0))
        world.set(7, Y - 1, 0, ICE)                                          # SJ:125-130: ice is not a solid floor
        ctx.block_changed()
        jump = SprintJump.plan(report(), ctx, path, 0, Settings())
        self.assertIsNotNone(jump)
        self.assertEqual(len(jump.floors), 7, "cells 0..6 before the ice")
        self.assertFalse(solid_floor(ctx, 7, Y - 1, 0))
        world.set(7, Y - 1, 0, BOTTOM_SLAB)
        ctx.block_changed()
        self.assertFalse(solid_floor(ctx, 7, Y - 1, 0))
        world.set(5, Y - 1, 0, ICE)                                          # five cells: the hop would slide off the end
        ctx.block_changed()
        self.assertIsNone(SprintJump.plan(report(), ctx, path, 0, Settings()))
        world = flat(radius=30)
        cliff(world, 6, -3, 20, 3, Y - 4, 4)                                 # a step up at x 6: an ascend on the path
        path, ctx = plan_path(world, (0, Y, 0), GoalXZ(14, 0))
        kinds = [type(m).__name__ for m in path.movements()]
        self.assertIn("MovementAscend", kinds)
        jump = SprintJump.plan(report(), ctx, path, 0, Settings())
        self.assertIsNotNone(jump)
        self.assertIn(1.0, jump.floors, "the ascend's dest is a cell one up on the runway")
        world.set(3, Y + 1, 0, STONE)                                        # SJ:93-96: a block to break ends the runway
        ctx.block_changed()
        for m in path.movements():
            m.reset_block_cache()
        jump = SprintJump.plan(report(), ctx, path, 0, Settings())
        self.assertTrue(jump is None or len(jump.floors) <= 3)

    def test_a_turn_ends_the_runway(self):
        world = flat(radius=30)
        wall(world, 5, -30, 5, 30, Y, 3)                                     # a wall across x 5 with one doorway at z 4
        clear(world, 5, Y, 4, 5, Y + 2, 4)
        path, ctx = plan_path(world, (0, Y, 0), GoalXZ(10, 0))
        directions = [m.direction for m in path.movements()]
        self.assertGreater(len(set(directions)), 1, "the path turns")
        jump = SprintJump.plan(report(), ctx, path, 0, Settings())
        straight = 1
        while straight < len(directions) and directions[straight] == directions[0]:
            straight += 1
        if jump is not None:
            self.assertLessEqual(len(jump.floors), straight + 1)


if __name__ == "__main__":
    unittest.main()
