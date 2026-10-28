"""Goals (java/baritone/src/api/java/baritone/api/pathing/goals/*.java), with Baritone's formulas exactly.

A goal answers is_in_goal(x, y, z) and heuristic(x, y, z), the estimated cost in ticks from a position to the goal;
heuristic() with no arguments is the largest heuristic value guaranteed to be inside the goal (used by processes
that stop short, Goal.java:50-57). Baritone reads costHeuristic and the ActionCosts from globals; here they are
constructor arguments with the same defaults, so a Settings or Costs of your own can be passed in.
"""
import math

from .costs import DEFAULT as DEFAULT_COSTS

SQRT_2 = math.sqrt(2)
COST_HEURISTIC = 3.563    # Settings.costHeuristic (ST:455-457), the per-block estimate, a hair under the sprint cost


def calculate_xz(x_diff, z_diff, cost_heuristic=COST_HEURISTIC):
    """GoalXZ.calculate (GoalXZ.java:96-114): the straight part plus the diagonal part times sqrt(2), times
    costHeuristic. Walking forward 1 and right 2 takes 1 + sqrt(2), not sqrt(5)."""
    x = abs(x_diff)
    z = abs(z_diff)
    if x < z:
        straight = z - x
        diagonal = x
    else:
        straight = x - z
        diagonal = z
    diagonal *= SQRT_2
    return (diagonal + straight) * cost_heuristic


def calculate_y(goal_y, current_y, costs=DEFAULT_COSTS):
    """GoalYLevel.calculate (GoalYLevel.java:47-58): half a two-block fall per block down, a jump per block up."""
    if current_y > goal_y:
        return costs.FALL_N_BLOCKS_COST[2] / 2 * (current_y - goal_y)
    if current_y < goal_y:
        return (goal_y - current_y) * costs.JUMP_ONE_BLOCK_COST
    return 0.0


def calculate_block(x_diff, y_diff, z_diff, cost_heuristic=COST_HEURISTIC, costs=DEFAULT_COSTS):
    """GoalBlock.calculate (GoalBlock.java:88-96): the y part as a GoalYLevel of 0 plus the xz part."""
    return calculate_y(0, y_diff, costs) + calculate_xz(x_diff, z_diff, cost_heuristic)


class Goal:
    """The protocol (Goal.java). Subclasses implement is_in_goal and heuristic."""

    def is_in_goal(self, x, y, z):
        raise NotImplementedError

    def heuristic(self, x, y, z):
        raise NotImplementedError

    def heuristic_inside(self):
        """Goal.heuristic() with no arguments: 0 by default (Goal.java:55-57)."""
        return 0.0

    def is_in_goal_pos(self, pos):
        return self.is_in_goal(pos[0], pos[1], pos[2])


class GoalXZ(Goal):
    """GoalXZ.java: a column; the y does not matter."""

    def __init__(self, x, z, cost_heuristic=COST_HEURISTIC):
        self.x, self.z, self.cost_heuristic = x, z, cost_heuristic

    def is_in_goal(self, x, y, z):
        return x == self.x and z == self.z                                           # GoalXZ:50-52

    def heuristic(self, x, y, z):
        return calculate_xz(x - self.x, z - self.z, self.cost_heuristic)             # GoalXZ:55-59

    def __eq__(self, other):
        return isinstance(other, GoalXZ) and (self.x, self.z) == (other.x, other.z)

    def __hash__(self):
        return hash(("GoalXZ", self.x, self.z))

    def __repr__(self):
        return f"GoalXZ{{x={self.x},z={self.z}}}"


class GoalYLevel(Goal):
    """GoalYLevel.java: any position at a height."""

    def __init__(self, level, costs=DEFAULT_COSTS):
        self.level, self.costs = level, costs

    def is_in_goal(self, x, y, z):
        return y == self.level                                                       # GoalYLevel:38-40

    def heuristic(self, x, y, z):
        return calculate_y(self.level, y, self.costs)                                # GoalYLevel:43-45

    def __eq__(self, other):
        return isinstance(other, GoalYLevel) and self.level == other.level

    def __hash__(self):
        return hash(("GoalYLevel", self.level))

    def __repr__(self):
        return f"GoalYLevel{{y={self.level}}}"


class GoalBlock(Goal):
    """GoalBlock.java: stand exactly here (the feet block)."""

    def __init__(self, x, y, z, cost_heuristic=COST_HEURISTIC, costs=DEFAULT_COSTS):
        self.x, self.y, self.z = x, y, z
        self.cost_heuristic, self.costs = cost_heuristic, costs

    def is_in_goal(self, x, y, z):
        return x == self.x and y == self.y and z == self.z                           # GoalBlock:53-55

    def heuristic(self, x, y, z):
        return calculate_block(x - self.x, y - self.y, z - self.z, self.cost_heuristic, self.costs)   # GoalBlock:58-63

    def __eq__(self, other):
        return isinstance(other, GoalBlock) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __hash__(self):
        return hash(("GoalBlock", self.x, self.y, self.z))

    def __repr__(self):
        return f"GoalBlock{{x={self.x},y={self.y},z={self.z}}}"


class GoalNear(Goal):
    """GoalNear.java: within a range (Euclidean, squared) of a position."""

    def __init__(self, x, y, z, range_, cost_heuristic=COST_HEURISTIC, costs=DEFAULT_COSTS):
        self.x, self.y, self.z = x, y, z
        self.range_sq = range_ * range_                                              # GoalNear:48
        self.cost_heuristic, self.costs = cost_heuristic, costs

    def is_in_goal(self, x, y, z):
        xd, yd, zd = x - self.x, y - self.y, z - self.z
        return xd * xd + yd * yd + zd * zd <= self.range_sq                           # GoalNear:52-57

    def heuristic(self, x, y, z):
        return calculate_block(x - self.x, y - self.y, z - self.z, self.cost_heuristic, self.costs)   # GoalNear:60-65

    def heuristic_inside(self):
        """GoalNear:68-91: the largest heuristic of a position inside the goal that is still below every
        position outside it (pull request #1978)."""
        rng = int(math.ceil(math.sqrt(self.range_sq)))
        maybe_always_inside = set()
        min_outside = math.inf
        for dx in range(-rng, rng + 1):
            for dy in range(-rng, rng + 1):
                for dz in range(-rng, rng + 1):
                    h = self.heuristic(self.x + dx, self.y + dy, self.z + dz)
                    if h < min_outside and self.is_in_goal(self.x + dx, self.y + dy, self.z + dz):
                        maybe_always_inside.add(h)
                    else:
                        min_outside = min(min_outside, h)
        max_inside = -math.inf
        for inside in maybe_always_inside:
            if inside < min_outside:
                max_inside = max(max_inside, inside)
        return max_inside

    def __eq__(self, other):
        return isinstance(other, GoalNear) and (self.x, self.y, self.z, self.range_sq) == (other.x, other.y, other.z, other.range_sq)

    def __hash__(self):
        return hash(("GoalNear", self.x, self.y, self.z, self.range_sq))

    def __repr__(self):
        return f"GoalNear{{x={self.x}, y={self.y}, z={self.z}, rangeSq={self.range_sq}}}"


class GoalGetToBlock(Goal):
    """GoalGetToBlock.java: adjacent to a block, standing beside it, on it, or under it (one block below counts
    as two, since the feet would be two below the block's top)."""

    def __init__(self, x, y, z, cost_heuristic=COST_HEURISTIC, costs=DEFAULT_COSTS):
        self.x, self.y, self.z = x, y, z
        self.cost_heuristic, self.costs = cost_heuristic, costs

    def is_in_goal(self, x, y, z):
        xd, yd, zd = x - self.x, y - self.y, z - self.z
        return abs(xd) + abs(yd + 1 if yd < 0 else yd) + abs(zd) <= 1                # GoalGetToBlock:49-54

    def heuristic(self, x, y, z):
        xd, yd, zd = x - self.x, y - self.y, z - self.z
        return calculate_block(xd, yd + 1 if yd < 0 else yd, zd, self.cost_heuristic, self.costs)   # GoalGetToBlock:57-62

    def __eq__(self, other):
        return isinstance(other, GoalGetToBlock) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __hash__(self):
        return hash(("GoalGetToBlock", self.x, self.y, self.z))

    def __repr__(self):
        return f"GoalGetToBlock{{x={self.x},y={self.y},z={self.z}}}"


class GoalTwoBlocks(Goal):
    """GoalTwoBlocks.java: the block or the one below it (the feet in either of the two blocks a player occupies)."""

    def __init__(self, x, y, z, cost_heuristic=COST_HEURISTIC, costs=DEFAULT_COSTS):
        self.x, self.y, self.z = x, y, z
        self.cost_heuristic, self.costs = cost_heuristic, costs

    def is_in_goal(self, x, y, z):
        return x == self.x and (y == self.y or y == self.y - 1) and z == self.z       # GoalTwoBlocks:56-58

    def heuristic(self, x, y, z):
        xd, yd, zd = x - self.x, y - self.y, z - self.z
        return calculate_block(xd, yd + 1 if yd < 0 else yd, zd, self.cost_heuristic, self.costs)   # GoalTwoBlocks:61-66

    def __repr__(self):
        return f"GoalTwoBlocks{{x={self.x},y={self.y},z={self.z}}}"


class GoalComposite(Goal):
    """GoalComposite.java: any of several goals; the heuristic is the nearest."""

    def __init__(self, *goals):
        self.goals = tuple(goals)

    def is_in_goal(self, x, y, z):
        for goal in self.goals:                                                       # GoalComposite:44-50
            if goal.is_in_goal(x, y, z):
                return True
        return False

    def heuristic(self, x, y, z):
        best = math.inf
        for goal in self.goals:                                                       # GoalComposite:53-60
            best = min(best, goal.heuristic(x, y, z))
        return best

    def heuristic_inside(self):
        best = math.inf
        for goal in self.goals:                                                       # GoalComposite:63-70
            best = min(best, goal.heuristic_inside())
        return best

    def __eq__(self, other):
        return isinstance(other, GoalComposite) and self.goals == other.goals

    def __hash__(self):
        return hash(("GoalComposite", self.goals))

    def __repr__(self):
        return f"GoalComposite{list(self.goals)}"


class GoalInverted(Goal):
    """GoalInverted.java: run away from a goal; never reached, the heuristic is the negated original."""

    def __init__(self, origin):
        self.origin = origin

    def is_in_goal(self, x, y, z):
        return False                                                                 # GoalInverted:44-46

    def heuristic(self, x, y, z):
        return -self.origin.heuristic(x, y, z)                                       # GoalInverted:49-51

    def heuristic_inside(self):
        return -math.inf                                                             # GoalInverted:54-56

    def __repr__(self):
        return f"GoalInverted{{{self.origin!r}}}"


__all__ = ["Goal", "GoalXZ", "GoalYLevel", "GoalBlock", "GoalNear", "GoalGetToBlock", "GoalTwoBlocks",
           "GoalComposite", "GoalInverted", "calculate_xz", "calculate_y", "calculate_block", "COST_HEURISTIC", "SQRT_2"]
