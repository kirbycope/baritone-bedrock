"""calibrate.py against a fake probe: a player simulated with Java's numbers under a NullController, a tick per
wait_tick, so the whole calibration runs in a second and every section is checked against the numbers it was given."""
import json
import math
import os
import shlex
import tempfile
import time
import unittest
from collections import deque

from baritone_bedrock import calibrate
from baritone_bedrock.calibrate import Calibrator, Rig, make_controller, yaw_to
from baritone_bedrock.controller import NullController

AIR_FILL_LIMIT = 32768


class FakeProbe:
    """A player under the NullController's sticks, simulated a tick per wait_tick: Java's walk, sprint, sneak, wade and
    swim speeds (costs.DEFAULT_SPEEDS), its jump (0.42 up, 0.08 gravity, 0.98 drag: apex 1.25 in 6 ticks), a right
    stick of 180 degrees a second at full deflection with a 4 tick ramp, LATENCY ticks between a controller change and
    the report, the rig's platform, step, walls, pool and placed blocks from the commands it is sent, breaking by hand
    (dirt 15 ticks, stone 150) and placing under the feet while above the block."""

    LATENCY = 2
    WALK, SPRINT, SNEAK, WADE, SWIM = 4.317 / 20, 5.612 / 20, 1.3 / 20, 2.2 / 20, 2.86 / 20
    TURN, RAMP = 180.0 / 20, 4
    BREAK = {"dirt": 15, "stone": 150}

    def __init__(self, controller, name="Kirby", at=(100.5, 65.0, -40.5)):
        self.pad, self.name = controller, name
        self.x, self.y, self.z, self.yaw, self.pitch = at[0], at[1], at[2], 0.0, 0.0
        self.vy, self.ground, self.water, self.sprinting = 0.0, True, False, False
        self.ramp, self.ticks, self.game_tick = 0, 0, 5000
        self.blocks, self.platform, self.pool = {}, None, None
        self.commands, self._events, self.slots, self.slot = [], [], [None] * 9, 0
        self.breaking, self.jump_latch, self.lt_latch, self.seen_presses = None, False, False, 0
        self.history, self.report, self.mode = deque(), None, "survival"
        self._tick()

    # the Probe surface the calibrator uses
    def wait_tick(self, after, timeout=0.2):
        self._tick()
        return self.ticks

    def player(self, who, fresh=1.0):
        return self.report if who == self.name else None

    def hand(self, who, fresh=2.0):
        return self.slots[self.slot]

    def hotbar(self, who, fresh=2.0):
        return list(self.slots)

    def events(self):
        out, self._events = self._events, []
        return out

    def tick_rate(self):
        return 20.0

    def connected(self, timeout=60):
        return True

    def command(self, text, timeout=10.0, dim="overworld"):
        self.commands.append(text)
        ok = self._run(text)
        return {"cmd": text, "ok": ok, "successCount": int(ok), "error": None}

    # ------------------------------------------------------------------------------------------------ the world

    def _run(self, text):
        words = shlex.split(text)
        verb = words[0]
        if verb == "fill":
            x0, y0, z0, x1, y1, z1 = (int(v) for v in words[1:7])
            x0, x1, y0, y1, z0, z1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), min(z0, z1), max(z0, z1)
            block = words[7]
            volume = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
            if volume > AIR_FILL_LIMIT:
                return False
            inside = lambda k: x0 <= k[0] <= x1 and y0 <= k[1] <= y1 and z0 <= k[2] <= z1
            if block == "air":
                self.blocks = {k: v for k, v in self.blocks.items() if not inside(k)}
                if self.platform and y0 <= self.platform[4] <= y1:
                    self.platform = self.pool = None
                return True
            if block == "water":
                for k in [k for k in self.blocks if inside(k)]:
                    del self.blocks[k]
                self.pool = (x0, x1, z0, z1, y1)
                return True
            if y0 == y1 and volume > 1000:
                self.platform = (x0, x1, z0, z1, y0)
                return True
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    for z in range(z0, z1 + 1):
                        self.blocks[(x, y, z)] = block
            return True
        if verb == "setblock":
            self.blocks[tuple(int(v) for v in words[1:4])] = words[4]
            return True
        if verb == "tp":
            self.x, self.y, self.z = (float(v) for v in words[2:5])
            if len(words) >= 7:
                self.yaw, self.pitch = float(words[5]), float(words[6])
            self.vy, self.ground = 0.0, False
            return True
        if verb == "give":
            self.slots[3] = {"type": "minecraft:" + words[2], "amount": int(words[3])}
            return True
        if verb == "testfor":
            return f"m={self.mode}" in text or "m=" not in text
        if verb == "gamemode":
            self.mode = words[1]
            return True
        if verb == "clear":
            self.slots = [None if s and s["type"].endswith(words[2]) else s for s in self.slots]
            return True
        return True

    def solid(self, bx, by, bz):
        if self.platform:
            x0, x1, z0, z1, py = self.platform
            if by == py and x0 <= bx <= x1 and z0 <= bz <= z1 and not self.in_pool(bx, bz):
                return True
        block = self.blocks.get((bx, by, bz))
        if block is not None:
            return block not in ("air", "water")
        return by <= 64   # the flat world the player started on

    def in_pool(self, bx, bz):
        return bool(self.pool) and self.pool[0] <= bx <= self.pool[1] and self.pool[2] <= bz <= self.pool[3]

    def floor_below(self, x, z, y):
        """The height of the highest solid top at or under the feet."""
        bx, bz = math.floor(x), math.floor(z)
        for by in range(math.floor(y + 1e-9), math.floor(y) - 12, -1):
            if self.solid(bx, by, bz) and by + 1 <= y + 1e-6:
                return by + 1
        return -64

    def crosshair(self):
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        dx, dy, dz = -math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)
        ex, ey, ez = self.x, self.y + calibrate.EYE, self.z
        t = 0.05
        while t <= 6.0:
            bx, by, bz = math.floor(ex + dx * t), math.floor(ey + dy * t), math.floor(ez + dz * t)
            if self.solid(bx, by, bz):
                block = self.blocks.get((bx, by, bz), "quartz_block")
                return {"type": "minecraft:" + block, "x": bx, "y": by, "z": bz, "face": "Up" if dy < 0 else "West"}
            t += 0.05
        return None

    # ------------------------------------------------------------------------------------------------- a tick

    def _tick(self):
        self.ticks += 1
        self.game_tick += 1
        # the hotbar: each RB press steps the slot
        presses = self.pad.pressed[self.seen_presses:]
        self.seen_presses = len(self.pad.pressed)
        self.slot = (self.slot + sum(1 for p in presses if p.split(":")[0] == "rb")) % 9
        # the controller as it was LATENCY ticks ago
        self.history.append((self.pad.left, self.pad.right, self.pad.held))
        left, right, held = self.history[0] if len(self.history) <= self.LATENCY else self.history[-1 - self.LATENCY]
        while len(self.history) > self.LATENCY + 1:
            self.history.popleft()
        # the camera: a ramp to the full rate
        rx, ry = right
        self.ramp = min(self.RAMP, self.ramp + 1) if (abs(rx) > 0.01 or abs(ry) > 0.01) else 0
        factor = self.ramp / self.RAMP
        self.yaw = ((self.yaw + rx * self.TURN * factor + 180) % 360) - 180
        self.pitch = max(-90.0, min(90.0, self.pitch - ry * self.TURN * factor))
        # water
        self.water = bool(self.pool) and self.in_pool(math.floor(self.x), math.floor(self.z)) and self.y < self.pool[4] + 1
        # the jump, then gravity
        x0, z0 = self.x, self.z
        if "a" in held and not self.jump_latch and self.ground and not self.water:
            self.vy, self.ground, self.jump_latch = 0.42, False, True
        if "a" not in held:
            self.jump_latch = False
        if self.water:
            self.vy, self.ground = 0.0, False
            self.y = self.pool[4] + 0.8
        elif not self.ground:   # Java's order: the velocity moves the player, then gravity and drag change it
            ny = self.y + self.vy
            floor = self.floor_below(self.x, self.z, self.y)
            if self.vy <= 0 and ny <= floor:
                self.y, self.vy, self.ground = floor, 0.0, True
            else:
                self.y = ny
                self.vy = (self.vy - 0.08) * 0.98
        else:
            if self.floor_below(self.x, self.z, self.y) < self.y - 1e-6:
                self.ground = False
        # the walk
        forward = left[1] > 0.5
        if "ls" in held and forward:
            self.sprinting = True
        if not forward:
            self.sprinting = False
        if forward:
            if self.water:
                speed = self.SWIM if self.sprinting else self.WADE
            else:
                speed = self.SNEAK if "b" in held else self.SPRINT if self.sprinting else self.WALK
            yaw = math.radians(self.yaw)
            nx, nz = self.x - math.sin(yaw) * speed, self.z + math.cos(yaw) * speed
            bx, by, bz = math.floor(nx), math.floor(self.y), math.floor(nz)
            if not (self.solid(bx, by, bz) or self.solid(bx, by + 1, bz)):
                self.x, self.z = nx, nz
        # breaking and placing
        block = self.crosshair()
        if "rt" in held and block and block["type"].split(":")[1] in self.BREAK and (block["x"], block["y"], block["z"]) in self.blocks:
            pos = (block["x"], block["y"], block["z"])
            self.breaking = (pos, self.breaking[1] + 1) if self.breaking and self.breaking[0] == pos else (pos, 1)
            if self.breaking[1] >= self.BREAK[block["type"].split(":")[1]]:
                del self.blocks[pos]
                self._events.append({"type": "break", "tick": self.game_tick, "dim": "overworld", "x": pos[0], "y": pos[1], "z": pos[2],
                                     "block": block["type"], "player": "1", "damage": None, "cause": None})
                self.breaking = None
        else:
            self.breaking = None
        hand = self.slots[self.slot]
        if "lt" in held and not self.lt_latch:
            self.lt_latch = True
            if hand and hand["type"] == "minecraft:dirt" and block and block["face"] == "Up":
                pos = (block["x"], block["y"] + 1, block["z"])
                if self.y >= pos[1] + 1 - 1e-6 and pos not in self.blocks:
                    self.blocks[pos] = "dirt"
                    hand["amount"] -= 1
                    self._events.append({"type": "place", "tick": self.game_tick, "dim": "overworld", "x": pos[0], "y": pos[1], "z": pos[2],
                                         "block": "minecraft:dirt", "player": "1", "damage": None, "cause": None})
        if "lt" not in held:
            self.lt_latch = False
        self.report = {"id": "1", "name": self.name, "dim": "overworld", "x": self.x, "y": self.y, "z": self.z, "yaw": self.yaw, "pitch": self.pitch,
                       "vx": self.x - x0, "vy": self.vy, "vz": self.z - z0, "ground": self.ground, "water": self.water, "sneak": "b" in held,
                       "sprint": self.sprinting, "jump": "a" in held, "climbing": False, "swimming": self.water and self.sprinting,
                       "falling": self.vy < 0, "fall": 0.0, "slot": self.slot, "hand": hand, "hotbar": list(self.slots),
                       "input": {"move": {"x": left[0], "y": left[1]}, "jump": "a" in held, "sneak": "b" in held},
                       "health": {"current": 20, "max": 20}, "food": {"current": 20, "max": 20}, "look": None, "block": block,
                       "at": time.time(), "n": self.ticks, "tick": self.game_tick}


class RigTest(unittest.TestCase):
    def test_build_fits_fill_limits_and_clears_itself(self):
        rig = Rig(100, -40)
        for command in rig.build() + rig.clear():
            words = command.split()
            self.assertEqual(words[0], "fill")
            x0, y0, z0, x1, y1, z1 = (int(v) for v in words[1:7])
            self.assertLessEqual((abs(x1 - x0) + 1) * (abs(y1 - y0) + 1) * (abs(z1 - z0) + 1), AIR_FILL_LIMIT, command)
        self.assertEqual(rig.feet, 201)
        self.assertEqual(rig.step_edge, 100)
        x0, y0, z0, x1, y1, z1 = rig.box()
        self.assertEqual((x1 - x0 + 1, z1 - z0 + 1), (48, 48))
        for spot in (rig.flat_start, rig.step_start, rig.pool_start, rig.pillar_spot, *rig.break_spots.values()):
            self.assertTrue(x0 <= spot[0] <= x1 and z0 <= spot[2] <= z1, spot)
        self.assertTrue(all(c.endswith(" air") for c in rig.clear()))

    def test_yaw_convention(self):
        self.assertAlmostEqual(yaw_to(1, 0), -90.0)
        self.assertAlmostEqual(yaw_to(0, 1), 0.0)
        self.assertAlmostEqual(yaw_to(-1, 0), 90.0)
        self.assertAlmostEqual(calibrate.wrap(190), -170)


class CalibrateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pad = NullController()
        cls.probe = FakeProbe(cls.pad)
        cls.log = []
        cls.result = Calibrator(cls.pad, cls.probe, "Kirby", log=cls.log.append, trials=3, rounds=4).run_all()

    def test_every_section_is_there(self):
        for name in Calibrator.SECTIONS:
            self.assertIn(name, self.result)
            self.assertIsNotNone(self.result[name], f"{name}: {self.result['notes']}")
        for key in ("client", "date", "notes", "rig"):
            self.assertIn(key, self.result)
        self.assertEqual(self.result["notes"], [], "nothing failed")

    def test_speeds_recover_the_numbers_given(self):
        speeds = self.result["speeds"]
        self.assertAlmostEqual(speeds["walk"], 4.317, delta=0.05)
        self.assertAlmostEqual(speeds["sprint"], 5.612, delta=0.05)
        self.assertAlmostEqual(speeds["sneak"], 1.3, delta=0.05)
        self.assertAlmostEqual(speeds["walk_in_water"], 2.2, delta=0.05)
        self.assertAlmostEqual(speeds["swim"], 2.86, delta=0.05)

    def test_jump(self):
        jump = self.result["jump"]
        self.assertAlmostEqual(jump["apex"], 1.252, delta=0.01)
        self.assertIn(jump["air_ticks"], (11, 12, 13))
        self.assertAlmostEqual(jump["max_vy"], (0.42 - 0.08) * 0.98, delta=0.01)   # reported after the tick's gravity, as the game does
        self.assertGreater(jump["sprint_jump_length"], jump["walk_jump_length"])
        self.assertAlmostEqual(jump["walk_jump_length"], 12 * FakeProbe.WALK, delta=0.5)

    def test_step_up_bisects_within_the_range(self):
        step = self.result["step_up"]
        self.assertTrue(0.4 <= step["walk"] <= step["sprint"] <= 1.6)
        self.assertEqual(len(step["trials"]["walk"]), 4)
        for mid, wins, trials in step["trials"]["sprint"]:
            self.assertEqual(trials, 3)
            self.assertTrue(0.4 < mid < 1.6)

    def test_turn_rates_and_ramp(self):
        turn = self.result["turn"]
        self.assertAlmostEqual(turn["1.0"]["deg_per_s"], 180.0, delta=2.0)
        self.assertAlmostEqual(turn["0.5"]["deg_per_s"], 90.0, delta=2.0)
        self.assertAlmostEqual(turn["0.25"]["deg_per_s"], 45.0, delta=2.0)
        self.assertIn(turn["1.0"]["ramp_ticks"], (3, 4))
        self.assertGreater(turn["1.0"]["total_deg"], 140)   # 18 of the 20 ticks turn, the ramp takes 3

    def test_latency_in_ticks(self):
        latency = self.result["latency"]
        self.assertIn(latency["stick_ticks"], (FakeProbe.LATENCY, FakeProbe.LATENCY + 1))
        self.assertIn(latency["jump_ticks"], (FakeProbe.LATENCY, FakeProbe.LATENCY + 1))
        self.assertEqual(len(latency["samples"]["stick"]), 10)
        self.assertEqual(len(latency["samples"]["jump"]), 10)

    def test_break_ticks_with_latency_taken_off(self):
        broke = self.result["break"]
        self.assertAlmostEqual(broke["dirt"]["ticks"], 15, delta=1)
        self.assertAlmostEqual(broke["stone"]["ticks"], 150, delta=1)
        self.assertGreater(broke["dirt"]["raw_ticks"], broke["dirt"]["ticks"] - 1)

    def test_pillar_window(self):
        pillar = self.result["pillar"]
        self.assertIsNotNone(pillar["delay_ticks"])
        lo, hi = pillar["window_ticks"]
        self.assertTrue(lo <= pillar["delay_ticks"] <= hi)
        self.assertLessEqual(lo, 4)
        self.assertGreaterEqual(hi, 6)
        self.assertEqual(pillar["delay_ms"], pillar["delay_ticks"] * 50)

    def test_rig_built_by_command_then_cleared_and_player_sent_home(self):
        commands = self.probe.commands
        self.assertTrue(commands[0].startswith("fill") and commands[0].endswith(" air"))
        self.assertTrue(any(c.startswith("fill") and c.endswith(" quartz_block") for c in commands))
        # every fill stays inside one chunk column (Rig.tiled: a fill through runCommand does nothing past three)
        for c in commands:
            if c.startswith("fill"):
                x0, y0, z0, x1, y1, z1 = map(int, c.split()[1:7])
                self.assertEqual((x0 >> 4, z0 >> 4), (x1 >> 4, z1 >> 4), c)
        self.assertTrue(any(c.endswith(" water") for c in commands))
        self.assertTrue(any(c.startswith('give "Kirby" dirt') for c in commands))
        self.assertTrue(any(c.startswith("setblock") and c.endswith(" stone") for c in commands))
        tail = commands[-5:]
        self.assertTrue(all(c.startswith("fill") and c.endswith(" air") for c in tail[-4:]), tail)
        # home, then the clearing tiles (one per chunk column and eight levels, so more than a handful)
        home = next(i for i, c in enumerate(commands) if c.startswith('tp "Kirby" 100.50 65.00 -40.50'))
        self.assertTrue(all(c.startswith("fill") and c.endswith(" air") for c in commands[home + 1:]), commands[home + 1:])
        self.assertTrue(any(c.startswith('clear "Kirby" dirt') for c in commands[:home]))
        self.assertIsNone(self.probe.platform, "the rig is gone")
        self.assertEqual(self.probe.mode, "survival")
        self.assertEqual(self.pad.held, frozenset(), "everything let go")

    def test_written_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "calibration.json")
            calibrate.write(self.result, path)
            with open(path, encoding="utf-8") as file:
                back = json.load(file)
        self.assertEqual(set(back["speeds"]), {"walk", "sprint", "sneak", "walk_in_water", "swim"})
        from baritone_bedrock.costs import Costs
        costs = Costs(back["speeds"])
        self.assertAlmostEqual(costs.WALK_ONE_BLOCK_COST, 20 / back["speeds"]["walk"])
        self.assertAlmostEqual(costs.LADDER_UP_ONE_COST, 20 / 2.35, msg="unmeasured speeds keep Baritone's")


class ControllerTest(unittest.TestCase):
    def test_null_controller(self):
        self.assertIsInstance(make_controller("null"), NullController)
        with self.assertRaises(SystemExit):
            make_controller("nothing")

    def test_only_runs_the_sections_asked(self):
        pad = NullController()
        probe = FakeProbe(pad)
        result = Calibrator(pad, probe, "Kirby", log=lambda line: None).run_all(only={"turn"})
        self.assertIn("turn", result)
        self.assertNotIn("speeds", result)
        self.assertIsNone(probe.platform)


if __name__ == "__main__":
    unittest.main()
