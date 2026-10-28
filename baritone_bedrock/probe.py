"""The runner's end of the probe pack (pack/): an HTTP server on this machine the pack posts to every tick.

Ported from the FTUE skill's nav.py (C:/GitHub/.claude/skills/ftue/nav.py, Probe at lines 119-213 and ProbeTerrain
at 216-258) and kept to its design: a ThreadingHTTPServer on 127.0.0.1:47812, one Condition for everything, /tick
brings every player's state and takes back what the runner wants (bands to read, commands to run), /chunk brings one
band of one chunk. New here is what the plan's "The probe, version 2" adds (docs/baritone-port-plan.md): palette
bands, player fields, events, block updates and commands.

A band is 32 levels of one chunk (BAND), keyed (dim, cx, cz, y0) with y0 a multiple of 32; the pack may send it as
two half bands when its palette passes 90 entries and Band.merge folds them into the one. Each palette entry is
classified once per band (world.classify), so ProbeTerrain.kind is two list lookups.
"""
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import world
from .world import UNKNOWN_KIND, classify

BAND = 32
UNKNOWN = "?"


class Band:
    """One chunk's 32 levels as the probe keeps them: a palette of entries, each classified once, and a palette
    index per block. Index 0 is always "?", so a level not received yet reads as unknown."""

    __slots__ = ("dim", "cx", "cz", "y0", "height", "palette", "index", "kinds", "data", "covered", "unknown", "read_at")

    def __init__(self, dim, cx, cz, y0, height=BAND):
        self.dim, self.cx, self.cz, self.y0, self.height = dim, cx, cz, y0, height
        self.palette, self.index, self.kinds = [UNKNOWN], {UNKNOWN: 0}, [UNKNOWN_KIND]
        self.data = [0] * (256 * height)
        self.covered = [False] * height   # the levels a /chunk has filled
        self.unknown = 256 * height       # cells reading "?"
        self.read_at = 0.0

    @property
    def key(self):
        return (self.dim, self.cx, self.cz, self.y0)

    @property
    def complete(self):
        return all(self.covered)

    @property
    def partial(self):
        """Complete but holding a "?": worth reading again once the server has loaded the rest."""
        return self.complete and self.unknown > 0

    def code(self, entry):
        """The palette index of an entry, added (and classified) if new."""
        k = self.index.get(entry)
        if k is None:
            k = len(self.palette)
            self.palette.append(entry)
            self.index[entry] = k
            self.kinds.append(UNKNOWN_KIND if entry == UNKNOWN else classify(entry))
        return k

    def merge(self, y0, y1, palette, data):
        """Write a wire band (any y range inside this band) into it: nav.py's data layout, index
        ((x * 16) + z) * height + (y - y0), recoded from the wire palette to this band's."""
        height = y1 - y0 + 1
        if y0 < self.y0 or y1 >= self.y0 + self.height or len(data) != 256 * height:
            raise ValueError(f"band {y0}..{y1} with {len(data)} cells does not fit {self.key}")
        codes = [self.code(e) for e in palette]
        cells = [codes[ord(c) - 33] for c in data]
        if height == self.height:
            self.data[:] = cells
        else:
            offset = y0 - self.y0
            for col in range(256):
                self.data[col * self.height + offset:col * self.height + offset + height] = cells[col * height:(col + 1) * height]
        for y in range(y0, y1 + 1):
            self.covered[y - self.y0] = True
        self.unknown = self.data.count(0)
        self.read_at = time.time()

    def set(self, x, y, z, entry):
        """One block changed (a break or place event): recode its cell, adding to the palette if needed."""
        i = ((x & 15) * 16 + (z & 15)) * self.height + (y - self.y0)
        k = self.code(entry)
        old = self.data[i]
        self.data[i] = k
        if old == 0 and k != 0:
            self.unknown -= 1
        elif old != 0 and k == 0:
            self.unknown += 1

    def kind(self, x, y, z):
        return self.kinds[self.data[((x & 15) * 16 + (z & 15)) * self.height + (y - self.y0)]]

    def entry(self, x, y, z):
        return self.palette[self.data[((x & 15) * 16 + (z & 15)) * self.height + (y - self.y0)]]


def band_key(dim, x, y, z):
    return (dim, x >> 4, z >> 4, y // BAND * BAND)


class Probe:
    """The HTTP end of the pack. /tick brings every player's state each tick, the events since the last tick, the
    blocks those events changed and the results of the commands sent last time, and takes back the bands wanted and
    the commands to run; /chunk brings one band of blocks (nav.py:119-122)."""

    PORT = 47812

    def __init__(self, port=PORT):
        self.players, self.bands, self.wanted, self.asked = {}, {}, [], {}
        self.ticks, self.cv = 0, threading.Condition()
        self.game_tick = None            # the pack's system.currentTick in the latest report
        self._tick_times = deque(maxlen=64)
        self._events, self._commands, self._pending, self.stray_results = [], [], deque(), []
        self.scan_stats = deque(maxlen=512)   # (key, ms, wall, cells, arrival) per /chunk, from the pack's timing fields
        self.budget_ms = None                 # when set, sent in every /tick answer: the pack's scan budget a tick
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                except ValueError:
                    body = {}
                out = json.dumps(probe.handle(self.path, body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ---------------------------------------------------------------------------------------------- the pack's posts

    def handle(self, path, body):
        now = time.time()
        with self.cv:
            if path == "/tick":
                self.ticks += 1
                self._tick_times.append(now)
                self.game_tick = body.get("tick", self.game_tick)
                for p in body.get("players") or []:
                    p["at"], p["n"], p["tick"] = now, self.ticks, self.game_tick   # arrival time, report count, game tick
                    self.players[str(p["id"])] = self.players[p["name"]] = p
                self._events.extend(body.get("events") or [])
                for b in body.get("blocks") or []:
                    self._apply(b)
                for r in body.get("results") or []:
                    self._resolve(r)
                wanted, self.wanted = self.wanted, []
                commands, self._commands = self._commands, []
                self._pending.extend(commands)
                self.cv.notify_all()
                answer = {"scan": wanted, "commands": [{"dim": c["dim"], "cmd": c["cmd"]} for c in commands]}
                if self.budget_ms is not None:
                    answer["budget"] = self.budget_ms
                return answer
            if path == "/chunk":
                try:
                    key = (body["dim"], body["cx"], body["cz"], body["y0"] // BAND * BAND)
                    band = self.bands.get(key)
                    if band is None:
                        band = self.bands[key] = Band(*key)
                    band.merge(body["y0"], body["y1"], body["palette"], body["data"])
                except (KeyError, TypeError, ValueError):
                    return {}
                if "ms" in body:   # the pack's own timing of the band (pack/scripts/main.js emit): kept for the reports
                    self.scan_stats.append((key, body.get("ms"), body.get("wall"), body.get("cells"), now))
                if band.complete:
                    self.asked.pop(key, None)
                self.cv.notify_all()
            return {}

    def _apply(self, update):
        """A block update from a break or place event, into the band holding it if that band has been read."""
        try:
            x, y, z = int(update["x"]), int(update["y"]), int(update["z"])
            band = self.bands.get(band_key(update.get("dim", "overworld"), x, y, z))
            if band is not None:
                band.set(x, y, z, update["entry"])
        except (KeyError, TypeError, ValueError):
            pass

    def _resolve(self, result):
        """A command's result: the pack runs them in the order sent, so it is the oldest pending one, checked by
        its text."""
        cmd = result.get("cmd")
        for entry in self._pending:
            if entry["cmd"] == cmd and entry["result"] is None:
                entry["result"] = result
                self._pending.remove(entry)
                return
        self.stray_results.append(result)

    # --------------------------------------------------------------------------------------------------- the runner

    def connected(self, timeout=60):
        """True once the pack has reported, within timeout seconds."""
        end = time.time() + timeout
        with self.cv:
            while not self.ticks and time.time() < end:
                self.cv.wait(1)
            return bool(self.ticks)

    def wait_tick(self, after, timeout=0.2):
        """Wait for a report newer than tick count after; returns the count now (nav.py:171-176)."""
        end = time.time() + timeout
        with self.cv:
            while self.ticks <= after and time.time() < end:
                self.cv.wait(max(0.0, end - time.time()))
            return self.ticks

    def tick_rate(self):
        """Reports a second over the last few seconds; 20 when the pack and the runner keep up."""
        with self.cv:
            times = list(self._tick_times)
        if len(times) < 2 or times[-1] <= times[0]:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    def player(self, who, fresh=1.0):
        """The latest report on a player (by entity id or name), or None if there is none this last second."""
        with self.cv:
            p = self.players.get(str(who))
        return p if p and time.time() - p["at"] < fresh else None

    def hotbar(self, who, fresh=2.0):
        """The nine hotbar slots of the latest report, each {type, amount} or None; None when there is no report."""
        p = self.player(who, fresh)
        return p.get("hotbar") if p else None

    def hand(self, who, fresh=2.0):
        """The held item of the latest report, {type, amount} or None."""
        p = self.player(who, fresh)
        return p.get("hand") if p else None

    def events(self):
        """The events queued since the last call, oldest first, each
        {type: break|place|hurt, tick, dim, x, y, z, block, player, damage, cause}."""
        with self.cv:
            out, self._events = self._events, []
        return out

    def command(self, text, timeout=10.0, dim="overworld"):
        """Run a command in the world through the pack: queued to the next /tick, answered in the one after.
        Returns {cmd, ok, successCount, error}; on timeout ok is False and error "timeout"."""
        entry = {"cmd": text, "dim": dim, "result": None}
        end = time.time() + timeout
        with self.cv:
            self._commands.append(entry)
            self.cv.notify_all()
            while entry["result"] is None and time.time() < end:
                self.cv.wait(min(0.2, max(0.0, end - time.time())))
            if entry["result"] is None:
                entry["result"] = {"cmd": text, "ok": False, "successCount": 0, "error": "timeout"}
                if entry in self._commands:
                    self._commands.remove(entry)
                if entry in self._pending:
                    self._pending.remove(entry)
            return entry["result"]

    def scan(self, keys, timeout=None):
        """Read bands (dim, cx, cz, y0), each 16 by 16 by BAND from y0; returns {key: Band} for those complete.
        Asks once for each, then waits for them all, or timeout (a second a dozen bands) (nav.py:183-195)."""
        keys = list(dict.fromkeys(keys))
        end = time.time() + (timeout or 4 + len(keys) / 12)
        with self.cv:
            for key in keys:
                band = self.bands.get(key)
                if (band is None or not band.complete) and time.time() - self.asked.get(key, 0) > 10:
                    self.asked[key] = time.time()
                    self.wanted.append([key[0], key[1], key[2], key[3], key[3] + BAND - 1])
            while any(k not in self.bands or not self.bands[k].complete for k in keys) and time.time() < end:
                self.cv.wait(0.2)
            return {k: self.bands[k] for k in keys if k in self.bands and self.bands[k].complete}

    def forget(self, key):
        with self.cv:
            self.bands.pop(key, None)
            self.asked.pop(key, None)

    def band(self, key):
        with self.cv:
            return self.bands.get(key)


class ProbeTerrain(world.Terrain):
    """Terrain from the probe: a chunk's blocks a band of BAND levels at a time, kept as the pack sends them. A band
    read while part of it was not loaded ("?") is read again after a few seconds (nav.py:216-258)."""

    BAND = BAND

    def __init__(self, probe, dimension="overworld"):
        self.probe, self.dimension = probe, dimension
        self.bands, self.reads = {}, 0

    def _key(self, x, y, z):
        return (self.dimension, x >> 4, z >> 4, y // BAND * BAND)

    def _fetch(self, keys, timeout=None):
        """Read the keys not held, and read again those held but partial for more than four seconds; with a
        timeout, ask and take what is complete by then (a prefetch), else wait for them all."""
        now = time.time()
        keys = [k for k in dict.fromkeys(keys)
                if k not in self.bands or (self.bands[k].partial and now - self.bands[k].read_at > 4)]
        if not keys:
            return
        for k in keys:
            if k in self.bands:   # a re-read: the old band stays readable until the new one arrives, and is not
                self.probe.forget(k)   # asked again for another four seconds while that one is on its way
                self.bands[k].read_at = now
        for key, band in self.probe.scan(keys, timeout=timeout).items():
            if band is not self.bands.get(key):
                self.bands[key] = band
                self.reads += 1

    def section(self, x, y, z):
        """A miss in the middle of a search: the chunks round it are read with it, one request for nine, since the
        search will reach them next and each request waits a round trip through a game tick (nav.py:241-247)."""
        key = self._key(x, y, z)
        if key not in self.bands:
            self._fetch([key] + [(key[0], key[1] + dx, key[2] + dz, key[3]) for dx in (-1, 0, 1) for dz in (-1, 0, 1)])
        return key in self.bands

    def prefetch(self, keys, timeout=None):
        """Bands (dim, cx, cz, y0) to read ahead of a search, in one request; with a timeout (the navigator passes
        a millisecond every tick) the request goes out and whatever has arrived by then is taken, the rest on a
        later call."""
        self._fetch(list(keys), timeout=timeout)

    def kind(self, x, y, z):
        key = self._key(x, y, z)
        band = self.bands.get(key)
        if band is None:
            if not self.section(x, y, z):
                return UNKNOWN_KIND
            band = self.bands[key]
        self.last_y = y
        return band.kind(x, y, z)

    last_y = 64

    def loaded(self, x, z):
        """Whether the column's chunk can be read (Baritone: isLoaded(x, z)). Terrain.loaded reads the block at
        y 64, which here meant a fetch of the y0=64 band and its eight neighbours at every chunk crossing of a search
        200 blocks up (the course's first segment: 64 nodes in 4.6 s), so this answers from the bands already read
        for that chunk and, on a miss, reads the band at the height of the latest kind() call, the one the search is
        about to read anyway."""
        cx, cz = x >> 4, z >> 4
        for key in self.bands:
            if key[1] == cx and key[2] == cz:
                return True
        return self.section(x, self.last_y, z)

    def entry(self, x, y, z):
        """The palette entry at a position, "?" when unread."""
        band = self.bands.get(self._key(x, y, z))
        return band.entry(x, y, z) if band else UNKNOWN
