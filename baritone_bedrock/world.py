"""The world as the planner sees it: one flag byte per block, computed once per block type, the way Baritone's
PrecomputedData (java/baritone/src/main/java/baritone/pathing/precompute/PrecomputedData.java) makes
MovementHelper's predicates a table lookup. The probe sends bands of blocks as palette indices (pack/), the palette
entries are "typeId" or "typeId|state=value|..." strings, and classify() turns each into a BlockKind.

The predicates are those of MovementHelper (docs/research-movements.md section 4), decided per Bedrock block type
here because the Script API has no collision shapes to ask: WALK_ON is canWalkOn, WALK_THROUGH is canWalkThrough,
PASSABLE is fullyPassable, PLACE_AGAINST is canPlaceAgainst, DANGER is avoidWalkingInto. The table in KINDS is the
part of this file that grows; add a row rather than a special case.
"""
import re

# flags
WALK_ON = 1 << 0        # a full top to stand on
WALK_THROUGH = 1 << 1   # the body can be in it
PASSABLE = 1 << 2       # nothing to collide with at all (also WALK_THROUGH)
PLACE_AGAINST = 1 << 3  # a block can be placed against its faces
WATER = 1 << 4
LAVA = 1 << 5
FLOWING = 1 << 6        # a liquid that is not a source or a top layer
DANGER = 1 << 7         # avoidWalkingInto
TALL = 1 << 8           # fences, walls, closed gates: 1.5 high
CLIMB = 1 << 9          # ladders, vines
BOTTOM_SLAB = 1 << 10   # a bottom slab or an upright stair: walked up onto without a jump
GRAVITY = 1 << 11       # sand, gravel, concrete powder
OPENABLE = 1 << 12      # wooden doors, trapdoors, fence gates
BREAKABLE = 1 << 13     # natural terrain the navigator may break (the whitelist)
UNKNOWN = 1 << 14       # not loaded: fails every predicate
LIQUID = WATER | LAVA

AIR = "minecraft:air"


class BlockKind:
    __slots__ = ("entry", "type_id", "states", "flags", "hardness", "tool")

    def __init__(self, entry, type_id, states, flags, hardness=0.0, tool=None):
        self.entry, self.type_id, self.states, self.flags, self.hardness, self.tool = entry, type_id, states, flags, hardness, tool

    def has(self, flag):
        return bool(self.flags & flag)

    def __repr__(self):
        return f"BlockKind({self.entry!r}, flags={self.flags:#x}, hardness={self.hardness}, tool={self.tool})"


# (pattern on the bare name, flags, hardness, tool). First match wins, and the last row matches everything, so a
# block nobody has described is solid, unbreakable and not to be placed against. Hardness and the tool that digs it
# fast are the vanilla values (the Bedrock numbers are the Java ones for these blocks); a hardness under 0 is
# unbreakable. The names are Bedrock 1.26 typeIds without their namespace (mojang-blocks.json of the Script API),
# with the legacy flattened names (stone_slab2, tallgrass, leaves2) kept beside them. Where a row departs from
# MovementHelper the MH line is beside it (docs/research-movements.md section 4).
_SOLID = WALK_ON | PLACE_AGAINST
_PLANT = PASSABLE | WALK_THROUGH
_NATURAL = _SOLID | BREAKABLE
KINDS = [
    # air, then the liquids (MH:159 AirBlock YES; MH:738-771 isWater and isLava; MH:788-792 possiblyFlowing)
    (r"^(air|cave_air|void_air)$", _PLANT, 0, None),
    (r"^(water)$", WATER | WALK_THROUGH, 100, None),
    (r"^(flowing_water)$", WATER | FLOWING | WALK_THROUGH, 100, None),
    (r"^(lava)$", LAVA | DANGER, 100, None),
    (r"^(flowing_lava)$", LAVA | FLOWING | DANGER, 100, None),
    # avoidWalkingInto (MH:363-373) with the canWalkThrough NO list it shares (MH:160-170): never entered
    (r"^(fire|soul_fire|campfire|soul_campfire|cactus|sweet_berry_bush|magma|wither_rose|powder_snow|pointed_dripstone|web|cobweb|end_portal|end_gateway|bubble_column|portal|sulfur_spike)$", DANGER, 0, None),
    # climbables (MH:595-602): the ladder is canWalkOn YES (MH:408) and never fullyPassable (MH:255)
    (r"^(ladder)$", CLIMB | WALK_THROUGH | WALK_ON, 0.4, "axe"),
    (r"^(vine|weeping_vines|twisting_vines|cave_vines|cave_vines_body_with_berries|cave_vines_head_with_berries)$", CLIMB | WALK_THROUGH, 0.2, "shears"),
    # technical blocks with no collision
    (r"^(structure_void|light_block|light_block_\d+)$", _PLANT, -1, None),
    # unbreakable solids
    (r"^(bedrock|invisible_bedrock|barrier|command_block|chain_command_block|repeating_command_block|structure_block|end_portal_frame|jigsaw|allow|deny|border_block|moving_block|piston_arm_collision|sticky_piston_arm_collision|unknown|reinforced_deepslate|heavy_core|vault|trial_spawner|mob_spawner)$", _SOLID, -1, None),
    (r"^(obsidian|crying_obsidian|respawn_anchor|ancient_debris|netherite_block)$", _SOLID, 50, None),
    # the natural terrain whitelist (docs/baritone-port-plan.md, Terrain classification): the only blocks ever broken
    (r"^(grass_block|grass)$", _NATURAL, 0.6, "shovel"),
    (r"^(dirt|coarse_dirt|dirt_with_roots|rooted_dirt|podzol|mycelium|mud|crimson_nylium|warped_nylium)$", _NATURAL, 0.5, "shovel"),
    (r"^(clay)$", _NATURAL, 0.6, "shovel"),
    (r"^(snow)$", _NATURAL, 0.2, "shovel"),
    (r"^(sand|red_sand|gravel|suspicious_sand|suspicious_gravel)$", _NATURAL | GRAVITY, 0.5, "shovel"),
    (r"^(.*_concrete_powder|concrete_powder|concretepowder)$", _SOLID | GRAVITY, 0.5, "shovel"),   # built, so never broken; still never mined under
    (r"^(soul_sand|soul_soil)$", _NATURAL, 0.5, "shovel"),
    (r"^(stone|andesite|diorite|granite|tuff|dripstone_block|blackstone)$", _NATURAL, 1.5, "pickaxe"),
    (r"^(cobblestone|mossy_cobblestone)$", _NATURAL, 2.0, "pickaxe"),
    (r"^(calcite)$", _NATURAL, 0.75, "pickaxe"),
    (r"^(sandstone|red_sandstone)$", _NATURAL, 0.8, "pickaxe"),
    (r"^(netherrack)$", _NATURAL, 0.4, "pickaxe"),
    (r"^(basalt|smooth_basalt)$", _NATURAL, 1.25, "pickaxe"),
    (r"^(end_stone)$", _NATURAL, 3.0, "pickaxe"),
    (r"^(deepslate)$", _NATURAL, 3.0, "pickaxe"),
    (r"^(cobbled_deepslate)$", _NATURAL, 3.5, "pickaxe"),
    (r"^(deepslate_.*_ore)$", _NATURAL, 4.5, "pickaxe"),
    (r"^(infested_.*)$", _SOLID, 0.75, "pickaxe"),   # neverBreak: InfestedBlock (MH:90-92)
    (r"^(.*_ore|lit_redstone_ore|lit_deepslate_redstone_ore|gilded_blackstone)$", _NATURAL, 3.0, "pickaxe"),
    (r"^(.*_log|.*_wood|log|log2|wood|crimson_stem|warped_stem|stripped_crimson_stem|stripped_warped_stem|.*_hyphae|bamboo_block|stripped_bamboo_block)$", _NATURAL, 2.0, "axe"),
    (r"^(mangrove_roots)$", _NATURAL, 0.7, "axe"),
    (r"^(muddy_mangrove_roots)$", _NATURAL, 0.7, "shovel"),
    (r"^(.*_leaves|leaves|leaves2|azalea_leaves_flowered)$", _NATURAL, 0.2, "hoe"),
    (r"^(ice|packed_ice|blue_ice|frosted_ice)$", _SOLID, 0.5, "pickaxe"),   # ice is never broken (MH:90-92), the rest with it
    (r"^(moss_block|pale_moss_block)$", _NATURAL, 0.1, "hoe"),
    (r"^(sculk)$", _NATURAL, 0.2, "hoe"),
    (r"^(brown_mushroom_block|red_mushroom_block|mushroom_stem)$", _NATURAL, 0.2, "axe"),
    (r"^(nether_wart_block|warped_wart_block|shroomlight)$", _NATURAL, 1.0, "hoe"),
    (r"^(pumpkin|carved_pumpkin|lit_pumpkin|melon_block)$", _NATURAL, 1.0, "axe"),
    # halves and stairs: classify() reads vertical_half and upside_down_bit (slabs MH:430-437, stairs MH:420)
    (r"^(.*_slab|stone_slab\d?|double_stone_slab\d?|stone_block_slab\d?|double_stone_block_slab\d?|wooden_slab|double_wooden_slab)$", 0, 2.0, "pickaxe"),
    (r"^(.*_stairs|.*stairs)$", 0, 2.0, "pickaxe"),
    # fences, walls and gates are a block and a half high (the fence checks in MovementTraverse, docs 9.1)
    (r"^(.*_fence_gate|fence_gate)$", TALL | OPENABLE, 2.0, "axe"),
    (r"^(.*_fence|fence|nether_brick_fence)$", TALL, 2.0, "axe"),
    (r"^(.*_wall|cobblestone_wall)$", TALL, 2.0, "pickaxe"),
    # doors and trapdoors: wooden and copper ones open by hand and are walked through (MH:171-173, isWoodenDoor
    # is BlockSetType.canOpenByHand); iron ones are a wall. classify() reads open_bit for gates and trapdoors.
    (r"^(iron_door)$", 0, 5.0, "pickaxe"),
    (r"^(iron_trapdoor)$", 0, 5.0, "pickaxe"),
    (r"^(.*_door|wooden_door)$", OPENABLE | WALK_THROUGH, 3.0, "axe"),
    (r"^(.*_trapdoor|trapdoor)$", OPENABLE, 3.0, "axe"),
    # not full cubes, with collision: neither stood on (not isBlockNormalCube, MH:808-824) nor walked through.
    # Baritone's isPathfindable(LAND) would let the player into some of these; a stick-driven player bumps them.
    (r"^(glass_pane|.*_glass_pane|stained_glass_pane|iron_bars|.*copper_bars|chain|.*copper_chain|iron_chain|lantern|soul_lantern|.*copper_lantern|end_rod|lightning_rod|.*lightning_rod"
     r"|.*_skull|skull|.*_head|chorus_plant|bell|brewing_stand|cauldron|hopper|composter|lectern|grindstone|stonecutter_block|anvil|chipped_anvil|damaged_anvil|enchanting_table"
     r"|bed|straw_bed|cake|candle_cake|.*_candle_cake|candle|.*_candle|conduit|dragon_egg|turtle_egg|sniffer_egg|decorated_pot|flower_pot|.*_golem_statue|camera|dried_ghast|sea_pickle"
     r"|.*_amethyst_bud|amethyst_cluster|piston|sticky_piston|daylight_detector|daylight_detector_inverted|shelf_mushroom|big_dripleaf|cocoa|scaffolding|honey_block|bamboo"
     r"|.*_shulker_box|shulker_box|undyed_shulker_box|trip_wire|tripwire|string)$", 0, 1.0, None),   # trip_wire: blocksToAvoid (settings.py)
    # chests: canWalkOn YES (MH:414) without being a normal cube
    (r"^(chest|trapped_chest|ender_chest|.*copper_chest|barrel)$", WALK_ON, 2.5, "axe"),
    # azalea: canWalkOn YES (MH:405), canWalkThrough NO (MH:168)
    (r"^(azalea|flowering_azalea)$", WALK_ON, 0.0, None),
    # the chorus flower is a full cube, unlike the flowers below
    (r"^(chorus_flower)$", _SOLID, 1.5, "pickaxe"),
    # plants and crops: nothing to collide with (fullyPassable: isPathfindable(LAND), MH:248-309)
    (r"^(short_grass|tallgrass|tall_grass|fern|large_fern|double_plant|seagrass|tall_seagrass|kelp|kelp_plant|deadbush|dead_bush|bush|firefly_bush|short_dry_grass|tall_dry_grass"
     r"|.*_sapling|sapling|bamboo_sapling|mangrove_propagule|red_flower|yellow_flower|.*_flower|.*_tulip|poppy|dandelion|golden_dandelion|blue_orchid|allium|azure_bluet|oxeye_daisy"
     r"|cornflower|lily_of_the_valley|torchflower|sunflower|lilac|rose_bush|peony|pitcher_plant|wildflowers|pink_petals|leaf_litter|open_eyeblossom|closed_eyeblossom|red_shrub"
     r"|brown_mushroom|red_mushroom|crimson_roots|warped_roots|crimson_fungus|warped_fungus|nether_sprouts|sugar_cane|reeds|hanging_roots|spore_blossom|glow_lichen|sculk_vein"
     r"|small_dripleaf_block|pale_hanging_moss|frog_spawn|.*_coral_fan|.*_coral_wall_fan|coral_fan|coral_fan_dead|coral|.*coral|wheat|carrots|potatoes|beetroot|melon_stem|pumpkin_stem"
     r"|nether_wart|torchflower_crop|pitcher_crop)$", _PLANT, 0.0, None),
    # things with no collision fixed to a wall or a floor
    (r"^(torch|soul_torch|redstone_torch|unlit_redstone_torch|.*_torch|colored_torch_.*|underwater_torch|.*_button|lever|redstone_wire|tripwire_hook|rail|.*_rail|.*_sign"
     r"|.*hanging_sign|.*_banner|.*_pressure_plate|frame|glow_frame|painting)$", _PLANT, 0.0, None),
    # a thin layer walked over when there is a floor under it: CarpetBlock is MAYBE (MH:174, 206-208)
    (r"^(.*_carpet|carpet|moss_carpet|pale_moss_carpet|waterlily|snow_layer|powered_comparator|unpowered_comparator|powered_repeater|unpowered_repeater)$", WALK_THROUGH, 0.1, None),
    # glass counts as a face to place against (MH:587)
    (r"^(glass|stained_glass|.*_stained_glass|hard_glass|tinted_glass)$", _SOLID, 0.3, "pickaxe"),
    # slime is a normal cube (MH:402); the bounce is the executor's problem
    (r"^(slime)$", _SOLID, 0.0, None),
    (r"^(farmland|dirt_path|grass_path)$", WALK_ON | BREAKABLE, 0.6, "shovel"),   # canWalkOn YES (MH:412), not a normal cube
    # built full cubes: stood on and placed against, never broken
    (r"^(.*_wool|wool|.*_concrete|concrete|.*_terracotta|stained_hardened_clay|hardened_clay|.*_planks|planks|.*_bricks|.*_brick|brick_block|end_bricks|bamboo_mosaic|.*_tiles"
     r"|chiseled_.*|polished_.*|smooth_.*|cut_.*|cracked_.*|.*_copper|copper_block|copper_grate|.*copper_grate|copper_bulb|.*copper_bulb|.*_block|quartz_pillar|purpur_pillar"
     r"|prismarine|dark_prismarine|sponge|wet_sponge|glowstone|sea_lantern|bookshelf|chiseled_bookshelf|crafting_table|cartography_table|fletching_table|smithing_table|furnace"
     r"|lit_furnace|blast_furnace|lit_blast_furnace|smoker|lit_smoker|loom|jukebox|noteblock|dispenser|dropper|observer|crafter|target|tnt|underwater_tnt|lodestone|beacon|sulfur"
     r"|potent_sulfur|cinnabar|resin_clump|bee_nest|beehive|.*_shelf|packed_mud|.*_froglight|chorus_flower|budding_amethyst|element_\d+|creaking_heart|sculk_catalyst|sculk_sensor"
     r"|calibrated_sculk_sensor|sculk_shrieker|redstone_lamp|lit_redstone_lamp|compound_creator|lab_table|material_reducer|element_constructor|chemical_heat|nether_brick"
     r"|red_nether_brick|quartz_bricks|stone_bricks|mossy_stone_bricks|snow_block|hay_block|bone_block|prismarine_bricks|deepslate_tiles)$", _SOLID, 1.5, "pickaxe"),
    # everything else, any namespace: solid, not breakable, not a face to place against
    (r".*", WALK_ON, 1.5, None),
]
_COMPILED = [(re.compile(p), f, h, t) for p, f, h, t in KINDS]

_kinds = {}


def classify(entry):
    """A BlockKind for a palette entry. Unknown types are solid, unbreakable and not to be placed against: the
    safe reading for a block nobody has described."""
    kind = _kinds.get(entry)
    if kind is not None:
        return kind
    type_id, _, rest = entry.partition("|")
    states = dict(part.split("=", 1) for part in rest.split("|") if "=" in part) if rest else {}
    name = type_id.split(":", 1)[-1]
    flags, hardness, tool = _SOLID, 1.5, "pickaxe"
    for pattern, f, h, t in _COMPILED:
        if pattern.match(name):
            flags, hardness, tool = f, h, t
            break
    # states that change the answer
    if name == "snow_layer" or name.endswith(":snow_layer"):
        flags = _SOLID if int(states.get("height", 0)) >= 3 else PASSABLE | WALK_THROUGH
    elif name.endswith("_slab") or "slab" in name:
        if "double" in name or states.get("minecraft:vertical_half") == "double":
            flags = _SOLID
        elif states.get("minecraft:vertical_half", states.get("vertical_half", "bottom")) == "top":
            flags = _SOLID          # a top slab: a full top at the block's height (canWalkOn YES, not walk-through)
        else:
            flags = WALK_ON | PLACE_AGAINST | BOTTOM_SLAB
    elif name.endswith("stairs"):
        upside_down = states.get("upside_down_bit") in ("true", "1", True)
        flags = _SOLID if upside_down else WALK_ON | PLACE_AGAINST | BOTTOM_SLAB
    elif flags & OPENABLE:
        opened = states.get("open_bit") in ("true", "1", True)
        if "gate" in name:
            flags = (OPENABLE | WALK_THROUGH) if opened else (TALL | OPENABLE)
        elif "trapdoor" in name:
            flags = (OPENABLE | WALK_THROUGH | PASSABLE) if opened else (OPENABLE | WALK_ON)
    if name in ("water", "flowing_water") and states.get("liquid_depth", "0") not in ("0", 0):
        flags |= FLOWING
    if states.get("waterlogged") in ("true", "1", True):
        flags |= WATER
    kind = BlockKind(entry, type_id, states, flags, hardness, tool)
    _kinds[entry] = kind
    return kind


UNKNOWN_KIND = BlockKind("?", "?", {}, UNKNOWN, 0.0, None)
AIR_KIND = classify(AIR)


class Terrain:
    """What the planner and the executor read. kind() is the one abstract method; the rest are conveniences.
    Subclasses: ProbeTerrain (probe.py), DictWorld (tests)."""

    def kind(self, x, y, z):
        """The BlockKind at a position, UNKNOWN_KIND where it is not loaded."""
        raise NotImplementedError

    def flags(self, x, y, z):
        return self.kind(x, y, z).flags

    def has(self, x, y, z, flag):
        return bool(self.kind(x, y, z).flags & flag)

    def loaded(self, x, z):
        """Whether the column's chunk is loaded (Baritone: isLoaded(x, z))."""
        return not self.has(x, 64, z, UNKNOWN)

    def is_air(self, x, y, z):
        return self.kind(x, y, z).type_id == AIR


class DictWorld(Terrain):
    """A world in a dict for tests: {(x, y, z): entry}, everything else air inside the box, unknown outside."""

    def __init__(self, blocks=None, box=((-64, -64, -64), (64, 320, 64))):
        self.blocks = dict(blocks or {})
        self.box = box

    def kind(self, x, y, z):
        (x0, y0, z0), (x1, y1, z1) = self.box
        if not (x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1):
            return UNKNOWN_KIND
        return classify(self.blocks.get((x, y, z), AIR))

    def set(self, x, y, z, entry):
        self.blocks[(x, y, z)] = entry

    def fill(self, x0, y0, z0, x1, y1, z1, entry):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                for z in range(min(z0, z1), max(z0, z1) + 1):
                    self.blocks[(x, y, z)] = entry
        return self
