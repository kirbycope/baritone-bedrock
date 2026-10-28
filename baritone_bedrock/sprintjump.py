"""SprintJump (java/baritone/src/main/java/baritone/pathing/path/SprintJump.java, "SJ"): one sprint jump along a
straight run of path, simulated tick by tick the way LivingEntity does it, so the executor only jumps when the hop
lands back on the path without fall damage (settings.sprintJumping, docs/research-settings.md A1 SJ:44-114).

The numbers are the Java ones (SJ:44-45) and they are Bedrock's too: calibration.json has the sprint at 5.613
blocks a second against Java's 5.612, the apex at 1.252 (0.42 up, 0.08 gravity, 0.98 drag give 1.2523), and the
sprint-jump trace recorded under jump.sprint_jump_length_measured is what this file is checked against.

Where the port departs from the Java it says "departs:". The one departure that changes the plan: Baritone brakes
mid-air by facing backward while holding W (SJ:254-264, a throttle of -1), which keeps the sprint because W is still
down; a stick-driven player sprints only while the left stick points forward, and pulling it back drops the sprint
(control.Body.apply does exactly that to stop sprinting), so there is no brake here. plan() therefore offers full
send (NaN) or nothing (SJ:169-177's braked targets are never tried), and the chained hop before a step (SJ:157-162)
is either a full-send hop that happens to set the pop up, or a wait of a tick or two until one does.

The second: the pad-to-probe latency. A jump tapped from a report takes effect a tick later (calibration.json
latency.jump_ticks), so a plan that works from the player's position now is also required to work from where the
player will be after that many ground ticks (MARGINS over timing as SJ:50 has them over velocity).
"""
import math

from .helpers import fully_passable, can_walk_on, X_SOUL_SAND, X_MAGMA, X_FARMLAND, X_SLAB_HALF, X_STAIRS
from .moves import MovementTraverse, MovementAscend, MovementDescend, MovementDiagonal
from .world import LIQUID, BOTTOM_SLAB

# straight out of 1.21.4 LivingEntity.travelInAir / jumpFromGround / Player.getFlyingSpeed, aiStep scales move input
# by 0.98 (SJ:43-45)
GROUND_FRICTION = 0.6 * 0.91
AIR_FRICTION = 0.91
AIR_ACCEL = 0.026 * 0.98
JUMP_BOOST = 0.2
JUMP = 0.42
GRAVITY = 0.08
SAFE_FALL = 3
EPS = 1e-6
# a braked hop that lands short is a slow way to walk. ground sprint is ~0.28 a tick, going down steps or bonking
# one is way worse (SJ:46-47)
FLAT_PACE = 0.28
SLOW_PACE = 0.15
# ~4 blocks a jump, plus enough past that to see whether the next hop can pop a step (SJ:48-49)
MAX_RUNWAY = 10
MAX_TICKS = 40
MARGINS = (0.85, 1.0, 1.15)      # SJ:50: we're exact about vanilla, not about what the server does with our yaw
FULL_SEND = float("nan")         # SJ:153-155: the NaN target
# the ground acceleration of a sprinting player: Player.getSpeed() 0.13 times 0.98 (SJ:105), which with friction
# 0.6 is the 0.2806 blocks a tick sprint displacement times (1 - GROUND_FRICTION)
SPRINT_GROUND_ACCEL = 0.13 * 0.98
# blocks whose friction, speed factor or jump factor is not plain ground (SJ:125-130 solidFloor): no hop from them
SLIPPERY = frozenset(("ice", "packed_ice", "blue_ice", "frosted_ice", "slime", "slime_block", "honey_block"))


class Landing:
    """SJ:53: where a simulated hop came down."""
    __slots__ = ("s", "y", "apex", "v", "ticks")

    def __init__(self, s, y, apex, v, ticks):
        self.s, self.y, self.apex, self.v, self.ticks = s, y, apex, v, ticks

    def __repr__(self):
        return f"Landing(s={self.s:.3f}, y={self.y:.3f}, apex={self.apex:.3f}, v={self.v:.3f}, ticks={self.ticks})"


def solid_floor(ctx, x, y, z):
    """SJ:125-130: friction 0.6, speed and jump factor 1, not magma, a full collision box. Here: walkable, a full
    block (no slab, stair, soul sand, farmland or liquid), and not one of the slippery or bouncy blocks."""
    f = ctx.at(x, y, z)
    if not can_walk_on(ctx, x, y, z, f):
        return False
    if f & (BOTTOM_SLAB | X_SLAB_HALF | X_STAIRS | X_SOUL_SAND | X_MAGMA | X_FARMLAND | LIQUID):
        return False
    kind = ctx.kind(x, y, z)
    return kind.type_id.split(":")[-1] not in SLIPPERY


def clear(ctx, x, y, z, top):
    """SJ:136-143: fully passable from y to top inclusive."""
    for yy in range(y, top + 1):
        if not fully_passable(ctx, x, yy, z):
            return False
    return True


def side(ctx, x, y, z, top):
    """SJ:132-134."""
    return solid_floor(ctx, x, y - 1, z) and clear(ctx, x, y, z, top)


class SprintJump:
    """SJ:41: the runway (floors relative to the takeoff floor, cell 0 the one taken off from), the simulation, and
    the per-tick steering once airborne."""

    def __init__(self, floors, spacing, ground_accel, leads=(0,)):
        self.floors = list(floors)
        self.spacing = spacing
        self.half_width = 0.3 * spacing      # SJ:64: an axis aligned box projected onto a diagonal reaches sqrt 2 further
        self.ground_accel = ground_accel
        self.leads = tuple(leads)            # departs: the ground ticks between the plan and the jump to also work from
        self.origin_x = self.origin_z = self.takeoff_y = 0.0
        self.ux = self.uz = 0.0
        self.target = FULL_SEND
        self.apex = 0.0
        self.ticks = 0
        self.lead = 0

    # ------------------------------------------------------------------------------------------------- planning

    @classmethod
    def plan(cls, pl, ctx, path, path_position, settings, ground_accel=SPRINT_GROUND_ACCEL, lead_ticks=1,
             velocity_to_displacement=1.0):
        """SJ:68-123: None unless a hop from here lands back on the path. `pl` is the control.Player of this tick,
        `lead_ticks` the pad-to-probe jump latency in ticks (the hop is also checked from that many ground ticks
        ahead). The velocity the probe reports is the displacement of the last tick (times
        `velocity_to_displacement`, control.Body's reading of the calibration); Java's getDeltaMovement is that
        displacement after the tick's friction, so it is scaled by GROUND_FRICTION before the simulation (departs)."""
        positions, movements = path.positions(), path.movements()
        if path_position >= len(movements):
            return None
        start = positions[path_position]
        d = movements[path_position].direction
        dx, dz = d[0], d[2]
        diagonal = dx != 0 and dz != 0
        # SJ:73-83: the sim knows nothing about potions or modded physics (the probe reports no effects: departs,
        # none are checked). the head tops out at y + 3.05, so y + 3 has to be clear; a ceiling at y + 2 is a head
        # hitter, not ours
        if (not pl.ground or not pl.sprint or pl.water or pl.climbing or pl.feet != tuple(start)
                or abs(pl.y - start[1]) > 1e-3 or (diagonal and not settings.sprintJumpingDiagonals)
                or not solid_floor(ctx, start[0], start[1] - 1, start[2])
                or not clear(ctx, start[0], start[1], start[2], start[1] + 3)):
            return None
        floors = [0.0] * (MAX_RUNWAY + 1)                                    # SJ:84
        n = 1
        for i in range(path_position, len(movements)):                      # SJ:86-102
            if n > MAX_RUNWAY:
                break
            m = movements[i]
            src, dest = m.src, m.dest
            top = max(dest[1] + 2, start[1] + 3)                             # SJ:90: we fly over lower cells at takeoff height
            if diagonal:
                walking = isinstance(m, MovementDiagonal) and dest[1] == src[1]
            else:
                walking = isinstance(m, (MovementTraverse, MovementAscend, MovementDescend))
            # SJ:93-99: breaking is like 5x slower when you're jumping, and momentum doesn't wait for a block to be
            # placed. on a diagonal the hitbox corners sweep through both side cells, and drift can land us in one
            if (not walking or dest[0] - src[0] != dx or dest[2] - src[2] != dz
                    or m.to_break(ctx) or m.to_place(ctx)
                    or not solid_floor(ctx, dest[0], dest[1] - 1, dest[2]) or not clear(ctx, dest[0], dest[1], dest[2], top)
                    or (diagonal and not (side(ctx, src[0] + dx, src[1], src[2], top) and side(ctx, src[0], src[1], src[2] + dz, top)))):
                break
            floors[n] = float(dest[1] - start[1])                            # SJ:101
            n += 1
        spacing = math.sqrt(2) if diagonal else 1.0                          # SJ:103
        # SJ:104-105: solidFloor pins friction to 0.6, so the 0.216 / f^3 in the ground acceleration is 1
        jump = cls(floors[:n], spacing, ground_accel, leads=tuple(range(max(0, lead_ticks) + 1)))
        jump.origin_x, jump.origin_z, jump.takeoff_y = start[0] + 0.5, start[2] + 0.5, float(start[1])   # SJ:106-108
        jump.ux, jump.uz = dx / spacing, dz / spacing                        # SJ:109-110
        pos = (pl.x - jump.origin_x, pl.y, pl.z - jump.origin_z)             # SJ:111
        k = velocity_to_displacement * GROUND_FRICTION
        vel = (pl.vx * k, pl.vy, pl.vz * k)                                  # SJ:112, the displacement after friction
        if n < 3 or abs(jump.across(pos)) > 0.2 or abs(jump.across(vel)) > 0.08:   # SJ:113-116: drifting sideways
            return None
        target = jump.choose_target(jump.along(pos), jump.along(vel))        # SJ:117-121
        if target is None:
            return None
        jump.target = target
        return jump

    def along(self, vec):
        """SJ:145-147."""
        return vec[0] * self.ux + vec[2] * self.uz

    def across(self, vec):
        """SJ:149-151."""
        return vec[2] * self.ux - vec[0] * self.uz

    def choose_target(self, s, v):
        """SJ:156-163 plan(): full send (NaN) or don't jump (None). a hop that lands right in front of a step leaves
        the next one nothing to do but bonk the face, lose sprint, and hand it to MovementAscend. so with a step
        ahead, pick a hop the next one can pop it from, or sprint a tick or two more if that sets one up."""
        climb = self.step_up_ahead(0)
        chained = self.choose(s, v, MARGINS, True) if climb else None
        if chained is not None or (climb and self.can_hop(s, v, MARGINS, True, 1)):
            return chained
        return self.choose(s, v, MARGINS, False)

    def choose(self, s, v, margins, chain):
        """SJ:165-178. departs: only the full send is offered (SJ:169-177's braked targets need a brake)."""
        if self.works(s, v, margins, FULL_SEND, chain):
            return FULL_SEND
        return None

    def works(self, s, v, margins, target, chain):
        """SJ:180-188, for every velocity margin and (departs) every lead of ground ticks before the jump."""
        for lead in self.leads:
            s_lead, v_lead = self.advance(s, v, lead)
            for margin in margins:
                landing = self.jump(s_lead, v_lead * margin, target)
                if not self.safe(landing):
                    return False
                if not math.isnan(target) and landing.s - s_lead < (SLOW_PACE if (landing.y < -EPS or chain) else FLAT_PACE) * landing.ticks:
                    return False
        return not chain or self.chains(self.jump(s, v, target))

    def advance(self, s, v, ticks):
        """SJ:205-206: ticks of sprinting on the ground."""
        for _ in range(ticks):
            s += v + self.ground_accel
            v = (v + self.ground_accel) * GROUND_FRICTION
        return s, v

    def chains(self, landing):
        """SJ:190-198: the hop after this one can pop the step ahead of where it lands."""
        if landing is None:
            return False
        cell = int(round(landing.s / self.spacing))
        if not self.step_up_ahead(cell):
            return True
        nxt = [self.floors[cell + i] - landing.y for i in range(len(self.floors) - cell)]
        return SprintJump(nxt, self.spacing, self.ground_accel).can_hop(landing.s - cell * self.spacing, landing.v, (1.0,), False, 0)

    def can_hop(self, s, v, margins, chain, frm):
        """SJ:200-209: a hop works now, or after one or two more ticks on the ground."""
        for wait in range(3):
            if self.highest(s) > EPS:
                return False
            if wait >= frm and self.choose(s, v, margins, chain) is not None:
                return True
            s += v + self.ground_accel
            v = (v + self.ground_accel) * GROUND_FRICTION
        return False

    def step_up_ahead(self, cell):
        """SJ:211-218."""
        for k in range(cell + 1, len(self.floors)):
            if self.floors[k] > self.floors[cell] + EPS:
                return True
        return False

    # ----------------------------------------------------------------------------------------------- the physics

    def jump(self, s, v, target=FULL_SEND):
        """SJ:220-225: None if we hit a wall, leave the runway, or never come down."""
        return self.fly(s, v + JUMP_BOOST, 0.0, JUMP, 0.0, True, target)

    def fly(self, s, v, y, vy, apex, jumping, target):
        """SJ:227-251. departs: the throttle is always 1 (no brake on a stick), so `target` only chooses the pace
        check in works()."""
        for tick in range(MAX_TICKS):
            jump_tick = jumping and tick == 0                                # SJ:230-231: onGround is still true on the jump tick
            disp = v + (self.ground_accel if jump_tick else AIR_ACCEL)       # SJ:234
            # SJ:235: vanilla collides y before x/z, so we're already as high as we're going to get this tick
            y += vy
            apex = max(apex, y)
            landed = vy < 0 and y <= self.highest(s) + EPS                   # SJ:238
            if landed:
                y = self.highest(s)
            s += disp
            if self.highest(s) > y + EPS:                                    # SJ:241-243: face first into a step
                return None
            if landed:
                return Landing(s, y, apex, disp * AIR_FRICTION, tick + 1)    # SJ:244-246
            v = disp * (GROUND_FRICTION if jump_tick else AIR_FRICTION)      # SJ:247
            vy = (vy - GRAVITY) * 0.98                                       # SJ:248
        return None

    def safe(self, landing):
        """SJ:266-275: small hills only, the landing cell on the path with its floor at the landing height, and the
        slide after landing (v / (1 - 0.546) with W let go) ending over the runway too."""
        if landing is None or landing.apex - landing.y >= SAFE_FALL:
            return False
        cell = int(round(landing.s / self.spacing))
        return (0 <= cell < len(self.floors) and abs(self.floors[cell] - landing.y) < EPS
                and self.highest(landing.s + max(0.0, landing.v) / (1 - GROUND_FRICTION)) != math.inf)

    def highest(self, s):
        """SJ:277-290: the tallest floor under a hitbox centred at s. Past the runway is a wall so the whole flight
        stays over checked cells, behind it is where we came from."""
        top = -math.inf
        first = int(math.floor((s - self.half_width) / self.spacing - 0.5)) + 1
        last = int(math.ceil((s + self.half_width) / self.spacing + 0.5)) - 1
        for k in range(first, last + 1):
            if k >= len(self.floors):
                return math.inf
            top = max(top, self.floors[max(k, 0)])
        return top

    # ----------------------------------------------------------------------------------------------- steering

    def steer(self, pl):
        """SJ:292-313: which way to push this tick, as a unit vector in the world: forward on takeoff so the jump
        boost goes down the line, then forward tilted to kill sideways drift. aiming at a point on the line ignores
        how fast we're already drifting toward it, so we'd sail past and swing back forever (the diagonal wiggle).
        ask for a sideways speed that shrinks with the offset instead. departs: the throttle is 1, and the yaw Java
        sets is here the left stick's direction (control.Body.strafe) while the camera leads along the line."""
        pos = (pl.x - self.origin_x, pl.y, pl.z - self.origin_z)
        vel = (pl.vx, pl.vy, pl.vz)
        accel = JUMP_BOOST + self.ground_accel
        if self.ticks > 0:
            self.apex = max(self.apex, pos[1] - self.takeoff_y)
            accel = AIR_ACCEL
        self.ticks += 1
        side_speed = max(-0.5, min(0.5, (-self.across(pos) * 0.3 - self.across(vel)) / accel))   # SJ:309
        forward = math.sqrt(1 - side_speed * side_speed)                     # SJ:310 with throttle 1
        return (self.ux * forward - self.uz * side_speed, self.uz * forward + self.ux * side_speed)   # SJ:311

    def look_point(self, pl, ahead=3.0):
        """A point on the line ahead of the player for the camera to lead to."""
        s = self.along((pl.x - self.origin_x, 0.0, pl.z - self.origin_z))
        return (self.origin_x + self.ux * (s + ahead), pl.y, self.origin_z + self.uz * (s + ahead))

    def __repr__(self):
        return f"SprintJump(floors={self.floors}, spacing={self.spacing:.3f}, target={self.target}, ticks={self.ticks})"


__all__ = ["SprintJump", "Landing", "solid_floor", "clear", "GROUND_FRICTION", "AIR_FRICTION", "AIR_ACCEL", "JUMP_BOOST",
           "JUMP", "GRAVITY", "SAFE_FALL", "MAX_RUNWAY", "MAX_TICKS", "MARGINS", "FULL_SEND", "SPRINT_GROUND_ACCEL"]
