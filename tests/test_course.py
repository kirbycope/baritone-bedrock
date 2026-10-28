"""The obstacle course's geometry without a probe: the layout keeps every piece where it is whatever subset is
built, the phase 3 pieces (8 parkour, 9 forest) build parseable commands, the parkour legs have the gaps the plan
asks for over a 2 block drop, and the forest has no clear line through it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.calibrate import Rig                                              # noqa: E402
from baritone_bedrock.course import layout, Course, PIECES, Parkour, Forest, Stairs, FLOOR, START, END   # noqa: E402

OX, OZ, Y = -24, -24, 200


def blocks_of(piece):
    """What the piece's commands put where, the way the game applies them in order: {(x, y, z): block}."""
    world = {}
    for command in piece.build(Rig.tiled) + piece.markers():
        words = command.split()
        if words[0] == "fill":
            x0, y0, z0, x1, y1, z1 = (int(w) for w in words[1:7])
            block = words[7]
            for x in range(min(x0, x1), max(x0, x1) + 1):
                for y in range(min(y0, y1), max(y0, y1) + 1):
                    for z in range(min(z0, z1), max(z0, z1) + 1):
                        if block == "air":
                            world.pop((x, y, z), None)
                        else:
                            world[(x, y, z)] = block
        else:
            x, y, z = (int(w) for w in words[1:4])
            world[(x, y, z)] = words[4]
    return world


class LayoutTest(unittest.TestCase):

    def test_pieces_keep_their_places_whatever_subset_is_built(self):
        everything = {p.number: (p.x0, p.z) for p in layout(OX, OZ, Y, list(PIECES))}
        self.assertEqual(sorted(everything), list(range(1, 10)))
        some = {p.number: (p.x0, p.z) for p in layout(OX, OZ, Y, [1, 8, 9])}
        for n, place in some.items():
            self.assertEqual(place, everything[n])
        zs = [everything[n][1] for n in sorted(everything)]
        self.assertEqual(zs, sorted(zs), "the lanes run along +z in piece order")

    def test_every_command_names_a_chunk_and_legs_carry_markers(self):
        for piece in layout(OX, OZ, Y, list(PIECES)):
            commands = piece.build(Rig.tiled) + piece.markers()
            for command in commands:
                Course.chunk_of(command)
            world = blocks_of(piece)
            for _label, (sx, sy, sz), (ex, ey, ez) in piece.legs():
                self.assertEqual(world.get((sx, sy - 1, sz)), START, f"piece {piece.number}: no start marker under {sx},{sy},{sz}")
                self.assertEqual(world.get((ex, ey - 1, ez)), END, f"piece {piece.number}: no end marker under {ex},{ey},{ez}")
                self.assertNotIn((sx, sy, sz), world, "the start is clear to stand in")
                self.assertNotIn((ex, ey, ez), world, "the end is clear to stand in")
            checks = piece.checks()
            for x, y, z, block in checks:
                self.assertEqual(world.get((x, y, z)), block, f"piece {piece.number}: check {x},{y},{z} wants {block}")

    def test_stairs_have_a_descending_leg(self):
        piece = next(p for p in layout(OX, OZ, Y, [2]))
        self.assertIsInstance(piece, Stairs)
        labels = [label for label, _s, _e in piece.legs()]
        self.assertEqual(len(labels), 2)
        self.assertIn("down", labels[1])
        _label, top, end = piece.legs()[1]
        self.assertEqual(top[1], Y + 11, "the top of the stairs, feet ten up")
        self.assertEqual(end, piece.end)


class ParkourPieceTest(unittest.TestCase):

    def setUp(self):
        self.piece = next(p for p in layout(OX, OZ, Y, [8]))
        self.world = blocks_of(self.piece)

    def test_four_legs_with_the_gaps_of_the_plan(self):
        piece, world = self.piece, self.world
        self.assertIsInstance(piece, Parkour)
        legs = piece.legs()
        self.assertEqual(len(legs), 4)
        for k, (dist, ascend) in enumerate(Parkour.GAPS):
            label, start, end = legs[k]
            self.assertIn(f"dist {dist}", label)
            z = piece.z
            gx0 = piece.leg_x(k) + 8
            run_up = [x for x in range(start[0], gx0) if world.get((x, Y, z)) in (FLOOR, START)]
            self.assertEqual(len(run_up), gx0 - start[0], "a solid run-up to the edge (the start marker is gold)")
            air = [x for x in range(gx0, gx0 + 6) if (x, Y, z) not in world]
            self.assertEqual(air, list(range(gx0, gx0 + dist - 1)), f"leg {k}: {dist - 1} columns of air")
            for x in range(gx0, gx0 + dist - 1):
                self.assertEqual(world.get((x, Y - 2, z)), FLOOR, "the pit floor two below the pad")
                self.assertNotIn((x, Y - 1, z), world)
                self.assertEqual(world.get((x, Y + 1, z - piece.half_width - 1)), FLOOR, "walled beside the gap")
            landing = gx0 + dist - 1
            if ascend:
                self.assertEqual(world.get((landing, Y + 1, z)), FLOOR, "the landing one up")
                self.assertEqual(end[1], Y + 2)
            else:
                self.assertEqual(world.get((landing, Y, z)), FLOOR)
                self.assertNotIn((landing, Y + 1, z), world)
                self.assertEqual(end[1], Y + 1)

    def test_pit_is_a_closed_tub(self):
        """A miss drops two blocks into quartz on every side, never into the sky under the course."""
        piece, world = self.piece, self.world
        for k, (dist, _ascend) in enumerate(Parkour.GAPS):
            gx0 = piece.leg_x(k) + 8
            for x in range(gx0, gx0 + dist - 1):
                for z in range(piece.z - piece.half_width, piece.z + piece.half_width + 1):
                    self.assertEqual(world.get((x, Y - 3, z)), FLOOR)
                    self.assertEqual(world.get((x, Y - 2, z)), FLOOR)
                self.assertEqual(world.get((x, Y - 1, piece.z - piece.half_width - 1)), FLOOR)
                self.assertEqual(world.get((x, Y - 1, piece.z + piece.half_width + 1)), FLOOR)
            self.assertEqual(world.get((gx0 - 1, Y - 1, piece.z)), FLOOR, "the near wall of the pit")
            self.assertEqual(world.get((gx0 + dist - 1, Y - 1, piece.z)), FLOOR, "the far wall of the pit")


class ForestPieceTest(unittest.TestCase):

    def test_no_clear_line_and_real_dark_oak(self):
        piece = next(p for p in layout(OX, OZ, Y, [9]))
        self.assertIsInstance(piece, Forest)
        world = blocks_of(piece)
        fx0, fx1 = piece.strip
        self.assertEqual(fx1 - fx0 + 1, 40)
        logs = [p for p, b in world.items() if b == "dark_oak_log"]
        leaves = [p for p, b in world.items() if b == "dark_oak_leaves"]
        self.assertGreater(len(logs), 100)
        self.assertGreater(len(leaves), 2000)
        self.assertTrue(all(fx0 <= x <= fx1 for x, _y, _z in logs + leaves))
        # the canopy covers the strip
        for x in range(fx0, fx1 + 1):
            for z in range(piece.z - piece.half_width, piece.z + piece.half_width + 1):
                self.assertEqual(world.get((x, Y + 7, z)), "dark_oak_leaves")
        # no row along x is clear at feet and head height, and the walls close the sides
        for z in range(piece.z - piece.half_width, piece.z + piece.half_width + 1):
            blocked = [x for x in range(fx0, fx1 + 1) if (x, Y + 1, z) in world or (x, Y + 2, z) in world]
            self.assertTrue(blocked, f"row z {z} is a clear line")
        for x in range(fx0 - 1, fx1 + 2):
            self.assertEqual(world.get((x, Y + 1, piece.z - piece.half_width - 1)), FLOOR)
            self.assertEqual(world.get((x, Y + 1, piece.z + piece.half_width + 1)), FLOOR)
        # the start and the end are outside the strip, on the pad
        self.assertLess(piece.start[0], fx0)
        self.assertGreater(piece.end[0], fx1)


if __name__ == "__main__":
    unittest.main()
