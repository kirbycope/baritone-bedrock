"""Test worlds for the planner: DictWorld builders for the movement rules, and ArrayWorld, a Terrain backed by
per-band bytearrays of palette indices, the storage probe.py uses for the bands the pack sends (band key
(dim, cx, cz, y0), 32 levels, index ((x & 15) * 16 + (z & 15)) * 32 + (y - y0)), with a rolling-hills generator
for the A* tests and the benchmark.
"""
import random

from baritone_bedrock.world import Terrain, DictWorld, classify, UNKNOWN_KIND, AIR_KIND, AIR

# palette entries used by the tests
GRASS = "minecraft:grass_block"
DIRT = "minecraft:dirt"
STONE = "minecraft:stone"
COBBLESTONE = "minecraft:cobblestone"
DEEPSLATE = "minecraft:deepslate"
SAND = "minecraft:sand"
GRAVEL = "minecraft:gravel"
SOUL_SAND = "minecraft:soul_sand"
ICE = "minecraft:ice"
WATER = "minecraft:water"
FLOWING_WATER = "minecraft:flowing_water"
LAVA = "minecraft:lava"
LADDER = "minecraft:ladder"
VINE = "minecraft:vine"
MAGMA = "minecraft:magma"
OAK_LOG = "minecraft:oak_log"
OAK_LEAVES = "minecraft:oak_leaves"
TALL_GRASS = "minecraft:tall_grass"
SHORT_GRASS = "minecraft:short_grass"
POPPY = "minecraft:poppy"
CACTUS = "minecraft:cactus"
FENCE = "minecraft:oak_fence"
FENCE_GATE = "minecraft:oak_fence_gate"
BOTTOM_SLAB = "minecraft:stone_slab|minecraft:vertical_half=bottom"
TOP_SLAB = "minecraft:stone_slab|minecraft:vertical_half=top"
DOUBLE_SLAB = "minecraft:stone_slab|minecraft:vertical_half=double"
STAIRS = "minecraft:stone_stairs|upside_down_bit=false"
CHEST = "minecraft:chest"
GLASS = "minecraft:glass"
BEDROCK = "minecraft:bedrock"
UNKNOWN_BLOCK = "cobblemon:anything"   # not in world.py's table: solid, not BREAKABLE


# ---------------------------------------------------------------------------------------------------------------
# DictWorld builders
# ---------------------------------------------------------------------------------------------------------------

def flat(height=63, radius=24, top=GRASS, under=DIRT, depth=4, box=None):
    """Flat ground: `top` at y = height, `under` for two below it, stone down to height - depth, over a square of
    the given radius around the origin. The DictWorld box (where known blocks end) is its default unless given."""
    world = DictWorld() if box is None else DictWorld(box=box)
    world.fill(-radius, height - depth, -radius, radius, height - 3, radius, STONE)
    world.fill(-radius, height - 2, -radius, radius, height - 1, radius, under)
    world.fill(-radius, height, -radius, radius, height, radius, top)
    world.ground = height
    return world


def wall(world, x0, z0, x1, z1, y0, height, block=STONE):
    """A solid wall from y0 up `height` blocks over the rectangle."""
    world.fill(x0, y0, z0, x1, y0 + height - 1, z1, block)
    return world


def clear(world, x0, y0, z0, x1, y1, z1):
    """Air over a box (removes the entries)."""
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                world.blocks.pop((x, y, z), None)
    return world


def hill_of_steps(world, x0, z0, z1, base_y, steps, step_width=1, block=STONE, direction=1):
    """A staircase rising one block per step along +x (or -x) from x0, each step `step_width` wide, spanning
    z0..z1, solid down to base_y. Returns the x after the last step."""
    x = x0
    for i in range(1, steps + 1):
        world.fill(x, base_y, z0, x + (step_width - 1) * direction, base_y + i, z1, block)
        x += step_width * direction
    return x


def cliff(world, x0, z0, x1, z1, base_y, height, block=STONE):
    """A plateau `height` blocks above base_y over the rectangle, solid to the base."""
    world.fill(x0, base_y, z0, x1, base_y + height, z1, block)
    return world


def water_pool(world, x0, z0, x1, z1, surface_y, depth=1, flowing=False, floor=STONE):
    """Still (or flowing) water with its surface at surface_y, `depth` deep, over a floor. The ground entries in
    the pool are replaced, so the top of the water is at the level of the surrounding ground."""
    entry = FLOWING_WATER if flowing else WATER
    world.fill(x0, surface_y - depth, z0, x1, surface_y - depth, z1, floor)
    world.fill(x0, surface_y - depth + 1, z0, x1, surface_y, z1, entry)
    return world


def tunnel(world, x0, z, x1, floor_y, block=STONE, thickness=3):
    """A 1x2 tunnel along x at z through a block of stone: the stone spans x0..x1 and z-thickness..z+thickness
    up to floor_y + 4; the tunnel is air at floor_y + 1 and floor_y + 2 for every x in x0..x1."""
    world.fill(x0, floor_y - 1, z - thickness, x1, floor_y + 4, z + thickness, block)
    clear(world, x0, floor_y + 1, z, x1, floor_y + 2, z)
    return world


def column(world, x, z, y0, entries):
    """Stack entries upward from y0 at (x, z); None leaves air."""
    for i, entry in enumerate(entries):
        if entry is None:
            world.blocks.pop((x, y0 + i, z), None)
        else:
            world.set(x, y0 + i, z, entry)
    return world


# ---------------------------------------------------------------------------------------------------------------
# ArrayWorld
# ---------------------------------------------------------------------------------------------------------------

class ArrayWorld(Terrain):
    """Bands of 16 x 16 x 32 palette indices in bytearrays, one palette (a list of BlockKind) per band. A chunk
    counts as loaded once any band of it exists; a loaded chunk's missing bands read as air between min_y and
    max_y, so a flat world needs only the bands with ground in them."""
    BAND = 32

    def __init__(self, dimension="overworld", min_y=-64, max_y=319):
        self.dimension = dimension
        self.min_y, self.max_y = min_y, max_y
        self.bands = {}       # (dim, cx, cz, y0) -> [bytearray, palette list, palette index dict]
        self.chunks = set()   # (cx, cz) loaded

    def band_y0(self, y):
        return self.min_y + ((y - self.min_y) // self.BAND) * self.BAND

    def band(self, x, y, z, create=False):
        key = (self.dimension, x >> 4, z >> 4, self.band_y0(y))
        band = self.bands.get(key)
        if band is None and create:
            band = [bytearray(16 * 16 * self.BAND), [AIR_KIND], {AIR_KIND.entry: 0}]
            self.bands[key] = band
            self.chunks.add((x >> 4, z >> 4))
        return band

    def kind(self, x, y, z):
        band = self.bands.get((self.dimension, x >> 4, z >> 4, self.min_y + ((y - self.min_y) // 32) * 32))
        if band is None:
            if y < self.min_y or y > self.max_y or (x >> 4, z >> 4) not in self.chunks:
                return UNKNOWN_KIND
            return AIR_KIND
        y0 = self.min_y + ((y - self.min_y) // 32) * 32
        return band[1][band[0][((x & 15) * 16 + (z & 15)) * 32 + (y - y0)]]

    def loaded(self, x, z):
        return (x >> 4, z >> 4) in self.chunks

    def load_chunk(self, cx, cz):
        """Mark a chunk loaded (all air) without creating a band."""
        self.chunks.add((cx, cz))

    def palette_index(self, band, entry):
        index = band[2].get(entry)
        if index is None:
            index = len(band[1])
            if index > 255:
                raise ValueError("more than 256 kinds in one band")
            band[1].append(classify(entry))
            band[2][entry] = index
        return index

    def set(self, x, y, z, entry):
        band = self.band(x, y, z, create=True)
        y0 = self.band_y0(y)
        band[0][((x & 15) * 16 + (z & 15)) * 32 + (y - y0)] = self.palette_index(band, entry)

    def set_column(self, x, z, y_from, y_to, entry):
        """Fill (x, z) from y_from to y_to inclusive, a band slice at a time."""
        y = y_from
        while y <= y_to:
            band = self.band(x, y, z, create=True)
            y0 = self.band_y0(y)
            top = min(y_to, y0 + self.BAND - 1)
            base = ((x & 15) * 16 + (z & 15)) * 32
            index = self.palette_index(band, entry)
            band[0][base + (y - y0):base + (top - y0) + 1] = bytes((index,)) * (top - y + 1)
            y = top + 1

    def fill(self, x0, y0, z0, x1, y1, z1, entry):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                self.set_column(x, z, min(y0, y1), max(y0, y1), entry)
        return self

    def height_at(self, x, z, top=None):
        """The y of the highest non-air block in the column (scanning down from `top`), or None."""
        y = self.max_y if top is None else top
        while y >= self.min_y:
            if self.kind(x, y, z) is not AIR_KIND and self.kind(x, y, z) is not UNKNOWN_KIND:
                return y
            y -= 1
        return None


def flat_array(size=256, height=63, origin=(0, 0), top=GRASS):
    """A flat ArrayWorld: grass at `height` over dirt and stone, chunks covering origin .. origin + size."""
    world = ArrayWorld()
    ox, oz = origin
    for x in range(ox, ox + size):
        for z in range(oz, oz + size):
            world.set_column(x, z, height - 6, height - 3, STONE)
            world.set_column(x, z, height - 2, height - 1, DIRT)
            world.set_column(x, z, height, height, top)
    return world


def _lattice(seed, size, scale):
    rng = random.Random(seed)
    n = size // scale + 2
    return [[rng.random() for _ in range(n)] for _ in range(n)]


def _smooth(t):
    return t * t * (3 - 2 * t)


def value_noise(lattice, x, z, scale):
    gx, gz = x / scale, z / scale
    ix, iz = int(gx), int(gz)
    fx, fz = _smooth(gx - ix), _smooth(gz - iz)
    a = lattice[ix][iz] * (1 - fx) + lattice[ix + 1][iz] * fx
    b = lattice[ix][iz + 1] * (1 - fx) + lattice[ix + 1][iz + 1] * fx
    return a * (1 - fz) + b * fz


def hills_heights(size=256, seed=1, base=60, amplitude=6, scale=48):
    """A seeded height map: two octaves of smooth value noise, heights base .. base + amplitude. The defaults are
    rolling plains (a 6 block rise over 48); amplitude 8 over 28 is hill country and is what the benchmark's
    "steep" run uses."""
    coarse = _lattice(seed, size, scale)
    fine = _lattice(seed + 1, size, scale // 3)
    heights = []
    for x in range(size):
        row = []
        for z in range(size):
            n = 0.7 * value_noise(coarse, x, z, scale) + 0.3 * value_noise(fine, x, z, scale // 3)
            row.append(base + int(round(n * amplitude)))
        heights.append(row)
    return heights


def rolling_hills(size=256, seed=1, base=60, amplitude=6, scale=48, origin=(0, 0)):
    """An ArrayWorld of rolling hills over size x size blocks: stone, three of dirt, grass on top. Returns the
    world and its height map (heights[x][z] is the grass block's y)."""
    heights = hills_heights(size, seed, base, amplitude, scale)
    world = ArrayWorld()
    ox, oz = origin
    for x in range(size):
        row = heights[x]
        for z in range(size):
            h = row[z]
            world.set_column(ox + x, oz + z, base - 8, h - 4, STONE)
            world.set_column(ox + x, oz + z, h - 3, h - 1, DIRT)
            world.set_column(ox + x, oz + z, h, h, GRASS)
    world.heights = heights
    return world, heights


__all__ = [name for name in globals() if not name.startswith("_")]
