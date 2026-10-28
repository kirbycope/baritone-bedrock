"""The movement costs, pinned to docs/research-movements.md section 9's default-setting tables (4 decimals), on
DictWorld fixtures. Feet are at y = 64 on the flat fixture (grass at 63, dirt 61-62, stone 59-60)."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.costs import COST_INF, DEFAULT as C                              # noqa: E402
from baritone_bedrock.context import CalculationContext, ToolSet, pack, unpack         # noqa: E402
from baritone_bedrock.settings import Settings                                         # noqa: E402
from baritone_bedrock.world import DictWorld, classify, UNKNOWN                        # noqa: E402
from baritone_bedrock import helpers                                                   # noqa: E402
from baritone_bedrock.moves import (MOVES, MOVES_BY_NAME, MoveResult, MovementStatus, traverse_cost, ascend_cost,
                                    descend_cost, diagonal_cost, downward_cost, pillar_cost, parkour_cost,
                                    MovementTraverse, MovementAscend, MovementDescend, MovementFall,
                                    MovementDiagonal, MovementDownward, MovementPillar, MovementParkour)   # noqa: E402
from tests.fixtures import (flat, clear, column, GRASS, DIRT, STONE, SAND, SOUL_SAND, ICE, WATER, FLOWING_WATER,
                            LADDER, BOTTOM_SLAB, DOUBLE_SLAB, UNKNOWN_BLOCK, POPPY, TALL_GRASS, CACTUS)   # noqa: E402

Y = 64   # the feet on the flat fixture


def ctx_for(world, hotbar=(), can_sprint=True, **overrides):
    return CalculationContext(world, settings=Settings(**overrides), hotbar=list(hotbar), can_sprint=can_sprint)


def descend(ctx, x, y, z, dx, dz):
    res = MoveResult()
    descend_cost(ctx, x, y, z, x + dx, z + dz, res)
    return res


def diagonal(ctx, x, y, z, dx, dz):
    res = MoveResult()
    diagonal_cost(ctx, x, y, z, x + dx, z + dz, res)
    return res


def parkour(ctx, x, y, z, dx, dz):
    res = MoveResult()
    parkour_cost(ctx, x, y, z, dx, dz, res)
    return res


class TraverseTest(unittest.TestCase):

    def test_sprint_and_walk(self):
        world = flat()
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 3.5638, places=4)           # Trav:116
        self.assertAlmostEqual(traverse_cost(ctx_for(world, can_sprint=False), 0, Y, 0, 1, 0), 4.6328, places=4)
        self.assertAlmostEqual(traverse_cost(ctx_for(world, allowSprint=False), 0, Y, 0, 1, 0), 4.6328, places=4)

    def test_water_costs_the_wading_speed(self):
        # departs: plan Fidelity 1. Baritone as written gives 4.6328 here (quirk 1); ours wades at 9.0909
        world = flat()
        world.set(0, Y, 0, WATER)
        world.set(1, Y, 0, WATER)
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 9.0909, places=4)

    def test_soul_sand_halves(self):
        world = flat()
        world.set(1, Y - 1, 0, SOUL_SAND)
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 6.9493 * C.SPRINT_MULTIPLIER, places=4)

    def test_side_place_bridge(self):
        world = flat()
        clear(world, 1, Y - 1, 0, 1, Y - 1, 0)                 # a hole where the dest floor was
        self.assertAlmostEqual(traverse_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), 24.6328, places=4)   # Trav:153
        self.assertGreaterEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)                    # no throwaway

    def test_sneak_backplace(self):
        world = DictWorld()
        column(world, 0, 0, 60, [STONE, STONE, STONE, STONE])  # a lone pillar, feet at 64, nothing beside the dest floor
        self.assertAlmostEqual(traverse_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), 35.3846, places=4)   # Trav:167-168
        # quirk 4: the backplace never checks canPlaceAgainst(src floor); it is refused from soul sand and half slabs
        world.set(0, Y - 1, 0, SOUL_SAND)
        self.assertGreaterEqual(traverse_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), COST_INF)             # Trav:157
        world.set(0, Y - 1, 0, BOTTOM_SLAB)
        self.assertGreaterEqual(traverse_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), COST_INF)
        world.set(0, Y - 1, 0, DOUBLE_SLAB)
        self.assertAlmostEqual(traverse_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), 35.3846, places=4)

    def test_breaking(self):
        world = flat()
        world.set(1, Y, 0, DIRT)
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 4.6328 + 17, places=4)        # dirt by hand
        world.set(1, Y, 0, STONE)
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 4.6328 + 152, places=4)       # stone by hand
        self.assertAlmostEqual(traverse_cost(ctx_for(world, ["minecraft:wooden_pickaxe"]), 0, Y, 0, 1, 0), 4.6328 + 24.5, places=4)
        self.assertAlmostEqual(traverse_cost(ctx_for(world, ["minecraft:diamond_pickaxe"]), 0, Y, 0, 1, 0), 4.6328 + 7.625, places=4)
        world.set(1, Y, 0, UNKNOWN_BLOCK)                      # departs: Fidelity 5, not on the whitelist
        self.assertGreaterEqual(traverse_cost(ctx_for(world, ["minecraft:diamond_pickaxe"]), 0, Y, 0, 1, 0), COST_INF)
        world.set(1, Y, 0, ICE)                                # neverBreak (MH:91)
        self.assertGreaterEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        world.set(1, Y, 0, DIRT)
        self.assertGreaterEqual(traverse_cost(ctx_for(world, allowBreak=False), 0, Y, 0, 1, 0), COST_INF)   # CC:279
        world.set(1, Y + 1, 0, DIRT)                            # head block too, with falling blocks above it
        world.set(1, Y + 2, 0, SAND)
        world.set(1, Y + 3, 0, SAND)
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 4.6328 + 17 + 17 + 17 + 17, places=4)   # MH:647-652

    def test_avoid_breaking_beside_water_and_unsupported_sand(self):
        world = flat()
        world.set(1, Y, 0, DIRT)
        world.set(1, Y, 1, WATER)                              # a source beside it (MH:115-118)
        self.assertGreaterEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        world = flat()
        world.set(1, Y, 0, DIRT)
        world.set(1, Y + 1, 0, DIRT)
        world.set(2, Y + 1, 0, SAND)                           # unsupported sand beside the head block (MH:104-109)
        self.assertGreaterEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        self.assertLess(traverse_cost(ctx_for(world, avoidUpdatingFallingBlocks=False), 0, Y, 0, 1, 0), COST_INF)

    def test_walk_on_water_penalty_and_danger(self):
        world = flat()
        world.set(1, Y, 0, CACTUS)                             # danger is not mined: it is not on the whitelist
        self.assertGreaterEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        world = flat()
        world.set(1, Y, 0, POPPY)                              # a plant is walked through
        self.assertAlmostEqual(traverse_cost(ctx_for(world), 0, Y, 0, 1, 0), 3.5638, places=4)

    def test_movement_object(self):
        world = flat()
        move = MovementTraverse((0, Y, 0), (1, Y, 0))
        self.assertEqual(move.positions_to_break, ((1, Y + 1, 0), (1, Y, 0)))
        self.assertEqual(move.position_to_place, (1, Y - 1, 0))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (1, Y, 0)})
        self.assertEqual(move.direction, (1, 0, 0))
        ctx = ctx_for(world)
        self.assertAlmostEqual(move.get_cost(ctx), 3.5638, places=4)
        move.override(1.0)
        self.assertEqual(move.get_cost(), 1.0)
        self.assertAlmostEqual(move.recalculate_cost(ctx), 3.5638, places=4)
        self.assertEqual(move.to_break(ctx), [])
        self.assertEqual(move.to_place(world), [])
        self.assertEqual(move.status, MovementStatus.PREPPING)
        self.assertTrue(callable(move.tick))                                  # phase 2: tests/test_executor.py drives it
        self.assertTrue(callable(MovementParkour((0, Y, 0), 2, (1, 0), False).update_state))   # phase 3: driven there too
        world.set(1, Y, 0, DIRT)
        clear(world, 1, Y - 1, 0, 1, Y - 1, 0)
        move = MovementTraverse((0, Y, 0), (1, Y, 0))
        self.assertEqual(move.to_break(world), [(1, Y, 0)])
        self.assertEqual(move.to_place(world), [(1, Y - 1, 0)])


class AscendTest(unittest.TestCase):

    def test_plain_soul_sand_and_placement(self):
        world = flat()
        world.set(1, Y, 0, STONE)
        self.assertAlmostEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), 6.6328, places=4)              # Asc:139-141
        world.set(1, Y, 0, SOUL_SAND)
        self.assertAlmostEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), 11.2657, places=4)             # Asc:134-135
        world = flat()
        self.assertAlmostEqual(ascend_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), 26.6328, places=4)     # Asc:71, +20
        self.assertGreaterEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        world.set(1, Y, 0, POPPY)                              # not replaceable (Asc:75-77)
        self.assertGreaterEqual(ascend_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), COST_INF)
        world.set(1, Y, 0, TALL_GRASS)                         # replaceable (MH:325-327)
        self.assertAlmostEqual(ascend_cost(ctx_for(world, [DIRT]), 0, Y, 0, 1, 0), 26.6328, places=4)

    def test_slabs(self):
        world = flat()
        world.set(0, Y - 1, 0, BOTTOM_SLAB)
        world.set(1, Y, 0, STONE)
        self.assertGreaterEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)                    # Asc:121-123
        world.set(1, Y, 0, BOTTOM_SLAB)
        self.assertAlmostEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), 6.6328, places=4)              # Asc:127-128
        world.set(0, Y - 1, 0, GRASS)
        self.assertAlmostEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), 4.6328, places=4)              # Asc:130

    def test_head_room_and_falling_blocks(self):
        world = flat()
        world.set(1, Y, 0, STONE)
        world.set(0, Y + 2, 0, DIRT)                           # src head+1 must be broken (Asc:147)
        self.assertAlmostEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), 6.6328 + 17, places=4)
        world.set(0, Y + 3, 0, SAND)                           # Asc:96-108: it would fall on us
        self.assertGreaterEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)
        world = flat()
        world.set(1, Y, 0, STONE)
        world.set(0, Y - 1, 0, LADDER)                         # Asc:115-117
        self.assertGreaterEqual(ascend_cost(ctx_for(world), 0, Y, 0, 1, 0), COST_INF)

    def test_valid_positions(self):
        move = MovementAscend((0, Y, 0), (1, Y + 1, 0))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (0, Y + 1, 0), (1, Y + 1, 0), (-1, Y, 0), (-1, Y + 1, 0)})
        self.assertEqual(move.positions_to_break, ((1, Y + 1, 0), (0, Y + 2, 0), (1, Y + 2, 0)))
        self.assertEqual(move.position_to_place, (1, Y, 0))


class DescendAndFallTest(unittest.TestCase):

    def test_descend(self):
        world = flat()
        clear(world, 1, Y - 1, 0, 1, Y - 1, 0)
        res = descend(ctx_for(world), 0, Y, 0, 1, 0)
        self.assertEqual((res.x, res.y, res.z), (1, Y - 1, 0))
        self.assertAlmostEqual(res.cost, 9.3210, places=4)                                                # Desc:130
        world.set(0, Y - 1, 0, SOUL_SAND)
        self.assertAlmostEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, 13.0273, places=4)            # Desc:126-129
        world.set(0, Y - 1, 0, LADDER)
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)                    # Desc:97-99
        world = flat()
        clear(world, 1, Y - 1, 0, 1, Y - 1, 0)
        world.set(1, Y - 1, 0, LADDER)                         # Desc:117-119: a ladder at the dest feet
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_descend_digs_the_front_column(self):
        world = flat()                                         # the dest feet block is grass: dug, then stepped down into
        res = descend(ctx_for(world), 0, Y, 0, 1, 0)
        self.assertAlmostEqual(res.cost, 9.3210 + 20, places=4)

    def test_falls(self):
        world = flat()
        clear(world, 1, Y - 2, 0, 1, Y - 1, 0)                 # drop 2 onto dirt at 61
        res = descend(ctx_for(world), 0, Y, 0, 1, 0)
        self.assertEqual(res.y, Y - 2)
        self.assertAlmostEqual(res.cost, 13.1750, places=4)
        clear(world, 1, Y - 3, 0, 1, Y - 3, 0)                 # drop 3 onto stone at 60
        res = descend(ctx_for(world), 0, Y, 0, 1, 0)
        self.assertEqual(res.y, Y - 3)
        self.assertAlmostEqual(res.cost, 14.6179, places=4)
        clear(world, 1, Y - 4, 0, 1, Y - 4, 0)                 # drop 4: over maxFallHeightNoWater
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)
        self.assertLess(descend(ctx_for(world, maxFallHeightNoWater=4), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_fall_into_water(self):
        world = flat()
        clear(world, 1, Y - 5, 0, 1, Y - 1, 0)
        world.set(1, Y - 7, 0, STONE)
        world.set(1, Y - 6, 0, WATER)                          # drop 6 into still water: no height cap (quirk 5)
        res = descend(ctx_for(world), 0, Y, 0, 1, 0)
        self.assertEqual(res.y, Y - 6)
        self.assertAlmostEqual(res.cost, C.WALK_OFF_BLOCK_COST + C.FALL_N_BLOCKS_COST[6], places=4)
        world.set(1, Y - 6, 0, FLOWING_WATER)                  # Desc:161-163 / 167-169
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)
        world.set(1, Y - 6, 0, WATER)
        world.set(1, Y - 7, 0, WATER)                          # a two-deep pool: water over water is a floor (MH:455)
        self.assertLess(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)
        clear(world, 1, Y - 7, 0, 1, Y - 7, 0)                 # Desc:170-173: water over air, punch right through
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_fall_onto_bottom_slab_is_refused(self):
        world = flat()
        clear(world, 1, Y - 2, 0, 1, Y - 1, 0)
        world.set(1, Y - 3, 0, BOTTOM_SLAB)                    # Desc:203-205
        self.assertGreaterEqual(descend(ctx_for(world), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_movement_objects(self):
        world = flat()
        clear(world, 1, Y - 2, 0, 1, Y - 1, 0)
        ctx = ctx_for(world)
        move = MOVES_BY_NAME["DESCEND_EAST"].apply0(ctx, (0, Y, 0))
        self.assertIsInstance(move, MovementFall)
        self.assertEqual(move.dest, (1, Y - 2, 0))
        self.assertEqual(move.positions_to_break, ((1, Y + 1, 0), (1, Y, 0), (1, Y - 1, 0), (1, Y - 2, 0)))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (1, Y, 0), (1, Y - 1, 0), (1, Y - 2, 0)})
        self.assertAlmostEqual(move.calculate_cost(ctx), 13.1750, places=4)
        self.assertGreaterEqual(MovementDescend((0, Y, 0), (1, Y - 1, 0)).calculate_cost(ctx), COST_INF)   # Desc:66-73
        world.set(1, Y - 2, 0, DIRT)
        ctx = ctx_for(world)
        move = MOVES_BY_NAME["DESCEND_EAST"].apply0(ctx, (0, Y, 0))
        self.assertIsInstance(move, MovementDescend)
        self.assertEqual(move.valid_positions, {(0, Y, 0), (1, Y, 0), (1, Y - 1, 0)})
        self.assertEqual(move.position_to_place, (1, Y - 2, 0))


class DiagonalTest(unittest.TestCase):

    def test_sprint_edging_and_blocked(self):
        world = flat()
        res = diagonal(ctx_for(world), 0, Y, 0, 1, 1)
        self.assertEqual((res.x, res.y, res.z), (1, Y, 1))
        self.assertAlmostEqual(res.cost, 5.0400, places=4)                                                # Diag:261-264
        self.assertAlmostEqual(diagonal(ctx_for(world, can_sprint=False), 0, Y, 0, 1, 1).cost, 6.5518, places=4)
        world.set(1, Y, 0, STONE)                              # corner B blocked: edging, no mining cost (quirk 6)
        self.assertAlmostEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, 9.2591, places=4)             # Diag:250
        world.set(0, Y, 1, STONE)                              # both corners
        self.assertGreaterEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, COST_INF)                   # Diag:224-228

    def test_danger_beside(self):
        world = flat()
        world.set(1, Y, 0, CACTUS)                             # Diag:236-239
        self.assertGreaterEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, COST_INF)
        world = flat()
        world.set(1, Y - 1, 0, "minecraft:lava")               # Diag:178-181 cutting over lava
        self.assertGreaterEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, COST_INF)

    def test_ascend(self):
        world = flat()
        world.set(1, Y, 1, STONE)
        self.assertGreaterEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, COST_INF)                   # Diag:130, off by default
        res = diagonal(ctx_for(world, allowDiagonalAscend=True), 0, Y, 0, 1, 1)
        self.assertEqual(res.y, Y + 1)
        self.assertAlmostEqual(res.cost, 11.7152, places=4)                                               # Diag:214
        world.set(0, Y, 1, STONE)                              # Diag:208: we could just ascend
        self.assertGreaterEqual(diagonal(ctx_for(world, allowDiagonalAscend=True), 0, Y, 0, 1, 1).cost, COST_INF)

    def test_descend(self):
        world = flat()
        clear(world, 1, Y - 1, 1, 1, Y - 1, 1)
        self.assertGreaterEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, COST_INF)                   # Diag:142, off by default
        res = diagonal(ctx_for(world, allowDiagonalDescend=True), 0, Y, 0, 1, 1)
        self.assertEqual(res.y, Y - 1)
        self.assertAlmostEqual(res.cost, 10.6547, places=4)                                               # Diag:266

    def test_water(self):
        world = flat()
        world.set(0, Y, 0, WATER)
        world.set(1, Y, 1, WATER)
        self.assertAlmostEqual(diagonal(ctx_for(world), 0, Y, 0, 1, 1).cost, 9.0909 * math.sqrt(2), places=4)   # Diag:191

    def test_movement_object(self):
        world = flat()
        world.set(1, Y, 0, STONE)
        move = MovementDiagonal((0, Y, 0), 1, 1, 0)
        self.assertEqual(move.dest, (1, Y, 1))
        self.assertEqual(move.positions_to_break, ((0, Y, 1), (0, Y + 1, 1), (1, Y, 0), (1, Y + 1, 0), (1, Y, 1), (1, Y + 1, 1)))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (1, Y, 1), (0, Y, 1), (1, Y, 0)})
        self.assertEqual(move.to_walk_into(world), [(1, Y, 0)])
        self.assertEqual(move.to_break(world), [])
        self.assertAlmostEqual(move.calculate_cost(ctx_for(world)), 9.2591, places=4)
        up = MovementDiagonal((0, Y, 0), 1, -1, 1)
        self.assertEqual(up.valid_positions, {(0, Y, 0), (0, Y + 1, 0), (0, Y, -1), (1, Y, 0), (1, Y + 1, -1), (0, Y + 1, -1), (1, Y + 1, 0)})
        down = MovementDiagonal((0, Y, 0), -1, -1, -1)
        self.assertEqual(down.valid_positions, {(0, Y, 0), (-1, Y, -1), (0, Y, -1), (-1, Y, 0), (-1, Y - 1, -1), (0, Y - 1, -1), (-1, Y - 1, 0)})
        self.assertEqual(MOVES_BY_NAME["DIAGONAL_SOUTHEAST"].apply0(ctx_for(world), (0, Y, 0)).dest, (1, Y, 1))


class DownwardTest(unittest.TestCase):

    def test_costs(self):
        world = flat()
        world.set(0, Y - 1, 0, DIRT)
        self.assertAlmostEqual(downward_cost(ctx_for(world), 0, Y, 0), 22.6147, places=4)                  # Down:70
        self.assertGreaterEqual(downward_cost(ctx_for(world, allowDownward=False), 0, Y, 0), COST_INF)     # Down:58-60
        world.set(0, Y - 1, 0, LADDER)
        self.assertAlmostEqual(downward_cost(ctx_for(world), 0, Y, 0), 6.6667, places=4)                   # Down:66-67
        world.set(0, Y - 1, 0, DIRT)
        clear(world, 0, Y - 2, 0, 0, Y - 2, 0)                 # Down:61-63: no floor two below
        self.assertGreaterEqual(downward_cost(ctx_for(world), 0, Y, 0), COST_INF)
        world = flat()
        world.set(0, Y - 1, 0, SAND)                           # standing on sand: mined like anything else (Down:69)
        self.assertAlmostEqual(downward_cost(ctx_for(world), 0, Y, 0), 22.6147, places=4)

    def test_movement_object(self):
        move = MovementDownward((0, Y, 0))
        self.assertEqual(move.dest, (0, Y - 1, 0))
        self.assertEqual(move.positions_to_break, ((0, Y - 1, 0),))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (0, Y - 1, 0)})


class PillarTest(unittest.TestCase):

    def test_costs(self):
        world = flat()
        self.assertAlmostEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), 25.1634, places=4)            # Pill:134
        self.assertGreaterEqual(pillar_cost(ctx_for(world), 0, Y, 0), COST_INF)                             # Pill:86-89
        world.set(0, Y - 1, 0, BOTTOM_SLAB)
        self.assertGreaterEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), COST_INF)                     # Pill:67-69
        world = DictWorld()
        world.set(0, Y - 2, 0, STONE)                          # over air: + 0.1 (Pill:90-92)
        self.assertAlmostEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), 25.2634, places=4)
        world = flat()
        world.set(0, Y + 2, 0, DIRT)                           # Pill:104: break the block two above
        self.assertAlmostEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), 25.1634 + 17, places=4)
        world.set(0, Y + 3, 0, SAND)                           # Pill:113-120: it would fall on us
        self.assertGreaterEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), COST_INF)
        world = flat()
        world.set(0, Y + 2, 0, "minecraft:oak_fence_gate")     # Pill:73-75
        self.assertGreaterEqual(pillar_cost(ctx_for(world, [DIRT]), 0, Y, 0), COST_INF)

    def test_water_column_and_ladder(self):
        world = flat()
        world.set(0, Y, 0, WATER)
        world.set(0, Y + 1, 0, WATER)
        world.set(0, Y + 2, 0, WATER)
        self.assertAlmostEqual(pillar_cost(ctx_for(world), 0, Y, 0), 8.5106, places=4)                     # Pill:77-81
        world = flat()
        world.set(0, Y, 0, LADDER)
        self.assertAlmostEqual(pillar_cost(ctx_for(world), 0, Y, 0), 8.5106, places=4)                     # Pill:131-132

    def test_movement_object(self):
        move = MovementPillar((0, Y, 0))
        self.assertEqual(move.dest, (0, Y + 1, 0))
        self.assertEqual(move.positions_to_break, ((0, Y + 2, 0),))
        self.assertEqual(move.position_to_place, (0, Y, 0))
        self.assertEqual(move.valid_positions, {(0, Y, 0), (0, Y + 1, 0)})


class ParkourTest(unittest.TestCase):

    def gap(self, width, world=None):
        world = world or flat()
        clear(world, 1, Y - 1, 0, width, Y - 1, 0)
        return world

    def test_off_when_not_allowed(self):
        self.assertGreaterEqual(parkour(ctx_for(self.gap(1), allowParkour=False), 0, Y, 0, 1, 0).cost, COST_INF)   # Park:64-66
        self.assertLess(parkour(ctx_for(self.gap(1)), 0, Y, 0, 1, 0).cost, COST_INF, "on by default since phase 3")
        self.assertGreaterEqual(parkour(ctx_for(self.gap(3), maxParkourJump=3), 0, Y, 0, 1, 0).cost, COST_INF)    # departs: the cap

    def test_flat_jumps(self):
        res = parkour(ctx_for(self.gap(1), allowParkour=True), 0, Y, 0, 1, 0)
        self.assertEqual((res.x, res.y, res.z), (2, Y, 0))
        self.assertAlmostEqual(res.cost, 11.2657, places=4)                                               # Park:153
        res = parkour(ctx_for(self.gap(2), allowParkour=True), 0, Y, 0, 1, 0)
        self.assertEqual(res.x, 3)
        self.assertAlmostEqual(res.cost, 15.8985, places=4)
        res = parkour(ctx_for(self.gap(3), allowParkour=True), 0, Y, 0, 1, 0)
        self.assertEqual(res.x, 4)
        self.assertAlmostEqual(res.cost, 16.2552, places=4)
        self.assertGreaterEqual(parkour(ctx_for(self.gap(3), allowParkour=True, can_sprint=False), 0, Y, 0, 1, 0).cost, COST_INF)   # Park:109-112
        world = self.gap(1)
        world.set(0, Y - 1, 0, SOUL_SAND)                      # Park:107-108: a 1 block gap only
        self.assertAlmostEqual(parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0).cost, 11.2657, places=4)
        self.assertGreaterEqual(parkour(ctx_for(self.gap(2, world), allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_ascend(self):
        world = self.gap(1)
        world.set(2, Y, 0, STONE)
        res = parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0)
        self.assertEqual((res.x, res.y, res.z), (2, Y + 1, 0))
        self.assertAlmostEqual(res.cost, 9.1276, places=4)                                                # Park:136
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True, allowParkourAscend=False), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_place(self):
        world = self.gap(4)
        self.assertGreaterEqual(parkour(ctx_for(world, [DIRT], allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)   # Park:167-169
        res = parkour(ctx_for(world, [DIRT], allowParkour=True, allowParkourPlace=True), 0, Y, 0, 1, 0)
        self.assertEqual(res.x, 4)                             # Park:171: the longest jump first
        self.assertAlmostEqual(res.cost, 36.2552, places=4)
        world.set(3, Y + 1, 0, STONE)                          # the scan stops at 3, so the place is at 2
        res = parkour(ctx_for(world, [DIRT], allowParkour=True, allowParkourPlace=True), 0, Y, 0, 1, 0)
        self.assertEqual(res.x, 2)
        self.assertAlmostEqual(res.cost, 31.2657, places=4)
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True, allowParkourPlace=True), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_preconditions(self):
        world = flat()
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)   # Park:77-79: could just traverse
        world = self.gap(1)
        world.set(0, Y - 1, 0, BOTTOM_SLAB)                    # Park:94-96
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)
        world = self.gap(1)
        world.set(0, Y + 2, 0, STONE)                          # Park:90-92: head room at the takeoff
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)
        world = self.gap(1)
        world.set(3, Y, 0, CACTUS)                             # Park:149 overshoot safety
        self.assertGreaterEqual(parkour(ctx_for(world, allowParkour=True), 0, Y, 0, 1, 0).cost, COST_INF)

    def test_movement_object(self):
        world = self.gap(2)
        ctx = ctx_for(world, allowParkour=True)
        move = MOVES_BY_NAME["PARKOUR_EAST"].apply0(ctx, (0, Y, 0))
        self.assertIsInstance(move, MovementParkour)
        self.assertEqual(move.dest, (3, Y, 0))
        self.assertEqual(move.dist, 3)
        self.assertEqual(move.position_to_place, (3, Y - 1, 0))
        self.assertEqual(move.valid_positions, {(x, y, 0) for x in range(4) for y in (Y, Y + 1)})
        self.assertAlmostEqual(move.calculate_cost(ctx), 15.8985, places=4)
        self.assertGreaterEqual(MovementParkour((0, Y, 0), 2, (1, 0), False).calculate_cost(ctx), COST_INF)   # Park:223-230


class MovesTableTest(unittest.TestCase):

    def test_order_and_offsets(self):
        names = [m.name for m in MOVES]
        self.assertEqual(names[:2], ["DOWNWARD", "PILLAR"])
        self.assertEqual(names[2:6], ["TRAVERSE_NORTH", "TRAVERSE_SOUTH", "TRAVERSE_EAST", "TRAVERSE_WEST"])
        self.assertEqual(names[10:14], ["DESCEND_EAST", "DESCEND_WEST", "DESCEND_NORTH", "DESCEND_SOUTH"])
        self.assertEqual(len(MOVES), 22)
        self.assertEqual((MOVES_BY_NAME["TRAVERSE_NORTH"].x_offset, MOVES_BY_NAME["TRAVERSE_NORTH"].z_offset), (0, -1))
        self.assertEqual((MOVES_BY_NAME["PARKOUR_EAST"].x_offset, MOVES_BY_NAME["PARKOUR_EAST"].dynamic_xz), (4, True))
        self.assertTrue(MOVES_BY_NAME["DESCEND_EAST"].dynamic_y)
        self.assertFalse(MOVES_BY_NAME["ASCEND_EAST"].dynamic_y)

    def test_unknown_destination_is_inf_and_unloaded(self):
        world = flat(box=((-64, -64, -64), (5, 320, 64)))      # everything east of x = 5 is unread
        ctx = ctx_for(world)
        res = MoveResult()
        for name in ("TRAVERSE_EAST", "ASCEND_EAST", "DESCEND_EAST", "DIAGONAL_SOUTHEAST"):
            MOVES_BY_NAME[name].apply(ctx, 5, Y, 0, res)
            self.assertGreaterEqual(res.cost, COST_INF, name)
            self.assertTrue(res.unloaded, name)
        MOVES_BY_NAME["TRAVERSE_WEST"].apply(ctx, 5, Y, 0, res)
        self.assertLess(res.cost, COST_INF)
        self.assertFalse(res.unloaded)
        self.assertFalse(world.loaded(6, 0))
        self.assertTrue(world.loaded(5, 0))

    def test_apply_matches_static_costs(self):
        world = flat()
        ctx = ctx_for(world, [DIRT])
        res = MoveResult()
        for move in MOVES:
            move.apply(ctx, 0, Y, 0, res)
            if res.cost < COST_INF:
                self.assertEqual((res.x - 0, res.y - Y, res.z - 0)[1], move.y_offset if not move.dynamic_y else res.y - Y)
                built = move.apply0(ctx, (0, Y, 0))
                self.assertEqual(built.dest, (res.x, res.y, res.z), move.name)
                self.assertAlmostEqual(built.calculate_cost(ctx), res.cost, places=6, msg=move.name)


class HelpersAndContextTest(unittest.TestCase):

    def test_pack_unpack(self):
        for p in ((0, 0, 0), (-5, 63, 7), (123456, -64, -98765), (-1, 319, -1), (30_000_000, 2047, -30_000_000)):
            self.assertEqual(unpack(pack(*p)), p)

    def test_predicates(self):
        world = flat()
        world.set(0, Y, 0, WATER)
        world.set(0, Y, 1, WATER)
        world.set(0, Y + 1, 1, WATER)
        world.set(2, Y, 0, FLOWING_WATER)
        world.set(3, Y, 0, WATER)
        ctx = ctx_for(world)
        self.assertTrue(helpers.can_walk_through(ctx, 0, Y, 0))          # still water, air above
        self.assertFalse(helpers.can_walk_through(ctx, 0, Y, 1))         # water above it
        self.assertFalse(helpers.can_walk_through(ctx, 2, Y, 0))         # flowing
        self.assertFalse(helpers.can_walk_through(ctx, 3, Y, 0))         # a flowing neighbour (MH:800-805)
        self.assertTrue(helpers.is_flowing(ctx, 3, Y, 0))
        self.assertFalse(helpers.can_walk_on(ctx, 0, Y, 0))              # water with air above
        self.assertTrue(helpers.can_walk_on(ctx, 0, Y, 1))               # water with water above (MH:455)
        self.assertTrue(helpers.can_walk_on(ctx_for(world, assumeWalkOnWater=True), 0, Y, 0))
        self.assertFalse(helpers.can_walk_on(ctx, 0, Y + 2, 0))          # air
        self.assertTrue(helpers.can_walk_on(ctx, 0, Y - 1, 0))           # grass
        self.assertTrue(helpers.fully_passable(ctx, 0, Y + 2, 0))
        self.assertFalse(helpers.fully_passable(ctx, 0, Y, 0))
        self.assertTrue(helpers.is_replaceable(ctx, 0, Y + 2, 0))
        self.assertTrue(helpers.is_replaceable(ctx, 0, Y, 0))
        self.assertFalse(helpers.is_replaceable(ctx, 0, Y - 1, 0))
        self.assertTrue(helpers.must_be_solid_to_walk_on(ctx, 0, Y - 1, 0))
        self.assertFalse(helpers.must_be_solid_to_walk_on(ctx, 0, Y, 1))  # water under water
        self.assertTrue(helpers.avoid_walking_into(ctx.at(0, Y, 0)))
        self.assertFalse(helpers.avoid_walking_into(ctx.at(0, Y - 1, 0)))
        self.assertTrue(ctx.at(100, Y, 100) & UNKNOWN)
        self.assertFalse(helpers.can_walk_through(ctx, 100, Y, 100))
        self.assertFalse(helpers.can_walk_on(ctx, 100, Y, 100))
        self.assertFalse(helpers.can_place_against(ctx, 100, Y, 100))
        self.assertTrue(ctx.touched_unknown)

    def test_toolset(self):
        settings = Settings()
        dirt, stone, leaves = classify(DIRT), classify(STONE), classify("minecraft:oak_leaves")
        self.assertAlmostEqual(1 / ToolSet([], settings).str_vs_block(dirt), 15)
        self.assertAlmostEqual(1 / ToolSet([], settings).str_vs_block(stone), 150)
        self.assertAlmostEqual(1 / ToolSet([None, {"typeId": "minecraft:wooden_shovel", "amount": 1}], settings).str_vs_block(dirt), 7.5)
        self.assertAlmostEqual(1 / ToolSet(["minecraft:wooden_shovel"], settings).str_vs_block(stone), 150)   # wrong class
        self.assertAlmostEqual(1 / ToolSet(["minecraft:golden_pickaxe"], settings).str_vs_block(stone), 3.75)
        self.assertAlmostEqual(1 / ToolSet(["minecraft:shears"], settings).str_vs_block(leaves), 0.4)
        self.assertEqual(ToolSet(["minecraft:stone_axe", "minecraft:iron_pickaxe"], settings).best_slot(stone), 1)
        self.assertIsNone(ToolSet(["minecraft:stone_axe"], settings).best_slot(stone))
        self.assertLess(ToolSet([], settings).str_vs_block(classify("minecraft:bedrock")), 0)
        chest = classify("minecraft:chest")
        self.assertAlmostEqual(ToolSet([], settings).str_vs_block(chest) / (1 / (2.5 * 30)), 0.1, places=6)   # blocksToAvoidBreaking x10

    def test_context_snapshot(self):
        world = flat()
        ctx = CalculationContext(world, hotbar=["minecraft:cobblestone"], can_sprint=False, dimension="minecraft:nether")
        self.assertTrue(ctx.has_throwaway)
        self.assertFalse(ctx.can_sprint)
        self.assertEqual((ctx.min_y, ctx.max_y), (0, 127))
        self.assertEqual((CalculationContext(world).min_y, CalculationContext(world).max_y), (-64, 319))
        self.assertEqual(CalculationContext(world, dimension="the_end").max_y, 255)
        self.assertFalse(CalculationContext(world, hotbar=["minecraft:stick"]).has_throwaway)
        self.assertFalse(CalculationContext(world, settings=Settings(allowPlace=False), hotbar=[DIRT]).has_throwaway)
        self.assertAlmostEqual(ctx.water_walk_speed, 9.0909, places=4)
        self.assertAlmostEqual(CalculationContext(world, settings=Settings(allowSwimming=True)).water_walk_speed, 6.9930, places=4)
        self.assertEqual(ctx.min_fall_height, 3)
        self.assertEqual(ctx.place_block_cost, 20)
        self.assertEqual(ctx.cost_of_placing_at(0, Y, 0), 20)
        self.assertGreaterEqual(CalculationContext(world).cost_of_placing_at(0, Y, 0), COST_INF)

    def test_mining_memo_and_block_changed(self):
        world = flat()
        ctx = ctx_for(world)
        self.assertAlmostEqual(ctx.get_mining_duration_ticks(0, Y - 1, 0, False), 20, places=4)   # grass, 0.6 hardness
        world.set(0, Y - 1, 0, DIRT)
        self.assertAlmostEqual(ctx.get_mining_duration_ticks(0, Y - 1, 0, False), 20, places=4)   # memoized
        ctx.block_changed()
        self.assertAlmostEqual(ctx.get_mining_duration_ticks(0, Y - 1, 0, False), 17, places=4)
        self.assertEqual(ctx.get_mining_duration_ticks(0, Y, 0, False), 0.0)
        self.assertGreaterEqual(ctx.get_mining_duration_ticks(0, Y - 1, 0, False, ctx.at(0, Y - 1, 0)), 0)


if __name__ == "__main__":
    unittest.main()
