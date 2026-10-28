"""MovementHelper's predicates (java/baritone/src/main/java/baritone/pathing/movement/MovementHelper.java, "MH")
as bit tests on a block's flags, per docs/research-movements.md section 4.

world.py gives every block type the flags Baritone's PrecomputedData would (WALK_ON, WALK_THROUGH, PASSABLE, ...).
The few places the movements ask for a block *type* rather than a predicate (soul sand, magma, a ladder, a lily pad,
"is this Blocks.WATER") are answered by the extended bits below, which CalculationContext computes once per
BlockKind and folds into the same int, so every question the planner asks is one `&`. The position-dependent
answers (still water with air above, a flowing neighbour, the block above a waterlogged floor) read the
neighbours through the context's per-search cache.

Every function takes the context and coordinates; most also accept the block's flags `f` when the caller already
has them, so the hot path never fetches a block twice. UNKNOWN (an unread band) fails every predicate: the plan's
fidelity item 2, "unknown blocks are not air" (Baritone reads them as air, BSI:154-215).
"""
from .world import (WALK_ON, WALK_THROUGH, PASSABLE, PLACE_AGAINST, WATER, LAVA, FLOWING, DANGER, TALL, CLIMB,
                    BOTTOM_SLAB, GRAVITY, OPENABLE, BREAKABLE, UNKNOWN, LIQUID)

# extended bits, above world.py's 0..14, set by CalculationContext.kind_flags() from the type id and states
X_SOUL_SAND = 1 << 16        # Blocks.SOUL_SAND
X_MAGMA = 1 << 17            # Blocks.MAGMA_BLOCK
X_LADDER_OR_VINE = 1 << 18   # Blocks.LADDER or Blocks.VINE exactly (Desc:117, Down:66), narrower than isClimbable
X_NEVER_BREAK = 1 << 19      # neverBreak: ICE or an InfestedBlock (PD:132-136, MH:90-92)
X_AIR = 1 << 20              # AirBlock
X_WATER_BLOCK = 1 << 21      # Blocks.WATER: the water block itself (source or flowing), not a waterlogged block
X_LIQUID_BLOCK = 1 << 22     # LiquidBlock: water or lava, not a waterlogged block
X_LILY_PAD = 1 << 23         # Blocks.LILY_PAD (Bedrock: waterlily)
X_CARPET = 1 << 24           # CarpetBlock
X_FARMLAND = 1 << 25         # Blocks.FARMLAND (Park:146)
X_FENCE_GATE = 1 << 26       # FenceGateBlock (Pill:73)
X_SLAB_HALF = 1 << 27        # SlabBlock with TYPE != DOUBLE (Trav:157)
X_STAIRS = 1 << 28           # StairBlock (Park:94)
X_REPLACEABLE = 1 << 29      # isReplaceable (MH:311-338): air, fluids, replaceable plants, a one-layer snow layer

X_LILY_OR_CARPET = X_LILY_PAD | X_CARPET

# the hot path's two fast answers, one mask test each, so the cost functions call the functions below only when a
# position rule (water, a neighbour) could change the answer:
#   f & WALK_THROUGH_MASK == WALK_THROUGH   certainly can_walk_through (walk-through, no fluid)
#   f & WALK_ON_MASK == WALK_ON             certainly can_walk_on (a full top, no fluid)
WALK_THROUGH_MASK = WALK_THROUGH | LIQUID
WALK_ON_MASK = WALK_ON | LIQUID

# vanilla's replaceable() blocks (BlockBehaviour.Properties.replaceable), by Bedrock name, plus Baritone's own
# LARGE_FERN and TALL_GRASS (MH:325-327). Flowers, saplings, crops and mushrooms are not replaceable.
REPLACEABLE_NAMES = frozenset((
    "air", "cave_air", "void_air", "water", "flowing_water", "lava", "flowing_lava", "fire", "soul_fire",
    "short_grass", "tallgrass", "fern", "large_fern", "tall_grass", "dead_bush", "deadbush", "seagrass",
    "tall_seagrass", "vine", "glow_lichen", "hanging_roots", "crimson_roots", "warped_roots", "nether_sprouts",
    "bubble_column", "structure_void", "light_block",
))


def is_water(f):
    """MH:738-741: the fluid is water, waterlogged blocks included."""
    return bool(f & WATER)


def is_lava(f):
    """MH:768-771."""
    return bool(f & LAVA)


def is_liquid(f):
    """MH:784-786: any fluid."""
    return bool(f & LIQUID)


def is_bottom_slab(f):
    """MH:657-660."""
    return bool(f & BOTTOM_SLAB)


def is_climbable(f):
    """MH:595-602: ladder and the vines."""
    return bool(f & CLIMB)


def can_place_against(ctx, x, y, z, f=None):
    """MH:567-588: a normal cube or glass; UNKNOWN never."""
    if f is None:
        f = ctx.at(x, y, z)
    return bool(f & PLACE_AGAINST)


def fully_passable(ctx, x, y, z, f=None):
    """MH:248-309: nothing to collide with at all."""
    if f is None:
        f = ctx.at(x, y, z)
    return bool(f & PASSABLE)


def is_replaceable(ctx, x, y, z, f=None):
    """MH:311-338."""
    if f is None:
        f = ctx.at(x, y, z)
    return bool(f & X_REPLACEABLE)


def avoid_walking_into(f):
    """MH:363-373: any fluid, magma (unless allowWalkOnMagmaBlocks, stripped from DANGER by kind_flags), cactus,
    berry bush, fire, end portal, cobweb, bubble column: world.py's DANGER bit."""
    return bool(f & (DANGER | LIQUID))


def is_waterlogged_solid_top(f):
    """MH:763-766: WATERLOGGED and the top face is sturdy. With flags: a waterlogged block that has a full top
    (WALK_ON, not a bottom slab) and is not the water block itself."""
    return bool(f & WATER) and bool(f & WALK_ON) and not (f & BOTTOM_SLAB) and not (f & X_LIQUID_BLOCK)


def is_flowing(ctx, x, y, z, f=None):
    """MH:794-806: not a fluid -> False; not a full-level fluid -> True; else any of the four horizontal
    neighbours possiblyFlowing (MH:788-792), which is world.py's FLOWING bit."""
    if f is None:
        f = ctx.at(x, y, z)
    if not f & LIQUID:
        return False
    if f & FLOWING:
        return True
    at = ctx.at
    return bool((at(x + 1, y, z) | at(x - 1, y, z) | at(x, y, z + 1) | at(x, y, z - 1)) & FLOWING)


def can_walk_through(ctx, x, y, z, f=None):
    """MH:146-246. The per-state answer is world.py's WALK_THROUGH (MH:157-204; carpet and snow layers are folded
    into the flags, MH:206-220). A fluid is MAYBE (MH:195-199) and goes to the position rule (MH:228-243): only
    still water, with no fluid or lily pad above and assumeWalkOnWater off, is walk-through; lava never."""
    if f is None:
        f = ctx.at(x, y, z)
    if not f & WALK_THROUGH:
        return False                                    # NO states, and UNKNOWN
    if f & LIQUID:
        if f & FLOWING:                                 # MH:195-196: getAmount != 8 -> NO
            return False
        if is_flowing(ctx, x, y, z, f):                 # MH:230
            return False
        if ctx.assume_walk_on_water:                    # MH:233
            return False
        up = ctx.at(x, y + 1, z)
        if up & LIQUID or up & X_LILY_PAD:              # MH:237-239
            return False
        return bool(f & WATER)                          # MH:242: WaterFluid only
    return True


def can_walk_on(ctx, x, y, z, f=None):
    """MH:389-465. The per-state answer is world.py's WALK_ON (MH:400-439). Water that is not a waterlogged solid
    top is MAYBE (MH:423-425) and goes to the position rule (MH:441-465): with the defaults a water block is
    walk-on only when there is water above it (swimming at the surface counts as standing). Lava is walk-on
    only when still and assumeWalkOnLava."""
    if f is None:
        f = ctx.at(x, y, z)
    if f & WATER:
        if is_waterlogged_solid_top(f):                 # MH:423: rule 8 does not fire, the block's own rule answers
            return bool(f & WALK_ON)
        up = ctx.at(x, y + 1, z)                        # MH:444
        if up & X_LILY_OR_CARPET:                       # MH:445-448
            return True
        if is_flowing(ctx, x, y, z, f) or (up & X_WATER_BLOCK and up & FLOWING):   # MH:449-452
            return bool(up & WATER) and not ctx.assume_walk_on_water
        return bool(up & WATER) != ctx.assume_walk_on_water                        # MH:455
    if f & LAVA:
        return ctx.assume_walk_on_lava and not is_flowing(ctx, x, y, z, f)         # MH:427-429, 458-461
    return bool(f & WALK_ON)


def must_be_solid_to_walk_on(ctx, x, y, z, f=None):
    """MH:520-557: "will it have a top to walk on?". Climbable -> False. A waterlogged block keeps its top when
    it is a top slab, a top or inner stair, a closed top trapdoor, scaffolding or leaves (MH:527-547): with flags,
    any waterlogged block with a full top. Otherwise a wet floor is no floor when assumeWalkOnWater, or when the
    block above is a liquid block (MH:549-555)."""
    if f is None:
        f = ctx.at(x, y, z)
    if f & CLIMB:                                       # MH:522
        return False
    if f & LIQUID:
        if (f & WALK_ON) and not (f & BOTTOM_SLAB) and not (f & X_LIQUID_BLOCK):
            return True                                 # MH:527-547 as far as the flags allow
        if ctx.assume_walk_on_water:                    # MH:549
            return False
        if ctx.at(x, y + 1, z) & X_LIQUID_BLOCK:        # MH:552-555: blockAbove instanceof LiquidBlock
            return False
    return True


def is_free(f):
    """FallingBlock.isFree: air, fire, a liquid or a replaceable block (what an unsupported sand falls through)."""
    return bool(f & (X_AIR | LIQUID | X_REPLACEABLE))


def avoid_adjacent_breaking(ctx, x, y, z, directly_above):
    """MH:101-128."""
    f = ctx.at(x, y, z)
    if (not directly_above and f & GRAVITY and ctx.avoid_updating_falling_blocks
            and is_free(ctx.at(x, y - 1, z))):         # MH:104-109: an unsupported falling block beside it
        return True
    if f & X_LIQUID_BLOCK:                              # MH:111: a LiquidBlock, not a waterlogged block
        if directly_above or ctx.strict_liquid_check:   # MH:112-114
            return True
        if not f & FLOWING:                             # MH:115-118: LEVEL == 0, a source
            return True
        return not (ctx.at(x, y - 1, z) & X_LIQUID_BLOCK)   # MH:119
    return bool(f & LIQUID)                             # MH:121: a waterlogged neighbour


def avoid_breaking(ctx, x, y, z, f=None):
    """MH:74-83: never-break blocks (ice, infested), then the block above and the four beside it. The block below
    is never checked. The world border is not a thing here."""
    if f is None:
        f = ctx.at(x, y, z)
    return bool(f & X_NEVER_BREAK) or avoid_adjacent_breaking(ctx, x, y + 1, z, True) \
        or avoid_adjacent_breaking(ctx, x + 1, y, z, False) or avoid_adjacent_breaking(ctx, x - 1, y, z, False) \
        or avoid_adjacent_breaking(ctx, x, y, z + 1, False) or avoid_adjacent_breaking(ctx, x, y, z - 1, False)


def is_blocking_door(ctx, f, door_x, door_z, other_x, other_z):
    """Diag:366-385. Needs the door's FACING and HINGE, which the palette entry does not carry (world.py has only
    OPENABLE), so no door blocks a diagonal here. The call sites keep Baritone's argument shapes, quirk 2 included."""
    return False


__all__ = [name for name in globals() if name.isupper() or name.startswith(("is_", "can_", "avoid_", "fully_", "must_"))]
