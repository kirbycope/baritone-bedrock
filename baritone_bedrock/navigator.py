"""Navigator: the facade a caller drives. It owns a Body (control.py) over the caller's Controller and the Probe, a
ProbeTerrain over the same probe, and a PathingBehavior (executor.py) per walk, and it runs the tick loop: one
executor tick per probe report with a new game tick, the planner on its own thread.

    nav = Navigator(controller, probe, "Kirbycope", settings=None, log=print, say=run.say)
    left = nav.go((x, None, z))            # a GoalXZ; (x, y, z) is a GoalNear(within); or any Goal
    nav.look_at((x, y, z), block=(bx, by, bz))
    nav.gather("minecraft:dirt", 16)       # the plan's Gathering section: nothing is given
    nav.where(); nav.stand_up(); nav.stop()

Nothing here knows the FTUE or Cobblemon: the controller is whatever has state() and press() (controller.py), and
`say` is whatever the caller wants shown to a watcher.
"""
import math
import time

from .context import CalculationContext
from .control import Body, Player, center, rotation_from
from .costs import Costs
from .executor import PathingBehavior, PathEvent, TickState
from .goals import Goal, GoalXZ, GoalNear, GoalBlock, GoalGetToBlock
from .moves import MovementParkour
from .probe import ProbeTerrain
from .settings import Settings
from .world import WALK_THROUGH


class Navigator:

    def __init__(self, controller, probe, player, settings=None, log=print, say=None, calibration=None,
                 dimension="overworld", on_tick=None):
        """player is the name (or entity id) the probe reports; say(text) tells a watcher what the walk is doing;
        on_tick(record) receives one dict per executor tick for a log."""
        self.pad, self.probe, self.who = controller, probe, player
        self.settings = settings if settings is not None else Settings()
        self.log = log or (lambda text: None)
        self.say = say or (lambda text: None)
        self.on_tick = on_tick
        self.dimension = dimension
        self.body = Body(controller, probe, player, calibration, log=self.log)
        self.terrain = ProbeTerrain(probe, dimension)
        self.costs = Costs.from_calibration()
        self.behavior = None
        self.goal = None
        self._events = []
        self._said_movement = None
        self.last_search_stats = []
        self.parkour = []            # per go(): {src, dest, dist, ascend, status} for every parkour movement run
        self.flights = 0             # per go(): sprint jumps taken
        self._last_movement = None
        self._last_flight = None

    # ------------------------------------------------------------------------------------------- the player

    def report(self, fresh=5.0):
        p = self.probe.player(self.who, fresh=fresh)
        return Player(p) if p else None

    def where(self):
        """(x, y, z, yaw, pitch) of the player from the probe's latest report, or None."""
        pl = self.report()
        return (pl.x, pl.y, pl.z, pl.yaw, pl.pitch) if pl else None

    def hotbar(self):
        pl = self.report()
        return list(pl.hotbar or []) if pl else []

    def count(self, kind):
        """How many of an item type the hotbar holds."""
        return sum(int(item.get("amount") or 0) for item in self.hotbar() if item and (item.get("type") or item.get("typeId")) == kind)

    def context(self):
        """A CalculationContext with the hotbar and food as the probe reports them now: the factory the behavior
        calls once per segment (the plan's Gathering section: allowPlace is true only with a throwaway in hand)."""
        pl = self.report()
        hotbar = list(pl.hotbar or []) if pl else []
        food = pl.food if pl else 20
        return CalculationContext(self.terrain, self.costs, self.settings, hotbar, can_sprint=food > 6, dimension=self.dimension)

    def stand_up(self):
        """Out of a crouch if the probe says the player is in one (B toggles); True if it stood up."""
        pl = self.report()
        if not (pl and pl.sneak):
            return False
        self.log("  crouched: standing up")
        return self.body.stand_up()

    def stop(self):
        """Cancel the walk and let the pad go."""
        if self.behavior is not None:
            self.behavior.force_cancel()
        self.body.release()

    # ------------------------------------------------------------------------------------------------- goals

    @staticmethod
    def make_goal(goal, within=1.5):
        """A Goal from a point: (x, None, z) is a GoalXZ, (x, y, z) a GoalNear of `within`; a Goal passes through."""
        if isinstance(goal, Goal):
            return goal
        x, y, z = goal
        if y is None:
            return GoalXZ(math.floor(x), math.floor(z))
        return GoalNear(math.floor(x), math.floor(y), math.floor(z), within)

    @staticmethod
    def distance_left(pl, goal):
        """Flat distance from the player to the goal's column (the number go() returns)."""
        gx = getattr(goal, "x", None)
        gz = getattr(goal, "z", None)
        if gx is None or gz is None:
            return 0.0
        return math.hypot(gx + 0.5 - pl.x, gz + 0.5 - pl.z)

    def on_event(self, event):
        self._events.append(event)
        if event == PathEvent.CONTINUING_ONTO_PLANNED_NEXT or event == PathEvent.SPLICING_ONTO_NEXT_EARLY:
            self.say("Next segment")
        elif event == PathEvent.NEXT_SEGMENT_CALC_FINISHED:
            self.log("  next segment planned")

    # ---------------------------------------------------------------------------------------------------- go

    def go(self, goal, within=1.5, timeout=300):
        """Walk to the goal: a (x, y, z) or (x, None, z) point, or a Goal. Drives a PathingBehavior until the goal,
        the timeout, or a failure the planner cannot get past, saying what it does. Returns the flat distance left,
        or None when the probe lost the player (a death, a disconnect)."""
        goal = self.make_goal(goal, within)
        pl = self.body.next_report(None, timeout=5.0) or self.report()
        if pl is None:
            self.log("go: no report on the player")
            return None
        self.goal = goal
        self.arrived = goal.is_in_goal(*pl.feet)
        left = self.distance_left(pl, goal)
        gx, gz = getattr(goal, "x", pl.feet[0]), getattr(goal, "z", pl.feet[2])
        self.say(f"Walking to {gx}, {gz} ({left:.0f} blocks)")
        self.stand_up()
        behavior = PathingBehavior(self.context, self.settings, log=self.log, on_event=self.on_event)
        self.behavior = behavior
        self._events = []
        self._said_movement = None
        self.parkour, self.flights, self._last_movement, self._last_flight = [], 0, None, None
        s = TickState(pl, behavior.context_factory(), self.body, self.settings, log=self.log)
        behavior.context = s.ctx
        if goal.is_in_goal(*pl.feet):
            return left
        # the bands round the player before the first search: a search that reads them itself spends its budget
        # waiting on the pack and can run out of pathingMaxChunkBorderFetch misses first (piece 2's first try); and
        # the corridor toward the goal asked for behind them, so the pack is reading it while the search runs
        self.terrain.section(*pl.feet)
        self.prefetch_ahead(pl, goal)
        behavior.set_goal_and_path(goal, s)
        deadline = time.time() + timeout
        last_tick = pl.tick
        failures, retry_at, lost_since = 0, None, None
        try:
            while time.time() < deadline:
                pl = self.body.next_report(last_tick, timeout=1.0)
                if pl is None:
                    lost_since = lost_since or time.time()
                    if time.time() - lost_since > 10:
                        self.log("go: the probe stopped reporting the player")
                        return None
                    continue
                lost_since = None
                last_tick = pl.tick
                if pl.health <= 0:
                    self.log("go: the player is dead")
                    return None
                events = self.probe.events()
                s = TickState(pl, behavior.context, self.body, self.settings, events=events, log=self.log)
                self.body.begin(pl)
                self.prefetch_ahead(pl, goal)
                behavior.on_tick(s)
                self.record(pl, behavior)
                if goal.is_in_goal(*pl.feet):
                    self.body.release()
                    self.arrived = True
                    self.say("Arrived")
                    return self.distance_left(pl, goal)
                failed = PathEvent.CALC_FAILED in self._events
                self._events = []
                if behavior.current is None and behavior.in_progress is None and behavior.next is None:
                    # a calculation failed, or a segment ended off the goal with nothing planned: Baritone's process
                    # would re-issue the goal next tick (CustomGoalProcess); here it is re-issued after a breath
                    if failed:
                        failures += 1
                        if failures > 3:
                            self.say("No way through")
                            return self.distance_left(pl, goal)
                        self.say("Stuck, finding another way")
                        retry_at = time.time() + 1.0
                        self.rescan_around(pl)
                    if retry_at is None or time.time() >= retry_at:
                        retry_at = None
                        self.stand_up()
                        behavior.set_goal_and_path(goal, s)
                elif behavior.current is not None:
                    failures = 0
            self.say("Out of time")
            return self.distance_left(pl, goal)
        finally:
            self.stop()
            self.note_movement(None, None)
            if behavior.searches:
                self.last_search_stats = list(behavior.searches)

    PREFETCH_REACH = (20, 36, 52)    # blocks ahead along the line to the goal whose chunk columns are asked for

    def prefetch_ahead(self, pl, goal):
        """Ask the pack, without waiting, for the bands along the line to the goal: the chunk column at each of
        PREFETCH_REACH blocks ahead and the two beside it, at the feet's height, so the planner finds them read when
        it gets there. The pack reads a band in about 50 ms of script time under a 15 ms a tick budget (pack/
        scripts/main.js), six or seven a second, so a corridor's worth asked at once is read before the walk reaches
        it; the probe asks for a key once per 10 s."""
        gx, gz = getattr(goal, "x", None), getattr(goal, "z", None)
        if gx is None or gz is None:
            return
        dx, dz = gx + 0.5 - pl.x, gz + 0.5 - pl.z
        dist = math.hypot(dx, dz)
        if dist < 8:
            return
        ux, uz = dx / dist, dz / dist
        px, pz = -uz, ux
        fy = pl.feet[1]
        keys = []
        for reach in self.PREFETCH_REACH:
            if reach > dist + 16:
                break
            ax, az = pl.x + ux * reach, pl.z + uz * reach
            for side in (0, 1, -1):
                x, z = int(math.floor(ax + px * 16 * side)), int(math.floor(az + pz * 16 * side))
                keys.append((self.dimension, x >> 4, z >> 4, fy // self.terrain.BAND * self.terrain.BAND))
        # through the terrain, so a band read while part of its chunk was unloaded (a "?" in it, which a search
        # counts toward pathingMaxChunkBorderFetch) is read again after four seconds with the old one readable
        # until the new one lands (a band forgotten outright made the path under the player unknown, and the
        # executor cancelled it: piece 1 went from 23 s to 33 s with no hops)
        self.terrain.prefetch(keys, timeout=0.001)

    def rescan_around(self, pl):
        """Forget the bands round the player that still hold unread blocks, so the next search reads them afresh."""
        for key, band in list(self.terrain.bands.items()):
            if band.partial and abs(key[1] - (pl.feet[0] >> 4)) <= 2 and abs(key[2] - (pl.feet[2] >> 4)) <= 2:
                self.terrain.bands.pop(key, None)
                self.probe.forget(key)

    def note_movement(self, movement, ex):
        """The parkour tally and the sprint jump count: a parkour movement's final status is read when the
        executor moves off it (SUCCESS advances the path in the same tick, so the tick log never shows it), and a
        flight is counted when the executor's flight appears."""
        last = self._last_movement
        if last is not movement:
            if isinstance(last, MovementParkour):
                self.parkour.append({"src": list(last.src), "dest": list(last.dest), "dist": last.dist, "ascend": last.ascend,
                                     "status": last.status})
            self._last_movement = movement
        flight = ex.flight if ex is not None else None
        if flight is not None and flight is not self._last_flight:   # a landing plans the next hop in the same tick
            self.flights += 1
        self._last_flight = flight

    def record(self, pl, behavior):
        """One line of the tick log, and the "Breaking" and "Placing" announcements as a movement starts them."""
        ex = behavior.current
        movement = ex.current_movement() if ex is not None else None
        status = movement.status if movement is not None else None
        self.note_movement(movement, ex)
        if movement is not None and movement is not self._said_movement:
            ctx = behavior.context
            if movement.to_break(ctx):
                kind = ctx.kind(*movement.to_break(ctx)[0]).type_id.split(":")[-1]
                self.say(f"Breaking {kind}")
                self._said_movement = movement
            elif movement.to_place(ctx):
                self.say("Placing a block")
                self._said_movement = movement
        if self.on_tick is None:
            return
        last = self.body.last
        hand = pl.hand or {}
        self.on_tick({"t": round(time.time(), 3), "tick": pl.tick, "x": round(pl.x, 3), "y": round(pl.y, 3), "z": round(pl.z, 3),
                      "feet": list(pl.feet), "yaw": round(pl.yaw, 1), "pitch": round(pl.pitch, 1), "ground": pl.ground,
                      "water": pl.water, "sneak": pl.sneak, "sprint": pl.sprint, "left": list(last["left"]),
                      "right": list(last["right"]), "held": list(last["held"]), "item": hand.get("type"),
                      "movement": f"{movement.name} {movement.src}->{movement.dest}" if movement is not None else None,
                      "status": status, "pos": ex.path_position if ex is not None else None,
                      "length": ex.path.length() if ex is not None else None,
                      "planning": behavior.in_progress is not None, "flight": bool(ex is not None and ex.flight is not None)})

    # ------------------------------------------------------------------------------------------------- look

    def look_at(self, point, entity=None, block=None, timeout=6.0, tolerance=2.0):
        """Turn the camera until the eyes point at point (x, y, z), or a callable returning it, or, with entity (an
        id) or block ((x, y, z)), until the probe says the crosshair is on it. True once on target."""
        return self.body.look_at(point, block=block, entity=entity, timeout_ticks=max(1, int(timeout * 20)), tolerance=tolerance)

    # ----------------------------------------------------------------------------------------------- gather

    def nearest_exposed(self, kind, pl, radius=24, exclude=()):
        """The nearest block of a type within radius of the player, in the probe's bands, with a walk-through
        neighbour above or beside it (so a hand can reach it), and not in exclude (the ones gather gave up on)."""
        best, best_d = None, None
        fx, fy, fz = pl.feet
        exclude = set(exclude)
        for band in list(self.terrain.bands.values()):
            if band.dim != self.dimension:
                continue
            codes = [i for i, k in enumerate(band.kinds) if k.type_id == kind]
            if not codes:
                continue
            if abs(band.cx * 16 + 8 - fx) > radius + 8 or abs(band.cz * 16 + 8 - fz) > radius + 8:
                continue
            wanted = set(codes)
            h = band.height
            for i, code in enumerate(band.data):
                if code not in wanted:
                    continue
                col, dy = divmod(i, h)
                x, z = band.cx * 16 + col // 16, band.cz * 16 + col % 16
                y = band.y0 + dy
                d = math.hypot(x - fx, z - fz) + abs(y - fy) * 2
                if d > radius or (best_d is not None and d >= best_d) or (x, y, z) in exclude:
                    continue
                exposed = False
                for nx, ny, nz in ((x, y + 1, z), (x + 1, y, z), (x - 1, y, z), (x, y, z + 1), (x, y, z - 1)):
                    if self.terrain.kind(nx, ny, nz).flags & WALK_THROUGH:
                        exposed = True
                        break
                if exposed:
                    best, best_d = (x, y, z), d
        return best

    def gather(self, kind="minecraft:dirt", count=16, timeout=120):
        """Walk to the nearest exposed block of that kind in the probe's bands, break it by hand, walk over the drop,
        and repeat until the hotbar holds count of it or the time is up. Returns how many it holds."""
        deadline = time.time() + timeout
        short = kind.split(":")[-1]
        self.say(f"Gathering {short}")
        skipped = set()
        while time.time() < deadline:
            have = self.count(kind)
            if have >= count:
                self.say(f"Gathered {have} {short}")
                return have
            pl = self.report()
            if pl is None:
                return have
            self.terrain.section(pl.feet[0], pl.feet[1], pl.feet[2])
            target = self.nearest_exposed(kind, pl, exclude=skipped)
            if target is None:
                self.log(f"gather: no exposed {short} within the bands")
                return have
            self.log(f"gather: {have}/{count} {short}, next {target}")
            self.go(GoalGetToBlock(*target), timeout=min(60, max(5, deadline - time.time())))
            pl = self.report()
            if pl is None:
                return have
            if math.dist((pl.x, pl.y, pl.z), center(target)) > 4.5:
                skipped.add(target)
                continue
            if not self.body.look_at(center(target), block=target, timeout_ticks=80):
                skipped.add(target)
                continue
            if not self.break_block(target, deadline):
                skipped.add(target)
                continue
            # walk over the drop: into the block's own cell (a hole when it was the floor)
            self.go(GoalBlock(*target), timeout=min(20, max(3, deadline - time.time())))
            have_now = self.count(kind)
            if have_now <= have:
                self.go(GoalNear(target[0], target[1], target[2], 1), timeout=min(10, max(3, deadline - time.time())))
        return self.count(kind)

    def break_block(self, target, deadline, max_ticks=400):
        """RT held with the crosshair on the block until the probe's break event (stone by hand is about 150
        ticks). True on the event."""
        self.probe.events()
        last_tick = None
        held, on_target = False, 0
        # the points aimed at, in turn: the centre, then the top face, then the near faces. The probe's crosshair is
        # the server's ray; the client's own can differ by a block at an edge, and then RT on the "right" block
        # breaks nothing, so after 60 ticks on target with no event (dirt takes 15) the next point is tried
        c = center(target)
        points = [c, (c[0], c[1] + 0.45, c[2]), (c[0] + 0.45, c[1], c[2]), (c[0] - 0.45, c[1], c[2]),
                  (c[0], c[1], c[2] + 0.45), (c[0], c[1], c[2] - 0.45)]
        try:
            for _ in range(max_ticks):
                if time.time() > deadline:
                    return False
                pl = self.body.next_report(last_tick, timeout=1.0)
                if pl is None:
                    continue
                last_tick = pl.tick
                self.body.begin(pl)
                for e in self.probe.events():
                    if e.get("type") == "break" and (e.get("x"), e.get("y"), e.get("z")) == tuple(target):
                        return True
                aim = points[min(on_target // 60, len(points) - 1)]
                want = rotation_from(pl.eye, aim)
                if not pl.is_looking_at(target) or not pl.rotation.is_really_close_to(want, 3.0):
                    self.body._state((0.0, 0.0), self.body.aim(pl, want.yaw, want.pitch, 1.0), ("rt",) if held else ())
                    continue
                held = True
                on_target += 1
                self.body._state((0.0, 0.0), (0.0, 0.0), ("rt",))
            return False
        finally:
            self.body.release()


__all__ = ["Navigator"]
