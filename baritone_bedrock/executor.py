"""PathExecutor (java/baritone/src/main/java/baritone/pathing/path/PathExecutor.java, "PE") and PathingBehavior
(behavior/PathingBehavior.java, "PB"), ported line by line, with docs/research-movements.md section 10 and
docs/research-settings.md A1 as the sheet.

One executor tick per probe report (Navigator feeds `PathingBehavior.on_tick(TickState)` every game tick): the feet
against the current movement's valid positions with the back-skip and the forward-skip (PE:103-131), possiblyOffPath
(2 blocks for 200 ticks, 3 at once, flat distance during a Fall), the toBreak/toPlace/toWalkInto bookkeeping, the
cost re-verification of the next costVerificationLookahead movements, maxCostIncrease, movementTimeoutTicks past the
original estimate, shouldPause while the plan in progress backtracks, the movement's own tick, then the sprint policy
of shouldSprintNextTick (PE:348-502). The behavior runs the segments: a fresh path from pathStart(), planning ahead
from the current path's end once ticksRemainingInSegment drops under planningTickLookahead, splicing, snipsnap, the
timeouts per settings, and the calculations on a worker thread with the search cancelled when it no longer starts
where the player is.

Where the port departs from the Java it says so with "departs:": the controller cannot set keys or the rotation
directly (control.py takes the MovementState instead), head hitting is off, and there is no frost walker, bucket or
door state. SprintJump (settings.sprintJumping) is sprintjump.py: PE:362-366 plans a hop from shouldSprintNextTick,
PE:507-525 fly() steers it until the player comes down, PE:527-535 steer() is the one place the executor hands the
Body a state of its own rather than a movement's.
"""
import threading
import time

from .astar import AStarPathFinder, Favoring, PathCalculationResult
from .control import LOOK_AHEAD, MovementState, Rotation, yaw_to
from .costs import COST_INF
from .goals import GoalXZ
from .helpers import can_walk_on, can_walk_through, fully_passable, avoid_walking_into
from .moves import (MovementStatus, MovementTraverse, MovementAscend, MovementDescend, MovementFall, MovementDiagonal,
                    MovementParkour)
from .path import SplicedPath, CutoffPath
from .sprintjump import SprintJump, GROUND_FRICTION, MAX_TICKS as FLIGHT_MAX_TICKS
from .world import LIQUID


class PathEvent:
    """api/event/events/PathEvent.java."""
    CALC_STARTED = "CALC_STARTED"
    CALC_FINISHED_NOW_EXECUTING = "CALC_FINISHED_NOW_EXECUTING"
    CALC_FAILED = "CALC_FAILED"
    NEXT_SEGMENT_CALC_STARTED = "NEXT_SEGMENT_CALC_STARTED"
    NEXT_SEGMENT_CALC_FINISHED = "NEXT_SEGMENT_CALC_FINISHED"
    CONTINUING_ONTO_PLANNED_NEXT = "CONTINUING_ONTO_PLANNED_NEXT"
    SPLICING_ONTO_NEXT_EARLY = "SPLICING_ONTO_NEXT_EARLY"
    AT_GOAL = "AT_GOAL"
    PATH_FINISHED_NEXT_STILL_CALCULATING = "PATH_FINISHED_NEXT_STILL_CALCULATING"
    NEXT_CALC_FAILED = "NEXT_CALC_FAILED"
    DISCARD_NEXT = "DISCARD_NEXT"
    CANCELED = "CANCELED"


class TickState:
    """What one tick hands the executor and the movements: the player (control.Player), the live
    CalculationContext, the Body, the settings, pathStart() and this tick's probe events."""
    __slots__ = ("player", "ctx", "body", "settings", "path_start", "events", "log")

    def __init__(self, player, ctx, body, settings, path_start=None, events=(), log=None):
        self.player, self.ctx, self.body, self.settings = player, ctx, body, settings
        self.path_start = path_start
        self.events = list(events)
        self.log = log or (lambda text: None)


def _same_flat_direction(a, b):
    """PE:408-409: next continues in the descend's flat direction (the cross product of current.direction.above()
    and next.direction is zero and their sum is not)."""
    ax, az = a[0], a[2]
    bx, bz = b[0], b[2]
    if (ax + bx, az + bz) == (0, 0):
        return False
    return ax * bz - az * bx == 0 and (ax * bx + az * bz) > 0


class PathExecutor:
    """PE: executes one path, a movement at a time."""

    MAX_MAX_DIST_FROM_PATH = 3.0      # PE:50
    MAX_DIST_FROM_PATH = 2.0          # PE:51
    MAX_TICKS_AWAY = 200              # PE:60: 10 seconds, at least 5.5 s (issue #102)

    def __init__(self, behavior, path):
        self.behavior = behavior                                             # PE:80-85
        self.path = path
        self.path_position = 0
        self.ticks_away = 0
        self.ticks_on_current = 0
        self.current_movement_original_cost_estimate = None
        self.cost_estimate_index = None
        self._failed = False
        self.recalc_bp = True
        self.to_break_set, self.to_place_set, self.to_walk_into_set = set(), set(), set()
        self.sprint_next_tick = False
        self.pending = None          # the MovementState to apply at the end of the tick (the last movement ticked)
        self.pending_movement = None
        self.pending_sprint = None
        self.pending_look = None     # a camera point of the executor's own (the flight), else the pursuit point
        self.jump_override = None    # PE:377, 460: a JUMP forced on or off after the movement's own inputs
        self.flight = None           # PE:78: the SprintJump in the air, or None
        self.flight_state = MovementState(MovementStatus.RUNNING)   # the keys steer() presses
        self.s = None

    # --------------------------------------------------------------------------------------------- the tick

    def on_tick(self, s):
        """PE:93-257. True when a movement just finished (a stable state), the way PathingBehavior reads it."""
        self.s = s
        if self.flight is not None and self.fly(s):                          # PE:94-96
            return False                 # mid air is not a stable state, no matter what the movement we left behind thinks
        if self.path_position == self.path.length() - 1:                     # PE:97-99
            self.path_position += 1
        if self.path_position >= self.path.length():                         # PE:100-102
            return True
        movements = self.path.movements()
        movement = movements[self.path_position]                             # PE:103
        where_am_i = s.player.feet                                           # PE:104
        if where_am_i not in movement.valid_positions:                       # PE:105
            for i in range(min(self.path_position, self.path.length())):     # PE:106-117: lagged back a few blocks
                if where_am_i in movements[i].valid_positions:
                    previous = self.path_position
                    self.path_position = i
                    for j in range(self.path_position, previous + 1):
                        movements[j].reset()
                    self.on_change_in_path_position()
                    self.on_tick(s)
                    return False
            for i in range(self.path_position + 3, self.path.length() - 1):  # PE:118-130: skip forward, not +1 or +2
                if where_am_i in movements[i].valid_positions:
                    if i - self.path_position > 2:
                        s.log(f"Skipping forward {i - self.path_position} steps, to {i}")
                    self.path_position = i - 1
                    self.on_change_in_path_position()
                    self.on_tick(s)
                    return False
        status = self.closest_path_pos(s.player)                             # PE:132
        if self.possibly_off_path(s.player, status, self.MAX_DIST_FROM_PATH):   # PE:133-143
            self.ticks_away += 1
            if self.ticks_away > self.MAX_TICKS_AWAY:
                s.log("Too far away from path for too long, cancelling path")
                self.cancel()
                return False
        else:
            self.ticks_away = 0
        if self.possibly_off_path(s.player, status, self.MAX_MAX_DIST_FROM_PATH):   # PE:144-148
            s.log(f"too far from path ({status[0]:.1f})")
            self.cancel()
            return False
        ctx = s.ctx
        for i in range(self.path_position - 10, self.path_position + 10):    # PE:150-169: the bookkeeping
            if i < 0 or i >= len(movements):
                continue
            m = movements[i]
            prev_break, prev_place, prev_walk = list(m.to_break(ctx)), list(m.to_place(ctx)), list(m.to_walk_into(ctx))
            m.reset_block_cache()
            if prev_break != m.to_break(ctx) or prev_place != m.to_place(ctx) or prev_walk != m.to_walk_into(ctx):
                self.recalc_bp = True
        if self.recalc_bp:                                                   # PE:170-184
            new_break, new_place, new_walk = set(), set(), set()
            for m in movements[self.path_position:]:
                new_break.update(m.to_break(ctx))
                new_place.update(m.to_place(ctx))
                new_walk.update(m.to_walk_into(ctx))
            self.to_break_set, self.to_place_set, self.to_walk_into_set = new_break, new_place, new_walk
            self.recalc_bp = False
        if self.path_position < len(movements) - 1:                          # PE:189-196
            nxt = movements[self.path_position + 1]
            if not ctx.is_loaded(nxt.dest[0], nxt.dest[2]):
                s.log("Pausing since destination is at edge of loaded chunks")
                self.clear_keys()
                return True
        can_cancel = movement.safe_to_cancel(s)                              # PE:197
        if self.cost_estimate_index is None or self.cost_estimate_index != self.path_position:   # PE:198-209
            self.cost_estimate_index = self.path_position
            # only once, when the movement starts, and deliberately the cost as cached when the path was calculated
            self.current_movement_original_cost_estimate = movement.get_cost()
            if self.current_movement_original_cost_estimate is None:
                self.current_movement_original_cost_estimate = movement.get_cost(ctx)
            lookahead = s.settings.costVerificationLookahead
            for i in range(1, lookahead):
                if self.path_position + i >= self.path.length() - 1:
                    break
                if movements[self.path_position + i].calculate_cost(ctx) >= COST_INF and can_cancel:
                    s.log("Something has changed in the world and a future movement has become impossible. Cancelling.")
                    self.cancel()
                    return True
        current_cost = movement.recalculate_cost(ctx)                        # PE:210
        if current_cost >= COST_INF and can_cancel:                          # PE:211-215
            s.log("Something has changed in the world and this movement has become impossible. Cancelling.")
            self.cancel()
            return True
        if (not movement.calculated_while_loaded and self.current_movement_original_cost_estimate is not None
                and current_cost - self.current_movement_original_cost_estimate > s.settings.maxCostIncrease and can_cancel):   # PE:216-222
            s.log(f"Original cost {self.current_movement_original_cost_estimate} current cost {current_cost}. Cancelling.")
            self.cancel()
            return True
        if self.should_pause(s):                                             # PE:223-227
            s.log("Pausing since current best path is a backtrack")
            self.clear_keys()
            return True
        movement_status = movement.tick(s)                                   # PE:228 movement.update()
        self.pending, self.pending_movement, self.pending_sprint = movement.current, movement, None
        if movement_status in (MovementStatus.UNREACHABLE, MovementStatus.FAILED):   # PE:229-233
            s.log(f"Movement returns status {movement_status}")
            self.cancel()
            return True
        if movement_status == MovementStatus.SUCCESS:                        # PE:234-239
            self.path_position += 1
            self.on_change_in_path_position()
            self.on_tick(s)
            return True
        self.sprint_next_tick = self.should_sprint_next_tick(s)              # PE:241
        if self.pending is movement.current:
            self.pending_sprint = self.sprint_next_tick                      # PE:242-244: the executor decides sprint
        self.ticks_on_current += 1                                           # PE:245
        if self.current_movement_original_cost_estimate is not None and \
                self.ticks_on_current > self.current_movement_original_cost_estimate + s.settings.movementTimeoutTicks:   # PE:246-254
            s.log(f"This movement has taken too long ({self.ticks_on_current} ticks, expected "
                  f"{self.current_movement_original_cost_estimate:.1f}). Cancelling.")
            self.cancel()
            return True
        return can_cancel and self.flight is None                            # PE:256

    def closest_path_pos(self, player):
        """PE:259-272: the nearest valid position of any movement, by distance from the player to its centre."""
        best, best_pos = -1.0, None
        for movement in self.path.movements():
            for pos in movement.valid_positions:
                dist = player.distance_to_center(pos)
                if dist < best or best == -1:
                    best, best_pos = dist, pos
        return (best, best_pos)

    def should_pause(self, s):
        """PE:274-305: pause when the search in progress already runs through the player's feet (a backtrack)."""
        current = self.behavior.in_progress
        if current is None:
            return False
        pl, ctx = s.player, s.ctx
        if not pl.ground:
            return False
        feet = pl.feet
        if not can_walk_on(ctx, feet[0], feet[1] - 1, feet[2]):              # PE:282-285: sketchy, maybe parkouring
            return False
        if not can_walk_through(ctx, *feet) or not can_walk_through(ctx, feet[0], feet[1] + 1, feet[2]):   # PE:286-289
            return False
        if not self.path.movements()[self.path_position].safe_to_cancel(s):  # PE:290-292
            return False
        try:
            best = current.best_path_so_far()                                # PE:293
        except Exception:
            return False
        if best is None:
            return False
        positions = best.positions()
        if len(positions) < 3:                                               # PE:298-300
            return False
        return feet in positions[1:]                                         # PE:301-304

    def possibly_off_path(self, player, status, leniency):
        """PE:307-320: beyond leniency from every valid position; a Fall is judged by flat distance to its dest."""
        if status[0] > leniency:
            movement = self.path.movements()[self.path_position]
            if isinstance(movement, MovementFall):                           # PE:311-313
                fall_dest = self.path.positions()[self.path_position + 1]
                return player.flat_distance_to_center(fall_dest) >= leniency
            return True
        return False

    def snipsnapifpossible(self, s):
        """PE:327-346: snap pathPosition to the player's feet when they are on this path and the player is not
        falling."""
        pl = s.player
        if not pl.ground and not (s.ctx.at(*pl.feet) & LIQUID):             # PE:328-330
            return False
        if pl.vy < -0.1:                                                     # PE:333-337
            return False
        try:
            index = self.path.positions().index(pl.feet)                     # PE:339
        except ValueError:
            return False
        self.path_position = index                                           # PE:343
        self.clear_keys()
        return True

    # ------------------------------------------------------------------------------------ the sprint policy

    def should_sprint_next_tick(self, s):
        """PE:348-502 shouldSprintNextTick: the SPRINT a movement requested is consumed and the executor decides,
        knowing the movement after this one."""
        ms = self.pending
        requested = bool(ms is not None and ms.is_down("sprint"))            # PE:349
        if ms is not None:
            ms.set_input("sprint", False)                                    # PE:352: we'll take it from here
        pl, ctx = s.player, s.ctx
        if not s.settings.allowSprint or pl.food <= 6:                       # PE:357-359
            return False
        movements = self.path.movements()
        current = movements[self.path_position]                              # PE:360
        if s.settings.sprintJumping and not (ms is not None and ms.is_down("sneak")):   # PE:362-363
            self.flight = SprintJump.plan(pl, ctx, self.path, self.path_position, s.settings,
                                          ground_accel=s.body.sprint_speed * (1 - GROUND_FRICTION),
                                          lead_ticks=s.body.jump_lead,
                                          velocity_to_displacement=s.body.velocity_to_displacement)
            if self.flight is not None:
                self.steer(s, True)                                          # PE:364-365
                return True
        if isinstance(current, MovementTraverse) and self.path_position < self.path.length() - 3:   # PE:369
            nxt = movements[self.path_position + 1]
            if isinstance(nxt, MovementAscend) and self.sprintable_ascend(ctx, current, nxt, movements[self.path_position + 2]):   # PE:371
                if self.skip_now(ctx, pl, current):                          # PE:372-378
                    s.log("Skipping traverse to straight ascend")
                    self.path_position += 1
                    self.on_change_in_path_position()
                    self.on_tick(s)
                    self.jump_override = True                                # PE:377: JUMP forced on
                    return True
                s.log("Too far to the side to safely sprint ascend")
        if requested:                                                        # PE:386-391
            # PE:387-389 headHitters: off (settings.py), phase 3
            return True
        if isinstance(current, MovementDescend):                             # PE:394
            # PE:396-415 frost walker forceSafeMode: canUseFrostWalker is never true here (plan, Fidelity 6)
            if current.safe_mode(ctx) and not current.skip_to_ascend(ctx):   # PE:416-419
                return False
            if self.path_position < self.path.length() - 2:                  # PE:421
                nxt = movements[self.path_position + 1]
                d, n = current.direction, nxt.direction
                if isinstance(nxt, MovementAscend) and (d[0], d[1] + 1, d[2]) == (n[0], n[1] - 1, n[2]):   # PE:423-431
                    self.path_position += 1
                    self.on_change_in_path_position()
                    self.on_tick(s)
                    s.log("Skipping descend to straight ascend")
                    return True
                if self.can_sprint_from_descend_into(ctx, current, nxt):     # PE:432
                    if isinstance(nxt, MovementDescend) and self.path_position < self.path.length() - 3:   # PE:434-440
                        next_next = movements[self.path_position + 2]
                        if isinstance(next_next, MovementDescend) and not self.can_sprint_from_descend_into(ctx, nxt, next_next):
                            return False
                    if pl.feet == current.dest:                              # PE:441-445
                        self.path_position += 1
                        self.on_change_in_path_position()
                        self.on_tick(s)
                    return True
        if isinstance(current, MovementAscend) and self.path_position != 0:  # PE:452
            prev = movements[self.path_position - 1]
            d, p = current.direction, prev.direction
            if isinstance(prev, MovementDescend) and (p[0], p[1] + 1, p[2]) == (d[0], d[1] - 1, d[2]):   # PE:454
                center_y = current.src[1] + 1                                # PE:455
                if pl.y >= center_y - 0.07:                                  # PE:459-462: 0.07 for farmland
                    self.jump_override = False
                    return True
            if (self.path_position < self.path.length() - 2 and isinstance(prev, MovementTraverse)
                    and self.sprintable_ascend(ctx, prev, current, movements[self.path_position + 1])):   # PE:464-466
                return True
        if isinstance(current, MovementFall):                                # PE:468-500
            data = self.override_fall(ctx, current)
            if data is not None:
                point, fall_dest = data
                if fall_dest not in self.path.positions():                   # PE:472-476
                    raise RuntimeError(f"Fall override at {current.src} returned illegal destination {fall_dest}")
                if pl.feet == fall_dest:                                     # PE:477-482
                    self.path_position = self.path.positions().index(fall_dest)
                    self.on_change_in_path_position()
                    self.on_tick(s)
                    return True
                self.clear_keys()                                            # PE:483
                # PE:488-495 openDoors: departs. PE:496-497: we are not ticking the movement, so look at the point
                # and walk forward ourselves (clearKeys dropped the movement's own keys; these are set after it)
                ms = current.current
                ms.clear()
                current.move_towards_point(s, point)
                self.pending, self.pending_movement, self.pending_sprint = ms, current, True
                return True
        return False                                                         # PE:501

    # ------------------------------------------------------------------------------------------- the flight

    def fly(self, s):
        """PE:504-525: True if we're still in the air and this tick is handled. departs: a jump tapped from a report
        shows in the one after (the latency), so the flight is kept through its first FLIGHT_GRACE reports on the
        ground; a jump that never happened ends it after those."""
        pl, fl = s.player, self.flight
        airborne = not pl.ground or fl.ticks <= self.FLIGHT_GRACE
        if (airborne and not pl.water and not pl.climbing and fl.ticks < FLIGHT_MAX_TICKS
                and s.settings.sprintJumping):                                # PE:508-509
            self.steer(s, False)                                             # PE:510-512
            self.sprint_next_tick = True
            return True
        # PE:514-521: we probably flew over a few movements, pick up at whichever one we came down in (diagonal side
        # cells count). anywhere else and the usual valid positions / off path recovery takes it from here
        movements = self.path.movements()
        for i in range(self.path_position, min(len(movements), self.path_position + len(fl.floors))):
            if pl.feet in movements[i].valid_positions:
                self.path_position = i
                break
        self.flight = None                                                   # PE:522-524
        self.on_change_in_path_position()
        return False

    FLIGHT_GRACE = 2     # reports still on the ground after the takeoff tap before the flight is given up on

    def steer(self, s, takeoff):
        """PE:527-535: the movements steer at their own dest and drop W the moment our feet are a block up ("Wrong Y
        coordinate"), which was the mid air stall, so while we're up here nobody else gets a say. space only on
        takeoff: letting go in the air resets the vanilla 10 tick jump delay, holding it made a jump up a step wait
        on landing. departs: the yaw Java sets each tick is the left stick's direction here (the push, tilted to kill
        drift), with the camera led along the line, both through Body.apply."""
        pl, fl = s.player, self.flight
        self.clear_keys()                                                    # PE:531
        push = fl.steer(pl)                                                  # PE:532
        ms = self.flight_state
        ms.clear()
        ms.status = MovementStatus.RUNNING
        ms.move_to = (pl.x + push[0] * 2.0, pl.y, pl.z + push[1] * 2.0)
        ms.set_target(Rotation(yaw_to(fl.ux, fl.uz), None), False)
        ms.set_input("forward", True)                                        # PE:533
        ms.set_input("jump", bool(takeoff))                                  # PE:534
        self.pending, self.pending_movement, self.pending_sprint = ms, None, True
        self.pending_look = fl.look_point(pl)

    @staticmethod
    def skip_now(ctx, pl, current):
        """PE:650-663: centred on the traverse's line, and clear of a head bonk behind."""
        d = current.direction
        off_target = abs(d[0] * (current.src[2] + 0.5 - pl.z)) + abs(d[2] * (current.src[0] + 0.5 - pl.x))   # PE:651
        if off_target > 0.1:                                                 # PE:652-654
            return False
        head_bonk = (current.src[0] - d[0], current.src[1] + 2, current.src[2] - d[2])   # PE:656
        if fully_passable(ctx, *head_bonk):                                  # PE:657-659
            return True
        flat = abs(d[0] * (head_bonk[0] + 0.5 - pl.x)) + abs(d[2] * (head_bonk[2] + 0.5 - pl.z))   # PE:661
        return flat > 0.8                                                    # PE:662

    @staticmethod
    def sprintable_ascend(ctx, current, nxt, next_next):
        """PE:665-699."""
        if not ctx.settings.sprintAscends:                                   # PE:666-668
            return False
        d, n, nn = current.direction, nxt.direction, next_next.direction
        if (d[0], d[1], d[2]) != (n[0], n[1] - 1, n[2]):                     # PE:669-671
            return False
        if nn[0] != n[0] or nn[2] != n[2]:                                   # PE:672-674
            return False
        if not can_walk_on(ctx, current.dest[0], current.dest[1] - 1, current.dest[2]):   # PE:675-677
            return False
        if not can_walk_on(ctx, nxt.dest[0], nxt.dest[1] - 1, nxt.dest[2]):   # PE:678-680
            return False
        if nxt.to_break(ctx):                                                # PE:681-683: it's breaking
            return False
        for x in range(2):                                                   # PE:684-694: a 2x3 passable corridor
            for y in range(3):
                chk = (current.src[0], current.src[1] + y, current.src[2])
                if x == 1:
                    chk = (chk[0] + d[0], chk[1], chk[2] + d[2])
                if not fully_passable(ctx, *chk):
                    return False
        if avoid_walking_into(ctx.at(current.src[0], current.src[1] + 3, current.src[2])):   # PE:695-697
            return False
        return not avoid_walking_into(ctx.at(nxt.dest[0], nxt.dest[1] + 2, nxt.dest[2]))   # PE:698

    @staticmethod
    def can_sprint_from_descend_into(ctx, current, nxt):
        """PE:701-712."""
        if isinstance(nxt, MovementDescend) and nxt.direction == current.direction:   # PE:702-704
            return True
        d = current.direction
        if not can_walk_on(ctx, current.dest[0] + d[0], current.dest[1] + d[1], current.dest[2] + d[2]):   # PE:705-707
            return False
        # departs: PE:708 compares the whole direction vectors, (dx, -1, dz) against a traverse's (dx, 0, dz), which
        # never match, so as written a traverse after a descend never keeps the sprint; the sheet (section 10) and
        # the comment at PE:393 mean the same flat direction, which is what is compared here
        n = nxt.direction
        if isinstance(nxt, MovementTraverse) and (n[0], n[2]) == (d[0], d[2]):   # PE:708-710
            return True
        return isinstance(nxt, MovementDiagonal) and ctx.settings.allowOvershootDiagonalDescend   # PE:711

    def override_fall(self, ctx, movement):
        """PE:611-648: steer a short fall on through the same-direction traverses after it."""
        d = movement.direction
        if d[1] < -3:                                                        # PE:613-615
            return None
        if movement.to_break(ctx):                                           # PE:616-618
            return None
        flat = (d[0], 0, d[2])
        movements = self.path.movements()
        i = self.path_position + 1
        while i < self.path.length() - 1 and i < self.path_position + 3:     # PE:622
            nxt = movements[i]
            if not isinstance(nxt, MovementTraverse) or nxt.direction != flat:   # PE:624-629
                break
            ok = True
            for y in range(nxt.dest[1], movement.src[1] + 2):                # PE:630-635
                if not fully_passable(ctx, nxt.dest[0], y, nxt.dest[2]):
                    ok = False
                    break
            if not ok:
                break
            if not can_walk_on(ctx, nxt.dest[0], nxt.dest[1] - 1, nxt.dest[2]):   # PE:636-638
                break
            i += 1
        i -= 1                                                               # PE:640
        if i == self.path_position:                                          # PE:641-643
            return None
        length = i - self.path_position - 0.4                                # PE:644
        dest = movement.dest
        point = (flat[0] * length + dest[0] + 0.5, dest[1], flat[2] * length + dest[2] + 0.5)
        fall_dest = (dest[0] + flat[0] * (i - self.path_position), dest[1], dest[2] + flat[2] * (i - self.path_position))
        return (point, fall_dest)

    # ---------------------------------------------------------------------------------------- the pursuit point

    def look_point(self, player):
        """The pure pursuit point for the camera (human.js:188-203 carrot): LOOK_AHEAD blocks along the path's
        block centres from the player's projection, never past the end of the current straight run (a turn or a
        height change clips it, nav.py's rule), so the body turns at the corner and the camera leads it there."""
        positions = self.path.positions()
        movements = self.path.movements()
        i = self.path_position
        if i >= len(movements):
            return None
        end = i
        d0 = movements[i].direction
        if d0[1] != 0:
            return None                                                      # an ascend or descend aims itself
        while end + 1 < len(movements) and movements[end + 1].direction == d0:
            end += 1
        remaining = LOOK_AHEAD
        fx, fz = player.x, player.z
        for k in range(i + 1, end + 2):
            to = positions[k]
            tx, tz = to[0] + 0.5, to[2] + 0.5
            seg_x, seg_z = tx - fx, tz - fz
            length = (seg_x * seg_x + seg_z * seg_z) ** 0.5
            if length >= remaining and length > 0:
                t = remaining / length
                return (fx + seg_x * t, to[1], fz + seg_z * t)
            remaining -= length
            fx, fz = tx, tz
        last = positions[end + 1]
        return (last[0] + 0.5, last[1], last[2] + 0.5)

    # ------------------------------------------------------------------------------------------ bookkeeping

    def on_change_in_path_position(self):
        """PE:714-717."""
        self.clear_keys()
        self.ticks_on_current = 0

    def clear_keys(self):
        """PE:719-722 clearAllKeys: nothing is applied at the end of this tick."""
        self.pending, self.pending_movement, self.pending_sprint = None, None, None
        self.pending_look = None
        self.jump_override = None

    def cancel(self):
        """PE:724-729."""
        self.clear_keys()
        self.path_position = self.path.length() + 3
        self._failed = True

    def get_position(self):
        return self.path_position

    def try_splice(self, nxt):
        """PE:736-754."""
        if nxt is None:
            return self.cut_if_too_long()
        spliced = SplicedPath.try_splice(self.path, nxt.path, False)
        if spliced is None:
            return self.cut_if_too_long()
        if spliced.get_dest() != nxt.path.get_dest():
            raise RuntimeError(f"Path has end {spliced.get_dest()} instead of {nxt.path.get_dest()} after splicing")
        ret = PathExecutor(self.behavior, spliced)
        ret.path_position = self.path_position
        ret.current_movement_original_cost_estimate = self.current_movement_original_cost_estimate
        ret.cost_estimate_index = self.cost_estimate_index
        ret.ticks_on_current = self.ticks_on_current
        ret.pending, ret.pending_movement, ret.pending_sprint = self.pending, self.pending_movement, self.pending_sprint
        ret.pending_look = self.pending_look
        ret.flight, ret.flight_state = self.flight, self.flight_state      # PE:751: a new path showing up doesn't make us any less airborne
        return ret

    def cut_if_too_long(self):
        """PE:756-777."""
        settings = self.behavior.settings
        if self.path_position > settings.maxPathHistoryLength:
            cutoff = settings.pathHistoryCutoffAmount
            new_path = CutoffPath(self.path, self.path.length() - 1, first_position_to_include=cutoff)
            if new_path.get_dest() != self.path.get_dest():
                raise RuntimeError(f"Path has end {new_path.get_dest()} instead of {self.path.get_dest()} after trimming its start")
            self.behavior.log(f"Discarding earliest segment movements, length cut from {self.path.length()} to {new_path.length()}")
            ret = PathExecutor(self.behavior, new_path)
            ret.path_position = self.path_position - cutoff
            ret.current_movement_original_cost_estimate = self.current_movement_original_cost_estimate
            if self.cost_estimate_index is not None:
                ret.cost_estimate_index = self.cost_estimate_index - cutoff
            ret.ticks_on_current = self.ticks_on_current
            ret.pending, ret.pending_movement, ret.pending_sprint = self.pending, self.pending_movement, self.pending_sprint
            ret.pending_look = self.pending_look
            ret.flight, ret.flight_state = self.flight, self.flight_state  # PE:773
            return ret
        return self

    def get_path(self):
        return self.path

    def failed(self):
        return self._failed

    def finished(self):
        return self.path_position >= self.path.length()

    def to_break(self):
        return frozenset(self.to_break_set)

    def to_place(self):
        return frozenset(self.to_place_set)

    def to_walk_into(self):
        return frozenset(self.to_walk_into_set)

    def current_movement(self):
        movements = self.path.movements()
        if 0 <= self.path_position < len(movements):
            return movements[self.path_position]
        return None

    def is_sprinting(self):
        return self.sprint_next_tick


class PathingBehavior:
    """PB: the goal, the current and next segments, the search in progress, and the tick that drives them.

    `context_factory()` returns a fresh CalculationContext for a search (the hotbar snapshot from the probe each
    segment); `self.context` is the live one the executor re-costs against, refreshed per segment and told of block
    changes. Events are queued (PB:81-92) and handed to `on_event(name)` at the next tick."""

    def __init__(self, context_factory, settings, log=print, on_event=None):
        self.context_factory = context_factory
        self.settings = settings
        self.log = log
        self.on_event = on_event or (lambda name: None)
        self.goal = None
        self.current = None
        self.next = None
        self.context = None
        self.in_progress = None
        self.path_calc_lock = threading.RLock()
        self.path_plan_lock = threading.RLock()
        self.safe_to_cancel = True
        self.pause_requested_last_tick = False
        self.unpaused_last_tick = False
        self.paused_this_tick = False
        self.cancel_requested = False
        self.calc_failed_last_tick = False
        self.expected_segment_start = None
        self.ticks_elapsed_so_far = 0
        self.start_position = None
        self.to_dispatch = []
        self.threads = []
        self.last_search = None      # (finder, result, elapsed ms) for the reports
        self.searches = []

    # ------------------------------------------------------------------------------------------- events

    def queue_path_event(self, event):
        self.to_dispatch.append(event)                                       # PB:81-83

    def dispatch_events(self):
        """PB:85-92."""
        with self.path_plan_lock:
            current, self.to_dispatch = self.to_dispatch, []
        self.calc_failed_last_tick = PathEvent.CALC_FAILED in current
        for event in current:
            self.on_event(event)

    # ------------------------------------------------------------------------------------------- the tick

    def on_tick(self, s):
        """PB:94-108."""
        self.dispatch_events()
        s.path_start = lambda: self.path_start(s)
        if self.context is None:
            self.context = self.context_factory()
        s.ctx = self.context
        if any(e.get("type") in ("break", "place") for e in s.events):
            self.context.block_changed()                                     # the pack already moved the band on
        self.expected_segment_start = self.path_start(s)                     # PB:103
        self.tick_path(s)                                                    # PB:105
        self.ticks_elapsed_so_far += 1
        self.dispatch_events()

    def tick_path(self, s):
        """PB:117-235."""
        self.paused_this_tick = False
        if self.pause_requested_last_tick and self.safe_to_cancel:           # PB:119-128
            self.pause_requested_last_tick = False
            if self.unpaused_last_tick:
                s.body.release()
            self.unpaused_last_tick = False
            self.paused_this_tick = True
            return
        self.unpaused_last_tick = True
        if self.cancel_requested:                                            # PB:130-133
            self.cancel_requested = False
            s.body.release()
        with self.path_plan_lock:
            with self.path_calc_lock:
                if self.in_progress is not None:                             # PB:136-148: are we calculating the right thing?
                    calc_from = self.in_progress.get_start()
                    try:
                        current_best = self.in_progress.best_path_so_far()
                    except Exception:
                        current_best = None
                    feet = s.player.feet
                    if ((self.current is None or self.current.path.get_dest() != calc_from)
                            and calc_from != feet and calc_from != self.expected_segment_start
                            and (current_best is None or (feet not in current_best.positions()
                                                          and self.expected_segment_start not in current_best.positions()))):
                        self.in_progress.cancel()                            # PB:146
            if self.current is None:                                         # PB:150-152
                s.body.release()
                return
            self.safe_to_cancel = self.current.on_tick(s)                    # PB:153
            if self.current.failed() or self.current.finished():             # PB:154
                self.current = None
                feet = s.player.feet
                if self.goal is None or self.goal.is_in_goal(*feet):          # PB:156-164
                    self.log(f"All done. At {self.goal}")
                    self.queue_path_event(PathEvent.AT_GOAL)
                    self.next = None
                    s.body.release()
                    return
                if (self.next is not None and feet not in self.next.path.positions()
                        and self.expected_segment_start not in self.next.path.positions()):   # PB:165-175
                    self.log("Discarding next path as it does not contain current position")
                    self.queue_path_event(PathEvent.DISCARD_NEXT)
                    self.next = None
                if self.next is not None:                                    # PB:176-183
                    self.log("Continuing on to planned next path")
                    self.queue_path_event(PathEvent.CONTINUING_ONTO_PLANNED_NEXT)
                    self.current, self.next = self.next, None
                    self.refresh_context()
                    self.current.on_tick(s)
                    self.apply(s)
                    return
                with self.path_calc_lock:                                    # PB:185-193
                    if self.in_progress is not None:
                        self.queue_path_event(PathEvent.PATH_FINISHED_NEXT_STILL_CALCULATING)
                        s.body.release()
                        return
                    self.queue_path_event(PathEvent.CALC_STARTED)
                    self.find_path_in_new_thread(self.expected_segment_start, True, s)
                s.body.release()
                return
            if self.safe_to_cancel and self.next is not None and self.next.snipsnapifpossible(s):   # PB:197-205
                self.log("Splicing into planned next path early...")
                self.queue_path_event(PathEvent.SPLICING_ONTO_NEXT_EARLY)
                self.current, self.next = self.next, None
                self.refresh_context()
                self.current.on_tick(s)
                self.apply(s)
                return
            if self.settings.splicePath:                                     # PB:206-208
                self.current = self.current.try_splice(self.next)
            if self.next is not None and self.current.path.get_dest() == self.next.path.get_dest():   # PB:209-211
                self.next = None
            self.apply(s)
            with self.path_calc_lock:                                        # PB:212-233
                if self.in_progress is not None:
                    return
                if self.next is not None:
                    return
                if self.goal is None or self.goal.is_in_goal(*self.current.path.get_dest()):
                    return
                if self.ticks_remaining_in_segment(False) < self.settings.planningTickLookahead:   # PB:225
                    self.log("Path almost over. Planning ahead...")
                    self.queue_path_event(PathEvent.NEXT_SEGMENT_CALC_STARTED)
                    self.find_path_in_new_thread(self.current.path.get_dest(), False, s)

    def apply(self, s):
        """The last movement ticked this tick hands its keys and target to the Body, with the executor's sprint
        decision and pursuit point (Movement.update's apply at M:163-171, after PE:241-244)."""
        ex = self.current
        if ex is None or ex.pending is None:
            s.body.release()
            return
        ms = ex.pending
        if ex.jump_override is not None:
            ms.set_input("jump", ex.jump_override)
            ex.jump_override = None
        look = ex.pending_look
        if look is None and ms.move_to is not None and not ms.force and ex.pending_movement is not None \
                and isinstance(ex.pending_movement, (MovementTraverse, MovementDiagonal)):
            look = ex.look_point(s.player)
        s.body.apply(s.player, ms, look=look, sprint=ex.pending_sprint)

    def refresh_context(self):
        """A new segment: the live context with the hotbar as it is now (the plan's Gathering section)."""
        self.context = self.context_factory()

    def ticks_remaining_in_segment(self, include_current=True):
        """PB ticksRemainingInSegment (IPathingBehavior:52-63)."""
        if self.current is None:
            return None
        start = self.current.path_position + (0 if include_current else 1)
        return self.current.path.ticks_remaining_from(start)

    # ------------------------------------------------------------------------------------------- goals

    def set_goal_and_path(self, goal, s):
        """PB:258-284 secretInternalSetGoalAndPath: True when a calculation was started."""
        self.goal = goal
        if self.context is None:
            self.context = self.context_factory()
        if goal is None:                                                     # PB:265-267
            return False
        if goal.is_in_goal(*s.player.feet):                                  # PB:268-270
            return False
        with self.path_plan_lock:
            if self.current is not None:                                     # PB:272-274
                return False
            with self.path_calc_lock:
                if self.in_progress is not None:                             # PB:276-278
                    return False
                self.queue_path_event(PathEvent.CALC_STARTED)
                self.expected_segment_start = self.path_start(s)
                self.find_path_in_new_thread(self.expected_segment_start, True, s)
                return True

    def is_pathing(self):
        return self.current is not None and not self.paused_this_tick        # PB:292-294

    def has_path(self):
        return self.current is not None

    def request_pause(self):
        self.pause_requested_last_tick = True                                # PB:318-320

    def cancel_everything(self):
        """PB:331-338 and secretInternalSegmentCancel (PB:358-369)."""
        self.queue_path_event(PathEvent.CANCELED)
        with self.path_plan_lock:
            if self.in_progress is not None:
                self.in_progress.cancel()
            self.current = None
            self.next = None
        return True

    def force_cancel(self):
        """PB:372-378."""
        self.cancel_everything()
        with self.path_calc_lock:
            self.in_progress = None

    def estimated_ticks_to_goal(self, s):
        """PB:384-403."""
        current_pos = s.player.feet
        if self.goal is None or self.start_position is None:
            return None
        if self.goal.is_in_goal(*current_pos):
            return 0.0
        if self.ticks_elapsed_so_far == 0:
            return None
        current = self.goal.heuristic(*current_pos)
        start = self.goal.heuristic(*self.start_position)
        if current == start:
            return None
        return abs(current - self.goal.heuristic_inside()) * self.ticks_elapsed_so_far / abs(start - current)

    # --------------------------------------------------------------------------------------- pathStart

    def path_start(self, s):
        """PB:423-461 pathStart (issue #209): the feet, or the block the player sneaks off, or the block below when
        falling."""
        pl, ctx = s.player, s.ctx if s.ctx is not None else self.context
        feet = pl.feet
        if not can_walk_on(ctx, feet[0], feet[1] - 1, feet[2]):              # PB:425
            if pl.ground:                                                    # PB:426-450
                closest = [(feet[0] + dx, feet[1], feet[2] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
                closest.sort(key=lambda p: ((p[0] + 0.5) - pl.x) ** 2 + ((p[2] + 0.5) - pl.z) ** 2)
                for support in closest[:4]:
                    x_dist = abs((support[0] + 0.5) - pl.x)
                    z_dist = abs((support[2] + 0.5) - pl.z)
                    if x_dist > 0.8 and z_dist > 0.8:                        # PB:440-443
                        continue
                    if (can_walk_on(ctx, support[0], support[1] - 1, support[2]) and can_walk_through(ctx, *support)
                            and can_walk_through(ctx, support[0], support[1] + 1, support[2])):   # PB:444-448
                        return support
            else:                                                            # PB:451-458: midair
                if can_walk_on(ctx, feet[0], feet[1] - 2, feet[2]):
                    return (feet[0], feet[1] - 1, feet[2])
        return feet                                                          # PB:460

    # ------------------------------------------------------------------------------------- the search

    def find_path_in_new_thread(self, start, talk_about_it, s):
        """PB:469-554: the search on a worker thread, its result folded in under the plan lock."""
        if self.in_progress is not None:
            raise RuntimeError("Already doing it")                           # PB:476-478
        goal = self.goal
        if goal is None:
            self.log("no goal")
            return
        if self.current is None:                                             # PB:489-495
            primary, failure = self.settings.primaryTimeoutMS, self.settings.failureTimeoutMS
        else:
            primary, failure = self.settings.planAheadPrimaryTimeoutMS, self.settings.planAheadFailureTimeoutMS
        ctx = self.context_factory()
        pathfinder = self.create_pathfinder(start, goal, None if self.current is None else self.current.path, ctx, s)
        if pathfinder.goal is not goal:                                      # PB:497-499
            self.log(f"Simplifying {type(goal).__name__} to GoalXZ due to distance")
        self.in_progress = pathfinder                                        # PB:500
        expected_start = self.expected_segment_start

        def work():
            if talk_about_it:
                self.log(f"Starting to search for path from {start} to {goal}")
            t0 = time.perf_counter()
            result = pathfinder.calculate(primary, failure)                  # PB:506
            elapsed = (time.perf_counter() - t0) * 1000.0
            with self.path_plan_lock:
                self.last_search = (pathfinder, result, elapsed)
                self.searches.append({"start": start, "type": result.type, "nodes": pathfinder.num_nodes_considered,
                                      "ms": round(elapsed, 1), "length": result.path.length() if result.path else 0})
                executor = PathExecutor(self, result.path) if result.path is not None else None   # PB:508
                if self.current is None:                                     # PB:509-523
                    if executor is not None:
                        if expected_start in executor.path.positions() or (self.expected_segment_start in executor.path.positions()):
                            self.queue_path_event(PathEvent.CALC_FINISHED_NOW_EXECUTING)
                            self.current = executor
                            self.refresh_context()
                            self.ticks_elapsed_so_far = 0                    # PB:514 resetEstimatedTicksToGoal
                            self.start_position = tuple(start)
                        else:
                            self.log("Warning: discarding orphan path segment with incorrect start")
                    elif result.type not in (PathCalculationResult.CANCELLATION, PathCalculationResult.EXCEPTION):   # PB:519-522
                        if result.type == PathCalculationResult.EXCEPTION:
                            self.log(f"search error: {result.error!r}")
                        self.queue_path_event(PathEvent.CALC_FAILED)
                    elif result.type == PathCalculationResult.EXCEPTION:
                        self.log(f"search error: {result.error!r}")
                else:                                                        # PB:524-541
                    if self.next is None:
                        if executor is not None:
                            if executor.path.get_src() == self.current.path.get_dest():
                                self.queue_path_event(PathEvent.NEXT_SEGMENT_CALC_FINISHED)
                                self.next = executor
                            else:
                                self.log("Warning: discarding orphan next segment with incorrect start")
                        else:
                            self.queue_path_event(PathEvent.NEXT_CALC_FAILED)
                    else:
                        self.log("Warning: PathingBehavior illegal state! Discarding invalid path!")
                if talk_about_it and self.current is not None:                # PB:542-548
                    dest = self.current.path.get_dest()
                    nodes = pathfinder.num_nodes_considered
                    if goal.is_in_goal(*dest):
                        self.log(f"Finished finding a path from {start} to {goal}. {nodes} nodes considered, {elapsed:.0f} ms")
                    else:
                        self.log(f"Found path segment from {start} towards {goal}. {nodes} nodes considered, {elapsed:.0f} ms")
                with self.path_calc_lock:                                    # PB:549-551
                    self.in_progress = None

        thread = threading.Thread(target=work, name="baritone-search", daemon=True)
        self.threads.append(thread)
        thread.start()

    def create_pathfinder(self, start, goal, previous, ctx, s):
        """PB:556-573."""
        transformed = goal
        # PB:558-563: IGoalRenderPos is every goal with a block position (GoalBlock, GoalNear, GoalGetToBlock,
        # GoalTwoBlocks); GoalXZ has no y to simplify
        has_pos = all(hasattr(goal, a) for a in ("x", "y", "z")) and not isinstance(goal, GoalXZ)
        if self.settings.simplifyUnloadedYCoord and has_pos:
            if not ctx.is_loaded(goal.x, goal.z):
                transformed = GoalXZ(goal.x, goal.z)
        favoring = Favoring(previous, ctx.backtrack_cost_favoring_coefficient)   # PB:564
        feet = s.player.feet                                                 # PB:565-570
        real_start = tuple(start)
        sub = (feet[0] - real_start[0], feet[1] - real_start[1], feet[2] - real_start[2])
        if feet[1] == real_start[1] and abs(sub[0]) <= 1 and abs(sub[2]) <= 1:
            real_start = feet
        return AStarPathFinder(real_start, start[0], start[1], start[2], transformed, favoring, ctx)   # PB:571


__all__ = ["PathExecutor", "PathingBehavior", "PathEvent", "TickState"]
