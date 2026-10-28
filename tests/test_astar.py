"""The search: a straight line, a detour, the partial path on a walled hill, rolling hills in the primary budget,
Favoring, the unloaded-chunk fetch limit, cancel from another thread, the heap and the goals."""
import math
import os
import random
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.costs import DEFAULT as C                                        # noqa: E402
from baritone_bedrock.context import CalculationContext, pack                          # noqa: E402
from baritone_bedrock.settings import Settings                                         # noqa: E402
from baritone_bedrock.goals import (GoalXZ, GoalBlock, GoalYLevel, GoalNear, GoalGetToBlock, GoalTwoBlocks,
                                    GoalComposite, GoalInverted, calculate_xz)         # noqa: E402
from baritone_bedrock.astar import (AStarPathFinder, BinaryHeapOpenSet, PathNode, Favoring,
                                    PathCalculationResult)                             # noqa: E402
from baritone_bedrock.moves import MovementTraverse                                    # noqa: E402
from tests.fixtures import flat, clear, wall, flat_array, rolling_hills, BEDROCK, STONE, DIRT   # noqa: E402

Y = 64


def search(world, start, goal, settings=None, hotbar=(), primary_ms=500, failure_ms=2000, favoring=None):
    ctx = CalculationContext(world, settings=settings, hotbar=list(hotbar))
    finder = AStarPathFinder.create(start, goal, ctx, favoring=favoring)
    t = time.perf_counter()
    result = finder.calculate(primary_ms, failure_ms)
    return finder, result, time.perf_counter() - t


class StraightLineTest(unittest.TestCase):

    def test_flat_ground(self):
        world = flat(radius=30)
        finder, result, elapsed = search(world, (0, Y, 0), GoalXZ(20, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        path = result.path
        self.assertEqual(path.length(), 21)
        self.assertEqual(path.get_src(), (0, Y, 0))
        self.assertEqual(path.get_dest(), (20, Y, 0))
        self.assertEqual(path.positions(), [(x, Y, 0) for x in range(21)])
        self.assertTrue(all(isinstance(m, MovementTraverse) for m in path.movements()))
        self.assertAlmostEqual(sum(m.get_cost() for m in path.movements()), 20 * C.SPRINT_ONE_BLOCK_COST, places=3)
        self.assertAlmostEqual(finder.nodes[pack(20, Y, 0)].cost, 20 * C.SPRINT_ONE_BLOCK_COST, places=3)
        self.assertLess(elapsed, 0.05)
        self.assertEqual(finder.num_nodes_considered, 21)      # the heuristic is a hair under the sprint cost
        self.assertTrue(finder.is_finished)
        with self.assertRaises(RuntimeError):
            finder.calculate(500, 2000)                        # ANCS:122-124: not reusable

    def test_diagonal_and_mixed(self):
        world = flat(radius=30)
        _finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(10, 4))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(result.path.length(), 11)             # 4 diagonals and 6 traverses
        self.assertAlmostEqual(sum(m.get_cost() for m in result.path.movements()),
                               4 * 5.0400 + 6 * 3.5638, places=3)

    def test_goal_at_start(self):
        world = flat(radius=30)
        _finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(0, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(result.path.positions(), [(0, Y, 0)])
        self.assertEqual(result.path.movements(), [])

    def test_failure_when_nothing_is_reachable(self):
        world = flat(radius=2)                                 # a 5x5 island, no throwaway, no breaking
        _finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0), settings=Settings(allowBreak=False))
        self.assertEqual(result.type, PathCalculationResult.FAILURE)   # nothing more than 5 blocks away
        self.assertIsNone(result.path)


class DetourTest(unittest.TestCase):

    def test_wall_forces_a_detour(self):
        world = flat(radius=30)
        wall(world, 10, -6, 10, 6, Y, 3, BEDROCK)
        _finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        path = result.path
        # diagonals carry the sideways offset without adding positions, so the detour shows in the cost
        self.assertGreater(sum(m.get_cost() for m in path.movements()), 20 * C.SPRINT_ONE_BLOCK_COST + 10)
        crossings = [z for x, _y, z in path.positions() if x == 10]
        self.assertTrue(crossings)
        for z in crossings:
            self.assertGreater(abs(z), 6)
        self.assertEqual(path.get_dest(), (20, Y, 0))

    def test_breaking_through_when_the_detour_is_longer(self):
        world = flat(radius=40)
        wall(world, 10, -40, 10, 40, Y, 3, DIRT)              # dirt across the whole world: 17 ticks a block
        _finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(result.path.length(), 21)
        through = result.path.movements()[9]
        self.assertEqual(through.dest, (10, Y, 0))
        self.assertAlmostEqual(through.get_cost(), C.WALK_ONE_BLOCK_COST + 17 + 17, places=3)
        self.assertEqual(through.to_break(world), [(10, Y + 1, 0), (10, Y, 0)])


class WalledHillTest(unittest.TestCase):
    """The plan's two-wall case: a 3 block cliff across the way with a 3 deep trench at its foot, a ramp up it
    walled off. The goal beyond is unreachable without blocks or digging, so the search exhausts the surface and
    returns its best segment. The dead end against the slope is the trench floor, nearest the goal but costly to
    reach (a fall, no way back up); Baritone's best-so-far metric, estimatedCostToGoal + cost / 1.5, prefers the
    trench edge, which a smallest-heuristic pick (the old navigator) never would."""

    def build(self):
        world = flat(radius=24)
        world.fill(-24, Y, 9, 24, Y + 2, 24, STONE)           # the plateau, 3 above the ground, from z = 9 south
        clear(world, -24, Y - 3, 7, 24, Y - 1, 8)              # the trench at its foot, floor at 60
        world.fill(-16, Y - 3, 7, -14, Y - 1, 8, STONE)        # except where the ramp is approached
        clear(world, -15, Y + 1, 9, -15, Y + 2, 9)             # the ramp at x = -15: tops at 64, 65, 66
        clear(world, -15, Y + 2, 10, -15, Y + 2, 10)
        wall(world, -18, 5, -12, 5, Y, 4, BEDROCK)             # the wall around the ramp approach
        wall(world, -18, 5, -18, 8, Y, 4, BEDROCK)
        wall(world, -12, 5, -12, 8, Y, 4, BEDROCK)
        return world

    def test_the_ramp_works_when_not_walled(self):
        world = self.build()
        clear(world, -18, Y, 5, -12, Y + 3, 8)                 # take the walls away
        _finder, result, _elapsed = search(world, (-15, Y, 0), GoalXZ(0, 20), settings=Settings(allowBreak=False))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(result.path.get_dest()[1], Y + 3)
        self.assertIn((-15, Y + 1, 9), result.path.positions())

    def test_partial_path_avoids_the_dead_end(self):
        world = self.build()
        finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(0, 20), settings=Settings(allowBreak=False))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_SEGMENT)
        self.assertIsNone(result.error)
        dest = result.path.get_dest()
        self.assertGreater(math.dist(dest, (0, Y, 0)), 5)      # ANCS:245 MIN_DIST_PATH
        self.assertEqual(dest, (0, Y, 6))                      # the trench edge
        self.assertIn(pack(0, Y - 3, 8), finder.nodes)         # the dead end was reached and considered
        self.assertLess(finder.nodes[pack(0, Y - 3, 8)].estimated_cost_to_goal, finder.nodes[pack(0, Y, 6)].estimated_cost_to_goal)
        self.assertEqual(finder.best_so_far_nodes[0].pos, dest)   # the coefficient 1.5 tracker won
        self.assertTrue(all(y == Y for _x, y, _z in result.path.positions()))
        self.assertEqual(result.path.get_src(), (0, Y, 0))


class RollingHillsTest(unittest.TestCase):

    def test_150_blocks_in_the_primary_budget(self):
        world, heights = rolling_hills(256, seed=1)
        start = (20, heights[20][20] + 1, 20)
        finder, result, elapsed = search(world, start, GoalXZ(170, 170), primary_ms=500, failure_ms=2000)
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL, (finder.num_nodes_considered, elapsed))
        self.assertLess(elapsed, 0.6)
        path = result.path
        self.assertEqual(path.get_src(), start)
        self.assertEqual(path.get_dest()[0::2], (170, 170))
        for (x, y, z), movement in zip(path.positions(), path.movements()):
            self.assertEqual(movement.src, (x, y, z))
            self.assertLessEqual(abs(movement.dest[0] - x), 1)
            self.assertLessEqual(abs(movement.dest[2] - z), 1)
            self.assertEqual(y, heights[x][z] + 1)             # on the surface, no digging
        self.assertGreater(finder.num_nodes_considered, 150)

    def test_with_dirt_in_hand(self):
        world, heights = rolling_hills(256, seed=2)
        start = (30, heights[30][30] + 1, 30)
        _finder, result, _elapsed = search(world, start, GoalXZ(180, 180), hotbar=[DIRT], primary_ms=1500, failure_ms=4000)
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)


class FavoringTest(unittest.TestCase):

    def test_previous_path_positions_cost_half(self):
        world = flat(radius=30)
        _first, result, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0))
        previous = result.path
        favoring = Favoring(previous, 0.5)                     # Favoring:39-46
        self.assertFalse(favoring.is_empty())
        self.assertTrue(Favoring().is_empty())
        for pos in previous.positions():
            self.assertEqual(favoring.calculate(pack(*pos)), 0.5)
        self.assertEqual(favoring.calculate(pack(0, Y, 5)), 1.0)
        self.assertTrue(Favoring(previous, 1.0).is_empty())    # Favoring:43: a coefficient of 1 favors nothing
        finder, again, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0), favoring=favoring)
        self.assertEqual(again.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(again.path.positions(), previous.positions())
        self.assertAlmostEqual(finder.nodes[pack(20, Y, 0)].cost, 10 * C.SPRINT_ONE_BLOCK_COST, places=3)   # AS:175
        for movement in again.path.movements():                # Path:135: the node difference is the stricter
            self.assertAlmostEqual(movement.get_cost(), C.SPRINT_ONE_BLOCK_COST / 2, places=4)

    def test_favoring_is_applied_once_per_node(self):
        # AS:168-176: the favor is looked up when the node is first reached and reused for every edge into it;
        # a favored lane off the straight line is not necessarily taken, because halving costs under a fixed
        # costHeuristic makes the heuristic inadmissible, in Baritone as here
        world = flat(radius=30)
        favoring = Favoring()
        favoring.add(5, Y, 0, 0.5)
        finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(20, 0), favoring=favoring)
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_TO_GOAL)
        self.assertEqual(finder.nodes[pack(5, Y, 0)].favor, 0.5)
        self.assertEqual(finder.nodes[pack(6, Y, 0)].favor, 1.0)
        self.assertAlmostEqual(finder.nodes[pack(20, Y, 0)].cost, 19.5 * C.SPRINT_ONE_BLOCK_COST, places=3)


class UnloadedChunksTest(unittest.TestCase):

    def test_fetch_limit_stops_the_search_mid_chunk(self):
        world = flat(radius=40, box=((-64, -64, -64), (8, 320, 64)))   # the ground goes on, the bands do not
        finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(60, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_SEGMENT)
        self.assertGreaterEqual(finder.num_empty_chunk, 50)    # AS:87, 113-115 and the Fidelity 2 count
        self.assertTrue(all(x <= 8 for x, _y, _z in result.path.positions()))
        self.assertGreater(math.dist(result.path.get_dest(), (0, Y, 0)), 5)

    def test_fetch_limit_at_a_chunk_border(self):
        world = flat(radius=40, box=((-64, -64, -64), (15, 320, 64)))
        finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(60, 0))
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_SEGMENT)
        self.assertGreaterEqual(finder.num_empty_chunk, 50)    # AS:111-116: counted before any cost is computed
        self.assertTrue(all(x <= 15 for x, _y, _z in result.path.positions()))
        self.assertFalse(finder.ctx.is_loaded(16, 0))
        self.assertTrue(finder.ctx.is_loaded(15, 0))

    def test_a_higher_limit_searches_on(self):
        world = flat(radius=40, box=((-64, -64, -64), (8, 320, 64)))
        finder, result, _elapsed = search(world, (0, Y, 0), GoalXZ(60, 0),
                                          settings=Settings(pathingMaxChunkBorderFetch=1 << 30, allowBreak=False),
                                          primary_ms=200, failure_ms=400)
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_SEGMENT)
        self.assertGreater(finder.num_empty_chunk, 50)


class CancelTest(unittest.TestCase):

    def test_cancel_from_another_thread(self):
        world = flat_array(128)
        ctx = CalculationContext(world, settings=Settings(pathingMaxChunkBorderFetch=1 << 30))
        finder = AStarPathFinder.create((64, Y, 64), GoalXZ(100_000, 100_000), ctx)
        box = {}

        def run():
            box["result"] = finder.calculate(5000, 5000)

        thread = threading.Thread(target=run)
        t = time.perf_counter()
        thread.start()
        time.sleep(0.05)
        finder.cancel()                                        # ANCS:116-118
        thread.join(timeout=5)
        elapsed = time.perf_counter() - t
        self.assertFalse(thread.is_alive())
        self.assertEqual(box["result"].type, PathCalculationResult.CANCELLATION)   # ANCS:129-131
        self.assertIsNone(box["result"].path)
        self.assertLess(elapsed, 2.0)
        self.assertGreater(finder.num_nodes_considered, 0)
        self.assertIsNotNone(finder.best_path_so_far())        # the partial result is still there
        self.assertIsNotNone(finder.path_to_most_recent_node_considered())


class HeapTest(unittest.TestCase):

    def test_ordering_and_decrease_key(self):
        goal = GoalXZ(0, 0)
        heap = BinaryHeapOpenSet()
        rng = random.Random(3)
        nodes = []
        for i in range(500):
            node = PathNode(i, 0, 0, goal)
            node.combined_cost = rng.random() * 100
            nodes.append(node)
            heap.insert(node)
            self.assertTrue(node.is_open())
        self.assertEqual(len(heap), 500)
        for node in nodes[::7]:                                # decrease-key through heap_position
            node.combined_cost -= 50
            heap.update(node)
        out = []
        while not heap.is_empty():
            node = heap.remove_lowest()
            self.assertEqual(node.heap_position, -1)
            out.append(node.combined_cost)
        self.assertEqual(out, sorted(out))
        self.assertEqual(len(out), 500)
        with self.assertRaises(RuntimeError):
            heap.remove_lowest()

    def test_node(self):
        node = PathNode(1, 2, 3, GoalXZ(4, 3))
        self.assertEqual(node.pos, (1, 2, 3))
        self.assertAlmostEqual(node.estimated_cost_to_goal, calculate_xz(3, 0))
        self.assertEqual(node.cost, C.COST_INF)
        self.assertFalse(node.is_open())


class GoalsTest(unittest.TestCase):

    def test_formulas(self):
        self.assertAlmostEqual(GoalXZ(0, 0).heuristic(3, 50, 4), (3 * math.sqrt(2) + 1) * 3.563)   # GoalXZ:96-114
        self.assertAlmostEqual(GoalXZ(0, 0).heuristic(-7, 0, 0), 7 * 3.563)
        self.assertTrue(GoalXZ(5, 5).is_in_goal(5, 99, 5))
        self.assertFalse(GoalXZ(5, 5).is_in_goal(5, 99, 6))
        self.assertAlmostEqual(GoalYLevel(60).heuristic(0, 63, 0), 3 * C.FALL_N_BLOCKS_COST[2] / 2)   # GoalYLevel:47-58
        self.assertAlmostEqual(GoalYLevel(60).heuristic(0, 58, 0), 2 * C.JUMP_ONE_BLOCK_COST)
        self.assertEqual(GoalYLevel(60).heuristic(0, 60, 0), 0)
        self.assertAlmostEqual(GoalBlock(0, 60, 0).heuristic(3, 62, 4), GoalXZ(0, 0).heuristic(3, 0, 4) + GoalYLevel(60).heuristic(0, 62, 0))
        self.assertTrue(GoalBlock(1, 2, 3).is_in_goal(1, 2, 3))
        self.assertFalse(GoalBlock(1, 2, 3).is_in_goal(1, 3, 3))

    def test_near_and_get_to_block(self):
        near = GoalNear(0, 64, 0, 2)
        self.assertTrue(near.is_in_goal(1, 65, 1))             # 3 <= 4
        self.assertFalse(near.is_in_goal(2, 64, 1))            # 5 > 4
        self.assertEqual(near.range_sq, 4)
        self.assertGreater(near.heuristic_inside(), 0)         # GoalNear:68-91
        self.assertLess(near.heuristic_inside(), near.heuristic(0, 64, 3))
        get = GoalGetToBlock(0, 64, 0)                         # GoalGetToBlock:49-54
        # beside it, on it, under it (one or two below: the block is at head height or the one above), and one
        # below and beside (the block beside the head)
        for pos in ((1, 64, 0), (0, 64, 1), (0, 65, 0), (0, 63, 0), (0, 62, 0), (-1, 64, 0), (1, 63, 0)):
            self.assertTrue(get.is_in_goal(*pos), pos)
        for pos in ((1, 64, 1), (0, 66, 0), (0, 61, 0), (2, 64, 0), (1, 62, 0), (1, 65, 0)):
            self.assertFalse(get.is_in_goal(*pos), pos)
        self.assertEqual(get.heuristic(0, 63, 0), 0)
        two = GoalTwoBlocks(0, 64, 0)
        self.assertTrue(two.is_in_goal(0, 63, 0))
        self.assertFalse(two.is_in_goal(0, 65, 0))

    def test_composite_and_inverted(self):
        composite = GoalComposite(GoalXZ(0, 0), GoalXZ(10, 0))
        self.assertTrue(composite.is_in_goal(10, 5, 0))
        self.assertAlmostEqual(composite.heuristic(8, 0, 0), 2 * 3.563)
        self.assertEqual(composite.heuristic_inside(), 0)
        inverted = GoalInverted(GoalXZ(0, 0))
        self.assertFalse(inverted.is_in_goal(0, 0, 0))
        self.assertAlmostEqual(inverted.heuristic(2, 0, 0), -2 * 3.563)
        self.assertEqual(inverted.heuristic_inside(), -math.inf)
        self.assertEqual(GoalXZ(1, 2), GoalXZ(1, 2))
        self.assertEqual(len({GoalXZ(1, 2), GoalXZ(1, 2)}), 1)


if __name__ == "__main__":
    unittest.main()
