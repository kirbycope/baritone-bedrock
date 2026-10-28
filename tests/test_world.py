"""Pins the classification table: the flags and hardness of the blocks the planner meets, every state case, and the
rule that a block nobody described is solid, unbreakable and not a face to place against."""
import unittest

from baritone_bedrock import world
from baritone_bedrock.world import (AIR_KIND, BOTTOM_SLAB, BREAKABLE, CLIMB, DANGER, FLOWING, GRAVITY, LAVA, OPENABLE,
                                    PASSABLE, PLACE_AGAINST, TALL, UNKNOWN, UNKNOWN_KIND, WALK_ON, WALK_THROUGH, WATER,
                                    DictWorld, classify)

SOLID = WALK_ON | PLACE_AGAINST
NATURAL = SOLID | BREAKABLE
PLANT = PASSABLE | WALK_THROUGH
SLAB_BOTTOM = WALK_ON | PLACE_AGAINST | BOTTOM_SLAB

# (entry, flags, hardness): the table, pinned
PINNED = [
    # air and liquids
    ("minecraft:air", PLANT, 0),
    ("minecraft:cave_air", PLANT, 0),
    ("minecraft:water|liquid_depth=0", WATER | WALK_THROUGH, 100),
    ("minecraft:water|liquid_depth=3", WATER | WALK_THROUGH | FLOWING, 100),
    ("minecraft:flowing_water|liquid_depth=1", WATER | FLOWING | WALK_THROUGH, 100),
    ("minecraft:flowing_water|liquid_depth=0", WATER | FLOWING | WALK_THROUGH, 100),
    ("minecraft:lava|liquid_depth=0", LAVA | DANGER, 100),
    ("minecraft:flowing_lava|liquid_depth=2", LAVA | FLOWING | DANGER, 100),
    # the natural terrain whitelist
    ("minecraft:grass_block", NATURAL, 0.6),
    ("minecraft:dirt", NATURAL, 0.5),
    ("minecraft:coarse_dirt", NATURAL, 0.5),
    ("minecraft:podzol", NATURAL, 0.5),
    ("minecraft:mycelium", NATURAL, 0.5),
    ("minecraft:mud", NATURAL, 0.5),
    ("minecraft:clay", NATURAL, 0.6),
    ("minecraft:snow", NATURAL, 0.2),
    ("minecraft:sand", NATURAL | GRAVITY, 0.5),
    ("minecraft:red_sand", NATURAL | GRAVITY, 0.5),
    ("minecraft:gravel", NATURAL | GRAVITY, 0.5),
    ("minecraft:white_concrete_powder", SOLID | GRAVITY, 0.5),
    ("minecraft:soul_sand", NATURAL, 0.5),
    ("minecraft:soul_soil", NATURAL, 0.5),
    ("minecraft:stone", NATURAL, 1.5),
    ("minecraft:cobblestone", NATURAL, 2.0),
    ("minecraft:andesite", NATURAL, 1.5),
    ("minecraft:granite", NATURAL, 1.5),
    ("minecraft:diorite", NATURAL, 1.5),
    ("minecraft:tuff", NATURAL, 1.5),
    ("minecraft:calcite", NATURAL, 0.75),
    ("minecraft:dripstone_block", NATURAL, 1.5),
    ("minecraft:sandstone", NATURAL, 0.8),
    ("minecraft:netherrack", NATURAL, 0.4),
    ("minecraft:basalt", NATURAL, 1.25),
    ("minecraft:end_stone", NATURAL, 3.0),
    ("minecraft:blackstone", NATURAL, 1.5),
    ("minecraft:deepslate", NATURAL, 3.0),
    ("minecraft:cobbled_deepslate", NATURAL, 3.5),
    ("minecraft:deepslate_iron_ore", NATURAL, 4.5),
    ("minecraft:coal_ore", NATURAL, 3.0),
    ("minecraft:nether_gold_ore", NATURAL, 3.0),
    ("minecraft:infested_stone", SOLID, 0.75),
    ("minecraft:oak_log", NATURAL, 2.0),
    ("minecraft:stripped_birch_log", NATURAL, 2.0),
    ("minecraft:cherry_log", NATURAL, 2.0),
    ("minecraft:pale_oak_log", NATURAL, 2.0),
    ("minecraft:pale_oak_wood", NATURAL, 2.0),
    ("minecraft:crimson_stem", NATURAL, 2.0),
    ("minecraft:warped_hyphae", NATURAL, 2.0),
    ("minecraft:bamboo_block", NATURAL, 2.0),
    ("minecraft:mangrove_roots", NATURAL, 0.7),
    ("minecraft:muddy_mangrove_roots", NATURAL, 0.7),
    ("minecraft:oak_leaves", NATURAL, 0.2),
    ("minecraft:cherry_leaves", NATURAL, 0.2),
    ("minecraft:pale_oak_leaves", NATURAL, 0.2),
    ("minecraft:azalea_leaves_flowered", NATURAL, 0.2),
    ("minecraft:ice", SOLID, 0.5),
    ("minecraft:packed_ice", SOLID, 0.5),
    ("minecraft:moss_block", NATURAL, 0.1),
    ("minecraft:red_mushroom_block", NATURAL, 0.2),
    ("minecraft:mushroom_stem", NATURAL, 0.2),
    ("minecraft:nether_wart_block", NATURAL, 1.0),
    ("minecraft:shroomlight", NATURAL, 1.0),
    ("minecraft:pumpkin", NATURAL, 1.0),
    ("minecraft:melon_block", NATURAL, 1.0),
    ("minecraft:farmland", WALK_ON | BREAKABLE, 0.6),
    ("minecraft:grass_path", WALK_ON | BREAKABLE, 0.6),
    # slabs: bottom, top, double, and the legacy names
    ("minecraft:oak_slab|minecraft:vertical_half=bottom", SLAB_BOTTOM, 2.0),
    ("minecraft:oak_slab|minecraft:vertical_half=top", SOLID, 2.0),
    ("minecraft:oak_double_slab", SOLID, 2.0),
    ("minecraft:smooth_stone_slab|minecraft:vertical_half=bottom", SLAB_BOTTOM, 2.0),
    ("minecraft:cut_copper_slab|minecraft:vertical_half=top", SOLID, 2.0),
    ("minecraft:stone_slab|minecraft:vertical_half=bottom", SLAB_BOTTOM, 2.0),
    ("minecraft:stone_slab2|minecraft:vertical_half=top", SOLID, 2.0),
    ("minecraft:double_stone_slab3", SOLID, 2.0),
    ("minecraft:stone_block_slab4|minecraft:vertical_half=bottom", SLAB_BOTTOM, 2.0),
    # stairs
    ("minecraft:oak_stairs|upside_down_bit=false", SLAB_BOTTOM, 2.0),
    ("minecraft:stone_stairs|upside_down_bit=true", SOLID, 2.0),
    ("minecraft:normal_stone_stairs|upside_down_bit=false", SLAB_BOTTOM, 2.0),
    ("minecraft:prismarine_bricks_stairs|upside_down_bit=true", SOLID, 2.0),
    # snow layers: three of eight and up are a floor
    ("minecraft:snow_layer|height=0", PLANT, 0.1),
    ("minecraft:snow_layer|height=1", PLANT, 0.1),
    ("minecraft:snow_layer|height=2", PLANT, 0.1),
    ("minecraft:snow_layer|height=3", SOLID, 0.1),
    ("minecraft:snow_layer|height=7", SOLID, 0.1),
    ("minecraft:snow_layer|height=8", SOLID, 0.1),
    # gates, fences, walls
    ("minecraft:oak_fence_gate|open_bit=false", TALL | OPENABLE, 2.0),
    ("minecraft:oak_fence_gate|open_bit=true", OPENABLE | WALK_THROUGH, 2.0),
    ("minecraft:fence_gate|open_bit=false", TALL | OPENABLE, 2.0),
    ("minecraft:pale_oak_fence_gate|open_bit=true", OPENABLE | WALK_THROUGH, 2.0),
    ("minecraft:oak_fence", TALL, 2.0),
    ("minecraft:bamboo_fence", TALL, 2.0),
    ("minecraft:nether_brick_fence", TALL, 2.0),
    ("minecraft:cobblestone_wall", TALL, 2.0),
    ("minecraft:stone_brick_wall", TALL, 2.0),
    ("minecraft:mud_brick_wall", TALL, 2.0),
    # doors and trapdoors
    ("minecraft:wooden_door|open_bit=false", OPENABLE | WALK_THROUGH, 3.0),
    ("minecraft:wooden_door|open_bit=true", OPENABLE | WALK_THROUGH, 3.0),
    ("minecraft:spruce_door|open_bit=false", OPENABLE | WALK_THROUGH, 3.0),
    ("minecraft:copper_door|open_bit=false", OPENABLE | WALK_THROUGH, 3.0),
    ("minecraft:iron_door|open_bit=false", 0, 5.0),
    ("minecraft:iron_door|open_bit=true", 0, 5.0),
    ("minecraft:trapdoor|open_bit=false", OPENABLE | WALK_ON, 3.0),
    ("minecraft:trapdoor|open_bit=true", OPENABLE | WALK_THROUGH | PASSABLE, 3.0),
    ("minecraft:cherry_trapdoor|open_bit=true", OPENABLE | WALK_THROUGH | PASSABLE, 3.0),
    ("minecraft:iron_trapdoor|open_bit=true", 0, 5.0),
    # colours and glass
    ("minecraft:white_wool", SOLID, 1.5),
    ("minecraft:red_carpet", WALK_THROUGH, 0.1),
    ("minecraft:white_stained_glass", SOLID, 0.3),
    ("minecraft:glass", SOLID, 0.3),
    ("minecraft:tinted_glass", SOLID, 0.3),
    ("minecraft:glass_pane", 0, 1.0),
    ("minecraft:white_stained_glass_pane", 0, 1.0),
    ("minecraft:iron_bars", 0, 1.0),
    # plants and crops
    ("minecraft:short_grass", PLANT, 0.0),
    ("minecraft:tall_grass", PLANT, 0.0),
    ("minecraft:tallgrass", PLANT, 0.0),
    ("minecraft:fern", PLANT, 0.0),
    ("minecraft:poppy", PLANT, 0.0),
    ("minecraft:red_tulip", PLANT, 0.0),
    ("minecraft:oak_sapling", PLANT, 0.0),
    ("minecraft:wildflowers", PLANT, 0.0),
    ("minecraft:pink_petals", PLANT, 0.0),
    ("minecraft:leaf_litter", PLANT, 0.0),
    ("minecraft:bush", PLANT, 0.0),
    ("minecraft:deadbush", PLANT, 0.0),
    ("minecraft:reeds", PLANT, 0.0),
    ("minecraft:brown_mushroom", PLANT, 0.0),
    ("minecraft:crimson_roots", PLANT, 0.0),
    ("minecraft:nether_sprouts", PLANT, 0.0),
    ("minecraft:kelp", PLANT, 0.0),
    ("minecraft:seagrass", PLANT, 0.0),
    ("minecraft:hanging_roots", PLANT, 0.0),
    ("minecraft:glow_lichen", PLANT, 0.0),
    ("minecraft:tube_coral", PLANT, 0.0),
    ("minecraft:brain_coral_fan", PLANT, 0.0),
    ("minecraft:dead_brain_coral_wall_fan", PLANT, 0.0),
    ("minecraft:wheat", PLANT, 0.0),
    ("minecraft:cactus_flower", PLANT, 0.0),
    # waterlogged
    ("minecraft:kelp|waterlogged=true", PLANT | WATER, 0.0),
    ("minecraft:oak_stairs|upside_down_bit=false|waterlogged=true", SLAB_BOTTOM | WATER, 2.0),
    ("minecraft:oak_slab|minecraft:vertical_half=top|waterlogged=true", SOLID | WATER, 2.0),
    # the odd ones
    ("minecraft:azalea", WALK_ON, 0.0),
    ("minecraft:flowering_azalea", WALK_ON, 0.0),
    ("minecraft:big_dripleaf", 0, 1.0),
    ("minecraft:cocoa", 0, 1.0),
    ("minecraft:bamboo", 0, 1.0),
    ("minecraft:chorus_plant", 0, 1.0),
    ("minecraft:chorus_flower", SOLID, 1.5),
    ("minecraft:ladder", CLIMB | WALK_THROUGH | WALK_ON, 0.4),
    ("minecraft:vine", CLIMB | WALK_THROUGH, 0.2),
    ("minecraft:weeping_vines", CLIMB | WALK_THROUGH, 0.2),
    ("minecraft:cave_vines", CLIMB | WALK_THROUGH, 0.2),
    # danger
    ("minecraft:fire", DANGER, 0),
    ("minecraft:soul_fire", DANGER, 0),
    ("minecraft:campfire", DANGER, 0),
    ("minecraft:cactus", DANGER, 0),
    ("minecraft:sweet_berry_bush", DANGER, 0),
    ("minecraft:magma", DANGER, 0),
    ("minecraft:wither_rose", DANGER, 0),
    ("minecraft:powder_snow", DANGER, 0),
    ("minecraft:pointed_dripstone", DANGER, 0),
    ("minecraft:web", DANGER, 0),
    ("minecraft:end_portal", DANGER, 0),
    ("minecraft:bubble_column", DANGER, 0),
    ("minecraft:portal", DANGER, 0),
    # unbreakable and nearly so
    ("minecraft:bedrock", SOLID, -1),
    ("minecraft:barrier", SOLID, -1),
    ("minecraft:invisible_bedrock", SOLID, -1),
    ("minecraft:command_block", SOLID, -1),
    ("minecraft:structure_void", PLANT, -1),
    ("minecraft:light_block_15", PLANT, -1),
    ("minecraft:obsidian", SOLID, 50),
    ("minecraft:crying_obsidian", SOLID, 50),
    # furniture
    ("minecraft:chest", WALK_ON, 2.5),
    ("minecraft:trapped_chest", WALK_ON, 2.5),
    ("minecraft:ender_chest", WALK_ON, 2.5),
    ("minecraft:white_shulker_box", 0, 1.0),
    ("minecraft:enchanting_table", 0, 1.0),
    ("minecraft:anvil", 0, 1.0),
    ("minecraft:bed", 0, 1.0),
    ("minecraft:flower_pot", 0, 1.0),
    ("minecraft:lantern", 0, 1.0),
    ("minecraft:cauldron", 0, 1.0),
    ("minecraft:end_rod", 0, 1.0),
    ("minecraft:skeleton_skull", 0, 1.0),
    ("minecraft:player_head", 0, 1.0),
    ("minecraft:scaffolding", 0, 1.0),
    ("minecraft:honey_block", 0, 1.0),
    ("minecraft:trip_wire", 0, 1.0),
    ("minecraft:tripwire_hook", PLANT, 0.0),
    ("minecraft:slime", SOLID, 0.0),
    ("minecraft:waterlily", WALK_THROUGH, 0.1),
    ("minecraft:moss_carpet", WALK_THROUGH, 0.1),
    ("minecraft:torch", PLANT, 0.0),
    ("minecraft:oak_button", PLANT, 0.0),
    ("minecraft:rail", PLANT, 0.0),
    ("minecraft:standing_sign", PLANT, 0.0),
    ("minecraft:wall_banner", PLANT, 0.0),
    ("minecraft:stone_pressure_plate", PLANT, 0.0),
    ("minecraft:lever", PLANT, 0.0),
    ("minecraft:redstone_wire", PLANT, 0.0),
    # built full cubes: solid, not breakable
    ("minecraft:crafting_table", SOLID, 1.5),
    ("minecraft:furnace", SOLID, 1.5),
    ("minecraft:bookshelf", SOLID, 1.5),
    ("minecraft:oak_planks", SOLID, 1.5),
    ("minecraft:stone_bricks", SOLID, 1.5),
    ("minecraft:brick_block", SOLID, 1.5),
    ("minecraft:quartz_block", SOLID, 1.5),
    ("minecraft:copper_block", SOLID, 1.5),
    ("minecraft:waxed_oxidized_cut_copper", SOLID, 1.5),
    ("minecraft:white_terracotta", SOLID, 1.5),
    ("minecraft:hardened_clay", SOLID, 1.5),
    ("minecraft:white_glazed_terracotta", SOLID, 1.5),
    ("minecraft:coal_block", SOLID, 1.5),
    ("minecraft:hay_block", SOLID, 1.5),
    ("minecraft:bone_block", SOLID, 1.5),
    ("minecraft:brain_coral_block", SOLID, 1.5),
    ("minecraft:polished_deepslate", SOLID, 1.5),
    ("minecraft:deepslate_tiles", SOLID, 1.5),
    ("minecraft:cinnabar_bricks", SOLID, 1.5),
    ("minecraft:sulfur", SOLID, 1.5),
    ("minecraft:oak_shelf", SOLID, 1.5),
]


class ClassifyTest(unittest.TestCase):
    def test_pinned_flags_and_hardness(self):
        self.assertGreaterEqual(len(PINNED), 60)
        for entry, flags, hardness in PINNED:
            with self.subTest(entry=entry):
                kind = classify(entry)
                self.assertEqual(kind.flags, flags, f"{entry}: flags {kind.flags:#x}, wanted {flags:#x}")
                self.assertEqual(kind.hardness, hardness)

    def test_entry_is_parsed(self):
        kind = classify("minecraft:oak_slab|minecraft:vertical_half=top|waterlogged=true")
        self.assertEqual(kind.type_id, "minecraft:oak_slab")
        self.assertEqual(kind.states, {"minecraft:vertical_half": "top", "waterlogged": "true"})
        self.assertEqual(kind.tool, "pickaxe")
        self.assertIs(classify(kind.entry), kind, "classified once, then looked up")

    def test_unknown_namespace_is_solid_unbreakable_and_not_place_against(self):
        for entry in ("cobblemon:gym_floor", "cobblemon:pc", "somepack:thing|open_bit=true", "minecraft:not_a_block_yet"):
            with self.subTest(entry=entry):
                kind = classify(entry)
                self.assertTrue(kind.has(WALK_ON))
                self.assertFalse(kind.has(WALK_THROUGH))
                self.assertFalse(kind.has(BREAKABLE))
                self.assertFalse(kind.has(PLACE_AGAINST))
                self.assertEqual(kind.hardness, 1.5)
                self.assertIsNone(kind.tool)

    def test_unknown_kind_fails_every_predicate(self):
        self.assertEqual(UNKNOWN_KIND.flags, UNKNOWN)
        for flag in (WALK_ON, WALK_THROUGH, PASSABLE, PLACE_AGAINST, WATER, LAVA, DANGER, TALL, CLIMB, BOTTOM_SLAB, BREAKABLE):
            self.assertFalse(UNKNOWN_KIND.has(flag))
        self.assertEqual(AIR_KIND.type_id, world.AIR)
        self.assertTrue(AIR_KIND.has(PASSABLE))

    def test_namespace_is_ignored_for_the_name(self):
        self.assertEqual(classify("minecraft:stone").flags, classify("stone").flags)
        self.assertEqual(classify("minecraft:oak_fence").flags, TALL)

    def test_tools_on_the_whitelist(self):
        self.assertEqual(classify("minecraft:dirt").tool, "shovel")
        self.assertEqual(classify("minecraft:stone").tool, "pickaxe")
        self.assertEqual(classify("minecraft:oak_log").tool, "axe")
        self.assertEqual(classify("minecraft:oak_leaves").tool, "hoe")

    def test_every_vanilla_family_classifies(self):
        """A sweep of Bedrock 1.26 names, one of each family: nothing raises and nothing breakable is outside the
        whitelist's hardness range."""
        names = ("acacia_shelf", "amethyst_cluster", "bamboo_mosaic", "beacon", "budding_amethyst", "candle_cake", "chain",
                 "copper_golem_statue", "copper_grate", "creaking_heart", "decorated_pot", "dried_ghast", "element_12",
                 "frog_spawn", "heavy_core", "lightning_rod", "mangrove_propagule", "pale_moss_block", "piston", "poplar_log",
                 "red_poplar_leaves", "resin_block", "sculk_shrieker", "sea_pickle", "sniffer_egg", "sulfur_spike", "target",
                 "trial_spawner", "turtle_egg", "vault", "waxed_weathered_copper_trapdoor|open_bit=false")
        for name in names:
            with self.subTest(name=name):
                kind = classify("minecraft:" + name)
                if kind.has(BREAKABLE):
                    self.assertGreater(kind.hardness, 0)
                    self.assertLessEqual(kind.hardness, 4.5)


class TerrainTest(unittest.TestCase):
    def test_dict_world(self):
        w = DictWorld({(0, 63, 0): "minecraft:stone"}, box=((-8, 0, -8), (8, 128, 8)))
        self.assertTrue(w.has(0, 63, 0, WALK_ON))
        self.assertTrue(w.is_air(0, 64, 0))
        self.assertIs(w.kind(9, 64, 0), UNKNOWN_KIND)
        self.assertTrue(w.loaded(0, 0))
        self.assertFalse(w.loaded(20, 0))
        w.fill(0, 60, 0, 2, 60, 2, "minecraft:dirt")
        self.assertEqual(sum(1 for k in w.blocks.values() if k == "minecraft:dirt"), 9)
        self.assertEqual(w.flags(1, 60, 1), NATURAL)


if __name__ == "__main__":
    unittest.main()
