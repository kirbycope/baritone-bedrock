"""The movement set: Baritone's Moves enum (pathing/movement/Moves.java, "MV") and the eight Movement classes
(pathing/movement/movements/*.java: "Trav", "Asc", "Desc", "Fall", "Diag", "Down", "Pill", "Park"), planner side,
ported from docs/research-movements.md sections 8 and 9.

Each movement has a static cost function the search calls 22 times per node (traverse_cost, ascend_cost, ...),
and a class the path is assembled from, carrying src, dest, the blocks to break and the block to place, the feet
positions the executor accepts, and calculate_cost(ctx), and the executor side, `tick(state)`: Movement.update
(M:125-179) around this movement's updateState, ported from the sheet's section 9 and written for the controller
model in control.py (a MovementState of keys and a target rotation, applied by Body). Parkour's tick is phase 3.

Positions are (x, y, z) tuples of ints, y the feet block. A cost >= COST_INF is impossible (AS:128). The sheet's
quirks 2, 4, 6 and 8 are reproduced (plan, Fidelity 8); quirk 1 (water) is not (Fidelity 1, context.py); UNKNOWN
is not air (Fidelity 2): a move that read an unread block comes back with `unloaded = True` on its MoveResult.
"""
import math

from .costs import COST_INF
from .world import (WALK_ON, WALK_THROUGH, PASSABLE, PLACE_AGAINST, WATER, LAVA, CLIMB, BOTTOM_SLAB, GRAVITY, LIQUID)
from .helpers import (X_SOUL_SAND, X_MAGMA, X_LADDER_OR_VINE, X_AIR, X_WATER_BLOCK, X_LILY_OR_CARPET, X_FARMLAND,
                      X_FENCE_GATE, X_SLAB_HALF, X_STAIRS, X_REPLACEABLE, WALK_THROUGH_MASK, WALK_ON_MASK,
                      can_walk_on, can_walk_through, must_be_solid_to_walk_on, is_waterlogged_solid_top,
                      is_flowing, avoid_walking_into, is_blocking_door, can_place_against)
from .control import (MovementState, Rotation, rotation_from, center, offset, yaw_to, wrap, EYE, SNEAK_EYE)

SQRT_2 = math.sqrt(2)

# M:38 HORIZONTALS_BUT_ALSO_DOWN_____SO_EVERY_DIRECTION_EXCEPT_UP: the order every "find a face to place against"
# scan uses: NORTH, SOUTH, EAST, WEST, DOWN
AGAINST = ((0, 0, -1), (0, 0, 1), (1, 0, 0), (-1, 0, 0), (0, -1, 0))

# The cost functions below are the inner loop of the search (22 calls per node), so the three questions asked most
# are answered inline with one mask test, and the helpers are called only when a position rule could change the
# answer:
#   f & WALK_THROUGH_MASK == WALK_THROUGH    certainly can_walk_through, and nothing to mine (MH:629)
#   f & WALK_ON_MASK == WALK_ON              certainly can_walk_on
#   f & LIQUID                               the water rules apply: ask the helper
WT = WALK_THROUGH
WTM = WALK_THROUGH_MASK
WO = WALK_ON
WOM = WALK_ON_MASK


class MovementStatus:
    """MovementStatus.java. FAILED is never set by a movement (sheet section 0); PathExecutor treats it as
    UNREACHABLE."""
    PREPPING = "PREPPING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    UNREACHABLE = "UNREACHABLE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    COMPLETE = frozenset((SUCCESS, UNREACHABLE, FAILED, CANCELED))


class MoveResult:
    """MutableMoveResult.java: where a move ends and what it costs, reset to COST_INF before every move, plus
    `unloaded`: the move read an UNKNOWN block (plan, Fidelity 2)."""
    __slots__ = ("x", "y", "z", "cost", "unloaded")

    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0
        self.z = 0
        self.cost = COST_INF
        self.unloaded = False

    def __repr__(self):
        return f"MoveResult({self.x}, {self.y}, {self.z}, cost={self.cost:.4f}, unloaded={self.unloaded})"


# ---------------------------------------------------------------------------------------------------------------
# static cost functions (sheet section 9)
# ---------------------------------------------------------------------------------------------------------------

def traverse_cost(ctx, x, y, z, dest_x, dest_z):
    """MovementTraverse.cost (Trav:76-172): flat, one block cardinal."""
    at = ctx.at
    c = ctx.costs
    pb0 = at(dest_x, y + 1, dest_z)                                          # Trav:77 dest head
    pb1 = at(dest_x, y, dest_z)                                              # Trav:78 dest feet
    dest_on = at(dest_x, y - 1, dest_z)                                      # Trav:79 dest floor
    src_down = at(x, y - 1, z)                                               # Trav:80 src floor
    standing_on_a_block = not src_down & (CLIMB | LIQUID) or must_be_solid_to_walk_on(ctx, x, y - 1, z, src_down)   # Trav:82
    # Trav:83 frostWalker is never true: no enchantments (plan, Fidelity 6)
    if dest_on & WOM == WO or (dest_on & LIQUID and can_walk_on(ctx, dest_x, y - 1, dest_z, dest_on)):   # Trav:84: a walk, not a bridge
        wc = c.WALK_ONE_BLOCK_COST                                           # Trav:85
        water = False
        sneaking = False
        if (pb0 | pb1) & WATER:                                              # Trav:88
            wc = ctx.water_walk_speed                                        # Trav:89
            water = True                                                     # Trav:90
        else:
            if dest_on & X_SOUL_SAND:                                        # Trav:92-93
                wc += (c.WALK_ONE_OVER_SOUL_SAND_COST - c.WALK_ONE_BLOCK_COST) / 2
            elif dest_on & X_WATER_BLOCK:                                    # Trav:96-97
                wc += ctx.walk_on_water_one_penalty
            if src_down & X_SOUL_SAND:                                       # Trav:99-100
                wc += (c.WALK_ONE_OVER_SOUL_SAND_COST - c.WALK_ONE_BLOCK_COST) / 2
            elif ctx.allow_walk_on_magma_blocks and src_down & X_MAGMA:      # Trav:101-104 (quirk 8: src floor only)
                sneaking = True
                wc += (c.SNEAK_ONE_BLOCK_COST - c.WALK_ONE_BLOCK_COST) / 2
        hardness1 = 0.0 if pb1 & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y, dest_z, False, pb1)        # Trav:106
        if hardness1 >= COST_INF:                                            # Trav:107-109
            return COST_INF
        hardness2 = 0.0 if pb0 & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y + 1, dest_z, True, pb0)     # Trav:110
        if hardness1 == 0 and hardness2 == 0:                                # Trav:111
            if not water and not sneaking and ctx.can_sprint:                # Trav:112
                wc *= c.SPRINT_MULTIPLIER                                    # Trav:116
            return wc                                                        # Trav:118
        if src_down & CLIMB:                                                 # Trav:120-123
            hardness1 *= 5
            hardness2 *= 5
        return wc + hardness1 + hardness2                                    # Trav:124
    # a bridge: place the dest floor
    if src_down & CLIMB:                                                     # Trav:126-128
        return COST_INF
    if not dest_on & X_REPLACEABLE:                                          # Trav:129, 170
        return COST_INF
    through_water = bool((pb0 | pb1) & WATER)                                # Trav:130
    if dest_on & WATER and through_water:                                    # Trav:131-134
        return COST_INF
    if not ctx.has_throwaway:                                                # CC:260-262, ahead of the call
        return COST_INF
    place_cost = ctx.cost_of_placing_at(dest_x, y - 1, dest_z, dest_on)      # Trav:135
    if place_cost >= COST_INF:                                               # Trav:136-138
        return COST_INF
    hardness1 = 0.0 if pb1 & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y, dest_z, False, pb1)            # Trav:139
    if hardness1 >= COST_INF:                                                # Trav:140-142
        return COST_INF
    hardness2 = 0.0 if pb0 & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y + 1, dest_z, True, pb0)         # Trav:143
    wc = ctx.water_walk_speed if through_water else c.WALK_ONE_BLOCK_COST    # Trav:144
    for ax, ay, az in AGAINST:                                               # Trav:145-155 side place
        against_x = dest_x + ax
        against_z = dest_z + az
        if against_x == x and against_z == z:                                # Trav:149-151: that would be a backplace
            continue
        if at(against_x, y - 1 + ay, against_z) & PLACE_AGAINST:            # Trav:152-154
            return wc + place_cost + hardness1 + hardness2
    # backplace. quirk 4: canPlaceAgainst(src floor) is never checked, mustBeSolidToWalkOn stands in for it
    if src_down & X_SOUL_SAND or src_down & X_SLAB_HALF:                     # Trav:157-159
        return COST_INF
    if not standing_on_a_block:                                              # Trav:160-162
        return COST_INF
    if at(x, y, z) & X_LILY_OR_CARPET and src_down & LIQUID:                 # Trav:163-166
        return COST_INF
    wc = wc * (c.SNEAK_ONE_BLOCK_COST / c.WALK_ONE_BLOCK_COST)               # Trav:167
    return wc + place_cost + hardness1 + hardness2                           # Trav:168


def ascend_cost(ctx, x, y, z, dest_x, dest_z):
    """MovementAscend.cost (Asc:67-157): up one, cardinal."""
    at = ctx.at
    c = ctx.costs
    to_place = at(dest_x, y, dest_z)                                         # Asc:68: the step block
    additional_placement_cost = 0.0                                          # Asc:69
    if not (to_place & WOM == WO or (to_place & LIQUID and can_walk_on(ctx, dest_x, y, dest_z, to_place))):   # Asc:70
        if not ctx.has_throwaway:                                            # CC:260-262, ahead of the call
            return COST_INF
        additional_placement_cost = ctx.cost_of_placing_at(dest_x, y, dest_z, to_place)   # Asc:71
        if additional_placement_cost >= COST_INF:                            # Asc:72-74
            return COST_INF
        if not to_place & X_REPLACEABLE:                                     # Asc:75-77
            return COST_INF
        found_place_option = False
        for ax, ay, az in AGAINST:                                           # Asc:79-90
            against_x = dest_x + ax
            against_z = dest_z + az
            if against_x == x and against_z == z:                            # Asc:83-85: broken by the time we would use it
                continue
            if at(against_x, y + ay, against_z) & PLACE_AGAINST:            # Asc:86-89
                found_place_option = True
                break
        if not found_place_option:                                           # Asc:91-93
            return COST_INF
    src_up2 = at(x, y + 2, z)                                                # Asc:95
    if at(x, y + 3, z) & GRAVITY and (can_walk_through(ctx, x, y + 1, z) or not src_up2 & GRAVITY):   # Asc:96
        return COST_INF                                                      # Asc:108: it would fall on us
    src_down = at(x, y - 1, z)                                               # Asc:114
    if src_down & CLIMB:                                                     # Asc:115-117
        return COST_INF
    jumping_from_bottom_slab = bool(src_down & BOTTOM_SLAB)                  # Asc:119
    jumping_to_bottom_slab = bool(to_place & BOTTOM_SLAB)                    # Asc:120
    if jumping_from_bottom_slab and not jumping_to_bottom_slab:              # Asc:121-123
        return COST_INF
    if jumping_to_bottom_slab:                                               # Asc:125
        if jumping_from_bottom_slab:
            walk = max(c.JUMP_ONE_BLOCK_COST, c.WALK_ONE_BLOCK_COST) + ctx.jump_penalty    # Asc:127-128
        else:
            walk = c.WALK_ONE_BLOCK_COST                                     # Asc:130: no jump, walk into the slab
    else:
        if to_place & X_SOUL_SAND:                                           # Asc:134-135
            walk = c.WALK_ONE_OVER_SOUL_SAND_COST
        elif to_place & X_MAGMA:                                             # Asc:136-137 (quirk 8: the full sneak cost)
            walk = c.SNEAK_ONE_BLOCK_COST
        else:
            walk = max(c.JUMP_ONE_BLOCK_COST, c.WALK_ONE_BLOCK_COST)         # Asc:139
        walk += ctx.jump_penalty                                             # Asc:141
    total = walk + additional_placement_cost                                 # Asc:144
    if src_up2 & WTM != WT:
        total += ctx.get_mining_duration_ticks(x, y + 2, z, False, src_up2)   # Asc:147
    if total >= COST_INF:                                                    # Asc:148-150
        return COST_INF
    dest_feet = at(dest_x, y + 1, dest_z)
    if dest_feet & WTM != WT:
        total += ctx.get_mining_duration_ticks(dest_x, y + 1, dest_z, False, dest_feet)   # Asc:151
    if total >= COST_INF:                                                    # Asc:152-154
        return COST_INF
    dest_head = at(dest_x, y + 2, dest_z)
    if dest_head & WTM != WT:
        total += ctx.get_mining_duration_ticks(dest_x, y + 2, dest_z, True, dest_head)   # Asc:155
    return total                                                             # Asc:156


def descend_cost(ctx, x, y, z, dest_x, dest_z, res):
    """MovementDescend.cost (Desc:80-135): down one, cardinal, or a Fall through dynamic_fall_cost. Writes `res`;
    a bare return leaves it at COST_INF."""
    at = ctx.at
    c = ctx.costs
    dest_down = at(dest_x, y - 1, dest_z)                                    # Desc:82: dest feet
    total = 0.0 if dest_down & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y - 1, dest_z, False, dest_down)   # Desc:83
    if total >= COST_INF:                                                    # Desc:84-86
        return
    dest_mid = at(dest_x, y, dest_z)
    if dest_mid & WTM != WT:
        total += ctx.get_mining_duration_ticks(dest_x, y, dest_z, False, dest_mid)   # Desc:87
    if total >= COST_INF:                                                    # Desc:88-90
        return
    dest_top = at(dest_x, y + 1, dest_z)
    if dest_top & WTM != WT:
        total += ctx.get_mining_duration_ticks(dest_x, y + 1, dest_z, True, dest_top)   # Desc:91
    if total >= COST_INF:                                                    # Desc:92-94
        return
    from_down = at(x, y - 1, z)                                              # Desc:96
    if from_down & CLIMB:                                                    # Desc:97-99
        return
    below = at(dest_x, y - 2, dest_z)                                        # Desc:111
    if not (below & WOM == WO or (below & LIQUID and can_walk_on(ctx, dest_x, y - 2, dest_z, below))):   # Desc:112
        dynamic_fall_cost(ctx, x, y, z, dest_x, dest_z, total, below, res)   # Desc:113
        return
    if dest_down & X_LADDER_OR_VINE:                                         # Desc:117-119
        return
    # Desc:120-122 canUseFrostWalker: never (plan, Fidelity 6)
    walk = c.WALK_OFF_BLOCK_COST                                             # Desc:125
    if from_down & X_SOUL_SAND:                                              # Desc:126-129
        walk *= c.WALK_ONE_OVER_SOUL_SAND_COST / c.WALK_ONE_BLOCK_COST
    total += walk + max(c.FALL_N_BLOCKS_COST[1], c.CENTER_AFTER_FALL_COST)   # Desc:130
    res.x = dest_x                                                           # Desc:131-134
    res.y = y - 1
    res.z = dest_z
    res.cost = total


def dynamic_fall_cost(ctx, x, y, z, dest_x, dest_z, front_break, below, res):
    """MovementDescend.dynamicFallCost (Desc:137-224): scan down from a drop of 2. True only when a bucket would
    be placed, which never happens here. Water landings have no height cap (quirk 5, as written)."""
    at = ctx.at
    c = ctx.costs
    fall = c.FALL_N_BLOCKS_COST
    if front_break != 0 and at(dest_x, y + 2, dest_z) & GRAVITY:            # Desc:138-142
        return False
    if not can_walk_through(ctx, dest_x, y - 2, dest_z, below):              # Desc:144-146
        return False
    cost_so_far = 0.0                                                        # Desc:147
    effective_start_height = y                                               # Desc:148
    fall_height = 3
    while True:                                                              # Desc:149
        new_y = y - fall_height                                              # Desc:150
        if new_y < ctx.min_y:                                                # Desc:151-154: the void
            return False
        reached_minimum = fall_height >= ctx.min_fall_height                 # Desc:156
        onto = at(dest_x, new_y, dest_z)                                     # Desc:157
        unprotected = fall_height - (y - effective_start_height)             # Desc:158
        tentative = c.WALK_OFF_BLOCK_COST + fall[unprotected] + front_break + cost_so_far   # Desc:159
        if reached_minimum and onto & WATER and not is_waterlogged_solid_top(onto):          # Desc:160
            if not can_walk_through(ctx, dest_x, new_y, dest_z, onto):       # Desc:161-163
                return False
            if ctx.assume_walk_on_water:                                     # Desc:164-166
                return False
            if is_flowing(ctx, dest_x, new_y, dest_z, onto):                 # Desc:167-169
                return False
            if not can_walk_on(ctx, dest_x, new_y - 1, dest_z):              # Desc:170-173: punch right through
                return False
            res.x = dest_x                                                   # Desc:175-178: feet in the water
            res.y = new_y
            res.z = dest_z
            res.cost = tentative
            return False                                                     # Desc:179
        if reached_minimum and ctx.allow_fall_into_lava and onto & LAVA:     # Desc:181-188 (Elytra only)
            res.x = dest_x
            res.y = new_y
            res.z = dest_z
            res.cost = tentative
            return False
        if unprotected <= 11 and onto & CLIMB:                               # Desc:189
            cost_so_far += fall[unprotected - 1]                             # Desc:192
            cost_so_far += c.LADDER_DOWN_ONE_COST                            # Desc:193
            effective_start_height = new_y                                   # Desc:194
            fall_height += 1
            continue
        if onto & WTM == WT or (onto & LIQUID and can_walk_through(ctx, dest_x, new_y, dest_z, onto)):   # Desc:197-199
            fall_height += 1
            continue
        if not (onto & WOM == WO or (onto & LIQUID and can_walk_on(ctx, dest_x, new_y, dest_z, onto))):   # Desc:200-202
            return False
        if onto & BOTTOM_SLAB:                                               # Desc:203-205: really glitchy
            return False
        if reached_minimum and unprotected <= ctx.max_fall_height_no_water + 1:    # Desc:206
            res.x = dest_x                                                   # Desc:208-212
            res.y = new_y + 1
            res.z = dest_z
            res.cost = tentative
            return False
        if reached_minimum and ctx.has_water_bucket and unprotected <= ctx.max_fall_height_bucket + 1:   # Desc:214
            res.x = dest_x                                                   # Desc:215-219
            res.y = new_y + 1
            res.z = dest_z
            res.cost = tentative + ctx.place_bucket_cost()
            return True
        return False                                                         # Desc:221


def diagonal_cost(ctx, x, y, z, dest_x, dest_z, res):
    """MovementDiagonal.cost (Diag:116-273): one block on each axis, flat, or ascend/descend when allowed. Writes
    `res`; a bare return leaves it at COST_INF. Diagonals never break blocks (quirk 6)."""
    at = ctx.at
    c = ctx.costs
    dest_into_upper = at(dest_x, y + 1, dest_z)                              # Diag:117
    if not (dest_into_upper & WTM == WT or (dest_into_upper & LIQUID and can_walk_through(ctx, dest_x, y + 1, dest_z, dest_into_upper))):   # Diag:118-120
        return
    dest_into = at(dest_x, y, dest_z)                                        # Diag:121
    ascend = False                                                           # Diag:123
    descend = False                                                          # Diag:125
    sneaking = False                                                         # Diag:127
    if not (dest_into & WTM == WT or (dest_into & LIQUID and can_walk_through(ctx, dest_x, y, dest_z, dest_into))):   # Diag:128
        ascend = True                                                        # Diag:129
        if (not ctx.allow_diagonal_ascend or not can_walk_through(ctx, x, y + 2, z)
                or not can_walk_on(ctx, dest_x, y, dest_z, dest_into)
                or not can_walk_through(ctx, dest_x, y + 2, dest_z)):        # Diag:130-132
            return
        dest_walk_on = dest_into                                             # Diag:133
        from_down = at(x, y - 1, z)                                          # Diag:134
    else:
        dest_walk_on = at(dest_x, y - 1, dest_z)                             # Diag:136
        from_down = at(x, y - 1, z)                                          # Diag:137
        # Diag:138-139 frostWalker: never (plan, Fidelity 6)
        if not (dest_walk_on & WOM == WO or (dest_walk_on & LIQUID and can_walk_on(ctx, dest_x, y - 1, dest_z, dest_walk_on))):   # Diag:140
            descend = True                                                   # Diag:141
            if (not ctx.allow_diagonal_descend or not can_walk_on(ctx, dest_x, y - 2, dest_z)
                    or not can_walk_through(ctx, dest_x, y - 1, dest_z, dest_walk_on)):   # Diag:142-144
                return
    start_state = at(x, y, z)                                                # Diag:148
    # Diag:149: the second call passes (x, y) where (x, z) was meant: quirk 2, reproduced
    if is_blocking_door(ctx, start_state, x, z, dest_x, dest_z) \
            or is_blocking_door(ctx, dest_into_upper if ascend else dest_into, dest_x, dest_z, x, y):
        return                                                               # Diag:150
    multiplier = c.WALK_ONE_BLOCK_COST                                       # Diag:152
    if dest_walk_on & X_SOUL_SAND:                                           # Diag:154-155
        multiplier += (c.WALK_ONE_OVER_SOUL_SAND_COST - c.WALK_ONE_BLOCK_COST) / 2
    elif ctx.allow_walk_on_magma_blocks and dest_walk_on & X_MAGMA:          # Diag:156-158 (quirk 8: both floors)
        multiplier += (c.SNEAK_ONE_BLOCK_COST - c.WALK_ONE_BLOCK_COST) / 2
        sneaking = True
    elif dest_walk_on & X_WATER_BLOCK:                                       # Diag:161-162
        multiplier += ctx.walk_on_water_one_penalty * SQRT_2
    if from_down & CLIMB:                                                    # Diag:165-167
        return
    if from_down & X_SOUL_SAND:                                              # Diag:168-169
        multiplier += (c.WALK_ONE_OVER_SOUL_SAND_COST - c.WALK_ONE_BLOCK_COST) / 2
    elif ctx.allow_walk_on_magma_blocks and from_down & X_MAGMA:             # Diag:170-173
        multiplier += (c.SNEAK_ONE_BLOCK_COST - c.WALK_ONE_BLOCK_COST) / 2
        sneaking = True
    cutting_over1 = at(x, y - 1, dest_z)                                     # Diag:174
    if (not ctx.allow_walk_on_magma_blocks and cutting_over1 & X_MAGMA) or cutting_over1 & LAVA:   # Diag:175-177
        return
    cutting_over2 = at(dest_x, y - 1, z)                                     # Diag:178
    if (not ctx.allow_walk_on_magma_blocks and cutting_over2 & X_MAGMA) or cutting_over2 & LAVA:   # Diag:179-181
        return
    water = False                                                            # Diag:182
    if start_state & WATER or dest_into & WATER:                             # Diag:184
        if ascend:                                                           # Diag:185-187
            return
        multiplier = ctx.water_walk_speed                                    # Diag:191: replaces everything
        water = True                                                         # Diag:192
    pb0 = at(x, y, dest_z)                                                   # Diag:194: corner A low
    pb2 = at(dest_x, y, z)                                                   # Diag:195: corner B low
    if ascend:                                                               # Diag:196
        a_top = can_walk_through(ctx, x, y + 2, dest_z)                      # Diag:197
        a_mid = can_walk_through(ctx, x, y + 1, dest_z)                      # Diag:198
        a_low = can_walk_through(ctx, x, y, dest_z, pb0)                     # Diag:199
        b_top = can_walk_through(ctx, dest_x, y + 2, z)                      # Diag:200
        b_mid = can_walk_through(ctx, dest_x, y + 1, z)                      # Diag:201
        b_low = can_walk_through(ctx, dest_x, y, z, pb2)                     # Diag:202
        a_low = a_low and not is_blocking_door(ctx, pb0, x, dest_z, dest_x, z)         # Diag:203
        b_low = b_low and not is_blocking_door(ctx, pb2, dest_x, z, x, dest_z)         # Diag:204
        if ((not (a_top and a_mid and a_low) and not (b_top and b_mid and b_low))      # Diag:205: no option
                or avoid_walking_into(pb0) or avoid_walking_into(pb2)                  # Diag:206-207
                or (a_top and a_mid and can_walk_on(ctx, x, y, dest_z, pb0))           # Diag:208: could just ascend
                or (b_top and b_mid and can_walk_on(ctx, dest_x, y, z, pb2))           # Diag:209
                or (not a_top and a_mid and a_low)                                     # Diag:210: head bonk A
                or (not b_top and b_mid and b_low)):                                   # Diag:211: head bonk B
            return
        res.cost = multiplier * SQRT_2 + c.JUMP_ONE_BLOCK_COST + ctx.jump_penalty      # Diag:214
        res.x = dest_x                                                       # Diag:215-217
        res.z = dest_z
        res.y = y + 1
        return
    option_a = 0.0 if pb0 & WTM == WT else ctx.get_mining_duration_ticks(x, y, dest_z, False, pb0)      # Diag:220
    option_b = 0.0 if pb2 & WTM == WT else ctx.get_mining_duration_ticks(dest_x, y, z, False, pb2)      # Diag:221
    option_a += 1 if is_blocking_door(ctx, pb0, x, dest_z, dest_x, z) else 0           # Diag:222
    option_b += 1 if is_blocking_door(ctx, pb2, dest_x, z, x, dest_z) else 0           # Diag:223
    if option_a != 0 and option_b != 0:                                      # Diag:224-228
        return
    pb1 = at(x, y + 1, dest_z)                                               # Diag:229: corner A mid
    if pb1 & WTM != WT:
        option_a += ctx.get_mining_duration_ticks(x, y + 1, dest_z, True, pb1)   # Diag:230
    if option_a != 0 and option_b != 0:                                      # Diag:231-234
        return
    pb3 = at(dest_x, y + 1, z)                                               # Diag:235: corner B mid
    if option_a == 0 and ((avoid_walking_into(pb2) and not pb2 & X_WATER_BLOCK) or avoid_walking_into(pb3)):   # Diag:236-239
        return
    if pb3 & WTM != WT:
        option_b += ctx.get_mining_duration_ticks(dest_x, y + 1, z, True, pb3)   # Diag:240
    if option_a != 0 and option_b != 0:                                      # Diag:241-244
        return
    if option_b == 0 and ((avoid_walking_into(pb0) and not pb0 & X_WATER_BLOCK) or avoid_walking_into(pb1)):   # Diag:245-248
        return
    if option_a != 0 or option_b != 0:                                       # Diag:249: edging around one side
        multiplier *= SQRT_2 - 0.001                                         # Diag:250 (quirk 6: no mining cost added)
        if start_state & CLIMB:                                              # Diag:251-254
            return
    else:
        if ctx.can_sprint and not water and not sneaking:                    # Diag:257-262
            multiplier *= c.SPRINT_MULTIPLIER
    res.cost = multiplier * SQRT_2                                           # Diag:264
    if descend:                                                              # Diag:265-267
        res.cost += max(c.FALL_N_BLOCKS_COST[1], c.CENTER_AFTER_FALL_COST)
        res.y = y - 1
    else:
        res.y = y                                                            # Diag:269
    res.x = dest_x                                                           # Diag:271-272
    res.z = dest_z


def downward_cost(ctx, x, y, z):
    """MovementDownward.cost (Down:57-72): mine the block under the feet and drop one."""
    if not ctx.allow_downward:                                               # Down:58-60
        return COST_INF
    at = ctx.at
    floor = at(x, y - 2, z)
    if not (floor & WOM == WO or (floor & LIQUID and can_walk_on(ctx, x, y - 2, z, floor))):   # Down:61-63: the floor under it
        return COST_INF
    down = at(x, y - 1, z)                                                   # Down:64
    if down & X_LADDER_OR_VINE:                                              # Down:66-67
        return ctx.costs.LADDER_DOWN_ONE_COST
    mining = 0.0 if down & WTM == WT else ctx.get_mining_duration_ticks(x, y - 1, z, False, down)
    return ctx.costs.FALL_N_BLOCKS_COST[1] + mining   # Down:70


def pillar_cost(ctx, x, y, z):
    """MovementPillar.cost (Pill:58-136): jump and place under yourself, climb a ladder, or swim up a water
    column."""
    at = ctx.at
    c = ctx.costs
    from_state = at(x, y, z)                                                 # Pill:59 src feet
    ladder = bool(from_state & CLIMB)                                        # Pill:61
    from_down = at(x, y - 1, z)                                              # Pill:62 src floor
    if not ladder:                                                           # Pill:63
        if from_down & CLIMB:                                                # Pill:64-66
            return COST_INF
        if from_down & BOTTOM_SLAB:                                          # Pill:67-69: SlabBlock with TYPE == BOTTOM
            return COST_INF
    to_break = at(x, y + 2, z)                                               # Pill:71 src head+1
    if to_break & X_FENCE_GATE:                                              # Pill:73-75: issue #172
        return COST_INF
    src_up = None                                                            # Pill:76
    if to_break & WATER and from_state & WATER:                              # Pill:77
        src_up = at(x, y + 1, z)                                             # Pill:78
        if src_up & WATER:                                                   # Pill:79-81: a pillar of water
            return c.LADDER_UP_ONE_COST
    place_cost = 0.0                                                         # Pill:83
    if not ladder:                                                           # Pill:84
        if not ctx.has_throwaway:                                            # CC:260-262, ahead of the call
            return COST_INF
        place_cost = ctx.cost_of_placing_at(x, y, z, from_state)             # Pill:86
        if place_cost >= COST_INF:                                           # Pill:87-89
            return COST_INF
        if from_down & X_AIR:                                                # Pill:90-92
            place_cost += 0.1
    if (from_state & LIQUID and not from_down & PLACE_AGAINST) \
            or (from_down & LIQUID and not is_waterlogged_solid_top(from_down) and ctx.assume_walk_on_water):   # Pill:94-98
        return COST_INF
    if from_state & X_LILY_OR_CARPET and from_down & LIQUID:                 # Pill:100-102
        return COST_INF
    hardness = 0.0 if to_break & WTM == WT else ctx.get_mining_duration_ticks(x, y + 2, z, True, to_break)   # Pill:104
    if hardness >= COST_INF:                                                 # Pill:105-107
        return COST_INF
    if hardness != 0:                                                        # Pill:108
        if to_break & CLIMB:                                                 # Pill:109-110
            hardness = 0.0
        else:
            check = at(x, y + 3, z)                                          # Pill:112
            if check & GRAVITY:                                              # Pill:113
                if src_up is None:                                           # Pill:115-117
                    src_up = at(x, y + 1, z)
                if not to_break & GRAVITY or not src_up & GRAVITY:           # Pill:118-120
                    return COST_INF
    if ladder:                                                               # Pill:131-132
        return c.LADDER_UP_ONE_COST + hardness * 5
    return c.JUMP_ONE_BLOCK_COST + place_cost + ctx.jump_penalty + hardness  # Pill:134


def check_overshoot_safety(ctx, x, y, z):
    """Park:203-206: the two blocks walked into after the landing are not dangerous."""
    at = ctx.at
    return not avoid_walking_into(at(x, y, z)) and not avoid_walking_into(at(x, y + 1, z))


def cost_from_jump_distance(c, dist):
    """Park:208-219."""
    if dist == 2:
        return c.WALK_ONE_BLOCK_COST * 2
    if dist == 3:
        return c.WALK_ONE_BLOCK_COST * 3
    if dist == 4:
        return c.SPRINT_ONE_BLOCK_COST * 4
    raise ValueError(f"LOL {dist}")


def parkour_cost(ctx, x, y, z, x_diff, z_diff, res):
    """MovementParkour.cost (Park:63-201): jump a gap of 1 to 3 blocks, flat, up one, or with a block placed
    under the landing. Writes `res`; a bare return leaves it at COST_INF."""
    if not ctx.allow_parkour:                                                # Park:64-66
        return
    if not ctx.allow_jump_at_build_limit and y >= ctx.max_y:                 # Park:67-69
        return
    at = ctx.at
    c = ctx.costs
    if not at(x + x_diff, y, z + z_diff) & PASSABLE:                         # Park:72-74
        return
    adj = at(x + x_diff, y - 1, z + z_diff)                                  # Park:76
    if can_walk_on(ctx, x + x_diff, y - 1, z + z_diff, adj):                 # Park:77-79: could just traverse
        return
    if avoid_walking_into(adj) and not adj & WATER:                          # Park:81-83: magma sucks
        return
    if not at(x + x_diff, y + 1, z + z_diff) & PASSABLE:                     # Park:84-86
        return
    if not at(x + x_diff, y + 2, z + z_diff) & PASSABLE:                     # Park:87-89
        return
    if not at(x, y + 2, z) & PASSABLE:                                       # Park:90-92
        return
    standing_on = at(x, y - 1, z)                                            # Park:93
    if standing_on & CLIMB or standing_on & X_STAIRS or standing_on & BOTTOM_SLAB:   # Park:94-96
        return
    if ctx.assume_walk_on_water and standing_on & LIQUID and not is_waterlogged_solid_top(standing_on):   # Park:98-100
        return
    if at(x, y, z) & LIQUID:                                                 # Park:101-103: can't jump out of water
        return
    if ctx.allow_walk_on_magma_blocks and standing_on & X_MAGMA:             # Park:105-106
        max_jump = 2
    elif standing_on & X_SOUL_SAND:                                          # Park:107-108
        max_jump = 2
    elif ctx.can_sprint:                                                     # Park:109-110
        max_jump = 4
    else:                                                                    # Park:111-112
        max_jump = 3
    max_jump = min(max_jump, ctx.max_parkour_jump)                           # departs: settings.maxParkourJump
    verified_max_jump = 1                                                    # Park:115
    for i in range(2, max_jump + 1):                                         # Park:117
        dest_x = x + x_diff * i                                              # Park:118-119
        dest_z = z + z_diff * i
        if not at(dest_x, y + 1, dest_z) & PASSABLE:                         # Park:122-124
            break
        if not at(dest_x, y + 2, dest_z) & PASSABLE:                         # Park:125-127
            break
        dest_into = at(dest_x, y, dest_z)                                    # Park:130
        if not dest_into & PASSABLE:                                         # Park:131
            if (i <= 3 and ctx.allow_parkour_ascend and ctx.can_sprint
                    and can_walk_on(ctx, dest_x, y, dest_z, dest_into)
                    and check_overshoot_safety(ctx, dest_x + x_diff, y + 1, dest_z + z_diff)):   # Park:132
                res.x = dest_x                                               # Park:133-136
                res.y = y + 1
                res.z = dest_z
                res.cost = i * c.SPRINT_ONE_BLOCK_COST + ctx.jump_penalty
                return
            break                                                            # Park:139
        landing_on = at(dest_x, y - 1, dest_z)                               # Park:144
        # Park:146-147: a farmland landing is refused; the frost walker clause never holds (plan, Fidelity 6)
        if not landing_on & X_FARMLAND and can_walk_on(ctx, dest_x, y - 1, dest_z, landing_on):
            if check_overshoot_safety(ctx, dest_x + x_diff, y, dest_z + z_diff):   # Park:149
                res.x = dest_x                                               # Park:150-154
                res.y = y
                res.z = dest_z
                res.cost = cost_from_jump_distance(c, i) + ctx.jump_penalty
                return
            break                                                            # Park:156
        if not at(dest_x, y + 3, dest_z) & PASSABLE:                         # Park:159-161
            break
        verified_max_jump = i                                                # Park:163
    if not ctx.allow_parkour_place:                                          # Park:167-169
        return
    for i in range(verified_max_jump, 1, -1):                                # Park:171: the longest jump first
        dest_x = x + i * x_diff
        dest_z = z + i * z_diff
        to_replace = at(dest_x, y - 1, dest_z)                               # Park:174
        place_cost = ctx.cost_of_placing_at(dest_x, y - 1, dest_z, to_replace)   # Park:175
        if place_cost >= COST_INF:                                           # Park:176-178
            continue
        if not to_replace & X_REPLACEABLE:                                   # Park:179-181
            continue
        if not check_overshoot_safety(ctx, dest_x + x_diff, y, dest_z + z_diff):   # Park:182-184
            continue
        for ax, ay, az in AGAINST:                                           # Park:185
            against_x = dest_x + ax
            against_y = y - 1 + ay
            against_z = dest_z + az
            if against_x == dest_x - x_diff and against_z == dest_z - z_diff:    # Park:189-191: can't turn that fast
                continue
            if at(against_x, against_y, against_z) & PLACE_AGAINST:         # Park:192
                res.x = dest_x                                               # Park:193-197
                res.y = y
                res.z = dest_z
                res.cost = cost_from_jump_distance(c, i) + place_cost + ctx.jump_penalty
                return


def parkour_cost_to(ctx, x, y, z, dest_x, dest_z, res):
    """parkour_cost with the nominal destination (src + 4 along the cardinal) in place of the direction, the
    shape the search calls every dynamic move with."""
    parkour_cost(ctx, x, y, z, (dest_x > x) - (dest_x < x), (dest_z > z) - (dest_z < z), res)


# ---------------------------------------------------------------------------------------------------------------
# the Movement classes (Movement.java, "M", and the eight subclasses)
# ---------------------------------------------------------------------------------------------------------------

def _context_of(ctx_or_world):
    """to_break/to_place/to_walk_into take the executor's live CalculationContext, or a bare Terrain."""
    if hasattr(ctx_or_world, "get_mining_duration_ticks"):
        return ctx_or_world
    from .context import CalculationContext
    return CalculationContext(ctx_or_world)


class Movement:
    """M:40-116, 440-500: the planner-side state of one movement in a path."""
    name = "Movement"

    def __init__(self, src, dest, positions_to_break, position_to_place=None):
        self.src = tuple(src)                                                # M:44-46
        self.dest = tuple(dest)
        self.positions_to_break = tuple(tuple(p) for p in positions_to_break)   # M:51
        self.position_to_place = tuple(position_to_place) if position_to_place is not None else None   # M:56
        self._cost = None                                                    # M:58
        self.current = MovementState(MovementStatus.PREPPING)                # M:42 currentState
        self.aim_slot = 0            # which aim candidate break_block is on (RotationUtils.reachable, in turn)
        self.aim_settled = 0
        self.to_break_cached = None                                          # M:60-62
        self.to_place_cached = None
        self.to_walk_into_cached = None
        self._valid_positions = None                                         # M:64
        self.calculated_while_loaded = None                                  # M:66
        self.reset_counters()

    # ---- cost (M:81-101) ----

    def get_cost(self, ctx=None):
        """M:81-90: the cached cost; with a context, calculated on first use."""
        if ctx is not None and self._cost is None:
            self._cost = self.calculate_cost(ctx)
        return self._cost

    def calculate_cost(self, ctx):
        raise NotImplementedError

    def recalculate_cost(self, ctx):
        """M:94-97."""
        self._cost = None
        return self.get_cost(ctx)

    def override(self, cost):
        """M:99-101: the path assembly's min(calculate_cost, node cost difference) (Path.java:135)."""
        self._cost = cost

    # ---- geometry (M:103-116, 440-442) ----

    @property
    def direction(self):
        """M:440-442: dest - src."""
        return (self.dest[0] - self.src[0], self.dest[1] - self.src[1], self.dest[2] - self.src[2])

    def calculate_valid_positions(self):
        raise NotImplementedError

    @property
    def valid_positions(self):
        """M:105-111: the feet positions the executor accepts while this movement runs."""
        if self._valid_positions is None:
            self._valid_positions = frozenset(tuple(p) for p in self.calculate_valid_positions())
        return self._valid_positions

    # ---- block bookkeeping (M:445-500) ----

    def check_loaded_chunk(self, ctx):
        """M:445-447."""
        self.calculated_while_loaded = ctx.is_loaded(self.dest[0], self.dest[2])

    def reset_block_cache(self):
        """M:455-459."""
        self.to_break_cached = None
        self.to_place_cached = None
        self.to_walk_into_cached = None

    def to_break(self, ctx_or_world):
        """M:462-473: the positions to break that are not yet walk-through."""
        if self.to_break_cached is None:
            ctx = _context_of(ctx_or_world)
            self.to_break_cached = [p for p in self.positions_to_break if not can_walk_through(ctx, *p)]
        return self.to_break_cached

    def to_place(self, ctx_or_world):
        """M:475-484: the position to place when it is not yet walkable."""
        if self.to_place_cached is None:
            ctx = _context_of(ctx_or_world)
            p = self.position_to_place
            self.to_place_cached = [p] if p is not None and not can_walk_on(ctx, *p) else []
        return self.to_place_cached

    def to_walk_into(self, ctx_or_world):
        """M:486-491: empty except for a Diagonal."""
        if self.to_walk_into_cached is None:
            self.to_walk_into_cached = []
        return self.to_walk_into_cached

    def to_break_all(self):
        return self.positions_to_break

    # ---- execution (M:114-179, 353-437) ----

    @property
    def status(self):
        return self.current.status

    @status.setter
    def status(self, value):
        self.current.status = value

    def reset(self):
        """M:118-123 reset: back to PREPPING with the block caches dropped."""
        self.current = MovementState(MovementStatus.PREPPING)
        self.reset_block_cache()
        self.aim_slot = self.aim_settled = 0
        self.reset_counters()

    def reset_counters(self):
        """The per-movement tick counters (numTicks and the like), fresh for every attempt."""

    def safe_to_cancel(self, state=None):
        """M:395-402: true unless the subclass says otherwise."""
        return True

    def player_in_valid_position(self, s):
        """M:114-116: the feet, or pathStart, in the valid positions."""
        if s.player.feet in self.valid_positions:
            return True
        return s.path_start is not None and s.path_start() in self.valid_positions

    def tick(self, s):
        """M:125-179 update(): this tick's updateState, then the water handling, with the keys and the target left
        in self.current for the executor, which decides sprint (PE:348-352) and hands them to control.Body.apply.
        `s` is the executor's TickState (player, ctx, body, settings, path_start, events). Returns the status."""
        ms = self.current
        ms.clear()                                                           # M:167-171: the keys last one tick
        self.update_state(s)
        pl = s.player
        if pl.water or s.ctx.at(*pl.feet) & LIQUID:                          # M:128
            # M:129-143 shouldSwim needs allowSwimming, off here (settings.py); M:144-155 is the default bob
            if pl.y < self.dest[1] + 0.6:                                     # M:152-154
                ms.set_input("jump", True)
        # M:157-160 isInWall: the probe has no such flag; departs
        if ms.status in MovementStatus.COMPLETE:                             # M:173-176
            ms.inputs = {}
        return ms.status

    def update_state(self, s):
        """M:425-437: the status machine. The subclasses call this first and go on only when RUNNING."""
        ms = self.current
        if not self.prepared(s):                                             # M:426-427
            ms.status = MovementStatus.PREPPING
            return ms
        if ms.status == MovementStatus.PREPPING:                             # M:428-430
            ms.status = MovementStatus.WAITING
        if ms.status == MovementStatus.WAITING:                              # M:432-434
            ms.status = MovementStatus.RUNNING
        return ms

    def prepared(self, s):
        """M:353-393: break what is in positionsToBreak, in order, before running. walkWhileBreaking is off (plan,
        Fidelity 4), so the player stands while this works."""
        if self.current.status == MovementStatus.WAITING:                    # M:354-356
            return True
        for pos in self.positions_to_break:
            # M:359 pauseMiningForFallingBlocks: the probe reports no falling block entities; departs
            if not can_walk_through(s.ctx, *pos):                            # M:362
                if s.ctx.at(*pos) & LIQUID:
                    # departs: a liquid cannot be broken, and a path through one means the band was stale or the
                    # water has since flowed (run 7 stood "Breaking water" at a shore for a whole leg); give up
                    # the movement so the path is planned again on what is there now
                    s.ctx.block_changed()
                    self.current.status = MovementStatus.UNREACHABLE
                    return True
                self.break_block(s, pos)
                return False
        return True                                                          # M:386-392 is dead code (quirk 3)

    def break_block(self, s, pos):
        """M:364-383 on the controller: the best tool (RB), a forced rotation at a point of the block the crosshair
        can reach (RotationUtils.reachable: the centre, then the faces), and CLICK_LEFT (RT held) while the probe
        says the crosshair is on it, or on another block of this movement's own to-break set (the M:378-383
        fallback clicks whatever is in the way)."""
        pl, ctx, ms, body = s.player, s.ctx, self.current, s.body
        body.select_slot(pl, ctx.toolset.best_slot(ctx.kind(*pos)))        # M:364 switchToBestToolFor
        ms.set_target(self.aim_rotation(pl, self.aim_point(s, pos)), True)   # M:368
        on = pl.crosshair()
        if on == tuple(pos) or (on is not None and on in self.positions_to_break and not can_walk_through(ctx, *on)):
            ms.set_input("click_left", True)                                 # M:369-371
            self.aim_settled = 0

    @staticmethod
    def aim_rotation(pl, point, eye=None):
        """The rotation that looks from the eye at a point; for a point within 0.2 of straight below (or above) the
        yaw is kept, since rotation_from's would be noise and the camera would chase it (Pill:162-164 does the same
        for the pillar's own floor)."""
        eye = pl.eye if eye is None else eye
        rotation = rotation_from(eye, point)
        if math.hypot(point[0] - eye[0], point[2] - eye[2]) < 0.2:
            return Rotation(pl.yaw, rotation.pitch)
        return rotation

    def aim_point(self, s, pos):
        """RotationUtils.reachable (RotationUtils.java:177-225): the block centre, then the face centres. The probe
        does not raytrace a rotation ahead of time, so the candidates are tried in turn: when the camera has settled
        on one for three ticks without the crosshair reaching the block, the next is tried."""
        pl = s.player
        cands = self.aim_candidates(pos, pl)
        want = rotation_from(pl.eye, cands[self.aim_slot % len(cands)])
        if abs(wrap(want.yaw - pl.yaw)) < 3 and abs(want.pitch - pl.pitch) < 3 and not pl.is_looking_at(pos):
            self.aim_settled += 1
            if self.aim_settled >= 3:
                self.aim_slot, self.aim_settled = self.aim_slot + 1, 0
        return cands[self.aim_slot % len(cands)]

    @staticmethod
    def aim_candidates(pos, pl):
        """The centre, then points just inside the faces: the two faces toward the player, then up and down."""
        c = center(pos)
        dx, dz = pl.x - c[0], pl.z - c[2]
        sx = math.copysign(1.0, dx) if dx else 1.0
        sz = math.copysign(1.0, dz) if dz else 1.0
        order = [(sx, 0, 0), (0, 0, sz)] if abs(dx) >= abs(dz) else [(0, 0, sz), (sx, 0, 0)]
        order += [(0, 1, 0), (0, -1, 0)]
        return [c] + [(c[0] + 0.45 * ox, c[1] + 0.45 * oy, c[2] + 0.45 * oz) for ox, oy, oz in order]

    def move_towards(self, s, pos):
        """MH:685-692 moveTowards: face the block centre (yaw only, not forced) and press MOVE_FORWARD. The body
        reaches the point through the left stick whatever the camera is doing (control.Body.apply)."""
        ms, pl = self.current, s.player
        c = center(pos)
        ms.set_target(Rotation(yaw_to(c[0] - pl.x, c[2] - pl.z), None), False)
        ms.move_to = c
        ms.set_input("forward", True)

    def move_towards_point(self, s, point):
        """moveTowards at a world point rather than a block centre."""
        ms, pl = self.current, s.player
        ms.set_target(Rotation(yaw_to(point[0] - pl.x, point[2] - pl.z), None), False)
        ms.move_to = tuple(point)
        ms.set_input("forward", True)

    def move_towards_with_slight_rotation(self, s, pos):
        """MH:714-729 snaps the yaw to the nearest 45 degree multiple and walks with key combinations, because
        Baritone's body only goes along its keys. departs: the left stick points anywhere, so this is moveTowards."""
        self.move_towards(s, pos)

    NO_OPTION, ATTEMPTING, READY_TO_PLACE = "NO_OPTION", "ATTEMPTING", "READY_TO_PLACE"   # MH PlaceResult

    def attempt_to_place(self, s, place_at, prefer_down, would_sneak):
        """MH:844-898 attemptToPlaceABlock, with the probe's crosshair block and face in place of the raytrace."""
        pl, ctx, ms, body = s.player, s.ctx, self.current, s.body
        acceptable = ctx.settings.acceptableThrowawayItems
        place_at = tuple(place_at)
        found = None
        eye = (pl.x, pl.y + (SNEAK_EYE if would_sneak else EYE), pl.z)       # MH:849: the sneaking eye
        for a in AGAINST:                                                    # MH:852
            against = offset(place_at, a)
            if not can_place_against(ctx, *against):                         # MH:853
                continue
            if not body.throwaway_in(pl, acceptable):                        # MH:854-858
                ms.status = MovementStatus.UNREACHABLE
                return self.NO_OPTION
            face = ((place_at[0] + against[0] + 1) * 0.5, (place_at[1] + against[1] + 0.5) * 0.5,
                    (place_at[2] + against[2] + 1) * 0.5)                    # MH:859-861: the shared face
            # MH:862-868 raytrace: without one, the face counts when it is within reach and its outside is toward
            # the eye, and the crosshair check below is the real test
            toward = (eye[0] - face[0]) * -a[0] + (eye[1] - face[1]) * -a[1] + (eye[2] - face[2]) * -a[2]
            if toward > 0.05 and math.dist(eye, face) <= ctx.settings.blockReachDistance:
                found = (face, against)
                if not prefer_down:                                          # MH:873-875
                    break
        on = pl.crosshair()
        if on is not None:                                                   # MH:878-885
            f = pl.crosshair_face()
            if on == place_at or (can_place_against(ctx, *on) and f is not None and offset(on, f) == place_at):
                if would_sneak:
                    ms.set_input("sneak", True)
                body.select_throwaway(pl, acceptable)
                return self.READY_TO_PLACE
        if found is not None:                                                # MH:887-893
            ms.set_target(self.aim_rotation(pl, found[0], eye), True)
            if would_sneak:
                ms.set_input("sneak", True)
            body.select_throwaway(pl, acceptable)
            return self.ATTEMPTING
        return self.NO_OPTION                                                # MH:896

    def __repr__(self):
        cost = "?" if self._cost is None else f"{self._cost:.3f}"
        return f"{self.name}({self.src} -> {self.dest}, cost={cost})"


def _above(p, n=1):
    return (p[0], p[1] + n, p[2])


def _below(p, n=1):
    return (p[0], p[1] - n, p[2])


class MovementTraverse(Movement):
    """MovementTraverse (sheet 9.1)."""
    name = "Traverse"

    def __init__(self, src, dest):
        super().__init__(src, dest, (_above(dest), dest), _below(dest))     # Trav:56-58

    def calculate_cost(self, ctx):
        return traverse_cost(ctx, self.src[0], self.src[1], self.src[2], self.dest[0], self.dest[2])   # Trav:63-66

    def calculate_valid_positions(self):
        return (self.src, self.dest)                                         # Trav:68-71

    def reset_counters(self):
        self.was_the_bridge_block_always_there = True                        # Trav:53
        self.ticks_without_placement = 0
        self.backplacing = False     # the feet have reached dest once: stay in the backplace
        self.place_requested_at = -1000   # the Body tick of the last LT asked for, so one place is one tap

    def update_state(self, s):
        """Trav:175-350 updateState."""
        super().update_state(s)
        ms, pl, ctx = self.current, s.player, s.ctx
        if ms.status != MovementStatus.RUNNING:
            # Trav:179-217 walks while breaking only with walkWhileBreaking, off here (plan, Fidelity 4): stand
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        ladder = bool(ctx.at(src[0], src[1] - 1, src[2]) & CLIMB)            # Trav:220
        if ctx.allow_walk_on_magma_blocks and ctx.at(feet[0], feet[1] - 1, feet[2]) & X_MAGMA:   # Trav:224
            ms.set_input("sneak", True)
        # Trav:226-245 openDoors and fence gates: the palette carries no door facing; departs
        dest_down = (dest[0], dest[1] - 1, dest[2])
        is_the_bridge_block_there = can_walk_on(ctx, *dest_down) or ladder   # Trav:247 (frost walker never)
        if feet[1] != dest[1] and not ladder:                                # Trav:248-254: wrong Y
            if feet[1] < dest[1]:
                ms.set_input("jump", True)
            return ms
        if is_the_bridge_block_there:                                        # Trav:256
            if feet == dest:                                                 # Trav:257-259
                ms.status = MovementStatus.SUCCESS
                return ms
            dx, dz = dest[0] - src[0], dest[2] - src[2]
            if ctx.settings.overshootTraverse and (feet == (dest[0] + dx, dest[1], dest[2] + dz)
                                                   or feet == (dest[0] + 2 * dx, dest[1], dest[2] + 2 * dz)):   # Trav:260-263
                ms.status = MovementStatus.SUCCESS
                return ms
            if (pl.y > src[1] + 0.1 and not pl.ground and not pl.water
                    and (ctx.at(*src) & CLIMB or ctx.at(src[0], src[1] + 1, src[2]) & CLIMB)):   # Trav:264-268
                return ms
            into = (dest[0] + dx, dest[1], dest[2] + dz)                     # Trav:269
            into_below, into_above = ctx.at(*into), ctx.at(into[0], into[1] + 1, into[2])
            if (self.was_the_bridge_block_always_there
                    and (not ctx.at(*feet) & LIQUID or ctx.settings.sprintInWater)
                    and (not avoid_walking_into(into_below) or into_below & WATER)
                    and not avoid_walking_into(into_above)):                 # Trav:272-274
                ms.set_input("sprint", True)
            if feet[1] != dest[1] and ladder and ctx.at(*dest_down) & CLIMB:   # Trav:276-279
                ms.set_input("jump", True)
            self.move_towards(s, self.positions_to_break[0])                 # Trav:280: the dest head
            return ms
        # the bridge block is not there: place it. Trav:283-349 is Java's bridging: sneak out over the edge, face
        # back and down, place against the src floor's side face (or a side face of the dest floor from a distance).
        # departs: the sneak is asked for from the whole of src (the toggle shows two or three ticks after the tap),
        # the camera keeps its yaw for a point under the eye, and the first 20 ticks at the edge try the Bedrock
        # shortcut of tapping LT with the crosshair on the top face of the block under the feet near its edge (on
        # this client 22 such taps placed nothing, so the backplace follows). The place event confirms the block.
        self.was_the_bridge_block_always_there = False                       # Trav:283
        acceptable = ctx.settings.acceptableThrowawayItems
        if not s.body.throwaway_in(pl, acceptable):                          # MH:854-858: no throwaway
            ms.status = MovementStatus.UNREACHABLE
            return ms
        s.body.select_throwaway(pl, acceptable)                              # IB selectThrowawayForLocation
        dist1 = max(abs(pl.x - (dest[0] + 0.5)), abs(pl.z - (dest[2] + 0.5)))   # Trav:293
        dx, dz = dest[0] - src[0], dest[2] - src[2]
        src_floor = (src[0], src[1] - 1, src[2])                             # Trav:329 goalLook
        self.ticks_without_placement += 1
        ms.set_input("sneak", True)                                          # Trav:295-297, early for the latency
        if self.backplacing or feet == dest:
            # out over the edge: Trav:322-348. departs: latched once the feet have reached dest, and MOVE_BACK only
            # once the camera faces back, since with a camera that takes a dozen ticks to turn round, "back"
            # pressed early walks toward src and the feet flip between src and dest every tick
            self.backplacing = True
            face = ((dest[0] + src[0] + 1.0) * 0.5, (dest[1] + src[1] - 1.0) * 0.5, (dest[2] + src[2] + 1.0) * 0.5)   # Trav:325-327
            back_yaw = rotation_from(center(dest), pl.eye).yaw               # Trav:335: facing away from dest
            pitch = rotation_from(pl.eye, face).pitch                        # Trav:331-332
            ms.set_target(Rotation(back_yaw, pitch), True)
            dist2 = max(abs(pl.x - face[0]), abs(pl.z - face[2]))            # Trav:333
            facing_back = abs(wrap(back_yaw - pl.yaw)) < 30.0
            if facing_back and (dist2 < 0.29 or feet != dest):               # Trav:334-337: further out, backwards
                ms.set_input("back", True)
            f = pl.crosshair_face()
            settled = abs(s.body.yaw_rate) < 1.0 and abs(s.body.pitch_rate) < 1.0   # a tap lands a tick or two later
            if pl.is_looking_at(src_floor) and f is not None and f[1] == 0 and settled \
                    and s.body.tick - self.place_requested_at >= 8:         # Trav:341-343: the side face; one tap, then
                self.place_requested_at = s.body.tick                        # the event or the terrain says if it took
                ms.set_input("click_right", True)
            return ms
        if dist1 > 0.85:                                                     # still short of the edge
            if pl.sneak or dist1 > 1.05:                                     # past the middle only once crouched
                self.move_towards(s, dest)
            return ms
        # at the edge, toes over, still on src: creep out, the sneak holds the edge. The Bedrock shortcut the user
        # described (LT with the crosshair on the top face of the block under the feet, near its edge) was tried
        # here first: 22 such taps on this client placed nothing, and a tap in flight while the camera moved on put
        # a block on the gap floor, so the backplace is the method
        if pl.sneak:
            self.move_towards(s, dest)                                       # creep out: the sneak holds the edge
        return ms

    def safe_to_cancel(self, state=None):
        """Trav:353-358: not RUNNING, or the dest floor is walkable."""
        if self.current.status != MovementStatus.RUNNING:
            return True
        return state is None or can_walk_on(state.ctx, self.dest[0], self.dest[1] - 1, self.dest[2])

    def prepared(self, s):
        """Trav:361-369: sneak on a ladder at src before the default breaking."""
        feet = s.player.feet
        if feet in (self.src, (self.src[0], self.src[1] - 1, self.src[2])) and s.ctx.at(self.src[0], self.src[1] - 1, self.src[2]) & CLIMB:
            self.current.set_input("sneak", True)
        return super().prepared(s)


class MovementAscend(Movement):
    """MovementAscend (sheet 9.2)."""
    name = "Ascend"

    def __init__(self, src, dest):
        super().__init__(src, dest, (dest, _above(src, 2), _above(dest)), _below(dest))   # Asc:41-43

    def calculate_cost(self, ctx):
        return ascend_cost(ctx, self.src[0], self.src[1], self.src[2], self.dest[0], self.dest[2])   # Asc:52-55

    def calculate_valid_positions(self):
        dx, _dy, dz = self.direction
        prior = (self.src[0] - dx, self.src[1], self.src[2] - dz)            # Asc:59: src - direction, above
        return (self.src, _above(self.src), self.dest, prior, _above(prior))  # Asc:57-65

    def reset_counters(self):
        self.ticks_without_placement = 0                                     # Asc:39

    def update_state(self, s):
        """Asc:160-231 updateState."""
        ms, pl, ctx = self.current, s.player, s.ctx
        src, dest, feet = self.src, self.dest, pl.feet
        if feet[1] < src[1]:                                                 # Asc:161-163: before super
            ms.status = MovementStatus.UNREACHABLE
            return ms
        super().update_state(s)
        if ms.status != MovementStatus.RUNNING:
            return ms
        dx, dz = dest[0] - src[0], dest[2] - src[2]
        if feet == dest or feet == (dest[0] + dx, dest[1], dest[2] + dz):   # Asc:170-172: overshoot by one
            ms.status = MovementStatus.SUCCESS
            return ms
        # Asc:174-176 openDoors: departs (no door facing in the palette)
        step = (dest[0], dest[1] - 1, dest[2])
        jumping_onto = ctx.at(*step)                                         # Asc:178
        if not can_walk_on(ctx, *step, jumping_onto):                        # Asc:179
            self.ticks_without_placement += 1                                # Asc:180
            # Asc:181-185 sneaks and clicks when crouching. departs: in Bedrock the step goes down against the ground
            # ahead (or a solid neighbour) from where the player stands, nothing to fall off, so no sneak toggle
            if self.attempt_to_place(s, step, False, False) == self.READY_TO_PLACE:
                ms.set_input("click_right", True)
            if self.ticks_without_placement > 10:                            # Asc:187-190: we might be in the way
                ms.set_input("back", True)
            return ms                                                        # Asc:192
        self.move_towards(s, dest)                                           # Asc:194
        if ctx.allow_walk_on_magma_blocks and jumping_onto & X_MAGMA:        # Asc:195
            ms.set_input("sneak", True)
        if jumping_onto & BOTTOM_SLAB and not ctx.at(src[0], src[1] - 1, src[2]) & BOTTOM_SLAB:   # Asc:197-200
            return ms
        if ctx.settings.assumeStep or feet == (src[0], src[1] + 1, src[2]):   # Asc:202-205: already jumping
            return ms
        x_axis, z_axis = abs(dx), abs(dz)                                    # Asc:207-208
        flat_dist = x_axis * abs(dest[0] + 0.5 - pl.x) + z_axis * abs(dest[2] + 0.5 - pl.z)   # Asc:209
        side_dist = z_axis * abs(dest[0] + 0.5 - pl.x) + x_axis * abs(dest[2] + 0.5 - pl.z)   # Asc:210
        lateral_motion = x_axis * pl.vz + z_axis * pl.vx                     # Asc:212
        if abs(lateral_motion) > 0.1:                                        # Asc:213-215
            return ms
        if self.head_bonk_clear(ctx):                                        # Asc:217-219
            ms.set_input("jump", True)
            return ms
        # Asc:221-229: jump within 1.2 of the step and 0.2 of its line. The calibration's step_up is the distance
        # an A tap still lands the step from (1.525 on this client, so Baritone's 1.2 stands), read at run time;
        # and when the player is pressed against the step with no forward speed, jump whatever the distance
        jump_in = min(1.2, s.body.step_up_sprint if pl.sprint else s.body.step_up_walk)
        forward_speed = x_axis * abs(pl.vx) + z_axis * abs(pl.vz)
        stalled = forward_speed < 0.02 and flat_dist < 1.3 and pl.ground
        if (flat_dist > jump_in and not stalled) or side_dist > 0.2:
            return ms
        ms.set_input("jump", True)
        return ms

    def head_bonk_clear(self, ctx):
        """Asc:233-243: the four horizontal neighbours of src+2 are walk-through."""
        x, y, z = self.src
        for ax, az in ((0, -1), (0, 1), (1, 0), (-1, 0)):
            if not can_walk_through(ctx, x + ax, y + 2, z + az):
                return False
        return True

    def safe_to_cancel(self, state=None):
        """Asc:246-249."""
        return self.current.status != MovementStatus.RUNNING or self.ticks_without_placement == 0


class MovementDescend(Movement):
    """MovementDescend (sheet 9.3): down one, walking off an edge."""
    name = "Descend"

    def __init__(self, src, dest):
        super().__init__(src, dest, (_above(dest, 2), _above(dest), dest), _below(dest))   # Desc:47-49
        self.force_safe_mode = False                                         # Desc:45

    def calculate_cost(self, ctx):
        """Desc:66-73: COST_INF unless the result lands exactly one below (otherwise it is a Fall)."""
        res = MoveResult()
        descend_cost(ctx, self.src[0], self.src[1], self.src[2], self.dest[0], self.dest[2], res)
        if res.y != self.dest[1]:
            return COST_INF
        return res.cost

    def calculate_valid_positions(self):
        return (self.src, _above(self.dest), self.dest)                      # Desc:75-78

    def reset_counters(self):
        self.num_ticks = 0                                                   # Desc:44

    def update_state(self, s):
        """Desc:227-275 updateState."""
        super().update_state(s)
        ms, pl, ctx = self.current, s.player, s.ctx
        if ms.status != MovementStatus.RUNNING:
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        fake_dest = (2 * dest[0] - src[0], dest[1], 2 * dest[2] - src[2])     # Desc:233: one past dest
        if (feet == dest or feet == fake_dest) and (ctx.at(*dest) & LIQUID or pl.y - dest[1] < 0.5):   # Desc:234-237
            ms.status = MovementStatus.SUCCESS
            return ms
        # Desc:239-241 openDoors: departs
        if self.safe_mode(ctx):                                              # Desc:243-249
            point = ((src[0] + 0.5) * 0.17 + (dest[0] + 0.5) * 0.83, dest[1],
                     (src[2] + 0.5) * 0.17 + (dest[2] + 0.5) * 0.83)
            self.move_towards_point(s, point)                                # yaw only, forward; stops short
            return ms
        ab = pl.flat_distance_to_center(dest)                                # Desc:251-252
        from_start = pl.flat_distance_to_center(src)                         # Desc:253
        if ctx.allow_walk_on_magma_blocks and ctx.at(feet[0], feet[1] - 1, feet[2]) & X_MAGMA:   # Desc:255
            ms.set_input("sneak", True)
        if feet != dest or ab > 0.25:                                        # Desc:257
            self.num_ticks += 1
            if self.num_ticks <= 20 and from_start < 1.25:                   # Desc:258-262: numTicks++ < 20
                self.move_towards(s, fake_dest)
            else:
                self.move_towards(s, dest)
        return ms

    def safe_mode(self, ctx):
        """Desc:277-294."""
        if self.force_safe_mode:                                             # Desc:278-280
            return True
        src, dest = self.src, self.dest
        into = (2 * dest[0] - src[0], dest[1], 2 * dest[2] - src[2])         # Desc:281
        if self.skip_to_ascend(ctx):                                         # Desc:282-284
            return True
        for dy in range(3):                                                  # Desc:285-292
            if avoid_walking_into(ctx.at(into[0], into[1] + dy, into[2])):
                return True
        return False

    def skip_to_ascend(self, ctx):
        """Desc:296-299."""
        src, dest = self.src, self.dest
        into = (2 * dest[0] - src[0], dest[1], 2 * dest[2] - src[2])
        return (not can_walk_through(ctx, *into) and can_walk_through(ctx, into[0], into[1] + 1, into[2])
                and can_walk_through(ctx, into[0], into[1] + 2, into[2]))


class MovementFall(Movement):
    """MovementFall (sheet 9.4): a drop of two or more, into water at any height."""
    name = "Fall"

    def __init__(self, src, dest):
        super().__init__(src, dest, MovementFall.build_positions_to_break(src, dest))   # Fall:54-56

    @staticmethod
    def build_positions_to_break(src, dest):
        """Fall:188-198: the dest column from src head level down to dest feet."""
        diff_y = abs(src[1] - dest[1])
        return tuple((dest[0], src[1] + 1 - i, dest[2]) for i in range(diff_y + 2))

    def calculate_cost(self, ctx):
        """Fall:60-67: Descend's cost, required to land at this dest."""
        res = MoveResult()
        descend_cost(ctx, self.src[0], self.src[1], self.src[2], self.dest[0], self.dest[2], res)
        if res.y != self.dest[1]:
            return COST_INF
        return res.cost

    def calculate_valid_positions(self):
        positions = [self.src]                                               # Fall:68-76
        for j in range(self.src[1] - self.dest[1], -1, -1):
            positions.append(_above(self.dest, j))
        return positions

    def update_state(self, s):
        """Fall:86-169 updateState. No bucket here (plan, Fidelity 6), so no bucket clutch and no water pickup."""
        super().update_state(s)
        ms, pl, ctx = self.current, s.player, s.ctx
        if ms.status != MovementStatus.RUNNING:
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        to_dest = rotation_from(pl.eye, center(dest))                        # Fall:93
        is_water = bool(ctx.at(*dest) & WATER)                               # Fall:94
        # Fall:95-98 sneak over magma while airborne; Fall:99-116 bucket clutch (hasWaterBucket is never true here,
        # so willPlaceBucket is never true: Desc:214 is gated by it); Fall:117-119 openDoors: departs
        ms.set_target(to_dest, False)                                        # Fall:121-125
        cx, cz = dest[0] + 0.5, dest[2] + 0.5
        if feet == dest and (pl.y - feet[1] < 0.094 or is_water):            # Fall:127
            if is_water:                                                     # Fall:128-147: no bucket to pick up
                if pl.vy >= 0:
                    ms.status = MovementStatus.SUCCESS
                    return ms
            else:                                                            # Fall:148-150
                ms.status = MovementStatus.SUCCESS
                return ms
        avoid = (src[0] - dest[0], 0, src[2] - dest[2])                      # Fall:159-161: no ladder scan (departs)
        aim = (cx + 0.125 * avoid[0], dest[1], cz + 0.125 * avoid[2])        # Fall:166-168
        if abs(pl.x + pl.vx - cx) > 0.1 or abs(pl.z + pl.vz - cz) > 0.1:      # Fall:152
            # Fall:153-155 SNEAK while falling faster than 0.4: departs, B is a toggle and a fall is over before
            # two taps would land; forward toward the aim point
            self.move_towards_point(s, aim)
        else:
            dist = abs(avoid[0] * (cx - avoid[0] / 2.0 - pl.x)) + abs(avoid[2] * (cz - avoid[2] / 2.0 - pl.z))   # Fall:162
            if dist < 0.6:                                                   # Fall:163-164
                self.move_towards_point(s, aim)
        if ms.move_to is None:
            ms.set_target(rotation_from(pl.eye, aim), False)                 # Fall:166-168
        return ms

    def prepared(self, s):
        """Fall:201-213: break only when one of the first four positions is not walk-through (the last may be
        water)."""
        if self.current.status == MovementStatus.WAITING:
            return True
        for pos in self.positions_to_break[:4]:
            if not can_walk_through(s.ctx, *pos):
                return super().prepared(s)
        return True

    def safe_to_cancel(self, state=None):
        """Fall:215-218: at src, or not RUNNING."""
        if self.current.status != MovementStatus.RUNNING:
            return True
        return state is not None and state.player.feet == self.src


class MovementDiagonal(Movement):
    """MovementDiagonal (sheet 9.5): one block on each axis; dy is 0, +1 (allowDiagonalAscend) or -1
    (allowDiagonalDescend). dir1 is the north/south step (0, dz), dir2 the east/west step (dx, 0)."""
    name = "Diagonal"

    def __init__(self, src, dx, dz, dy=0):
        x, y, z = src
        dir1 = (x, y, z + dz)                                                # Diag:50-51 start.relative(dir1)
        dir2 = (x + dx, y, z)
        end = (x + dx, y + dy, z + dz)                                       # Diag:54-55
        super().__init__(src, end, (dir1, _above(dir1), dir2, _above(dir2), end, _above(end)))   # Diag:58-60

    def calculate_cost(self, ctx):
        """Diag:94-101: COST_INF unless the result's y matches."""
        res = MoveResult()
        diagonal_cost(ctx, self.src[0], self.src[1], self.src[2], self.dest[0], self.dest[2], res)
        if res.y != self.dest[1]:
            return COST_INF
        return res.cost

    def calculate_valid_positions(self):
        src, dest = self.src, self.dest
        diag_a = (src[0], src[1], dest[2])                                   # Diag:105
        diag_b = (dest[0], src[1], src[2])                                   # Diag:106
        if dest[1] < src[1]:                                                 # Diag:107-109
            return (src, _above(dest), diag_a, diag_b, dest, _below(diag_a), _below(diag_b))
        if dest[1] > src[1]:                                                 # Diag:110-112
            return (src, _above(src), diag_a, diag_b, dest, _above(diag_a), _above(diag_b))
        return (src, dest, diag_a, diag_b)                                   # Diag:113

    def to_break(self, ctx_or_world):
        """Diag:330-341: only the dest column, entries 4 and 5."""
        if self.to_break_cached is None:
            ctx = _context_of(ctx_or_world)
            self.to_break_cached = [p for p in self.positions_to_break[4:6] if not can_walk_through(ctx, *p)]
        return self.to_break_cached

    def to_walk_into(self, ctx_or_world):
        """Diag:343-357: the two corner columns, entries 0 to 3, that are not walk-through."""
        if self.to_walk_into_cached is None:
            ctx = _context_of(ctx_or_world)
            self.to_walk_into_cached = [p for p in self.positions_to_break[0:4] if not can_walk_through(ctx, *p)]
        return self.to_walk_into_cached

    def reset_counters(self):
        self.num_ticks = 0

    def prepared(self, s):
        """Diag:325-327: always; a diagonal never breaks."""
        return True

    def update_state(self, s):
        """Diag:276-310 updateState."""
        super().update_state(s)
        ms, pl, ctx = self.current, s.player, s.ctx
        if ms.status != MovementStatus.RUNNING:
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        self.num_ticks += 1
        if feet == dest:                                                     # Diag:281-283
            ms.status = MovementStatus.SUCCESS
            return ms
        if not self.player_in_valid_position(s) and not (ctx.at(*src) & LIQUID and (feet[0], feet[1] + 1, feet[2]) in self.valid_positions):   # Diag:284-286
            ms.status = MovementStatus.UNREACHABLE
            return ms
        # Diag:288-296 doors on either corner: departs
        if dest[1] > src[1] and pl.y < src[1] + 0.1 and self.num_ticks > 3 and math.hypot(pl.vx, pl.vz) < 0.03:
            ms.set_input("jump", True)                                       # Diag:298-300 horizontalCollision: no speed while walking stands in
        if self.sprint(ctx, pl):                                             # Diag:302-304
            ms.set_input("sprint", True)
        if ctx.allow_walk_on_magma_blocks and ctx.at(feet[0], feet[1] - 1, feet[2]) & X_MAGMA:   # Diag:305
            ms.set_input("sneak", True)
        self.move_towards(s, dest)                                           # Diag:308
        return ms

    def sprint(self, ctx, pl):
        """Diag:312-323: not in liquid without sprintInWater, and no corner block in the way."""
        if ctx.at(*pl.feet) & LIQUID and not ctx.settings.sprintInWater:
            return False
        for pos in self.positions_to_break[0:4]:
            if not can_walk_through(ctx, *pos):
                return False
        return True

    def safe_to_cancel(self, state=None):
        """Diag:65-91: every branch ends true but the corner one, which looks for a block under one of the four
        points 0.25 around the feet."""
        if state is None or self.current.status != MovementStatus.RUNNING:
            return True
        pl, ctx = state.player, state.ctx
        src, dest, feet = self.src, self.dest, pl.feet
        if feet == src:
            return True
        if can_walk_on(ctx, src[0], src[1] - 1, dest[2]) and can_walk_on(ctx, dest[0], src[1] - 1, src[2]):
            return True
        if feet[1] == src[1] and feet in ((src[0], src[1], dest[2]), (dest[0], src[1], src[2])):
            for ox in (-0.25, 0.25):
                for oz in (-0.25, 0.25):
                    if can_walk_on(ctx, math.floor(pl.x + ox), feet[1] - 1, math.floor(pl.z + oz)):
                        return True
            return False
        return True


class MovementDownward(Movement):
    """MovementDownward (sheet 9.6): mine the block under the feet and drop one."""
    name = "Downward"

    def __init__(self, src, dest=None):
        dest = _below(src) if dest is None else dest
        super().__init__(src, dest, (dest,))                                 # Down:37

    def calculate_cost(self, ctx):
        return downward_cost(ctx, self.src[0], self.src[1], self.src[2])     # Down:47-50

    def calculate_valid_positions(self):
        return (self.src, self.dest)                                         # Down:52-55

    def reset_counters(self):
        self.num_ticks = 0                                                   # Down:35

    def update_state(self, s):
        """Down:75-95 updateState: the break is prepared()'s, looking straight down at dest (Movement.break_block
        aims at its centre, which from the feet above it is pitch 90)."""
        super().update_state(s)
        ms, pl = self.current, s.player
        if ms.status != MovementStatus.RUNNING:
            return ms
        dest, feet = self.dest, pl.feet
        if feet == dest:                                                     # Down:80-82
            ms.status = MovementStatus.SUCCESS
            return ms
        if not self.player_in_valid_position(s):                             # Down:83-85
            ms.status = MovementStatus.UNREACHABLE
            return ms
        ab = pl.flat_distance_to_center(dest)                                # Down:87-89
        self.num_ticks += 1
        if self.num_ticks <= 10 and ab < 0.2:                                # Down:90-92: numTicks++ < 10
            return ms
        self.move_towards(s, dest)                                           # Down:93
        return ms


class MovementPillar(Movement):
    """MovementPillar (sheet 9.7): jump and place under yourself, or climb."""
    name = "Pillar"

    def __init__(self, src, dest=None):
        dest = _above(src) if dest is None else dest
        super().__init__(src, dest, (_above(src, 2),), src)                  # Pill:44-46

    def calculate_cost(self, ctx):
        return pillar_cost(ctx, self.src[0], self.src[1], self.src[2])       # Pill:48-51

    def calculate_valid_positions(self):
        return (self.src, self.dest)                                         # Pill:53-56

    def reset_counters(self):
        self.jump_tick = None        # the executor tick the A tap went out, for the timed LT

    def update_state(self, s):
        """Pill:139-231 updateState, with the place timed from the jump (calibration "pillar": LT 5 ticks after
        the A tap, window 3 to 7) rather than from the height, since the probe shows the apex a tick late."""
        super().update_state(s)
        ms, pl, ctx, body = self.current, s.player, s.ctx, s.body
        if ms.status != MovementStatus.RUNNING:
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        if feet[1] < src[1]:                                                 # Pill:144-146
            ms.status = MovementStatus.UNREACHABLE
            return ms
        from_state = ctx.at(*src)
        if from_state & WATER and ctx.at(*dest) & WATER:                     # Pill:148-157: a water column
            ms.set_target(rotation_from(pl.eye, center(dest)), False)
            if abs(pl.x - (dest[0] + 0.5)) > 0.2 or abs(pl.z - (dest[2] + 0.5)) > 0.2:
                self.move_towards(s, dest)
            if feet == dest:
                ms.status = MovementStatus.SUCCESS
            return ms
        ladder = bool(from_state & CLIMB)                                    # Pill:159
        rotation = rotation_from(pl.eye, center(src))                        # Pill:160: straight down
        if not ladder:
            ms.set_target(Rotation(pl.yaw, rotation.pitch), True)            # Pill:162-164
        block_is_there = can_walk_on(ctx, *src, from_state) or ladder        # Pill:166
        if ladder:                                                           # Pill:168-176
            if feet == dest:
                ms.status = MovementStatus.SUCCESS
                return ms
            self.move_towards(s, dest)
            ms.set_input("jump", True)
            return ms
        acceptable = ctx.settings.acceptableThrowawayItems
        if not body.throwaway_in(pl, acceptable):                            # Pill:178-181 selectThrowawayForLocation
            ms.status = MovementStatus.UNREACHABLE
            return ms
        body.select_throwaway(pl, acceptable)
        ms.set_input("sneak", True)                                          # Pill:183
        dist = pl.flat_distance_to_center(dest)                              # Pill:185
        flat_motion = math.hypot(pl.vx, pl.vz)                               # Pill:186
        if dist > 0.17:                                                      # Pill:187-190
            # departs: Pill:189 forces the rotation to src and walks forward along it; with a camera that turns at
            # a rate, the body goes to src through the left stick instead (move_to) and the pitch comes down
            # unforced, so a noisy yaw to a point under the eye cannot spin the player round
            self.move_towards(s, src)
            ms.set_target(Rotation(pl.yaw, rotation.pitch), False)
        elif flat_motion < 0.05:                                             # Pill:191-193
            if pl.y < dest[1] and pl.ground and (self.jump_tick is None or body.tick - self.jump_tick > body.jump_air_ticks + 4):
                ms.set_input("jump", True)
                self.jump_tick = body.tick
        if not block_is_there:                                               # Pill:196
            if not from_state & X_AIR and not from_state & X_REPLACEABLE:    # Pill:197-205: mine the obstruction
                ms.set_target(rotation_from(pl.eye, self.aim_point(s, src)), True)
                ms.set_input("jump", False)
                ms.set_input("click_left", True)
            elif self.jump_tick is not None and body.tick - self.jump_tick == body.pillar_delay \
                    and (pl.is_looking_at((src[0], src[1] - 1, src[2])) or pl.is_looking_at(src)):
                # Pill:206-215: Baritone places when crouching, looking at src or the src floor, and more than 0.1
                # above dest's floor (near the apex). departs: timed from the A tap, the calibrated delay
                ms.set_input("click_right", True)
        if feet == dest and block_is_there:                                  # Pill:218-220
            ms.status = MovementStatus.SUCCESS
        return ms

    def prepared(self, s):
        """Pill:234-245: sneak on a ladder at src; skip breaking under water."""
        feet = s.player.feet
        if feet in (self.src, (self.src[0], self.src[1] - 1, self.src[2])) and s.ctx.at(self.src[0], self.src[1] - 1, self.src[2]) & CLIMB:
            self.current.set_input("sneak", True)
        if s.ctx.at(self.dest[0], self.dest[1] + 1, self.dest[2]) & WATER:
            return True
        return super().prepared(s)


class MovementParkour(Movement):
    """MovementParkour (sheet 9.8): a jump of 2 to 4 blocks along a cardinal, flat or up one."""
    name = "Parkour"

    def __init__(self, src, dist, direction, ascend):
        dx, dz = direction
        x, y, z = src
        dest = (x + dx * dist, y + (1 if ascend else 0), z + dz * dist)      # Park:49
        to_place = (x + dx * dist, y + (0 if ascend else -1), z + dz * dist)
        super().__init__(src, dest, (), to_place)                            # Park:49-54
        self.direction_xz = (dx, dz)                                         # Park:50-52
        self.dist = dist
        self.ascend = ascend

    @classmethod
    def from_cost(cls, ctx, src, direction):
        """Park:56-61: run the cost and build the movement it describes."""
        res = MoveResult()
        parkour_cost(ctx, src[0], src[1], src[2], direction[0], direction[1], res)
        dist = abs(res.x - src[0]) + abs(res.z - src[2])
        return cls(src, dist, direction, res.y > src[1])

    def calculate_cost(self, ctx):
        """Park:223-230: COST_INF unless the result is exactly this dest."""
        res = MoveResult()
        parkour_cost(ctx, self.src[0], self.src[1], self.src[2], self.direction_xz[0], self.direction_xz[1], res)
        if (res.x, res.y, res.z) != self.dest:
            return COST_INF
        return res.cost

    def calculate_valid_positions(self):
        dx, dz = self.direction_xz                                           # Park:232-244
        x, y, z = self.src
        return [(x + dx * i, y + j, z + dz * i) for i in range(self.dist + 1) for j in range(2)]

    EDGE = 0.8   # the furthest the centre can be past the src centre with the 0.3 box still over src

    def update_state(self, s):
        """Park:252-315 updateState. departs: the A tap shows body.jump_lead ticks after it goes out, so the Java
        conditions that decide the jump tick (feet in src + dir, the 0.7 of a dist 3) are tested at where the
        player will be then, pos + vel * lead, which puts the real takeoff where Baritone's is; and the jump is
        pressed only while the probe says the player is on the ground, since a tap in the air does nothing and
        one landing with the player risks a second hop."""
        super().update_state(s)
        ms, pl, ctx, body = self.current, s.player, s.ctx, s.body
        if ms.status != MovementStatus.RUNNING:
            return ms
        src, dest, feet = self.src, self.dest, pl.feet
        dx, dz = self.direction_xz
        if feet[1] < src[1]:                                                 # Park:257-261: we have fallen ("sorry")
            ms.status = MovementStatus.UNREACHABLE
            return ms
        # Park:262-264 openDoors: departs (no door facing in the palette)
        if self.dist >= 4 or self.ascend:                                    # Park:265-267
            ms.set_input("sprint", True)
        if ctx.allow_walk_on_magma_blocks and ctx.at(feet[0], feet[1] - 1, feet[2]) & X_MAGMA:   # Park:268-270
            ms.set_input("sneak", True)
        self.move_towards(s, dest)                                           # Park:272
        past = (dest[0] + dx, dest[1], dest[2] + dz)
        if feet == dest or (feet == past and pl.ground):                     # Park:273
            # departs: a hop from the edge lands up to a block past dest, and Park:305-312 would then walk back
            # toward src to cross dest again; one past dest, on the ground at its height, is the landing (the way
            # Trav:260-263 overshootTraverse counts one past)
            if ctx.at(*dest) & X_LADDER_OR_VINE:                             # Park:274-279: parkour jumping onto a vine
                ms.status = MovementStatus.SUCCESS
                return ms
            if pl.y - feet[1] < 0.094:                                       # Park:280-282: lilypads
                ms.status = MovementStatus.SUCCESS
            return ms
        # Park:296-298 distFromStart along the jump, and where it will be when the tap lands (departs: the lead):
        # the box is 0.3 each side, so the last position the player can still jump from is EDGE past the src centre,
        # and a tick that would carry the next decision past it jumps now (the walking pace of a dist 2 or 3 is
        # 0.216 a tick, wider than the 0.1 between Baritone's 0.7 and the edge)
        along = (pl.x - (src[0] + 0.5)) * dx + (pl.z - (src[2] + 0.5)) * dz
        speed = max(0.0, (pl.vx * dx + pl.vz * dz) * body.velocity_to_displacement)
        ahead = along + speed * body.jump_lead
        edge = (src[0] + dx, src[1], src[2] + dz)
        if feet == edge or ahead >= 0.5 or pl.y - src[1] > 0.0001:          # Park:284: over the edge (feet in src + dir) or airborne
            # Park:285-293 mid-air place, see PR #3775: down before the horizontals, because you don't have to look
            # to the side while in midair, which could mess up the trajectory
            if (ctx.settings.allowPlace and body.throwaway_in(pl, ctx.settings.acceptableThrowawayItems)
                    and not can_walk_on(ctx, dest[0], dest[1] - 1, dest[2]) and not pl.ground
                    and self.attempt_to_place(s, (dest[0], dest[1] - 1, dest[2]), True, False) == self.READY_TO_PLACE):
                ms.set_input("click_right", True)
            need = 0.7 if (self.dist == 3 and not self.ascend) else 0.5      # Park:294-302: a 2 block gap waits for 0.7
            if ahead < need and ahead + speed <= self.EDGE:                  # not yet, and the next tick is still in time
                return ms
            if pl.ground:
                ms.set_input("jump", True)                                   # Park:304
            return ms
        if feet != src and feet != (dest[0] - dx, dest[1], dest[2] - dz):    # Park:283, 305-312: on the ground elsewhere: the run-up
            ms.set_input("sprint", False)
            back = (src[0] - dx, src[1], src[2] - dz)
            self.move_towards(s, src if feet == back else back)
        return ms

    def safe_to_cancel(self, state=None):
        """Park:317-320: once ticked for the first time it is RUNNING, and since we don't really know anything
        about momentum, Parkour can only be cancelled on the 0th tick."""
        return self.current.status != MovementStatus.RUNNING


# ---------------------------------------------------------------------------------------------------------------
# Moves (MV:30-325): the 22 moves A* tries from every node, in Baritone's order
# ---------------------------------------------------------------------------------------------------------------

class Move:
    """One entry of the Moves enum: the nominal offset, whether the result may land elsewhere (dynamic xz / y),
    the raw cost function and its calling shape (`kind`), apply(ctx, x, y, z, res) for callers that want a
    MoveResult, and apply0(ctx, src) for path assembly (MV:327-346).

    kind 0: cost(ctx, x, y, z, dest_x, dest_z) -> float, the result is the nominal offset (Traverse, Ascend)
    kind 1: cost(ctx, x, y, z) -> float, likewise (Downward, Pillar)
    kind 2: cost(ctx, x, y, z, dest_x, dest_z, res) writes res (Descend/Fall, Diagonal, Parkour)
    The search calls `cost` directly (astar.MOVE_TABLE); `apply` wraps it the way Moves.apply does."""
    __slots__ = ("name", "x_offset", "y_offset", "z_offset", "dynamic_xz", "dynamic_y", "kind", "cost", "apply", "apply0")

    def __init__(self, name, x_offset, y_offset, z_offset, dynamic_xz, dynamic_y, kind, cost, apply0):
        self.name = name
        self.x_offset, self.y_offset, self.z_offset = x_offset, y_offset, z_offset
        self.dynamic_xz, self.dynamic_y = dynamic_xz, dynamic_y
        self.kind = kind
        self.cost = cost
        self.apply = _make_apply(kind, cost, x_offset, y_offset, z_offset)
        self.apply0 = apply0

    def __repr__(self):
        return f"Moves.{self.name}"


def _make_apply(kind, cost, dx, dy, dz):
    """MV:338-346 Moves.apply: the result position and cost into a MoveResult, with `unloaded` set when the move
    read an UNKNOWN block (plan, Fidelity 2)."""
    if kind == 0:
        def apply(ctx, x, y, z, res):
            ctx.touched_unknown = False
            res.x = x + dx
            res.y = y + dy
            res.z = z + dz
            res.cost = cost(ctx, x, y, z, x + dx, z + dz)
            res.unloaded = ctx.touched_unknown
    elif kind == 1:
        def apply(ctx, x, y, z, res):
            ctx.touched_unknown = False
            res.x = x + dx
            res.y = y + dy
            res.z = z + dz
            res.cost = cost(ctx, x, y, z)
            res.unloaded = ctx.touched_unknown
    else:
        def apply(ctx, x, y, z, res):
            ctx.touched_unknown = False
            res.reset()                                                      # AS:124
            cost(ctx, x, y, z, x + dx, z + dz, res)
            res.unloaded = ctx.touched_unknown
    return apply


def _descend_apply0(dx, dz):
    def apply0(ctx, src):                                                    # MV:154-162: Descend or Fall by the landing
        res = MoveResult()
        descend_cost(ctx, src[0], src[1], src[2], src[0] + dx, src[2] + dz, res)
        if res.y == src[1] - 1:
            return MovementDescend(src, (res.x, res.y, res.z))
        return MovementFall(src, (res.x, res.y, res.z))
    return apply0


def _diagonal_apply0(dx, dz):
    def apply0(ctx, src):                                                    # MV:226-231
        res = MoveResult()
        diagonal_cost(ctx, src[0], src[1], src[2], src[0] + dx, src[2] + dz, res)
        return MovementDiagonal(src, dx, dz, res.y - src[1])
    return apply0


def _parkour_apply0(dx, dz):
    def apply0(ctx, src):                                                    # MV:282-285
        return MovementParkour.from_cost(ctx, src, (dx, dz))
    return apply0


def _traverse_apply0(dx, dz):
    return lambda ctx, src: MovementTraverse(src, (src[0] + dx, src[1], src[2] + dz))


def _ascend_apply0(dx, dz):
    return lambda ctx, src: MovementAscend(src, (src[0] + dx, src[1] + 1, src[2] + dz))


# NORTH = (0, -1), SOUTH = (0, +1), EAST = (+1, 0), WEST = (-1, 0): sheet section 1
_N, _S, _E, _W = (0, -1), (0, 1), (1, 0), (-1, 0)

MOVES = (
    Move("DOWNWARD", 0, -1, 0, False, False, 1, downward_cost, lambda ctx, src: MovementDownward(src)),       # MV:32-42
    Move("PILLAR", 0, 1, 0, False, False, 1, pillar_cost, lambda ctx, src: MovementPillar(src)),             # MV:44-54
    Move("TRAVERSE_NORTH", 0, 0, -1, False, False, 0, traverse_cost, _traverse_apply0(*_N)),                 # MV:56-66
    Move("TRAVERSE_SOUTH", 0, 0, 1, False, False, 0, traverse_cost, _traverse_apply0(*_S)),
    Move("TRAVERSE_EAST", 1, 0, 0, False, False, 0, traverse_cost, _traverse_apply0(*_E)),
    Move("TRAVERSE_WEST", -1, 0, 0, False, False, 0, traverse_cost, _traverse_apply0(*_W)),
    Move("ASCEND_NORTH", 0, 1, -1, False, False, 0, ascend_cost, _ascend_apply0(*_N)),                       # MV:104-114
    Move("ASCEND_SOUTH", 0, 1, 1, False, False, 0, ascend_cost, _ascend_apply0(*_S)),
    Move("ASCEND_EAST", 1, 1, 0, False, False, 0, ascend_cost, _ascend_apply0(*_E)),
    Move("ASCEND_WEST", -1, 1, 0, False, False, 0, ascend_cost, _ascend_apply0(*_W)),
    Move("DESCEND_EAST", 1, -1, 0, False, True, 2, descend_cost, _descend_apply0(*_E)),                      # MV:152-169
    Move("DESCEND_WEST", -1, -1, 0, False, True, 2, descend_cost, _descend_apply0(*_W)),
    Move("DESCEND_NORTH", 0, -1, -1, False, True, 2, descend_cost, _descend_apply0(*_N)),
    Move("DESCEND_SOUTH", 0, -1, 1, False, True, 2, descend_cost, _descend_apply0(*_S)),
    Move("DIAGONAL_NORTHEAST", 1, 0, -1, False, True, 2, diagonal_cost, _diagonal_apply0(1, -1)),             # MV:224-233
    Move("DIAGONAL_NORTHWEST", -1, 0, -1, False, True, 2, diagonal_cost, _diagonal_apply0(-1, -1)),
    Move("DIAGONAL_SOUTHEAST", 1, 0, 1, False, True, 2, diagonal_cost, _diagonal_apply0(1, 1)),
    Move("DIAGONAL_SOUTHWEST", -1, 0, 1, False, True, 2, diagonal_cost, _diagonal_apply0(-1, 1)),
    Move("PARKOUR_NORTH", 0, 0, -4, True, True, 2, parkour_cost_to, _parkour_apply0(*_N)),                   # MV:280-325
    Move("PARKOUR_SOUTH", 0, 0, 4, True, True, 2, parkour_cost_to, _parkour_apply0(*_S)),
    Move("PARKOUR_EAST", 4, 0, 0, True, True, 2, parkour_cost_to, _parkour_apply0(*_E)),
    Move("PARKOUR_WEST", -4, 0, 0, True, True, 2, parkour_cost_to, _parkour_apply0(*_W)),
)

MOVES_BY_NAME = {move.name: move for move in MOVES}

__all__ = ["MovementStatus", "MoveResult", "MovementState", "Movement", "MovementTraverse", "MovementAscend", "MovementDescend",
           "MovementFall", "MovementDiagonal", "MovementDownward", "MovementPillar", "MovementParkour", "Move",
           "MOVES", "MOVES_BY_NAME", "AGAINST", "SQRT_2", "traverse_cost", "ascend_cost", "descend_cost",
           "dynamic_fall_cost", "diagonal_cost", "downward_cost", "pillar_cost", "parkour_cost",
           "cost_from_jump_distance", "check_overshoot_safety", "parkour_cost_to"]
