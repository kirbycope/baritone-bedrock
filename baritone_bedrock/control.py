"""The controller model: how Baritone's per-tick inputs (MovementState: a target rotation plus a set of keys) become
stick positions and button taps on a real Bedrock client holding a gamepad.

Baritone sets the player's rotation and keys directly (baritone/utils/PlayerMovementInput.java and LookBehavior):
every tick the movement says "face this, press these" and it is so. Here the camera is a rate command on the right
stick with a measured curve and a one tick pad-to-probe latency, the body moves relative to the camera through the
left stick, and several buttons are toggles or clicks rather than keys. So `Body` keeps the semantics of
MovementState (a wanted rotation, forced or not, and the keys forward, back, left, right, jump, sneak, sprint,
click_left, click_right) and turns them into:

- steering by pure pursuit (java/mineflayer-pathfinder/lib/human.js:188-203 carrot, 221-258 stepHead): the right
  stick turns toward a look point 1.8 to 3 blocks along the path (clipped at the next turn or height change by the
  executor), through the inverse of the measured turn curve (calibration.json "turn": 0 deg/s at 0.25 deflection,
  25.6 at 0.5, 150 at 1.0) with a 4 degree deadband (human.js:46 deadband 4-12) and the error predicted one latency
  ahead, so the camera stops where it was told rather than a tick late;
- the LEFT stick pointing at the movement's own target relative to the camera yaw, so the body walks toward the
  path while the camera is still swinging (a stick-driven player strafes, Baritone's cannot; departs: Baritone's
  MOVE_FORWARD is always along the facing);
- sprint as the LS click (clicked once moving forward, clicked again when the probe's sprint goes false), jump as
  an A tap of two ticks, sneak as a B TAP that toggles (the probe's sneak field is the truth: never held), use as an
  LT tap, attack as RT held while breaking, the hotbar as RB steps until the probe's hand matches (never the D-pad,
  which opens the emote wheel in the world), and look_at as calibrate.Calibrator.look_at (aim until the probe says
  the crosshair is on the block or entity).

The numbers come from calibration.json through costs.load_calibration(); every one has a default.
"""
import math
import time

from .costs import load_calibration

EYE = 1.62              # the eyes above the feet, standing (nav.py:489)
SNEAK_EYE = 1.27        # Entity.getEyeHeight for the crouching pose (RotationUtils.reachable, the sneak eye)
FEET_OFFSET = 0.1251    # IPlayerContext.playerFeet: position.y + 0.1251 for soul sand and farmland
FREE_PITCH = 10.0       # degrees down the camera settles to when nothing forces a pitch (human.js:36 basePitch 6 +- 5)
DEADBAND = 4.0          # degrees of yaw error tolerated while walking (human.js:46, the low end of 4-12)
AIM_DEADBAND = 1.0      # degrees when a rotation is forced (breaking, placing): the crosshair decides, this just settles
LOOK_AHEAD = 2.4        # blocks of pursuit look-ahead (human.js:41 lookAhead 1.8-3.0: the middle)
SETTLE_TICKS = 4.0      # the proportional gain: close the predicted error in this many ticks (2 limit-cycled 20 degrees
                        # either side of an 87 degree aim on the course: the loop is nearer three ticks than one)
MAX_RATE = 150.0        # degrees a second at full deflection, the default when the calibration is missing

FACES = {"Up": (0, 1, 0), "Down": (0, -1, 0), "North": (0, 0, -1), "South": (0, 0, 1), "East": (1, 0, 0), "West": (-1, 0, 0)}
INPUTS = ("forward", "back", "left", "right", "jump", "sneak", "sprint", "click_left", "click_right")


def yaw_to(dx, dz):
    """Bedrock yaw facing a direction: 0 is +z, -90 is +x, increasing clockwise seen from above (calibrate.yaw_to)."""
    return math.degrees(math.atan2(-dx, dz))


def pitch_to(dx, dy, dz):
    """Bedrock pitch toward a direction: down is positive."""
    return -math.degrees(math.atan2(dy, math.hypot(dx, dz)))


def wrap(degrees):
    return (degrees + 180.0) % 360.0 - 180.0


def center(pos):
    """VecUtils.getBlockPosCenter."""
    return (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)


def offset(pos, d):
    return (pos[0] + d[0], pos[1] + d[1], pos[2] + d[2])


class Rotation:
    """Rotation.java: a yaw and a pitch in degrees."""
    __slots__ = ("yaw", "pitch")

    def __init__(self, yaw, pitch):
        self.yaw, self.pitch = yaw, pitch

    def is_really_close_to(self, other, tolerance=0.01):
        """Rotation.java:126-133 isReallyCloseTo (0.01); the tolerance is wider here because the camera is a rate."""
        return abs(wrap(self.yaw - other.yaw)) < tolerance and abs(self.pitch - other.pitch) < tolerance

    def __repr__(self):
        yaw = "-" if self.yaw is None else f"{self.yaw:.1f}"
        pitch = "-" if self.pitch is None else f"{self.pitch:.1f}"
        return f"Rotation({yaw}, {pitch})"


def rotation_from(eye, point):
    """RotationUtils.calcRotationFromVec3d: the rotation that looks from eye at point."""
    dx, dy, dz = point[0] - eye[0], point[1] - eye[1], point[2] - eye[2]
    return Rotation(yaw_to(dx, dz), pitch_to(dx, dy, dz))


class Player:
    """One probe report on the player (probe.py, README "The probe protocol"), as the movements read it: the
    position, the velocity in blocks a tick, the rotation, the flags, the feet block (IPlayerContext.playerFeet), the
    crosshair block and entity, the hand and the hotbar."""
    __slots__ = ("raw", "x", "y", "z", "vx", "vy", "vz", "yaw", "pitch", "ground", "water", "sneak", "sprint", "jump",
                 "swimming", "climbing", "block", "look", "hand", "hotbar", "slot", "food", "health", "tick", "n", "at",
                 "feet")

    def __init__(self, report):
        p = self.raw = report
        self.x, self.y, self.z = float(p["x"]), float(p["y"]), float(p["z"])
        self.vx, self.vy, self.vz = float(p.get("vx") or 0.0), float(p.get("vy") or 0.0), float(p.get("vz") or 0.0)
        self.yaw, self.pitch = float(p.get("yaw") or 0.0), float(p.get("pitch") or 0.0)
        self.ground, self.water = bool(p.get("ground")), bool(p.get("water"))
        self.sneak, self.sprint, self.jump = bool(p.get("sneak")), bool(p.get("sprint")), bool(p.get("jump"))
        self.swimming, self.climbing = bool(p.get("swimming")), bool(p.get("climbing"))
        self.block, self.look = p.get("block"), p.get("look")
        self.hand, self.hotbar, self.slot = p.get("hand"), p.get("hotbar"), p.get("slot")
        food, health = p.get("food"), p.get("health")
        self.food = int(food["current"]) if isinstance(food, dict) and food.get("current") is not None else 20
        self.health = float(health["current"]) if isinstance(health, dict) and health.get("current") is not None else 20.0
        self.tick, self.n, self.at = p.get("tick"), p.get("n"), p.get("at")
        self.feet = (math.floor(self.x), math.floor(self.y + FEET_OFFSET), math.floor(self.z))

    @property
    def pos(self):
        return (self.x, self.y, self.z)

    @property
    def eye(self):
        return (self.x, self.y + (SNEAK_EYE if self.sneak else EYE), self.z)

    @property
    def rotation(self):
        return Rotation(self.yaw, self.pitch)

    def crosshair(self):
        """The block under the crosshair as (x, y, z), or None."""
        b = self.block
        if not b:
            return None
        return (b["x"], b["y"], b["z"])

    def is_looking_at(self, pos):
        """IPlayerContext.isLookingAt: the probe's crosshair block is that block."""
        return self.crosshair() == tuple(pos)

    def crosshair_face(self):
        """The face hit, as an (dx, dy, dz) offset from the block to the air beside it, or None."""
        b = self.block
        return FACES.get((b or {}).get("face")) if b else None

    def flat_distance_to_center(self, pos):
        """VecUtils.entityFlatDistanceToCenter."""
        return math.hypot(pos[0] + 0.5 - self.x, pos[2] + 0.5 - self.z)

    def distance_to_center(self, pos):
        """VecUtils.entityDistanceToCenter: from the position (the feet point) to the block centre."""
        return math.sqrt((pos[0] + 0.5 - self.x) ** 2 + (pos[1] + 0.5 - self.y) ** 2 + (pos[2] + 0.5 - self.z) ** 2)


class MovementState:
    """MovementState.java (MS:28-92): the status, the target rotation (and whether it is forced: breaking and
    placing force it, walking does not), the keys pressed this tick, and `move_to`, the world point moveTowards
    aimed at, which the left stick follows relative to the camera."""
    __slots__ = ("status", "target", "force", "inputs", "move_to")

    def __init__(self, status="PREPPING"):
        self.status = status
        self.target = None
        self.force = False
        self.inputs = {}
        self.move_to = None

    def set_target(self, rotation, force=False):
        """MS:60-66 setTarget(MovementTarget(rotation, forceRotations))."""
        self.target, self.force = rotation, force
        return self

    def set_input(self, name, value):
        """MS:84-87: a later setInput(X, false) in the same tick overrides an earlier true."""
        self.inputs[name] = bool(value)
        return self

    def is_down(self, name):
        return bool(self.inputs.get(name))

    def clear(self):
        self.target, self.force, self.move_to = None, False, None
        self.inputs = {}

    def __repr__(self):
        keys = ",".join(k for k, v in self.inputs.items() if v)
        return f"MovementState({self.status}, target={self.target}, force={self.force}, keys=[{keys}], move_to={self.move_to})"


class TurnCurve:
    """The right stick as a rate command: degrees a second at a deflection, from calibration.json "turn"
    ({"0.25": {deg_per_s}, "0.5": ..., "1.0": ...}), inverted by piecewise-linear interpolation so a wanted rate gives
    a deflection. The defaults are this client's measurement."""

    # the smallest deflection that moves the camera at all: the calibration measured 0 at 0.25 and 25.6 deg/s at 0.5,
    # and the course showed the knee near 0.4 (a pitch command of 0.39 did nothing, 0.42 moved it a degree a tick),
    # so an interpolation between the two measured points asks for rates the pad cannot give
    MIN_DEFLECTION = 0.45

    def __init__(self, turn=None):
        points = {0.0: 0.0, 0.25: 0.0, 0.4: 0.0, 0.5: 25.6, 1.0: MAX_RATE}
        for key, value in (turn or {}).items():
            try:
                rate = (value or {}).get("deg_per_s") if isinstance(value, dict) else value
                if rate is not None:
                    points[float(key)] = float(rate)
            except (TypeError, ValueError):
                continue
        self.points = sorted(points.items())
        self.max_rate = max(rate for _d, rate in self.points)

    def rate(self, deflection):
        """Degrees a second at a deflection, interpolated."""
        d = min(1.0, abs(deflection))
        for (d0, r0), (d1, r1) in zip(self.points, self.points[1:]):
            if d <= d1:
                return r0 if d1 == d0 else r0 + (r1 - r0) * (d - d0) / (d1 - d0)
        return self.points[-1][1]

    def deflection(self, rate):
        """The smallest deflection giving at least a rate (degrees a second), 0 for a rate of 0, 1 past the top."""
        if rate <= 0:
            return 0.0
        for (d0, r0), (d1, r1) in zip(self.points, self.points[1:]):
            if rate <= r1:
                if r1 == r0:
                    continue
                return max(self.MIN_DEFLECTION, d0 + (d1 - d0) * (rate - r0) / (r1 - r0))
        return 1.0


class Body:
    """A Controller and the Probe for one player, driven a tick at a time.

        body.begin(player)                          # the new report: rates and tap counters
        body.apply(player, movement_state, look=None)   # steer and press for this tick
        body.release()                              # clearAllKeys

    plus the blocking helpers look_at, stand_up, sneak_on and select used between movements. `last` holds the
    sticks and buttons of the latest state() call for the tick log."""

    def __init__(self, controller, probe, player, calibration=None, log=None):
        self.pad, self.probe, self.who = controller, probe, player
        self.log = log or (lambda text: None)
        cal = load_calibration() if calibration is None else (calibration or {})
        self.calibration = cal
        self.turn = TurnCurve(cal.get("turn"))
        latency = (cal.get("latency") or {})
        # 56 ms median, two ticks at worst, and the rate estimate below lags a tick of its own: one more than measured
        self.latency_ticks = max(2, int(round(latency.get("stick_ticks") or 1)) + 1)
        # the ticks from an A tap to the report that shows the jump (1 on this client): what a sprint jump or a
        # parkour jump plans ahead by, so the takeoff lands where the plan put it
        self.jump_lead = max(1, int(round(latency.get("jump_ticks") or 1)))
        jump = cal.get("jump") or {}
        self.jump_air_ticks = int(jump.get("air_ticks") or 9)
        self.jump_apex = float(jump.get("apex") or 1.25)
        self.sprint_jump_length = float(jump.get("sprint_jump_length") or 2.5)
        self.walk_jump_length = float(jump.get("walk_jump_length") or 1.5)
        # the probe's vx, vy, vz are read as the tick's displacement in blocks (what the executor and the sprint jump
        # plan with); calibrate --trace-jump writes jump.sprint_jump_trace.velocity_is_displacement, and when the
        # client reports Java's post-friction momentum instead, the displacement is that over the ground friction
        trace = jump.get("sprint_jump_trace") or {}
        self.velocity_to_displacement = 1.0 if trace.get("velocity_is_displacement", True) else 1.0 / (0.6 * 0.91)
        step = cal.get("step_up") or {}
        # the distance before a 1 block step at which an A tap still lands it; Baritone jumps at 1.2 (Asc:224)
        self.step_up_walk = float(step.get("walk") or 1.2)
        self.step_up_sprint = float(step.get("sprint") or 1.2)
        pillar = cal.get("pillar") or {}
        self.pillar_delay = int(pillar.get("delay_ticks") or 5)        # LT this many ticks after the A tap (window 3-7)
        speeds = cal.get("speeds") or {}
        self.walk_speed = float(speeds.get("walk") or 4.317) / 20.0     # blocks a tick
        self.sprint_speed = float(speeds.get("sprint") or 5.612) / 20.0
        self.deadband, self.aim_deadband, self.free_pitch = DEADBAND, AIM_DEADBAND, FREE_PITCH
        self.tick = 0            # game ticks seen
        self.last_player = None
        self.yaw_rate = 0.0      # degrees a tick, from the last two reports
        self.pitch_rate = 0.0
        # taps in flight, in ticks left down, and when each was last started
        self.a_ticks = self.b_ticks = self.lt_ticks = self.rb_ticks = self.ls_ticks = 0
        self.a_released = True
        self.b_at = self.lt_at = self.rb_at = self.ls_at = self.a_at = -1000
        self.sprint_grace = 0    # ticks to keep walking after a stick release meant to drop the sprint
        self.attack = False
        self.last = {"left": (0.0, 0.0), "right": (0.0, 0.0), "held": ()}
        self.idle = True

    # ------------------------------------------------------------------------------------------------ per tick

    def begin(self, player):
        """A new report: the camera's rate (for the latency prediction) and the tap counters move on one tick."""
        prev = self.last_player
        if prev is not None and player.tick is not None and prev.tick is not None and player.tick > prev.tick:
            dt = player.tick - prev.tick
            self.yaw_rate = wrap(player.yaw - prev.yaw) / dt
            self.pitch_rate = (player.pitch - prev.pitch) / dt
        elif prev is None:
            self.yaw_rate = self.pitch_rate = 0.0
        self.last_player = player
        self.tick += 1
        for name in ("a_ticks", "b_ticks", "lt_ticks", "rb_ticks", "ls_ticks"):
            v = getattr(self, name)
            if v > 0:
                setattr(self, name, v - 1)
        if self.a_ticks == 0:
            self.a_released = True
        if self.sprint_grace > 0:
            self.sprint_grace -= 1

    def aim(self, player, yaw, pitch, deadband=None):
        """The right stick (x, y) that turns the camera toward a yaw and a pitch: the error predicted one latency
        ahead (the camera keeps turning that long after the stick moves), closed in SETTLE_TICKS, through the
        inverse turn curve, with a deadband. y is up (pitch decreasing)."""
        band = self.deadband if deadband is None else deadband
        rx = ry = 0.0
        if yaw is not None:
            err = wrap(yaw - player.yaw)
            pred = err - self.yaw_rate * self.latency_ticks
            if abs(pred) >= band and abs(err) >= band * 0.5:
                rate = min(self.turn.max_rate, abs(pred) * 20.0 / SETTLE_TICKS)
                rx = math.copysign(self.turn.deflection(rate), pred)
        if pitch is not None:
            err = max(-89.0, min(89.0, pitch)) - player.pitch
            pred = err - self.pitch_rate * self.latency_ticks
            if abs(pred) >= band and abs(err) >= band * 0.5:
                rate = min(self.turn.max_rate, abs(pred) * 20.0 / SETTLE_TICKS)
                ry = -math.copysign(self.turn.deflection(rate), pred)
        return (rx, ry)

    @staticmethod
    def strafe(player, point):
        """The left stick (x, y) that moves the body toward a world point whatever the camera is doing: the
        direction to the point relative to the camera yaw. x is right, y is forward."""
        dx, dz = point[0] - player.x, point[2] - player.z
        if abs(dx) < 1e-9 and abs(dz) < 1e-9:
            return (0.0, 0.0)
        err = math.radians(wrap(yaw_to(dx, dz) - player.yaw))
        return (math.sin(err), math.cos(err))

    @staticmethod
    def keys_to_stick(ms):
        """Baritone's keys in the camera frame (MOVE_FORWARD is along the facing)."""
        x = (1.0 if ms.is_down("right") else 0.0) - (1.0 if ms.is_down("left") else 0.0)
        y = (1.0 if ms.is_down("forward") else 0.0) - (1.0 if ms.is_down("back") else 0.0)
        if x and y:
            x, y = x / math.sqrt(2), y / math.sqrt(2)
        return (x, y)

    def apply(self, player, ms, look=None, sprint=None):
        """One tick of control from a MovementState: the camera at the forced target, else at `look` (the
        executor's pursuit point), else at move_to or the unforced target; the body toward move_to relative to the
        camera, else by the keys; then the buttons. `sprint` overrides the state's sprint key, the way
        PathExecutor.shouldSprintNextTick consumes it (PE:348-352)."""
        want_yaw = want_pitch = None
        if ms.target is not None:
            want_yaw, want_pitch = ms.target.yaw, ms.target.pitch
        if not ms.force:
            point = look if look is not None else ms.move_to
            if point is not None:
                # a point almost under the eye has no yaw worth turning to (the pillar circled for 90 ticks on it)
                if math.hypot(point[0] - player.x, point[2] - player.z) >= 0.25:
                    want_yaw = yaw_to(point[0] - player.x, point[2] - player.z)
                else:
                    want_yaw = None
            if want_pitch is None:
                # moveTowards keeps the current pitch (MH:688 withPitch); a free camera settles a little down instead
                want_pitch = self.free_pitch
        right = self.aim(player, want_yaw, want_pitch, self.aim_deadband if ms.force else None)
        if ms.move_to is not None and not ms.force:
            left = self.strafe(player, ms.move_to)
            if ms.is_down("back"):
                left = (-left[0], -left[1])
        else:
            left = self.keys_to_stick(ms)
        held = set()
        # jump: an A tap of two ticks; a new tap only after the last was released (vanilla's held-jump delay is the
        # game's; here each request is its own tap)
        if ms.is_down("jump") and self.a_ticks == 0 and self.a_released and self.tick - self.a_at >= 4:
            self.a_ticks, self.a_released, self.a_at = 2, False, self.tick
        # sneak: B toggles on this client whatever options.txt says, so tap when the probe disagrees with the wish,
        # and not again until the tap has had time to show (the latency plus the hold)
        want_sneak = ms.is_down("sneak")
        if want_sneak != player.sneak and self.b_ticks == 0 and self.tick - self.b_at >= 8:
            self.b_ticks, self.b_at = 3, self.tick
        # use: an LT tap, at most one every four ticks (the game's own use cooldown)
        if ms.is_down("click_right") and self.lt_ticks == 0 and self.tick - self.lt_at >= 4:
            self.lt_ticks, self.lt_at = 2, self.tick
        # attack: RT held while wanted (the movement stops wanting it on the probe's break event)
        self.attack = ms.is_down("click_left")
        # sprint: the LS click once moving forward; clicked again when the probe's sprint goes false (a collision)
        want_sprint = ms.is_down("sprint") if sprint is None else bool(sprint)
        if want_sprint and left[1] >= 0.8 and not player.sprint and self.ls_ticks == 0 and self.tick - self.ls_at >= 6:
            self.ls_ticks, self.ls_at = 3, self.tick
        if not want_sprint and player.sprint and left[1] > 0 and self.sprint_grace == 0:
            # departs: Baritone calls setSprinting(false) (PE:243); a stick has no such key, so the stick is let
            # go for one tick, which drops the sprint, and the walk goes on (a grace period stops it repeating)
            left = (0.0, 0.0)
            self.sprint_grace = 4
        for name, ticks in (("a", self.a_ticks), ("b", self.b_ticks), ("lt", self.lt_ticks), ("rb", self.rb_ticks), ("ls", self.ls_ticks)):
            if ticks > 0:
                held.add(name)
        if self.attack:
            held.add("rt")
        self._state(left, right, tuple(sorted(held)))

    def _state(self, left, right, held):
        left = (max(-1.0, min(1.0, left[0])), max(-1.0, min(1.0, left[1])))
        right = (max(-1.0, min(1.0, right[0])), max(-1.0, min(1.0, right[1])))
        self.last = {"left": (round(left[0], 3), round(left[1], 3)), "right": (round(right[0], 3), round(right[1], 3)), "held": held}
        self.idle = not any(left) and not any(right) and not held
        self.pad.state(left=left, right=right, held=held)

    def release(self):
        """InputOverrideHandler.clearAllKeys: sticks centred, every button up, the taps in flight dropped."""
        self.a_ticks = self.lt_ticks = self.rb_ticks = self.ls_ticks = 0
        self.b_ticks = 0
        self.attack = False
        if not self.idle:
            self._state((0.0, 0.0), (0.0, 0.0), ())

    # ----------------------------------------------------------------------------------------------- the hotbar

    def select(self, player, wanted):
        """RB steps toward the slot whose item satisfies wanted(item) (item is None for an empty hand): one step a
        tick pair, judged by the probe's hand. True once the hand matches."""
        if wanted(player.hand):
            return True
        hotbar = player.hotbar or []
        if hotbar and not any(wanted(item) for item in hotbar):
            return False
        if self.rb_ticks == 0 and self.tick - self.rb_at >= 4:
            self.rb_ticks, self.rb_at = 2, self.tick
        return False

    @staticmethod
    def item_type(item):
        if item is None:
            return None
        if isinstance(item, dict):
            return item.get("type") or item.get("typeId")
        return str(item)

    def throwaway_in(self, player, acceptable):
        """IB:163-239 hasGenericThrowaway: any acceptable item in the hotbar."""
        return any(self.item_type(item) in acceptable for item in (player.hotbar or []))

    def select_throwaway(self, player, acceptable):
        """IB selectThrowawayForLocation(select=true): RB until the hand holds an acceptable item. False (and no
        taps) when the hotbar has none."""
        if not self.throwaway_in(player, acceptable):
            return False
        return self.select(player, lambda item: self.item_type(item) in acceptable)

    def select_slot(self, player, slot):
        """MH:668-683 switchToBestToolFor: the hotbar slot; None means the hand (any slot will do)."""
        if slot is None or player.slot is None:
            return True
        if player.slot == slot:
            return True
        if self.rb_ticks == 0 and self.tick - self.rb_at >= 4:
            self.rb_ticks, self.rb_at = 2, self.tick
        return False

    # -------------------------------------------------------------------------------------- blocking helpers

    def next_report(self, after_tick=None, timeout=1.0):
        """The next report on the player with a game tick newer than after_tick, as a Player, or None after timeout."""
        end = time.time() + timeout
        n = self.probe.ticks
        while time.time() < end:
            n = self.probe.wait_tick(n, timeout=max(0.01, min(0.25, end - time.time())))
            p = self.probe.player(self.who, fresh=5.0)
            if p is None:
                continue
            if after_tick is None or p.get("tick") is None or p["tick"] > after_tick:
                return Player(p)
        return None

    def look_at(self, point, block=None, entity=None, timeout_ticks=120, tolerance=2.0):
        """Turn the camera with the right stick until the eyes point at point (x, y, z) (or a callable returning
        it), or, with block (x, y, z) or entity (an id), until the probe says the crosshair is on it: the game's own
        answer (calibrate.Calibrator.look_at, nav.py:490-531). Returns True once on target."""
        last_tick = None
        try:
            for _ in range(timeout_ticks):
                player = self.next_report(last_tick)
                if player is None:
                    return False
                last_tick = player.tick
                self.begin(player)
                target = point() if callable(point) else point
                eye = player.eye
                want = rotation_from(eye, target)
                yaw_error = wrap(want.yaw - player.yaw)
                pitch_error = want.pitch - player.pitch
                if entity is not None:
                    on = str((player.look or {}).get("id")) == str(entity)
                elif block is not None:
                    on = player.is_looking_at(block)
                else:
                    on = abs(yaw_error) <= tolerance and abs(pitch_error) <= tolerance
                if on:
                    self.release()
                    return True
                right = self.aim(player, want.yaw, want.pitch, min(self.aim_deadband, tolerance * 0.5))
                self._state((0.0, 0.0), right, ())
            return False
        finally:
            self.release()

    def _toggle_sneak(self, wanted, tries=3):
        """Tap B while the probe's sneak disagrees with wanted; True once it agrees."""
        last_tick = None
        for _ in range(tries):
            for _ in range(3):
                player = self.next_report(last_tick)
                if player is None:
                    return False
                last_tick = player.tick
            if player.sneak == wanted:
                self.release()
                return True
            self._state((0.0, 0.0), (0.0, 0.0), ("b",))
            for _ in range(3):
                player = self.next_report(last_tick) or player
                last_tick = player.tick
            self._state((0.0, 0.0), (0.0, 0.0), ())
            for _ in range(5):
                player = self.next_report(last_tick) or player
                last_tick = player.tick
        player = self.next_report(last_tick)
        return bool(player is not None and player.sneak == wanted)

    def stand_up(self):
        """Out of a crouch: a B tap while the probe's sneak is true (calibrate.Calibrator.stand_up)."""
        return self._toggle_sneak(False)

    def sneak_on(self):
        """Into a crouch: a B tap while the probe's sneak is false."""
        return self._toggle_sneak(True)


__all__ = ["Body", "Player", "MovementState", "Rotation", "TurnCurve", "rotation_from", "yaw_to", "pitch_to", "wrap",
           "center", "offset", "FACES", "INPUTS", "EYE", "SNEAK_EYE", "FEET_OFFSET", "LOOK_AHEAD"]
