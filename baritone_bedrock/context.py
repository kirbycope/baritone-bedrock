"""CalculationContext (java/baritone/src/main/java/baritone/pathing/movement/CalculationContext.java, "CC") and
ToolSet (utils/ToolSet.java, "TS"): everything a path calculation reads, snapshotted once so every movement in one
search sees the same settings (CC:168-170), plus the per-search caches: block flags by packed position, the two
mining memos (MH:604-624) and the loaded-chunk answers.

Build one per search:

    ctx = CalculationContext(world, costs, settings, hotbar, can_sprint=True, dimension="overworld")

`world` is a world.Terrain, `hotbar` the nine slot item ids (strings, or the probe's {"typeId", "amount"} dicts,
None for an empty slot), `can_sprint` the caller's "food > 6". `ctx.at(x, y, z)` is the one block read the planner
makes: the block's world.py flags with helpers.py's extended bits folded in, cached for the search. Call
`block_changed()` when the world changes under a live context (the executor's re-costing).
"""
from .costs import COST_INF, DEFAULT as DEFAULT_COSTS
from .settings import Settings
from .world import (WALK_THROUGH, PASSABLE, DANGER, BREAKABLE, UNKNOWN, LIQUID, FLOWING, GRAVITY, UNKNOWN_KIND)
from .helpers import (X_SOUL_SAND, X_MAGMA, X_LADDER_OR_VINE, X_NEVER_BREAK, X_AIR, X_WATER_BLOCK, X_LIQUID_BLOCK,
                      X_LILY_PAD, X_CARPET, X_FARMLAND, X_FENCE_GATE, X_SLAB_HALF, X_STAIRS, X_REPLACEABLE,
                      REPLACEABLE_NAMES, can_walk_through, avoid_breaking)

# the y range the search may use (BSI:91-93: minY and minY + height - 1), by dimension
DIMENSION_Y = {"overworld": (-64, 319), "nether": (0, 127), "the_end": (0, 255), "end": (0, 255)}


def pack(x, y, z):
    """One int per position, invertible (unlike BetterBlockPos.longHash), for every dict the planner keys on a
    position. Good for |y| < 2048 and |z| < 2**25."""
    return (x << 38) + (z << 12) + y


def unpack(key):
    y = ((key + 2048) & 0xFFF) - 2048
    rest = (key - y) >> 12
    z = ((rest + (1 << 25)) & 0x3FFFFFF) - (1 << 25)
    return (rest - z) >> 26, y, z


def item_id(item):
    """The type id of a hotbar slot: a string, the probe's dict, an object with typeId, or None when empty."""
    if item is None:
        return None
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("typeId") or item.get("id") or item.get("type")
    return getattr(item, "typeId", None) or getattr(item, "type_id", None)


# vanilla tool speeds by material (Tiers.java); gold digs faster than diamond
TIER_SPEED = {"wooden": 2.0, "stone": 4.0, "iron": 6.0, "golden": 12.0, "diamond": 8.0, "netherite": 9.0}
TOOL_CLASSES = ("pickaxe", "shovel", "axe", "hoe")
SHEARS_SPEED = 15.0   # ShearsItem.getDestroySpeed on leaves and cobweb


class ToolSet:
    """TS: the best speed the hotbar has against a block kind. The hand is 1. A tool of the kind's class digs at its
    tier speed; anything else is 1, as in vanilla. Memoized per kind (TS:94-96)."""

    def __init__(self, hotbar, settings):
        self.hotbar = [item_id(item) for item in hotbar]
        self.tools = []          # (hotbar slot, tool class, speed)
        self.has_pickaxe = False
        for slot, tid in enumerate(self.hotbar):
            if not tid:
                continue
            name = tid.split(":", 1)[-1]
            if name == "shears":
                self.tools.append((slot, "shears", SHEARS_SPEED))
                continue
            for cls in TOOL_CLASSES:
                if name.endswith("_" + cls):
                    speed = TIER_SPEED.get(name[:-len(cls) - 1])
                    if speed:
                        self.tools.append((slot, cls, speed))
                        if cls == "pickaxe":
                            self.has_pickaxe = True
                    break
        self.avoid_breaking = frozenset(settings.blocksToAvoidBreaking)
        self.avoid_breaking_multiplier = settings.avoidBreakingMultiplier
        self._memo = {}

    def best_slot(self, kind):
        """TS:137-180 getBestSlot: the hotbar slot with the best speed against the kind, or None for the hand (any
        slot will do). Ties go to the earliest slot, which is also the lowest tier in a sorted hotbar."""
        best, best_speed = None, 1.0
        for slot, cls, speed in self.tools:
            if self._applies(cls, kind) and speed > best_speed:
                best, best_speed = slot, speed
        return best

    @staticmethod
    def _applies(cls, kind):
        if cls == kind.tool:
            return True
        return cls == "shears" and (kind.type_id.endswith("leaves") or kind.tool == "shears")

    def speed_vs(self, kind):
        """ItemStack.getDestroySpeed for the best tool: the tier speed with the right class, else 1."""
        best = 1.0
        for _slot, cls, speed in self.tools:
            if speed > best and self._applies(cls, kind):
                best = speed
        return best

    def str_vs_block(self, kind):
        """TS:94-96, 188-195, 244-266: speed against the kind per tick of mining, or -1 when unbreakable."""
        strength = self._memo.get(kind)
        if strength is None:
            strength = self._str_vs_block(kind)
            self._memo[kind] = strength
        return strength

    def _str_vs_block(self, kind):
        hardness = kind.hardness
        if hardness is None or hardness < 0:                       # TS:250-256
            return -1.0
        speed = self.speed_vs(kind)                                # TS:257
        speed = speed / hardness if hardness > 0 else float("inf")  # TS:258 (a float division by 0 is Infinity in Java)
        correct_tool = kind.tool != "pickaxe" or self.has_pickaxe  # TS:259: requiresCorrectToolForDrops is the pickaxe blocks
        strength = speed / 30 if correct_tool else speed / 100     # TS:259-263
        if kind.type_id in self.avoid_breaking:                    # TS:193-195 avoidanceMultiplier
            strength *= self.avoid_breaking_multiplier
        return strength


class CalculationContext:
    """CC:128-172 snapshotted from a Settings, a Costs, a hotbar and a world."""

    def __init__(self, world, costs=None, settings=None, hotbar=(), can_sprint=True, dimension="overworld"):
        s = settings if settings is not None else Settings()
        c = costs if costs is not None else DEFAULT_COSTS
        self.world, self.costs, self.settings = world, c, s
        self.hotbar = list(hotbar or ())
        self.toolset = ToolSet(self.hotbar, s)
        self.dimension = dimension
        self.min_y, self.max_y = DIMENSION_Y[dimension.split(":", 1)[-1]]              # BSI:91-93
        throwaway = set(s.acceptableThrowawayItems)
        self.has_throwaway = s.allowPlace and any(item_id(i) in throwaway for i in self.hotbar)   # CC:118
        self.has_water_bucket = False       # CC:119: allowWaterBucketFall is off here and there is never a bucket (plan, Fidelity 6)
        self.can_sprint = s.allowSprint and bool(can_sprint)                            # CC:120
        self.place_block_cost = s.blockPlacementPenalty                                 # CC:141
        self.allow_break = s.allowBreak                                                 # CC:142
        self.allow_parkour = s.allowParkour                                             # CC:144
        self.allow_parkour_place = s.allowParkourPlace                                  # CC:145
        self.allow_jump_at_build_limit = s.allowJumpAtBuildLimit                        # CC:146
        self.allow_parkour_ascend = s.allowParkourAscend                                # CC:147
        self.max_parkour_jump = int(s.maxParkourJump)                                   # departs: settings.maxParkourJump
        self.assume_walk_on_water = s.assumeWalkOnWater                                 # CC:148
        self.allow_fall_into_lava = False                                               # CC:149
        self.frost_walker = 0                                                           # CC:150 (Fidelity 6: no enchantments)
        self.allow_diagonal_descend = s.allowDiagonalDescend                            # CC:151
        self.allow_diagonal_ascend = s.allowDiagonalAscend                              # CC:152
        self.allow_downward = s.allowDownward                                           # CC:153
        self.min_fall_height = 3                                                        # CC:154
        self.max_fall_height_no_water = s.maxFallHeightNoWater                          # CC:155
        self.max_fall_height_bucket = s.maxFallHeightBucket                             # CC:156
        # departs: plan Fidelity 1. CC:157 gives WALK_ONE_BLOCK_COST with no Depth Strider (quirk 1); the player
        # wades, so water costs WALK_ONE_IN_WATER_COST, with CC:158-161's swim discount kept
        water_speed = c.WALK_ONE_IN_WATER_COST
        if s.allowSwimming and self.can_sprint:
            water_speed = min(water_speed, c.SWIM_ONE_BLOCK_COST)
        self.water_walk_speed = water_speed
        self.break_block_additional_cost = s.blockBreakAdditionalPenalty                # CC:163
        self.backtrack_cost_favoring_coefficient = s.backtrackCostFavoringCoefficient   # CC:164
        self.jump_penalty = s.jumpPenalty                                               # CC:165
        self.walk_on_water_one_penalty = s.walkOnWaterOnePenalty                        # CC:166
        self.allow_walk_on_magma_blocks = s.allowWalkOnMagmaBlocks                      # CC:167
        # settings the helpers read directly (sheet section 3, "other settings referenced by movements")
        self.assume_walk_on_lava = s.assumeWalkOnLava
        self.avoid_updating_falling_blocks = s.avoidUpdatingFallingBlocks
        self.strict_liquid_check = s.strictLiquidCheck
        self.allow_place_in_fluids_source = s.extra.get("allowPlaceInFluidsSource", True)   # ST:83
        self.allow_place_in_fluids_flow = s.extra.get("allowPlaceInFluidsFlow", True)       # ST:88
        self.blocks_to_avoid = frozenset(s.blocksToAvoid)
        self._kind_flags = {}
        self._unknown = [False]     # touched_unknown, as a list cell so the search loop reads it by subscription
        self.claim_search_caches()

    @property
    def touched_unknown(self):
        """True once a block read since the last reset came back UNKNOWN (plan, Fidelity 2)."""
        return self._unknown[0]

    @touched_unknown.setter
    def touched_unknown(self, value):
        self._unknown[0] = value

    # ---- the per-search caches (CC:204-234) ----

    def claim_search_caches(self):
        """Fresh caches: block flags by packed position, the two mining memos, loaded chunks. A* calls this at the
        start of a search; the executor calls block_changed() instead."""
        self._flags = {}
        self._mining = {}
        self._mining_falling = {}
        self.at = self._make_at()

    release_search_caches = claim_search_caches
    block_changed = claim_search_caches     # any block change invalidates the neighbour-dependent memos too

    def _make_at(self):
        cache = self._flags
        kind_of = self.world.kind
        kind_flags = self._kind_flags
        compute = self.kind_flags
        unknown = self._unknown

        cache_get = cache.get

        def at(x, y, z):
            key = (x << 38) + (z << 12) + y
            f = cache_get(key)
            if f is not None:
                return f
            kind = kind_of(x, y, z)
            f = kind_flags.get(kind)
            if f is None:
                f = compute(kind)
            if f == UNKNOWN:                     # not cached, so every read of an unread block is noticed
                unknown[0] = True
                return f
            cache[key] = f
            return f
        return at

    def kind(self, x, y, z):
        return self.world.kind(x, y, z)

    def is_loaded(self, x, z):
        """CC:243-245 isLoaded: the terrain's own answer, asked only at chunk crossings (AS:111) and for the
        cutoffs, so not cached (a test world's box may end mid-chunk)."""
        return bool(self.world.loaded(x, z))

    def kind_flags(self, kind):
        """world.py's flags plus helpers.py's extended bits, once per BlockKind per context."""
        f = kind.flags
        if f & UNKNOWN or kind is UNKNOWN_KIND:
            self._kind_flags[kind] = UNKNOWN
            return UNKNOWN
        tid = kind.type_id
        name = tid.split(":", 1)[-1]
        states = kind.states or {}
        if tid in self.blocks_to_avoid:                                 # MH:177: blocksToAvoid -> canWalkThrough NO
            f &= ~(WALK_THROUGH | PASSABLE)
        if name == "soul_sand":
            f |= X_SOUL_SAND
        elif name == "magma":
            f |= X_MAGMA
            if self.allow_walk_on_magma_blocks:                         # MH:367: magma is danger only when not allowed
                f &= ~DANGER
        elif name in ("ladder", "vine"):
            f |= X_LADDER_OR_VINE
        elif name == "ice" or name.startswith("infested_"):
            f |= X_NEVER_BREAK
        elif name in ("air", "cave_air", "void_air"):
            f |= X_AIR
        elif name in ("water", "flowing_water"):
            f |= X_WATER_BLOCK | X_LIQUID_BLOCK
        elif name in ("lava", "flowing_lava"):
            f |= X_LIQUID_BLOCK
        elif name in ("waterlily", "lily_pad"):
            f |= X_LILY_PAD
        elif name.endswith("carpet"):
            f |= X_CARPET
        elif name == "farmland":
            f |= X_FARMLAND
        elif name.endswith("fence_gate"):
            f |= X_FENCE_GATE
        elif name.endswith("stairs"):
            f |= X_STAIRS
        if "slab" in name and "double" not in name \
                and states.get("minecraft:vertical_half", states.get("vertical_half")) != "double":
            f |= X_SLAB_HALF
        if name in REPLACEABLE_NAMES or name.startswith("light_block"):
            f |= X_REPLACEABLE
        elif name == "snow_layer":
            # MH:318-324: LAYERS == 1. Bedrock's height state counts from 0 and Java's LAYERS from 1, so both
            # one-layer readings are accepted
            if str(states.get("height", "0")) in ("0", "1"):
                f |= X_REPLACEABLE
        self._kind_flags[kind] = f
        return f

    # ---- CC:259-295 ----

    def cost_of_placing_at(self, x, y, z, f=None):
        """CC:259-276."""
        if not self.has_throwaway:                                      # CC:260-262
            return COST_INF
        if f is None:
            f = self.at(x, y, z)
        if f & LIQUID:
            if not (f & FLOWING) and not self.allow_place_in_fluids_source:    # CC:269-271
                return COST_INF
            if (f & FLOWING) and not self.allow_place_in_fluids_flow:          # CC:272-274
                return COST_INF
        return self.place_block_cost                                    # CC:275

    def break_cost_multiplier_at(self, x, y, z, f=None):
        """CC:278-286: INF when breaking is off (allowBreakAnyway is empty here), else 1."""
        if not self.allow_break:
            return COST_INF
        return 1.0

    def place_bucket_cost(self):
        return self.place_block_cost                                    # CC:288-290

    # ---- MH:604-655 ----

    def get_mining_duration_ticks(self, x, y, z, include_falling, f=None):
        """MH:604-624: memoized per (position, includeFalling) for the search."""
        if f is not None and f & WALK_THROUGH and not f & LIQUID:
            return 0.0                                                  # MH:629 fast path: certainly walk-through
        key = (x << 38) + (z << 12) + y
        memo = self._mining_falling if include_falling else self._mining
        try:
            return memo[key]
        except KeyError:
            result = memo[key] = self._mining_duration0(x, y, z, include_falling, f)
            return result

    def _mining_duration0(self, x, y, z, include_falling, f):
        """MH:626-655, with the whitelist rule in front of the tool table."""
        if f is None:
            f = self.at(x, y, z)
        if can_walk_through(self, x, y, z, f):                          # MH:629, 655: nothing to mine
            return 0.0
        if f & LIQUID:                                                  # MH:630-632
            return COST_INF
        mult = self.break_cost_multiplier_at(x, y, z, f)                # MH:633
        if mult >= COST_INF:                                            # MH:634-636
            return COST_INF
        if avoid_breaking(self, x, y, z, f):                            # MH:637-639
            return COST_INF
        if not f & BREAKABLE:
            # departs: plan Fidelity 5. Baritone breaks anything with a finite hardness; only the natural terrain
            # whitelist is broken here. UNKNOWN ends here too (never BREAKABLE)
            return COST_INF
        strength = self.toolset.str_vs_block(self.world.kind(x, y, z))  # MH:640
        if strength <= 0:                                               # MH:641-643
            return COST_INF
        result = 1 / strength + self.break_block_additional_cost        # MH:644-645
        result *= mult                                                  # MH:646
        if include_falling:                                             # MH:647-652
            above = self.at(x, y + 1, z)
            if above & GRAVITY:
                result += self.get_mining_duration_ticks(x, y + 1, z, True, above)
        return result

    def __repr__(self):
        return (f"CalculationContext(throwaway={self.has_throwaway}, sprint={self.can_sprint}, "
                f"y={self.min_y}..{self.max_y}, hotbar={self.toolset.hotbar})")


__all__ = ["CalculationContext", "ToolSet", "pack", "unpack", "item_id", "DIMENSION_Y", "TIER_SPEED"]
