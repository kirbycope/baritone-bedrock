"""The probe's end of the pack protocol, driven the way the pack drives it: /tick and /chunk posts, most through
Probe.handle directly and one through the real HTTP server."""
import http.client
import json
import threading
import time
import unittest

from baritone_bedrock import world
from baritone_bedrock.probe import BAND, Band, Probe, ProbeTerrain, band_key
from baritone_bedrock.world import UNKNOWN_KIND

STONE, AIR, DIRT, WATER = "minecraft:stone", "minecraft:air", "minecraft:dirt", "minecraft:water|liquid_depth=0"


def wire_band(dim, cx, cz, y0, y1, cell):
    """A /chunk body the way the pack builds one: cell(x, z, y) -> entry, data column by column."""
    palette, index, data = [], {}, []
    for x in range(16):
        for z in range(16):
            for y in range(y0, y1 + 1):
                entry = cell(x, z, y)
                k = index.get(entry)
                if k is None:
                    k = index[entry] = len(palette)
                    palette.append(entry)
                data.append(chr(33 + k))
    return {"dim": dim, "cx": cx, "cz": cz, "y0": y0, "y1": y1, "height": y1 - y0 + 1, "palette": palette, "data": "".join(data)}


def player(name="Kirby", **fields):
    p = {"id": "-123", "name": name, "dim": "overworld", "x": 0.5, "y": 65.0, "z": 0.5, "yaw": 0.0, "pitch": 0.0,
         "vx": 0.0, "vy": 0.0, "vz": 0.0, "ground": True, "water": False, "sneak": False, "sprint": False, "jump": False,
         "climbing": False, "swimming": False, "falling": False, "fall": 0.0, "slot": 2,
         "hotbar": [None, {"type": "minecraft:stone_pickaxe", "amount": 1}, {"type": "minecraft:dirt", "amount": 12}] + [None] * 6,
         "input": {"move": {"x": 0.0, "y": 1.0}, "jump": False, "sneak": False}, "health": {"current": 20, "max": 20},
         "food": {"current": 20, "max": 20}, "look": None, "block": {"type": STONE, "x": 0, "y": 64, "z": 2, "face": "Up"}}
    p["hand"] = p["hotbar"][p["slot"]]
    p.update(fields)
    return p


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(port=0)

    def tearDown(self):
        self.probe.close()

    def tick(self, **body):
        body.setdefault("tick", 1000 + self.probe.ticks)
        body.setdefault("players", [player()])
        return self.probe.handle("/tick", body)

    def test_tick_report_and_helpers(self):
        answer = self.tick()
        self.assertEqual(answer, {"scan": [], "commands": []})
        self.assertEqual(self.probe.ticks, 1)
        self.assertEqual(self.probe.game_tick, 1000)
        p = self.probe.player("Kirby")
        self.assertIsNotNone(p)
        self.assertIs(self.probe.player("-123"), p, "by entity id too")
        self.assertEqual(self.probe.hand("Kirby"), {"type": "minecraft:dirt", "amount": 12})
        self.assertEqual(len(self.probe.hotbar("Kirby")), 9)
        self.assertEqual(self.probe.hotbar("Kirby")[1]["type"], "minecraft:stone_pickaxe")
        self.assertIsNone(self.probe.player("nobody"))
        self.assertIsNone(self.probe.hand("nobody"))
        self.assertTrue(self.probe.connected(timeout=0.1))

    def test_wait_tick_and_tick_rate(self):
        self.assertEqual(self.probe.tick_rate(), 0.0)
        before = self.probe.ticks
        threading.Timer(0.05, self.tick).start()
        self.assertEqual(self.probe.wait_tick(before, timeout=2.0), before + 1)
        self.assertEqual(self.probe.wait_tick(before + 1, timeout=0.05), before + 1, "a timeout returns the count as it is")
        for _ in range(5):
            self.tick()
            time.sleep(0.01)
        self.assertGreater(self.probe.tick_rate(), 1.0)

    def test_band_decodes_through_the_terrain(self):
        body = wire_band("overworld", 0, 0, 64, 95, lambda x, z, y: STONE if y < 70 else (WATER if (x, z, y) == (3, 4, 70) else ("?" if (x, z) == (15, 15) else AIR)))
        self.probe.handle("/chunk", body)
        key = ("overworld", 0, 0, 64)
        band = self.probe.band(key)
        self.assertTrue(band.complete)
        self.assertTrue(band.partial, "it holds a ? column")
        self.assertEqual(band.unknown, 26, "the column's 26 levels above the stone")
        self.assertEqual(set(band.palette), {"?", STONE, WATER, AIR})
        terrain = ProbeTerrain(self.probe)
        self.assertTrue(terrain.has(5, 69, 5, world.WALK_ON))
        self.assertTrue(terrain.is_air(5, 70, 5))
        self.assertTrue(terrain.has(3, 70, 4, world.WATER))
        self.assertIs(terrain.kind(15, 80, 15), UNKNOWN_KIND)
        self.assertEqual(terrain.entry(5, 69, 5), STONE)
        self.assertEqual(terrain.entry(5, 69, 21), "?", "an unread band")
        self.assertEqual(terrain.reads, 1)
        self.assertTrue(terrain.loaded(5, 5))

    def test_half_bands_merge_into_one(self):
        lower = wire_band("overworld", 1, -2, 32, 47, lambda x, z, y: STONE if y < 40 else DIRT)
        upper = wire_band("overworld", 1, -2, 48, 63, lambda x, z, y: AIR if y > 50 else "minecraft:oak_log")
        self.probe.handle("/chunk", lower)
        key = ("overworld", 1, -2, 32)
        band = self.probe.band(key)
        self.assertFalse(band.complete)
        self.assertIs(band.kind(20, 55, -20), UNKNOWN_KIND, "the upper half is not here yet")
        self.probe.handle("/chunk", upper)
        self.assertTrue(band.complete)
        self.assertFalse(band.partial)
        self.assertEqual(band.unknown, 0)
        self.assertEqual(band.palette, ["?", STONE, DIRT, "minecraft:oak_log", AIR])
        self.assertEqual(band.kind(20, 39, -20).type_id, STONE)
        self.assertEqual(band.kind(20, 40, -20).type_id, DIRT)
        self.assertEqual(band.kind(31, 48, -17).type_id, "minecraft:oak_log")
        self.assertEqual(band.kind(31, 63, -17).type_id, AIR)

    def test_band_rejects_what_does_not_fit(self):
        band = Band("overworld", 0, 0, 0)
        with self.assertRaises(ValueError):
            band.merge(0, 31, [AIR], "!" * 10)
        with self.assertRaises(ValueError):
            band.merge(16, 47, [AIR], "!" * 256 * 32)

    def test_scan_asks_once_and_returns_complete_bands(self):
        key = ("overworld", 2, 2, 0)
        out = {}
        worker = threading.Thread(target=lambda: out.update(self.probe.scan([key, key], timeout=5)))
        worker.start()
        for _ in range(50):
            if self.probe.wanted:
                break
            time.sleep(0.01)
        answer = self.tick()
        self.assertEqual(answer["scan"], [["overworld", 2, 2, 0, 31]])
        self.assertEqual(self.tick()["scan"], [], "asked once, not every tick")
        self.probe.handle("/chunk", wire_band("overworld", 2, 2, 0, 31, lambda x, z, y: STONE))
        worker.join(5)
        self.assertEqual(list(out), [key])
        self.assertTrue(out[key].complete)
        self.probe.forget(key)
        self.assertIsNone(self.probe.band(key))

    def test_terrain_fetches_the_neighbourhood_on_a_miss(self):
        terrain = ProbeTerrain(self.probe)
        got = {}
        worker = threading.Thread(target=lambda: got.update(kind=terrain.kind(40, 70, 40)))
        worker.start()
        for _ in range(50):
            if self.probe.wanted:
                break
            time.sleep(0.01)
        asked = self.tick()["scan"]
        self.assertEqual(len(asked), 9, "the chunk and the eight round it, in one request")
        self.assertIn(["overworld", 2, 2, 64, 95], asked)
        self.assertEqual({(a[1], a[2]) for a in asked}, {(cx, cz) for cx in (1, 2, 3) for cz in (1, 2, 3)})
        for dim, cx, cz, y0, y1 in asked:
            self.probe.handle("/chunk", wire_band(dim, cx, cz, y0, y1, lambda x, z, y: DIRT if y == 70 else AIR))
        worker.join(5)
        self.assertEqual(got["kind"].type_id, DIRT)
        self.assertEqual(terrain.reads, 9)
        self.assertEqual(terrain.kind(20, 70, 20).type_id, DIRT, "a neighbour, read with it")

    def test_partial_band_is_read_again_after_four_seconds(self):
        key = ("overworld", 0, 0, 64)
        self.probe.handle("/chunk", wire_band("overworld", 0, 0, 64, 95, lambda x, z, y: "?" if x == 0 else STONE))
        terrain = ProbeTerrain(self.probe)
        terrain.prefetch([key])   # complete in the probe already: no wait, no neighbourhood
        self.assertEqual(self.probe.wanted, [])
        self.assertIs(terrain.kind(0, 70, 0), UNKNOWN_KIND)
        self.assertEqual(terrain.kind(1, 70, 0).type_id, STONE)
        terrain.prefetch([key])
        self.assertEqual(self.probe.wanted, [], "too soon to read it again")
        self.probe.band(key).read_at -= 5
        worker = threading.Thread(target=terrain.prefetch, args=([key],))
        worker.start()
        for _ in range(50):
            if self.probe.wanted:
                break
            time.sleep(0.01)
        self.assertEqual(self.tick()["scan"], [["overworld", 0, 0, 64, 95]])
        self.assertEqual(terrain.kind(1, 70, 0).type_id, STONE, "the old band answers until the new one arrives")
        self.probe.handle("/chunk", wire_band("overworld", 0, 0, 64, 95, lambda x, z, y: STONE))
        worker.join(5)
        self.assertEqual(terrain.kind(0, 70, 0).type_id, STONE)
        self.assertEqual(terrain.reads, 2)

    def test_block_updates_recode_one_cell(self):
        self.probe.handle("/chunk", wire_band("overworld", 0, 0, 64, 95, lambda x, z, y: STONE if y < 70 else AIR))
        terrain = ProbeTerrain(self.probe)
        self.assertEqual(terrain.kind(4, 70, 4).type_id, AIR)
        self.tick(blocks=[{"dim": "overworld", "x": 4, "y": 70, "z": 4, "entry": DIRT},
                          {"dim": "overworld", "x": 4, "y": 69, "z": 4, "entry": AIR},
                          {"dim": "overworld", "x": 400, "y": 69, "z": 4, "entry": DIRT},   # an unread band: ignored
                          {"dim": "nether", "x": 4, "y": 69, "z": 4, "entry": DIRT}])      # another dimension: ignored
        self.assertEqual(terrain.kind(4, 70, 4).type_id, DIRT)
        self.assertEqual(terrain.kind(4, 69, 4).type_id, AIR)
        self.assertEqual(terrain.kind(5, 69, 4).type_id, STONE)
        band = self.probe.band(("overworld", 0, 0, 64))
        self.assertIn(DIRT, band.palette)
        self.assertEqual(band.unknown, 0)
        self.assertIsNone(self.probe.band(("overworld", 25, 0, 64)))
        band.set(4, 70, 4, "?")
        self.assertEqual(band.unknown, 1)
        self.assertTrue(band.partial)

    def test_chunk_timing_fields_are_optional_and_kept(self):
        """The pack's ms, wall and cells (pack/scripts/main.js emit) ride beside the band: a band without them is
        the same band, one with them leaves a scan_stats entry."""
        plain = wire_band("overworld", 1, 1, 64, 95, lambda x, z, y: STONE)
        self.probe.handle("/chunk", plain)
        self.assertEqual(len(self.probe.scan_stats), 0)
        timed = dict(wire_band("overworld", 2, 1, 64, 95, lambda x, z, y: STONE), ms=27, wall=110, cells=8192)
        self.probe.handle("/chunk", timed)
        self.assertEqual(len(self.probe.scan_stats), 1)
        key, ms, wall, cells, at = self.probe.scan_stats[0]
        self.assertEqual((key, ms, wall, cells), (("overworld", 2, 1, 64), 27, 110, 8192))
        for cx in (1, 2):
            band = self.probe.band(("overworld", cx, 1, 64))
            self.assertTrue(band.complete)
            self.assertEqual(band.kind(cx * 16 + 3, 70, 20).type_id, STONE)

    def test_events_queue_until_read(self):
        self.assertEqual(self.probe.events(), [])
        broke = {"type": "break", "tick": 1001, "dim": "overworld", "x": 1, "y": 64, "z": 2, "block": STONE, "player": "-123", "damage": None, "cause": None}
        hurt = {"type": "hurt", "tick": 1002, "dim": "overworld", "x": 0, "y": 65, "z": 0, "block": None, "player": "-123", "damage": 2, "cause": "fall"}
        self.tick(events=[broke])
        self.tick(events=[hurt])
        self.assertEqual(self.probe.events(), [broke, hurt])
        self.assertEqual(self.probe.events(), [])

    def test_command_round_trip(self):
        out = {}
        worker = threading.Thread(target=lambda: out.update(result=self.probe.command("fill 0 64 0 3 64 3 dirt", timeout=5)))
        worker.start()
        for _ in range(50):
            if self.probe._commands:
                break
            time.sleep(0.01)
        answer = self.tick()
        self.assertEqual(answer["commands"], [{"dim": "overworld", "cmd": "fill 0 64 0 3 64 3 dirt"}])
        self.assertEqual(self.tick()["commands"], [], "sent once")
        self.assertTrue(worker.is_alive(), "waits for the result")
        result = {"cmd": "fill 0 64 0 3 64 3 dirt", "ok": True, "successCount": 1, "error": None}
        self.tick(results=[{"cmd": "something else", "ok": True, "successCount": 1, "error": None}, result])
        worker.join(5)
        self.assertEqual(out["result"], result)
        self.assertEqual(self.probe.stray_results[0]["cmd"], "something else")

    def test_command_times_out_without_the_pack(self):
        started = time.time()
        result = self.probe.command("say hi", timeout=0.2)
        self.assertLess(time.time() - started, 2.0)
        self.assertEqual(result, {"cmd": "say hi", "ok": False, "successCount": 0, "error": "timeout"})
        self.assertEqual(self.tick()["commands"], [], "withdrawn")

    def test_command_in_another_dimension(self):
        worker = threading.Thread(target=self.probe.command, args=("say hi", 1.0, "nether"))
        worker.start()
        for _ in range(50):
            if self.probe._commands:
                break
            time.sleep(0.01)
        self.assertEqual(self.tick()["commands"], [{"dim": "nether", "cmd": "say hi"}])
        worker.join(3)

    def test_over_http(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.probe.port, timeout=5)
        body = json.dumps({"tick": 7, "players": [player()], "events": [], "blocks": [], "results": []})
        connection.request("POST", "/tick", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"scan": [], "commands": []})
        connection.request("POST", "/chunk", json.dumps(wire_band("overworld", 0, 0, 0, 31, lambda x, z, y: STONE)))
        self.assertEqual(connection.getresponse().read(), b"{}")
        self.assertTrue(self.probe.band(("overworld", 0, 0, 0)).complete)
        self.assertEqual(self.probe.game_tick, 7)
        connection.close()

    def test_band_key(self):
        self.assertEqual(band_key("overworld", -1, -1, -1), ("overworld", -1, -1, -BAND))
        self.assertEqual(band_key("overworld", 17, 64, 33), ("overworld", 1, 2, 64))
        self.assertEqual(band_key("overworld", 0, 319, 0), ("overworld", 0, 0, 288))


if __name__ == "__main__":
    unittest.main()
