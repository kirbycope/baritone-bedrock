"""Path, CutoffPath and SplicedPath: post_process's cut at an impossible movement, the static cutoff, the cut at
the load boundary, splicing, and the realStart fake node (Path.java:88-101)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.context import CalculationContext, pack                          # noqa: E402
from baritone_bedrock.goals import GoalXZ                                              # noqa: E402
from baritone_bedrock.settings import Settings                                         # noqa: E402
from baritone_bedrock.astar import AStarPathFinder, PathNode, PathCalculationResult    # noqa: E402
from baritone_bedrock.path import Path, CutoffPath, SplicedPath                        # noqa: E402
from baritone_bedrock.moves import MovementTraverse, MovementDescend, MovementDiagonal  # noqa: E402
from tests.fixtures import flat, clear, STONE                                          # noqa: E402

Y = 64


def search(world, start, goal, settings=None, hotbar=()):
    ctx = CalculationContext(world, settings=settings, hotbar=list(hotbar))
    finder = AStarPathFinder.create(start, goal, ctx)
    result = finder.calculate(2000, 4000)
    assert result.type == PathCalculationResult.SUCCESS_TO_GOAL, result
    return finder, result.path


def rebuild(finder, dest):
    """A fresh, un-processed Path over the finder's nodes, the way the search hands one to post_process."""
    return Path(finder.real_start, finder.start_node, finder.nodes[pack(*dest)], finder.num_nodes_considered,
                finder.goal, finder.ctx)


class PostProcessTest(unittest.TestCase):

    def test_cut_at_a_diagonal_that_became_impossible(self):
        world = flat(radius=30)
        finder, path = search(world, (0, Y, 0), GoalXZ(10, 10))
        self.assertEqual(len(path.positions()), 11)
        self.assertTrue(all(isinstance(m, MovementDiagonal) for m in path.movements()))
        world.set(5, Y, 6, STONE)                              # both corners of the 6th diagonal
        world.set(6, Y, 5, STONE)
        finder.ctx.block_changed()
        cut = rebuild(finder, (10, Y, 10)).post_process()      # Path:146-160
        self.assertIsInstance(cut, CutoffPath)
        self.assertEqual(cut.positions(), path.positions()[:6])
        self.assertEqual(len(cut.movements()), 5)
        self.assertEqual(cut.get_dest(), (5, Y, 5))
        self.assertEqual(cut.get_num_nodes_considered(), finder.num_nodes_considered)

    def test_cut_at_a_descend_that_became_a_fall(self):
        world = flat(radius=30)
        clear(world, 5, Y - 1, -2, 20, Y - 1, 2)               # a step down at x = 5
        finder, path = search(world, (0, Y, 0), GoalXZ(10, 0))
        kinds = [type(m) for m in path.movements()]
        self.assertIn(MovementDescend, kinds)
        descend_at = kinds.index(MovementDescend)
        src = path.positions()[descend_at]
        clear(world, src[0] + 1, Y - 2, src[2], src[0] + 1, Y - 2, src[2])   # the landing is now one deeper
        finder.ctx.block_changed()
        cut = rebuild(finder, path.get_dest()).post_process()
        self.assertIsInstance(cut, CutoffPath)
        self.assertEqual(len(cut.movements()), descend_at)
        self.assertEqual(cut.get_dest(), src)

    def test_an_unchanged_path_is_returned_whole(self):
        world = flat(radius=30)
        finder, path = search(world, (0, Y, 0), GoalXZ(10, 0))
        again = rebuild(finder, (10, Y, 0))
        with self.assertRaises(RuntimeError):
            again.movements()                                  # Path:169-173: not yet verified
        self.assertIs(again.post_process(), again)
        self.assertEqual(len(again.movements()), 10)
        with self.assertRaises(RuntimeError):
            again.post_process()                               # Path:147-149: never twice
        self.assertTrue(all(m.calculated_while_loaded for m in again.movements()))   # M:445-447
        self.assertAlmostEqual(again.ticks_remaining_from(5), 5 * 3.5638, places=3)  # IPath:99-107

    def test_override_takes_the_smaller_of_cost_and_node_difference(self):
        world = flat(radius=30)
        finder, path = search(world, (0, Y, 0), GoalXZ(10, 0))
        for movement in path.movements():                      # Path:135-136
            self.assertAlmostEqual(movement.get_cost(), 3.5638, places=4)

    def test_real_start_fake_node(self):
        world = flat(radius=30)
        ctx = CalculationContext(world)
        goal = GoalXZ(0, 0)
        start_node = PathNode(0, Y, 0, goal)
        start_node.cost = 0.0
        path = Path((1, Y, 0), start_node, start_node, 7, goal, ctx)   # Path:88-101
        self.assertEqual(path.positions(), [(1, Y, 0), (0, Y, 0)])
        self.assertEqual(path.get_src(), (1, Y, 0))
        self.assertEqual(path.get_dest(), (0, Y, 0))
        self.assertIs(path.post_process(), path)
        self.assertEqual(len(path.movements()), 1)
        self.assertIsInstance(path.movements()[0], MovementTraverse)
        self.assertEqual(path.movements()[0].get_cost(), 0.0)   # min(calculated, 0 - 0)
        self.assertEqual(path.get_num_nodes_considered(), 7)
        same = Path((0, Y, 0), start_node, start_node, 0, goal, ctx)
        self.assertEqual(same.positions(), [(0, Y, 0)])
        self.assertEqual(same.post_process().movements(), [])
        longer = PathNode(1, Y, 0, goal)
        longer.previous = start_node
        longer.cost = 3.5
        not_single = Path((5, Y, 0), start_node, longer, 0, goal, ctx)   # Path:92: only when A* gave no movements
        self.assertEqual(not_single.positions(), [(0, Y, 0), (1, Y, 0)])

    def test_sanity_check(self):
        with self.assertRaises(RuntimeError):
            SplicedPath([(0, Y, 0), (1, Y, 0)], [MovementTraverse((0, Y, 0), (2, Y, 0))], 0, None)
        with self.assertRaises(RuntimeError):
            SplicedPath([(0, Y, 0), (1, Y, 0), (0, Y, 0), (1, Y, 0)],
                        [MovementTraverse((0, Y, 0), (1, Y, 0)), MovementTraverse((1, Y, 0), (0, Y, 0)),
                         MovementTraverse((0, Y, 0), (1, Y, 0))], 0, None)   # doubles back


class StaticCutoffTest(unittest.TestCase):

    def test_long_path_short_of_the_goal_loses_its_tail(self):
        world = flat(radius=60)
        _finder, path = search(world, (0, Y, 0), GoalXZ(45, 0))
        self.assertEqual(path.length(), 46)
        self.assertIs(path.static_cutoff(GoalXZ(45, 0)), path)                        # PathBase:51-53: in the goal
        cut = path.static_cutoff(GoalXZ(100, 0))
        self.assertIsInstance(cut, CutoffPath)
        self.assertEqual(cut.length(), int((46 - 30) * 0.9) + 30)                      # PathBase:55: 44 positions
        self.assertEqual(len(cut.movements()), cut.length() - 1)
        self.assertEqual(cut.get_dest(), (43, Y, 0))
        self.assertIs(path.static_cutoff(None), path)

    def test_minimum_length(self):
        world = flat(radius=60)
        _finder, path = search(world, (0, Y, 0), GoalXZ(29, 0))
        self.assertEqual(path.length(), 30)
        self.assertEqual(path.static_cutoff(GoalXZ(100, 0)).length(), 30)             # 30 - 1 keeps all 30
        _finder, short = search(world, (0, Y, 0), GoalXZ(20, 0))
        self.assertIs(short.static_cutoff(GoalXZ(100, 0)), short)                      # PathBase:48-50

    def test_settings_are_read(self):
        world = flat(radius=60)
        _finder, path = search(world, (0, Y, 0), GoalXZ(45, 0), settings=Settings(pathCutoffFactor=0.5))
        self.assertEqual(path.static_cutoff(GoalXZ(100, 0)).length(), int(16 * 0.5) + 30)
        _finder, path = search(world, (0, Y, 0), GoalXZ(45, 0), settings=Settings(pathCutoffMinimumLength=100))
        self.assertIs(path.static_cutoff(GoalXZ(100, 0)), path)


class CutoffAtLoadedChunksTest(unittest.TestCase):

    def test_cut_at_the_first_unloaded_column(self):
        world = flat(radius=40)
        _finder, path = search(world, (0, Y, 0), GoalXZ(20, 0))
        smaller = CalculationContext(flat(radius=40, box=((-64, -64, -64), (10, 320, 64))))
        cut = path.cutoff_at_loaded_chunks(smaller)
        self.assertIsInstance(cut, CutoffPath)
        # PathBase:36-41 as written: CutoffPath(this, i) keeps position i, the first unloaded one
        self.assertEqual(cut.get_dest(), (11, Y, 0))
        self.assertEqual(cut.length(), 12)
        self.assertEqual(len(cut.movements()), 11)

    def test_untouched_when_loaded_or_off(self):
        world = flat(radius=40)
        _finder, path = search(world, (0, Y, 0), GoalXZ(20, 0))
        self.assertIs(path.cutoff_at_loaded_chunks(CalculationContext(world)), path)
        off = CalculationContext(flat(radius=40, box=((-64, -64, -64), (10, 320, 64))),
                                 settings=Settings(cutoffAtLoadBoundary=False))
        self.assertIs(path.cutoff_at_loaded_chunks(off), path)                         # PathBase:32-34

    def test_search_result_is_already_cut(self):
        world = flat(radius=40, box=((-64, -64, -64), (10, 320, 64)))
        ctx = CalculationContext(world)
        result = AStarPathFinder.create((0, Y, 0), GoalXZ(30, 0), ctx).calculate(2000, 4000)
        self.assertEqual(result.type, PathCalculationResult.SUCCESS_SEGMENT)
        self.assertTrue(all(ctx.is_loaded(x, z) for x, _y, z in result.path.positions()))


class SpliceTest(unittest.TestCase):

    def test_happy_path(self):
        world = flat(radius=40)
        finder_a, first = search(world, (0, Y, 0), GoalXZ(10, 0))
        finder_b, second = search(world, (10, Y, 0), GoalXZ(20, 0))
        spliced = SplicedPath.try_splice(first, second)                                 # SplicedPath:72-105
        self.assertIsInstance(spliced, SplicedPath)
        self.assertEqual(spliced.length(), 21)
        self.assertEqual(len(spliced.movements()), 20)
        self.assertEqual(spliced.get_src(), (0, Y, 0))
        self.assertEqual(spliced.get_dest(), (20, Y, 0))
        self.assertEqual(spliced.positions(), first.positions() + second.positions()[1:])
        self.assertEqual(spliced.movements(), first.movements() + second.movements())
        self.assertEqual(spliced.get_num_nodes_considered(), first.get_num_nodes_considered() + second.get_num_nodes_considered())
        self.assertIs(spliced.get_goal(), first.get_goal())
        self.assertAlmostEqual(spliced.ticks_remaining_from(0), 20 * 3.5638, places=3)

    def test_mismatched_ends(self):
        world = flat(radius=40)
        _a, first = search(world, (0, Y, 0), GoalXZ(10, 0))
        _b, elsewhere = search(world, (11, Y, 0), GoalXZ(20, 0))
        self.assertIsNone(SplicedPath.try_splice(first, elsewhere))                    # SplicedPath:76-78
        self.assertIsNone(SplicedPath.try_splice(first, None))
        self.assertIsNone(SplicedPath.try_splice(None, first))

    def test_overlap_is_refused_unless_allowed(self):
        world = flat(radius=40)
        _a, first = search(world, (0, Y, 0), GoalXZ(10, 0))
        _b, back = search(world, (10, Y, 0), GoalXZ(5, 0))      # walks back over the first path
        self.assertIsNone(SplicedPath.try_splice(first, back))                         # SplicedPath:87-90
        spliced = SplicedPath.try_splice(first, back, allow_overlap_cutoff=True)       # SplicedPath:81-86, 98-104
        self.assertEqual(spliced.positions(), first.positions()[:6])
        self.assertEqual(len(spliced.movements()), 5)
        self.assertEqual(spliced.get_dest(), (5, Y, 0))


if __name__ == "__main__":
    unittest.main()
