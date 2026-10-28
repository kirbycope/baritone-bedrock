# Baritone movement planner: re-implementation spec (from source)

## 0. Provenance, file key, and scope notes

Tree: `C:\GitHub\.claude\skills\ftue\java\baritone`. It is a shallow clone at HEAD `c55a9c5`, "Merge pull request #5150 …", dated 2026-10-22. It is a recent fork-era Baritone (MC 1.21 API) with additions upstream may not have: swimming (`SWIM_ONE_BLOCK_COST`, `allowSwimming`), `isWaterloggedSolidTop`, a precomputed-flag cache and mining memo.

File abbreviations used in citations (all absolute paths):

| Abbrev | Path |
|---|---|
| **AC** | `...\baritone\src\api\java\baritone\api\pathing\movement\ActionCosts.java` |
| **MH** | `...\baritone\src\main\java\baritone\pathing\movement\MovementHelper.java` |
| **MS** | `...\baritone\src\main\java\baritone\pathing\movement\MovementState.java` |
| **M** | `...\baritone\src\main\java\baritone\pathing\movement\Movement.java` |
| **CC** | `...\baritone\src\main\java\baritone\pathing\movement\CalculationContext.java` |
| **MV** | `...\baritone\src\main\java\baritone\pathing\movement\Moves.java` |
| **Trav / Asc / Desc / Fall / Diag / Down / Pill / Park** | `...\baritone\src\main\java\baritone\pathing\movement\movements\Movement{Traverse,Ascend,Descend,Fall,Diagonal,Downward,Pillar,Parkour}.java` |
| **TS** | `...\baritone\src\main\java\baritone\utils\ToolSet.java` |
| **PD** | `...\baritone\src\main\java\baritone\pathing\precompute\PrecomputedData.java` |
| **ST** | `...\baritone\src\api\java\baritone\api\Settings.java` |
| **PE** | `...\baritone\src\main\java\baritone\pathing\path\PathExecutor.java` |
| **IB** | `...\baritone\src\main\java\baritone\behavior\InventoryBehavior.java` |
| **AS** | `...\baritone\src\main\java\baritone\pathing\calc\AStarPathFinder.java` |
| **BSI** | `...\baritone\src\main\java\baritone\utils\BlockStateInterface.java` |

(`...` = `C:\GitHub\.claude\skills\ftue\java\baritone`)

Three names from the brief do not exist as described:
- **`breakFromAbove`** is not used by any movement. It is a BuilderProcess-only setting (ST:1099, `BuilderProcess.java:285`). The movement-side equivalent is the `includeFalling` flag of `getMiningDurationTicks`.
- **`PLACE_ONE_BLOCK_COST`** does not exist. The placement cost is the setting `blockPlacementPenalty` (default 20), returned by `CalculationContext.costOfPlacingAt`.
- **`MovementStatus.FAILED`** is never set by any movement; the enum comment calls it "Unused". Movements end with SUCCESS or UNREACHABLE. PathExecutor treats UNREACHABLE and FAILED the same (PE:229-233).

---

## 1. Conventions

- **Coordinates.** Source node `src = (x, y, z)`, where `y` is the feet block. Horizontal step `(dx, dz)`: NORTH = (0,−1), SOUTH = (0,+1), EAST = (+1,0), WEST = (−1,0).
- **Block names relative to src** (used throughout):
  - src floor = (x, y−1, z)
  - src feet = (x, y, z)
  - src head = (x, y+1, z)
  - src head+1 = (x, y+2, z)
  - src head+2 = (x, y+3, z)
  - The dest column (x+dx, z+dz) is named the same way: "dest floor", "dest feet", "dest head".
- **Unknown blocks are air.** Out-of-height y, an unloaded chunk with no cached region, or a null cached block all read as AIR (BSI:154-215). A re-implementation must treat unknown as air.
- **`playerFeet()`** = `BlockPos(floor(px), floor(py + 0.1251), floor(pz))`. If that block is a `SlabBlock`, it returns the block above (IPlayerContext.java:63-82).
- **Horizontal-and-down directions.** `HORIZONTALS_BUT_ALSO_DOWN_____SO_EVERY_DIRECTION_EXCEPT_UP = {NORTH, SOUTH, EAST, WEST, DOWN}` (M:38). This is the order used for every "find a face to place against" scan.
- **Cost rejection.** A cost `>= COST_INF` (1,000,000) means impossible. A* skips a move if `cost >= COST_INF`. It throws if `cost <= 0` or NaN (AS:127-139).

---

## 2. ActionCosts (AC) in full

Units are "roughly ticks" (AC:22-24).

| Constant | Formula (exact) | Value | Line |
|---|---|---|---|
| `WALK_ONE_BLOCK_COST` | `20 / 4.317` | **4.632847** | AC:25 |
| `WALK_ONE_IN_WATER_COST` | `20 / 2.2` | **9.090909** | AC:26 |
| `SWIM_ONE_BLOCK_COST` | `20 / 2.86` | **6.993007** | AC:31 |
| `WALK_ONE_OVER_SOUL_SAND_COST` | `WALK_ONE_BLOCK_COST * 2` | **9.265694** | AC:32 |
| `LADDER_UP_ONE_COST` | `20 / 2.35` | **8.510638** | AC:33 |
| `LADDER_DOWN_ONE_COST` | `20 / 3.0` | **6.666667** | AC:34 |
| `SNEAK_ONE_BLOCK_COST` | `20 / 1.3` | **15.384615** | AC:35 |
| `SPRINT_ONE_BLOCK_COST` | `20 / 5.612` | **3.563792** | AC:36 |
| `SPRINT_MULTIPLIER` | `SPRINT_ONE_BLOCK_COST / WALK_ONE_BLOCK_COST` | **0.769244** | AC:37 |
| `WALK_OFF_BLOCK_COST` | `WALK_ONE_BLOCK_COST * 0.8` | **3.706278** | AC:41 |
| `CENTER_AFTER_FALL_COST` | `WALK_ONE_BLOCK_COST - WALK_OFF_BLOCK_COST` | **0.926569** | AC:45 |
| `COST_INF` | `1000000` | 1e6 | AC:51 |
| `FALL_N_BLOCKS_COST[]` | `generateFallNBlocksCost()`: 4097 entries, `[i] = distanceToTicks(i)` | table below | AC:53, 72-78 |
| `FALL_1_25_BLOCKS_COST` | `distanceToTicks(1.25)` | **6.234400** | AC:55 |
| `FALL_0_25_BLOCKS_COST` | `distanceToTicks(0.25)` | **3.071002** | AC:56 |
| `JUMP_ONE_BLOCK_COST` | `FALL_1_25_BLOCKS_COST - FALL_0_25_BLOCKS_COST` | **3.163397** | AC:69 |

Physics helpers:
- `velocity(int ticks) = (Math.pow(0.98, ticks) - 1) * -3.92`, i.e. 3.92·(1 − 0.98^t). Values: v(0)=0, v(1)=0.0784, v(2)=0.155232, v(3)=0.230527, v(4)=0.304317, v(5)=0.376630 (AC:80-82).
- `distanceToTicks(d)` (AC:88-102):
  - If `d == 0`, return 0.
  - Otherwise set `t = 0` and loop: `f = velocity(t)`; if `remaining <= f`, return `t + remaining/f`; else `remaining -= f; t++`.
  - Tick 0 contributes 0 distance, so tick counting effectively starts at 1.
- `oldFormula(ticks) = -3.92 * (99 - 49.5 * (Math.pow(0.98, ticks) + 1) - ticks)` (AC:84-86) is unused.
- The test pins `FALL_1_25 = 6.2344`, `FALL_0_25 = 3.0710`, `JUMP = 3.1634` (`src/test/.../ActionCostsTest.java`).

`FALL_N_BLOCKS_COST[n]` (computed with the formula above):

| n | cost | n | cost | n | cost |
|---|---|---|---|---|---|
| 0 | 0 | 9 | 16.383894 | 18 | 23.469451 |
| 1 | 5.614724 | 10 | 17.292186 | 19 | 24.150752 |
| 2 | 7.788078 | 11 | 18.161894 | 20 | 24.814701 |
| 3 | 9.468673 | 12 | 18.998666 | 21 | 25.463783 |
| 4 | 10.911662 | 13 | 19.799001 | 22 | 26.103947 |
| 5 | 12.184288 | 14 | 20.574713 | 23 | 26.728272 |
| 6 | 13.344158 | 15 | 21.328972 | 24 | 27.342677 |
| 7 | 14.420596 | 16 | 22.064370 | 50 | 40.848065 |
| 8 | 15.429793 | 17 | 22.775299 | 100 | 60.900491 / 256: 109.874224 / 4096: 1094.897959 |

Common sub-expressions:
- `max(JUMP_ONE_BLOCK_COST, WALK_ONE_BLOCK_COST)` = **4.632847** (walk wins).
- `max(FALL_N_BLOCKS_COST[1], CENTER_AFTER_FALL_COST)` = **5.614724**.
- `(WALK_ONE_OVER_SOUL_SAND_COST − WALK)/2` = **2.316424**.
- `(SNEAK − WALK)/2` = **5.375884**.

---

## 3. CalculationContext values and settings defaults

Settings are snapshotted per search (CC:128-172).

| Context field | Source / formula | Default |
|---|---|---|
| `hasThrowaway` | `allowPlace && inventoryBehavior.hasGenericThrowaway()` (CC:118) | allowPlace=true (ST:78) |
| `hasWaterBucket` | `allowWaterBucketFall && water_bucket in hotbar && dimension != NETHER` (CC:119) | allowWaterBucketFall=true (ST:154) |
| `canSprint` | `allowSprint && foodLevel > 6` (CC:120) | allowSprint=true (ST:73) |
| `placeBlockCost` | `blockPlacementPenalty` (CC:141) | **20** (ST:124) |
| `allowBreak` / `allowBreakAnyway` | settings (CC:142-143) | true / [] (ST:63, 68) |
| `allowParkour` / `allowParkourPlace` / `allowParkourAscend` | (CC:144-147) | **false / false / true** (ST:362, 369, 202) |
| `allowJumpAtBuildLimit` | (CC:146) | false (ST:188) |
| `assumeWalkOnWater` | (CC:148) | false (ST:162) |
| `allowFallIntoLava` | hard false; ElytraProcess sets true | false (CC:149) |
| `frostWalker` | Frost Walker level on any equipped item (CC:174-186) | 0 |
| `allowDiagonalDescend` / `allowDiagonalAscend` | (CC:151-152) | **false / false** (ST:211, 218) |
| `allowDownward` | (CC:153) | true (ST:225) |
| `minFallHeight` | hard **3**; ElytraProcess sets 8 (ElytraProcess.java:559) | 3 (CC:154) |
| `maxFallHeightNoWater` | setting; ElytraProcess sets 10000 | **3** (ST:581) |
| `maxFallHeightBucket` | setting | **20** (ST:587) |
| `waterWalkSpeed` | see formula below (CC:157-162) | see note |
| `breakBlockAdditionalCost` | `blockBreakAdditionalPenalty` | **2** (ST:131) |
| `jumpPenalty` | setting | **2** (ST:136) |
| `walkOnWaterOnePenalty` | setting | **3** (ST:141) |
| `allowWalkOnMagmaBlocks` | setting | false (ST:1618) |
| `backtrackCostFavoringCoefficient` | setting | 0.5 (ST:472) |
| `minY` / `maxY` | dimension minY; `minY + height − 1` (BSI:91-93) | |

The `waterWalkSpeed` formula (CC:157-162):
```java
double waterSpeed = WALK_ONE_IN_WATER_COST * (1 - waterSpeedMultiplier) + WALK_ONE_BLOCK_COST * waterSpeedMultiplier;
if (allowSwimming && canSprint) waterSpeed = Math.min(waterSpeed, SWIM_ONE_BLOCK_COST);
```
- `waterSpeedMultiplier(player)` (CC:188-201) returns the amount of the first equipped enchantment with a `WATER_MOVEMENT_EFFICIENCY` attribute effect, at its level. Vanilla Depth Strider is about 0.3333 per level.
- **As written, it otherwise returns `1.0f`.** With no Depth Strider, waterWalkSpeed is therefore `WALK_ONE_BLOCK_COST` = 4.6328, and water costs the same as land. This contradicts the comment at CC:159 ("sprint swimming beats wading on the bottom"). It looks like a port bug: the older intent was mult = DS level / 3, giving 9.0909 with no Depth Strider.
- Per the formula: DS I gives 7.6049, DS II gives 6.1189, DS III gives 4.6328. With allowSwimming on and canSprint, the result is `min(…, 6.993)`.
- `allowSwimming` defaults to false (ST:846).

Other settings referenced by movements (defaults):

| Setting | Default | Line |
|---|---|---|
| `allowPlaceInFluidsSource` / `allowPlaceInFluidsFlow` | true / true | ST:83, 88 |
| `allowInventory` | false | ST:93 |
| `autoTool` / `assumeExternalAutoTool` | true / false | ST:116, 111 |
| `strictLiquidCheck` | false | ST:148 |
| `assumeWalkOnLava` | false | ST:167 |
| `assumeStep` | false | ST:172 |
| `assumeSafeWalk` | false | ST:181 |
| `acceptableThrowawayItems` | [DIRT, COBBLESTONE, NETHERRACK, STONE] | ST:230-235 |
| `blocksToAvoid` | [TRIPWIRE] | ST:240-242 |
| `blocksToDisallowBreaking` | [] | ST:247 |
| `blocksToAvoidBreaking` | [CRAFTING_TABLE, FURNACE, CHEST, TRAPPED_CHEST] | ST:254-259 |
| `avoidBreakingMultiplier` | 0.1 | ST:264 |
| `avoidUpdatingFallingBlocks` | true | ST:339 |
| `allowVines` | false | ST:347 |
| `allowWalkOnBottomSlab` | true | ST:353 |
| `considerPotionEffects` | true | ST:374 |
| `sprintAscends` | true | ST:379 |
| `overshootTraverse` | true | ST:410 |
| `pauseMiningForFallingBlocks` | true | ST:415 |
| `blockReachDistance` | 4.5 | ST:430 |
| `sprintInWater` | true | ST:833 |
| `preferSilkTouch` | false | ST:924 |
| `walkWhileBreaking` | true | ST:929 |
| `useSwordToMine` | true | ST:1443 |
| `remainWithExistingLookDirection` | true | ST:813 |
| `maxCostIncrease` | 10 | ST:540 |
| `costVerificationLookahead` | 5 | ST:547 |
| `movementTimeoutTicks` | 100 | ST:615 |

### `costOfPlacingAt(x, y, z, current)` (CC:259-276)
1. `!hasThrowaway` → COST_INF
2. `isPossiblyProtected` → COST_INF (always returns false, CC:292-295)
3. `!worldBorder.canPlaceAt(x, z)` → COST_INF
4. `!allowPlaceInFluidsSource && current.getFluidState().isSource()` → COST_INF
5. `!allowPlaceInFluidsFlow && fluid non-empty && !isSource` → COST_INF
6. Otherwise return `placeBlockCost` (20)

### Other context helpers
- `breakCostMultiplierAt(x, y, z, current)` (CC:278-286): `(!allowBreak && !allowBreakAnyway.contains(block))` → COST_INF; `isPossiblyProtected` → COST_INF; else 1.
- `placeBucketCost()` = `placeBlockCost` (20) (CC:288-290).

---

## 4. MovementHelper predicates (full logic)

**Precompute layer (PD).** The CalculationContext versions of `canWalkOn`, `canWalkThrough`, `fullyPassable`, `canPlaceAgainst` and `avoidBreaking` read per-BlockState flags from PD:
- `fillData`, PD:104-142, stores YES/MAYBE/NO per state.
- On MAYBE they call the `*Position` function with coordinates (PD:170-195).
- The answers are identical to the raw functions below.

### 4.1 `canWalkThrough(bsi, x, y, z, state)` (MH:146-155)
`canWalkThroughBlockState(state)` gives YES → true, NO → false, MAYBE → `canWalkThroughPosition(...)`.

**`canWalkThroughBlockState`** (MH:157-204). Rules are applied in this order:
1. `AirBlock` → YES
2. `BaseFireBlock`, `COBWEB`, `END_PORTAL`, `COCOA`, `AbstractSkullBlock`, `BUBBLE_COLUMN`, `ShulkerBoxBlock`, `SlabBlock`, `TrapDoorBlock`, `HONEY_BLOCK`, `END_ROD`, `SWEET_BERRY_BUSH`, `POINTED_DRIPSTONE`, `AmethystClusterBlock`, `AzaleaBlock` → NO
3. `BIG_DRIPLEAF` → NO
4. `POWDER_SNOW` → NO
5. in `blocksToAvoid` → NO
6. `DoorBlock` → `DoorBlock.isWoodenDoor(state) ? YES : NO`
7. `FenceGateBlock` → YES
8. `CarpetBlock` → MAYBE
9. `SnowLayerBlock` → MAYBE
10. fluid non-empty: `getAmount(fluidState) != 8` → NO; else MAYBE
11. `CauldronBlock` → NO
12. `state.isPathfindable(LAND)` ? YES : NO

**`canWalkThroughPosition`** (MH:206-246):
- Carpet → `canWalkOn(x, y−1, z)`.
- Snow layer:
  - `!bsi.worldContainsLoadedChunk(x, z)` → true
  - `LAYERS >= 3` → false
  - else `canWalkOn(x, y−1, z)`
- Fluid non-empty:
  - `isFlowing(...)` → false
  - `assumeWalkOnWater` → false
  - block above has a fluid or is a `WaterlilyBlock` → false
  - else `fluidState.getType() instanceof WaterFluid`

  Consequence: only the still top layer of water, with air above, counts as walk-through. Lava never does.
- Otherwise `state.isPathfindable(LAND)`.

### 4.2 `fullyPassable` (MH:248-309)
"canWalkThrough but also won't impede movement at all".

`fullyPassableBlockState` never returns MAYBE:
1. Air → YES
2. `BaseFireBlock`, `TRIPWIRE`, `COBWEB`, `VINE`, `LADDER`, `COCOA`, `AzaleaBlock`, `DoorBlock`, `FenceGateBlock`, `SnowLayerBlock`, any non-empty fluid, `TrapDoorBlock`, `EndPortalBlock`, `SkullBlock`, `ShulkerBoxBlock` → NO
3. Otherwise `isPathfindable(LAND)` ? YES : NO

`fullyPassablePosition` = `state.isPathfindable(LAND)`.

### 4.3 `canWalkOn(bsi, x, y, z, state)` (MH:389-398)
**`canWalkOnBlockState`** (MH:400-439). Rules are applied in this order:
1. `isBlockNormalCube(state) && (block != MAGMA_BLOCK || allowWalkOnMagmaBlocks) && block != BUBBLE_COLUMN && block != HONEY_BLOCK` → YES
2. `AzaleaBlock` → YES
3. `LADDER || (isClimbable(block) && allowVines)` → YES
4. `FARMLAND`, `DIRT_PATH`, `SOUL_SAND` → YES
5. `ENDER_CHEST`, `CHEST`, `TRAPPED_CHEST` → YES
6. `GLASS` or `StainedGlassBlock` → YES
7. `StairBlock` → YES
8. `isWater(state) && !isWaterloggedSolidTop(state)` → MAYBE
9. `isLava(state) && assumeWalkOnLava` → MAYBE
10. `SlabBlock`:
    - if `!allowWalkOnBottomSlab`: `TYPE != BOTTOM` → YES, else NO
    - otherwise YES
11. Otherwise NO. Magma when not allowed falls through to NO.

**`canWalkOnPosition`** (MH:441-465):
- If `isWater(state)`:
  - `up` = block above. If it is `LILY_PAD` or a `CarpetBlock` → true.
  - If `isFlowing(this)` or `up.fluid == FLOWING_WATER` → return `isWater(up) && !assumeWalkOnWater`.
  - Else → return `isWater(up) ^ assumeWalkOnWater`. With defaults, a water block is "walk-on" only when there is water above it.
- If `isLava(state) && !isFlowing && assumeWalkOnLava` → true.
- Else false.

### 4.4 `avoidWalkingInto(state)` (MH:363-373)
True if any of:
- `!state.getFluidState().isEmpty()`
- `(MAGMA_BLOCK && !allowWalkOnMagmaBlocks)`
- `CACTUS`, `SWEET_BERRY_BUSH`, `BaseFireBlock`, `END_PORTAL`, `COBWEB`, `BUBBLE_COLUMN`

### 4.5 `avoidBreaking` (MH:74-99)
Context version, MH:74-83. True if any of:
- `!worldBorder.canPlaceAt(x, z)`
- `neverBreak(state)`, which is `blocksToDisallowBreaking.contains(b) || b == ICE || b instanceof InfestedBlock` (PD:132-136; bsi version MH:90-92)
- `avoidAdjacentBreaking(x, y+1, z, directlyAbove=true)`
- `avoidAdjacentBreaking` on each of (x±1, y, z) and (x, y, z±1) with `directlyAbove=false`

The block below is never checked.

**`avoidAdjacentBreaking(bsi, x, y, z, directlyAbove)`** (MH:101-128):
1. If `!directlyAbove && block instanceof FallingBlock && avoidUpdatingFallingBlocks && FallingBlock.isFree(state at (x, y−1, z))` → true.
2. If `block instanceof LiquidBlock` (pure liquids only):
   - `directlyAbove || strictLiquidCheck` → true
   - `LEVEL == 0` (source) → true
   - else `!(block at (x, y−1, z) instanceof LiquidBlock)`
3. Else `!state.getFluidState().isEmpty()`. Waterlogged neighbours therefore block breaking.

### 4.6 Water, lava and fluid predicates
| Predicate | Logic | Line |
|---|---|---|
| `isWater(state)` | fluid type is `WATER` or `FLOWING_WATER`. Includes waterlogged blocks. | MH:738-741 |
| `isWaterloggedSolidTop(state)` | has `WATERLOGGED` property, it is true, and `isFaceSturdy(EmptyBlockGetter, ZERO, UP)` | MH:763-766 |
| `isLava(state)` | `LAVA` or `FLOWING_LAVA` | MH:768-771 |
| `isLiquid(state)` | fluid non-empty | MH:784-786 |
| `possiblyFlowing(state)` | fluid type is `FlowingFluid` and `getAmount != 8` | MH:788-792 |
| `isFlowing(x, y, z, state, bsi)` | not a FlowingFluid → false; `amount != 8` → true; else `possiblyFlowing` on any of the 4 horizontal neighbours | MH:794-806 |
| `isTransparent(Block)` | `AirBlock \|\| LAVA \|\| WATER` | MH:904-909 |

### 4.7 Shape and placement predicates
- **`isBottomSlab(state)`**: `SlabBlock && TYPE == BOTTOM` (MH:657-660).
- **`isClimbable(Block)`**: `LADDER, VINE, WEEPING_VINES, WEEPING_VINES_PLANT, TWISTING_VINES, TWISTING_VINES_PLANT` (MH:595-602).
- **`isBlockNormalCube(state)`** (MH:808-824):
  - false for `BambooStalkBlock`, `MovingPistonBlock`, `ScaffoldingBlock`, `ShulkerBoxBlock`, `PointedDripstoneBlock`, `AmethystClusterBlock`
  - else `Block.isShapeFullBlock(state.getCollisionShape(null, null))`
  - false on exception
- **`canPlaceAgainst`**: `worldBorder.canPlaceAt(x, z) && (isBlockNormalCube(state) || GLASS || StainedGlassBlock)` (MH:567-588; PD:128-130).
- **`isReplaceable(x, y, z, state, bsi)`** (MH:311-338):
  - Air → true
  - Snow layer: unloaded chunk → true; else `LAYERS == 1`
  - `LARGE_FERN` or `TALL_GRASS` → true
  - else `state.canBeReplaced()`

### 4.8 `mustBeSolidToWalkOn(context, x, y, z, state)` (MH:520-557)
"Will it have a top to walk on?"
1. `isClimbable` → false.
2. If the fluid is non-empty, any of these returns true:
   - `SlabBlock` with `TYPE != BOTTOM`
   - `StairBlock` with `HALF == TOP`, or shape `INNER_LEFT` / `INNER_RIGHT`
   - `TrapDoorBlock` that is `!OPEN && HALF == TOP`
   - `SCAFFOLDING`
   - `LeavesBlock`

   If none matched: `assumeWalkOnWater` → false; block above `instanceof LiquidBlock` → false.
3. Otherwise true.

### 4.9 Frost Walker, doors and gates
- **`canUseFrostWalker(context, state)`**: `frostWalker != 0 && state == FrostedIceBlock.meltsInto() && LEVEL == 0`, i.e. still source water (MH:491-495). The ctx overload scans equipment for Frost Walker (MH:497-515).
- **`isDoorPassable(state, side)`** (MH:340-348):
  - `openFacing = HINGE==LEFT ? FACING.clockwise : FACING.counterClockwise`
  - return `side != (OPEN ? openFacing : FACING)`
- **`isGatePassable(ctx, gatePos, playerPos)`**: `playerPos == gatePos` → false; not a FenceGate → true; else `OPEN` (MH:350-361).
- **`openDoors(ctx, state, from, to)`** (MH:826-842):
  - `direction` is the Direction with `from.relative(d) == to`. `.get()` throws if from and to are not adjacent.
  - For `pos` in `{from, to, from.above(), to.above()}`: `side = (pos is to or to.above) ? direction : direction.opposite`.
  - If `isWoodenDoor(door) && !isDoorPassable(door, side)`: set target = rotation to `calculateBlockCenter(pos)` (forced), press `CLICK_RIGHT`, return false.
  - Otherwise return true.

### 4.10 Movement and look helpers
**`moveTowards(ctx, state, pos)`** (MH:685-692):
- Target = rotation from eye to `getBlockPosCenter(pos)` (pos + 0.5 on each axis), but with `.withPitch(current pitch)`. Only the yaw changes.
- `forceRotations = false`.
- Press `MOVE_FORWARD`.

**`moveTowardsWithoutRotation(ctx, state, idealYaw)`** (MH:694-703, MovementOption.java):
- Evaluates 8 key options, using `s = sin(yaw)`, `c = cos(yaw)` and `k = 1.3` if `allowSprint` else 1:

  | Option | Motion vector |
  |---|---|
  | F | (s·k, c·k) |
  | B | (−s, −c) |
  | L | (−c, s) |
  | R | (c, −s) |
  | F+L | (s·k − c, c·k + s) |
  | F+R | (s·k + c, c·k − s) |
  | B+L | (−s − c, −c + s) |
  | B+R | (−s + c, −c − s) |

- Picks the option minimising `|mx − sin(ideal)| + |mz − cos(ideal)|` (L1) and presses its key(s).

**`moveTowardsWithSlightRotation(ctx, state, dest)`** (MH:714-729):
1. `idealYaw` = yaw to the dest centre.
2. `d = yawDistanceFromOffset(curYaw, idealYaw) % 45`.
3. `newYaw = d>0 ? (d>22.5 ? d−45 : d) : (d<−22.5 ? d+45 : d)`.
4. Target = `(curYaw − newYaw, curPitch)`, **forced**.
5. Then `moveTowardsWithoutRotation(idealYaw)`.

The net effect is to snap yaw to the nearest 45° multiple relative to the ideal direction and walk with combined keys.

**`switchToBestToolFor(ctx, state)`** (MH:668-683): if `autoTool && !assumeExternalAutoTool`, set `selected = toolSet.getBestSlot(block, preferSilkTouch)`.

**`steppingOnBlocks(ctx)`** (MH:911-921): the floor blocks (feetY − 1) of every column in the 3×3 around the player whose 1×1×1 cell at feet level intersects the player's AABB.

**Rotation tolerances.** `isReallyCloseTo`: yaw diff < 0.01 (or > 359.99) and pitch diff < 0.01 (Rotation.java:126-133).

---

## 5. Breaking cost: `getMiningDurationTicks(context, x, y, z, state, includeFalling)`

MH:604-624 is a memo keyed on (x, y, z, includeFalling) for the search thread. The semantics are in **`getMiningDurationTicks0`** (MH:626-655):

```text
if canWalkThrough(context, x,y,z,state): return 0          // nothing to mine; falling blocks above NOT checked
if fluid non-empty:                        return COST_INF
mult = breakCostMultiplierAt(x,y,z,state)  // 1, or COST_INF if !allowBreak && not in allowBreakAnyway
if mult >= COST_INF:                       return COST_INF
if avoidBreaking(context,x,y,z,state):     return COST_INF
str = toolSet.getStrVsBlock(state)
if str <= 0:                               return COST_INF   // unbreakable (hardness < 0) or no speed
result = 1/str + breakBlockAdditionalCost  // + 2 by default
result *= mult
if includeFalling and block at (x,y+1,z) is FallingBlock:
    result += getMiningDurationTicks(context, x, y+1, z, above, true)   // recursive up the stack
return result
```

**`ToolSet.getStrVsBlock(state)`** (TS:94-96, 188-195, 205-237, 244-266):
- Cached per **Block**, evaluated on the block's default state.
- `str = calculateSpeedVsBlock(bestHotbarStack, defaultState) * avoidanceMultiplier(block)`, multiplied by `potionAmplifier()` when `considerPotionEffects`.
- `avoidanceMultiplier` = `avoidBreakingMultiplier` (0.1) if the block is in `blocksToAvoidBreaking`, else 1. The 0.1 makes the time 10× longer.
- `calculateSpeedVsBlock(item, state)`:
  1. `hardness = getDestroySpeed()`. A null pointer or `hardness < 0` returns −1, which becomes COST_INF.
  2. `speed = item.getDestroySpeed(state)`.
  3. If `speed > 1`, add the first enchantment `MINING_EFFICIENCY` attribute amount at its level. Vanilla Efficiency is level²+1.
  4. `speed /= hardness`.
  5. Return `speed / 30` if `!requiresCorrectToolForDrops || (item non-empty && isCorrectToolForDrops)`, else `speed / 100`.

  So ticks = `1/str = hardness·30/speed` (or ·100 with the wrong tool).
- `potionAmplifier`:
  - Haste: × `(1 + (amp+1)·0.2)`.
  - Mining Fatigue by amplifier: 0 → ×0.3, 1 → ×0.09, 2 → ×0.0027, ≥3 → ×0.00081.
- `getBestSlot(block, silk, pathingCalculation=true)` (TS:137-180):
  - If `!autoTool`, return the currently selected slot.
  - Otherwise scan hotbar slots 0-8:
    - Skip swords if `!useSwordToMine`.
    - Skip items within `itemSaverThreshold` of breaking when `itemSaver` is on.
    - Take the max speed. Ties go to the lower material tier (wood < stone < iron < gold < diamond < netherite), with silk-touch preference rules.
- Not modelled: the in-air or underwater mining slowdown.

Worked examples, with vanilla hardness and tool speeds assumed and `+2` included:

| Block and tool | Calculation | Cost |
|---|---|---|
| Dirt (0.5), by hand | 0.5·30/1 + 2 | **17** |
| Stone (1.5, tool required), by hand | 1.5·100 + 2 | **152** |
| Stone, wooden pickaxe (2) | 22.5 + 2 | **24.5** |
| Stone, diamond pickaxe (8) | 5.625 + 2 | **7.625** |

**Execution-time breaking** happens in `Movement.prepared()`, see section 7.

---

## 6. Placing

### 6.1 Cost-time checks
- `costOfPlacingAt` (section 3) gives 20 or COST_INF.
- `isReplaceable(target)` must hold.
- At least one neighbour of the target among `{N, S, E, W, DOWN}` must satisfy `canPlaceAgainst`. Each movement excludes the "backplace" face differently; see each movement.

### 6.2 Throwaway items (IB:163-239)
- `hasGenericThrowaway()`: any `acceptableThrowawayItems` item found via `throwaway(false, …)`.
- `selectThrowawayForLocation(select, x, y, z)`:
  1. Try the BuilderProcess's desired state for that position, if it has one.
  2. Then each `acceptableThrowawayItems` entry.
- `throwaway(select, pred)`:
  1. Search hotbar slots 0-8 and select the slot if found.
  2. Else, if the offhand matches, select an empty hotbar slot or a pickaxe (so the main hand doesn't right-click).
  3. Else, if `allowInventory`, search slots 9-35 and swap the item to hotbar slot 7.

### 6.3 `attemptToPlaceABlock(state, baritone, placeAt, preferDown, wouldSneak)` (MH:844-898)
1. `direct = RotationUtils.reachable(ctx, placeAt, wouldSneak)`. If present, target = it (forced) and `found = true`.
2. For each `against1 = placeAt + {N, S, E, W, DOWN}[i]` with `canPlaceAgainst(ctx, against1)`:
   - If `!selectThrowawayForLocation(false, placeAt)`: log "bb pls get me some blocks. dirt, netherrack, cobble", **set status UNREACHABLE**, return `NO_OPTION`.
   - `face = ((placeAt.x + against.x + 1)·0.5, (placeAt.y + against.y + 0.5)·0.5, (placeAt.z + against.z + 1)·0.5)`. This is the shared face, at y + 0.25 for sides and y − 0.25 for down.
   - `place` = rotation from the eye (the sneaking eye if `wouldSneak`) to `face`. `actual = aimProcessor.peekRotation(place)`.
   - Raytrace with block reach. If it hits BLOCK `== against1` and `hitPos.relative(hitFace) == placeAt`: target = `place` (forced), `found = true`.
   - `if (!preferDown) break;`. With preferDown the loop continues, so the last valid option (DOWN) wins.
3. If the crosshair block exists and either `selected == placeAt`, or (`canPlaceAgainst(selected)` and `selected.relative(side) == placeAt`):
   - Press SNEAK if `wouldSneak`.
   - `selectThrowawayForLocation(true, …)`.
   - Return **READY_TO_PLACE**.
4. If `found`: press SNEAK if `wouldSneak`, select the throwaway, return **ATTEMPTING**.
5. Otherwise return **NO_OPTION**.

### 6.4 `RotationUtils.reachable(ctx, pos, reach, wouldSneak)` (RotationUtils.java:177-225)
1. If `remainWithExistingLookDirection` and already looking at `pos`: return the current rotation + (0, 0.0001). With sneaking this is re-verified by raytrace.
2. Otherwise try the block centre (the collision-shape centre).
3. Then the 6 face centres of the shape, in the order Down, Up, North, South, West, East.
4. Each candidate is checked by raytrace from the eye (sneak eye if `wouldSneak`) through the aim processor. It is accepted if the hit block is `pos`, or `pos.below()` when `pos` is fire.

---

## 7. Movement base class, MovementState, and the per-tick pipeline

### 7.1 MovementState (MS:28-92)
- Holds `status`.
- Holds `target`: a `MovementTarget(Rotation rotation /*nullable*/, boolean forceRotations)`. Force is "true if we're trying to place or break blocks, false if … look at the movement location".
- Holds `inputState`, a `Map<Input, Boolean>`. `setInput(input, forced)` puts the value, so a later `setInput(X, false)` in the same tick overrides an earlier true.

### 7.2 Status machine: `Movement.updateState` (M:425-437)
```text
if !prepared(state): status = PREPPING; return
else if status == PREPPING: status = WAITING
if status == WAITING: status = RUNNING
```
RUNNING is therefore reached on the first tick where `prepared()` returns true. Statuses with `isComplete()`: SUCCESS, UNREACHABLE, FAILED, CANCELED.

### 7.3 `Movement.prepared(state)` (M:353-393): default breaking
1. If `status == WAITING`, return true.
2. For each `blockPos` in `positionsToBreak`, in order:
   - If a `FallingBlockEntity` intersects `AABB(0, 0, 0, 1, 1.1, 1)` moved to `blockPos` **and** `pauseMiningForFallingBlocks`: return false (wait).
   - If `!canWalkThrough(ctx, blockPos)`:
     - `switchToBestToolFor`.
     - `reachable(blockPos, reach)`. If present: target = it (forced); press `CLICK_LEFT` if `isLookingAt(blockPos) || rotations.isReallyCloseTo(rot)`; return false.
     - Otherwise, fallback: target = rotation to the block centre (forced), press `CLICK_LEFT`, return false.
3. Return true.

The `somethingInTheWay → UNREACHABLE` branch at M:386-391 is dead code, because every path that sets it has already returned false.

### 7.4 `Movement.update()` (M:125-179): wrapper run every tick
1. `abilities.flying = false`.
2. `currentState = updateState(currentState)`.
3. If the player is in liquid (`isLiquid(feet) || isInWater()`):
   - If `shouldSwim()` (M:229-256):
     - Condition: `allowSwimming && isInWater && allowSprint && food > 6 && status != PREPPING`. If not on ground → true. If on ground → swim only while air ≥ 100 under water, or keep swimming / full air.
     - Effect: press SPRINT and `setSprinting(true)`.
     - If RUNNING, swimming and not on ground: `holdWaterline()` sets the pitch to hold the feet 0.15 below the surface (or `dest.y + 0.3` if higher) and presses JUMP. `preTurnIntoTheBend()` turns early toward path position +2 at corners.
     - Else if not swimming, on ground and `py < dest.y + 0.6`: press JUMP.
   - Else:
     - If `allowSwimming && isSwimming`: release SPRINT.
     - If `py < dest.y + 0.6`: press **JUMP**. This is the default "bob" in water.
4. If `player.isInWall()`: switch to the best tool for the crosshair block and press `CLICK_LEFT`.
5. Push the target rotation to LookBehavior (`updateTarget(rotation, force)`). `clearAllKeys`, apply all inputs, clear the map. If the status is complete, clear keys again.

### 7.5 Other base behaviour
- **`toBreak()` / `toPlace()`** (M:460-484): positions in `positionsToBreak` that are not walk-through, and `positionToPlace` if it is not canWalkOn. Used by PathExecutor bookkeeping.
- **`getDirection()`** = `dest − src` (M:440-442).
- **`playerInValidPosition()`** = valid positions contain `playerFeet` or `pathStart` (M:114-116).

---

## 8. Move enumeration and how A* uses it (MV, AS)

There are 22 moves per node (MV:30-325), as `(xOff, yOff, zOff, dynamicXZ, dynamicY)`:

| Moves | Offset | Flags | Builds |
|---|---|---|---|
| `DOWNWARD` | (0, −1, 0) | static | MovementDownward |
| `PILLAR` | (0, +1, 0) | static | MovementPillar |
| `TRAVERSE_{N,S,E,W}` | (dx, 0, dz) | static | MovementTraverse |
| `ASCEND_{N,S,E,W}` | (dx, +1, dz) | static | MovementAscend |
| `DESCEND_{E,W,N,S}` | (dx, −1, dz) | dynamicY | MovementDescend if `res.y == src.y−1`, else MovementFall (MV:151-221) |
| `DIAGONAL_{NE,NW,SE,SW}` | (±1, 0, ±1) | dynamicY | `MovementDiagonal(src, dir1 ∈ {NORTH, SOUTH}, dir2 ∈ {EAST, WEST}, res.y − src.y)` |
| `PARKOUR_{N,S,E,W}` | nominal ±4 | dynamicXZ + dynamicY | `MovementParkour.cost(context, src, dir)` |

A* rules (AS:108-165):
- Skip a move if the nominal destination is in a different, unloaded chunk.
- Skip a non-dynamicXZ move whose nominal x/z is outside the world border.
- Skip if `node.y + yOffset` is outside `[minY, maxY]`.
- Skip if the result cost `>= COST_INF`.
- For dynamicXZ moves, check the world border on the result.
- Static moves must land exactly on the nominal offsets, otherwise A* throws.
- Favoring: `actionCost *= neighbor.favor`.

---

## 9. The eight movements

### 9.1 MovementTraverse (flat, 1 block cardinal)

**Geometry.** `dest = (x+dx, y, z+dz)`.
- `positionsToBreak = {dest.above() /*pb0 = dest head*/, dest /*pb1 = dest feet*/}`.
- `positionToPlace = dest.below()` (dest floor) (Trav:56-58).
- Valid positions `{src, dest}`.

**Blocks read in `cost(context, x, y, z, destX, destZ)`** (Trav:76-172):
- `pb0` = dest head (destX, y+1, destZ)
- `pb1` = dest feet (destX, y, destZ)
- `destOn` = dest floor (destX, y−1, destZ)
- `srcDown` = src floor (x, y−1, z)
- `standingOnABlock = mustBeSolidToWalkOn(srcDown)`
- `frostWalker = standingOnABlock && !assumeWalkOnWater && canUseFrostWalker(destOn)`

**A. Walk branch.** Taken if `frostWalker || canWalkOn(destOn)` (Trav:84-124).
1. Base walk cost: `WC = WALK_ONE_BLOCK_COST`.
2. If `isWater(pb0) || isWater(pb1)`: `WC = waterWalkSpeed`, `water = true`, and no soul-sand or magma terms are added.
3. Otherwise:
   - dest floor: `SOUL_SAND` → `WC += (WALK_ONE_OVER_SOUL_SAND_COST − WALK)/2` (+2.3164); else if frostWalker, add nothing; else if `destOn.getBlock() == Blocks.WATER` → `WC += walkOnWaterOnePenalty` (+3).
   - src floor: `SOUL_SAND` → `WC += (SS − WALK)/2`; else if `allowWalkOnMagmaBlocks && srcDown == MAGMA_BLOCK` → `sneaking = true`, `WC += (SNEAK − WALK)/2` (+5.3759). A magma dest floor is not penalised.
4. `hardness1 = getMiningDurationTicks(pb1 /*dest feet*/, includeFalling=false)`. If it is `>= COST_INF` → **COST_INF**.
5. `hardness2 = getMiningDurationTicks(pb0 /*dest head*/, includeFalling=true)`.
6. If `hardness1 == 0 && hardness2 == 0`: if `!water && !sneaking && canSprint`, then `WC *= SPRINT_MULTIPLIER`. Return `WC`.
7. If `isClimbable(srcDown)`: `hardness1 *= 5; hardness2 *= 5`.
8. Return `WC + hardness1 + hardness2`. There is no sprint discount when breaking, and an INF hardness2 still yields ≥ INF.

**B. Bridge branch** (place the dest floor) (Trav:125-171):
1. `isClimbable(srcDown)` → **COST_INF**.
2. `!isReplaceable(destOn)` → **COST_INF** (Trav:170).
3. `throughWater = isWater(pb0) || isWater(pb1)`. If `isWater(destOn) && throughWater` → **COST_INF**.
4. `placeCost = costOfPlacingAt(destOn)`. If INF → **COST_INF**.
5. `hardness1` (dest feet, no falling). If INF → **COST_INF**. `hardness2` (dest head, includeFalling).
6. `WC = throughWater ? waterWalkSpeed : WALK_ONE_BLOCK_COST`.
7. **Side-place scan.** For each of {N, S, E, W, DOWN} around the dest floor, skip `against` when `againstX == x && againstZ == z` (that is the src floor, a backplace). If `canPlaceAgainst(against)` → return `WC + placeCost + hardness1 + hardness2`.
8. **Backplace**, used when no side face is available:
   - `srcDown == SOUL_SAND || (srcDown is SlabBlock && TYPE != DOUBLE)` → **COST_INF**
   - `!standingOnABlock` → **COST_INF**
   - src feet block is `LILY_PAD` or a `CarpetBlock` and `srcDown` has a fluid → **COST_INF**
   - `WC = WC * (SNEAK_ONE_BLOCK_COST / WALK_ONE_BLOCK_COST)` → return `WC + placeCost + hardness1 + hardness2`
   - Backplace does **not** call `canPlaceAgainst` on the src floor; it relies on `mustBeSolidToWalkOn`.

**Default-setting costs:**
| Case | Cost |
|---|---|
| Plain sprint | 3.5638 |
| No sprint | 4.6328 |
| Soul sand on one side (sprint) | 6.9493·0.7692 = 5.3456 |
| Water, as written (waterWalkSpeed = WALK) | 4.6328 |
| Side-place bridge | 24.6328 |
| Sneak backplace | 15.3846 + 20 = 35.3846 |
| Breaking | WC (no sprint) + Σhardness |

**`updateState`** (Trav:175-350). Run `super.updateState()` first, then read pb0/pb1 from the world.

*When not RUNNING (walk-while-breaking)* (Trav:179-217). Return the state unchanged unless all of these hold:
- `walkWhileBreaking`
- status == PREPPING
- `!avoidWalkingInto(pb0)` and `!avoidWalkingInto(pb1)`
- `dist = max(|px−(dest.x+.5)|, |pz−(dest.z+.5)|) >= 0.83`
- a target rotation exists

If they all hold:
- `yaw` = yaw to `calculateBlockCenter(dest)`.
- `pitch` = the current break-target pitch, but `pitch = 26` if `isBlockNormalCube(pb0) || (pb0 is Air && (isBlockNormalCube(pb1) || pb1 is Air))`. That is Java precedence `A || (B && C)`.
- Set target `(yaw, pitch)` forced, press `MOVE_FORWARD` and `SPRINT`.

*RUNNING:*
1. `ladder = isClimbable(src floor)`.
2. `SNEAK = allowWalkOnMagmaBlocks && any steppingOnBlocks is MAGMA`.
3. `openDoors(src, dest)` false → return.
4. Fence gates: if pb0 or pb1 is a `FenceGateBlock`:
   - `blocked = !isGatePassable(pb0pos, src.above()) ? pb0pos : !isGatePassable(pb1pos, src) ? pb1pos : null`.
   - If blocked and reachable: target forced, press `CLICK_RIGHT`, return.
5. `isTheBridgeBlockThere = canWalkOn(dest floor) || ladder || canUseFrostWalker(ctx, dest floor)`.
6. If `feet.y != dest.y && !ladder`: log "Wrong Y coordinate". If `feet.y < dest.y`, press **JUMP**. Return without moving.
7. **If the bridge block is there:**
   - `feet == dest` → **SUCCESS**.
   - **Overshoot:** `overshootTraverse && (feet == dest+dir || feet == dest+2·dir)` → **SUCCESS**.
   - If `py > src.y + 0.1 && !onGround && !isInWater && (isClimbable(src feet) || isClimbable(src head))` → return and wait. Pressing W could climb a ladder instead.
   - `into = dest + (dest − src)`. Press **SPRINT** if all hold: `wasTheBridgeBlockAlwaysThere`, `(!isLiquid(feet) || sprintInWater)`, `(!avoidWalkingInto(into) || isWater(into))`, `!avoidWalkingInto(into.above())`.
   - If `feet.y != dest.y && ladder && isClimbable(dest floor)` → press JUMP.
   - `moveTowards(dest head)`.
8. **Else (placing):**
   - `wasTheBridgeBlockAlwaysThere = false`.
   - If the block under the feet is `SOUL_SAND` or a `SlabBlock` and `max(|dest.x+.5−px|, |dest.z+.5−pz|) < 0.85`: `moveTowards(dest)`, then `MOVE_FORWARD = false`, `MOVE_BACK = true`, return.
   - `dist1 = max(|px−(dest.x+.5)|, |pz−(dest.z+.5)|)`.
   - `p = attemptToPlaceABlock(dest.below(), preferDown=false, wouldSneak=!assumeSafeWalk)`.
   - If `(p == READY_TO_PLACE || dist1 < 0.6) && !assumeSafeWalk` → press SNEAK.
   - `READY_TO_PLACE`: press `CLICK_RIGHT` if `isCrouching() || assumeSafeWalk`; return.
   - `ATTEMPTING`: if `dist1 > 0.83` and `|targetYaw − yawToDestCenter| < 0.1` → press `MOVE_FORWARD`. Else if `rotations.isReallyCloseTo(target)` → press `CLICK_LEFT` ("something in the way"). Return.
   - `NO_OPTION` falls through. attemptToPlaceABlock may already have set UNREACHABLE when there is no throwaway.
   - **Backplace, when `feet == dest`:**
     - `face = ((dest.x+src.x+1)·.5, (dest.y+src.y−1)·.5, (dest.z+src.z+1)·.5)`; `goalLook = src.below()`.
     - If `max(|px−faceX|, |pz−faceZ|) < 0.29`: target = (yaw from dest centre to the eye, i.e. facing away; pitch toward the face), forced, and press `MOVE_BACK`.
     - Else target = rotation to `face`, forced.
     - If `isLookingAt(goalLook)` → press `CLICK_RIGHT`. Else if really close to the target → press `CLICK_LEFT`. Return.
   - Otherwise `moveTowardsWithSlightRotation(dest)`.

**Other overrides:**
- `safeToCancel` = `status != RUNNING || canWalkOn(dest.below())` (Trav:353-358).
- `prepared`: if feet is src or src.below and `isClimbable(src floor)`, press SNEAK; then `super.prepared` (Trav:361-369).

---

### 9.2 MovementAscend (up 1, cardinal)

**Geometry.** `dest = (x+dx, y+1, z+dz)`.
- `positionsToBreak = {dest /*dest feet, y+1*/, src.above(2) /*src head+1*/, dest.above() /*dest head, y+2*/}`.
- `positionToPlace = dest.below()`, the step block at (x+dx, y, z+dz) (Asc:41-43).
- Valid positions: `{src, src.above(), dest, prior, prior.above()}` with `prior = (x−dx, y, z−dz)` (Asc:57-65).

**`cost(context, x, y, z, destX, destZ)`** (Asc:67-157):
1. `toPlace` = the step block (destX, y, destZ). If `!canWalkOn(toPlace)`, it must be placed:
   - `additionalPlacementCost = costOfPlacingAt(toPlace)`; INF → **COST_INF**.
   - `!isReplaceable(toPlace)` → **COST_INF**.
   - Scan {N, S, E, W, DOWN} around (destX, y, destZ), skipping `againstX == x && againstZ == z` (the src feet column: "it will have been broken by the time we'd need to use it"). If no `canPlaceAgainst` → **COST_INF**.
2. **Falling-block check.** If `(x, y+3, z)` is a `FallingBlock` **and** `(canWalkThrough(x, y+1, z) || !(srcUp2 /*(x,y+2,z)*/ instanceof FallingBlock))` → **COST_INF** (Asc:96-113).
3. `srcDown` (src floor) climbable → **COST_INF**.
4. `jumpingFromBottomSlab = isBottomSlab(srcDown)`; `jumpingToBottomSlab = isBottomSlab(toPlace)`. From a bottom slab to a non-bottom-slab → **COST_INF**.
5. `walk`:
   - To a bottom slab, from a bottom slab: `max(JUMP, WALK) + jumpPenalty`.
   - To a bottom slab, not from one: `WALK_ONE_BLOCK_COST` with **no** jump penalty.
   - Otherwise, `toPlace` is SOUL_SAND → `WALK_ONE_OVER_SOUL_SAND_COST`; MAGMA_BLOCK → `SNEAK_ONE_BLOCK_COST`; else `max(JUMP_ONE_BLOCK_COST, WALK_ONE_BLOCK_COST)`. Then `+= jumpPenalty`.
6. `total = walk + additionalPlacementCost`.
7. `+= getMiningDurationTicks(src head+1, srcUp2, false)`; INF → INF.
8. `+= getMiningDurationTicks(dest feet (destX, y+1), false)`; INF → INF.
9. `+= getMiningDurationTicks(dest head (destX, y+2), includeFalling=true)`. Return the total.

**Default costs:**
| Case | Cost |
|---|---|
| Plain | 4.6328 + 2 = **6.6328** |
| Onto soul sand | 11.2657 |
| Onto magma | 17.3846 |
| Slab to slab | 6.6328 |
| Non-slab to bottom slab | 4.6328 |
| With placement | +20 |

There is no sprint multiplier in the cost.

**`updateState`** (Asc:160-231):
1. **Before `super`:** `feet.y < src.y` → **UNREACHABLE**. This applies even while prepping.
2. `super`; if not RUNNING, return.
3. `feet == dest || feet == dest + (dx, 0, dz)` → **SUCCESS** (overshoot by one).
4. `openDoors(src.above(), dest)` false → return.
5. `jumpingOnto` = the world state at the step block. If `!canWalkOn`:
   - `ticksWithoutPlacement++`.
   - If `attemptToPlaceABlock(dest.below(), false, wouldSneak=true) == READY_TO_PLACE`: press SNEAK; if `isCrouching()`, press `CLICK_RIGHT`.
   - If `ticksWithoutPlacement > 10` → press `MOVE_BACK` ("we might be standing in the way").
   - Return.
6. `moveTowards(dest)`. `SNEAK = allowWalkOnMagmaBlocks && jumpingOnto is MAGMA`.
7. If the step is a bottom slab and the src floor is not → return without jumping.
8. If `assumeStep || feet == src.above()` → return (already jumping).
9. Compute, with `xAxis = |dx|`, `zAxis = |dz|`:
   - `flatDistToNext = xAxis·|dest.x+.5−px| + zAxis·|dest.z+.5−pz|`
   - `sideDist = zAxis·|dest.x+.5−px| + xAxis·|dest.z+.5−pz|`
   - `lateralMotion = xAxis·vz + zAxis·vx`
10. If `|lateralMotion| > 0.1` → return without jumping.
11. If `headBonkClear()` → press **JUMP**. `headBonkClear` is true when all 4 horizontal neighbours of `src.above(2)` are canWalkThrough (Asc:233-243).
12. Else if `flatDistToNext > 1.2 || sideDist > 0.2` → return. Otherwise press **JUMP**.

**Other overrides.** `safeToCancel` = `status != RUNNING || ticksWithoutPlacement == 0` (Asc:246-249).

---

### 9.3 MovementDescend (down 1, cardinal, walk off an edge)

**Geometry.** `dest = (x+dx, y−1, z+dz)`.
- `positionsToBreak = {dest.above(2) /*(dx, y+1)*/, dest.above() /*(dx, y)*/, dest /*(dx, y−1)*/}`.
- `positionToPlace = dest.below()` (Desc:47-49).
- Valid positions `{src, dest.above(), dest}`.
- `calculateCost` runs `cost()` and returns COST_INF unless `res.y == dest.y` (Desc:66-73).

**`cost(context, x, y, z, destX, destZ, res)`** (Desc:80-135). `res` is preset to COST_INF; every bare `return` below means impossible.
1. `total += mine(destX, y−1, destZ /*dest feet*/, false)`; INF → return.
2. `total += mine(destX, y, destZ /*dest head = column in front at src-feet level*/, false)`; INF → return.
3. `total += mine(destX, y+1, destZ /*front column at src-head level*/, includeFalling=true)`; INF → return.
4. `fromDown` = src floor; climbable → return.
5. `below` = dest floor (destX, y−2, destZ). If `!canWalkOn(below)` → `dynamicFallCost(context, x, y, z, destX, destZ, frontBreak=total, below, res)` and return. This is the **Fall** path.
6. The dest feet block (destX, y−1) is `LADDER` or `VINE` → return.
7. `canUseFrostWalker(dest feet block)` → return ("the water will freeze when we try to walk into it").
8. `walk = WALK_OFF_BLOCK_COST`. If the src floor is SOUL_SAND: `walk *= WALK_ONE_OVER_SOUL_SAND_COST / WALK_ONE_BLOCK_COST` (×2).
9. `total += walk + max(FALL_N_BLOCKS_COST[1], CENTER_AFTER_FALL_COST)`. Set `res = (destX, y−1, destZ, total)`.

**Default costs:** 3.7063 + 5.6147 = **9.3210**. From soul sand: **13.0273**. Plus breaking.

**`updateState`** (Desc:227-275):
1. `super`; if not RUNNING, return.
2. `fakeDest = (2·dest.x − src.x, dest.y, 2·dest.z − src.z)` (one block past dest).
3. If `(feet == dest || feet == fakeDest) && (isLiquid(dest) || py − dest.y < 0.5)` → **SUCCESS**.
4. `openDoors(src, dest.above())` false → return.
5. **`safeMode()`:** target the point `((src.x+.5)·0.17 + (dest.x+.5)·0.83, dest.y, (src.z+.5)·0.17 + (dest.z+.5)·0.83)` with yaw only (current pitch), not forced. Press `MOVE_FORWARD` and return. This stops short of the far edge.
6. `ab` = horizontal Euclidean distance to the dest centre; `fromStart` = horizontal distance to the src centre.
7. `SNEAK = allowWalkOnMagmaBlocks && block under player.blockPosition() is MAGMA`.
8. If `feet != dest || ab > 0.25`:
   - If `numTicks++ < 20 && fromStart < 1.25` → `moveTowards(fakeDest)` (aim past dest to get off the edge).
   - Else `moveTowards(dest)`.
   - `numTicks` increments every evaluation.

**`safeMode()`** (Desc:277-294):
- `forceSafeMode` → true. PathExecutor sets it when the next movement is a Traverse or Parkour in the same flat direction onto frost-walker water (PE:396-414).
- `into = dest + (dx, 0, dz)`.
- `skipToAscend()` → true, where `skipToAscend = !canWalkThrough(into) && canWalkThrough(into.above()) && canWalkThrough(into.above(2))`.
- Any of `into`, `into+1`, `into+2` is `avoidWalkingInto` → true.
- Otherwise false.

---

### 9.4 MovementFall (via Descend's `dynamicFallCost`; down ≥ 2)

**Geometry.** `dest = (x+dx, y−k, z+dz)`.
- `positionsToBreak = (dest.x, src.y+1−i, dest.z)` for `i = 0..|Δy|+1`, i.e. the dest column from src-head level down to dest feet (Fall:188-198).
- No `positionToPlace`.
- Valid positions: src plus `dest.above(j)` for j = 0..(src.y − dest.y).
- `calculateCost` reruns `MovementDescend.cost` and requires `res.y == dest.y` (Fall:60-67).

**`dynamicFallCost(context, x, y, z, destX, destZ, frontBreak, below, res)`** (Desc:137-224). It returns true only when a bucket is needed.
1. If `frontBreak != 0 && block (destX, y+2, destZ) is FallingBlock` → false.
2. If `!canWalkThrough(below = (destX, y−2, destZ))` → false.
3. Set `costSoFar = 0`, `effectiveStartHeight = y`.
4. Loop **`fallHeight = 3, 4, 5, …` unbounded**, with `newY = y − fallHeight`:
   - If `newY < context.minY` → false (void).
   - `reachedMinimum = fallHeight >= minFallHeight` (3, so always true by default; 8 under Elytra).
   - `onto = (destX, newY, destZ)`.
   - `unprotected = fallHeight − (y − effectiveStartHeight)`.
   - `tentativeCost = WALK_OFF_BLOCK_COST + FALL_N_BLOCKS_COST[unprotected] + frontBreak + costSoFar`.
   - **Water landing.** If `reachedMinimum && isWater(onto) && !isWaterloggedSolidTop(onto)`:
     - `!canWalkThrough(onto)` → false. This requires still top-layer water with no water or lily pad above.
     - `assumeWalkOnWater` → false.
     - `isFlowing(onto)` → false.
     - `!canWalkOn(destX, newY−1, destZ)` → false ("punch right through the water").
     - Otherwise `res = (destX, newY /*feet in the water*/, destZ, tentativeCost)` and return false.
     - **There is no height limit on water landings.**
   - **Lava landing.** If `reachedMinimum && allowFallIntoLava && isLava(onto)` → `res = (destX, newY, destZ, tentativeCost)`, return false. This is used by Elytra only.
   - **Ladder or vine catch.** If `unprotected <= 11 && isClimbable(onto)`:
     - `costSoFar += FALL_N_BLOCKS_COST[unprotected − 1] + LADDER_DOWN_ONE_COST`.
     - `effectiveStartHeight = newY`, then continue. This resets the fall speed.
   - `canWalkThrough(onto)` → continue scanning down.
   - `!canWalkOn(onto)` → false.
   - `isBottomSlab(onto)` → false ("really glitchy").
   - If `reachedMinimum && unprotected <= maxFallHeightNoWater + 1` → `res = (destX, newY+1, destZ, tentativeCost)`, return false.
   - Else if `reachedMinimum && hasWaterBucket && unprotected <= maxFallHeightBucket + 1` → `res = (destX, newY+1, destZ, tentativeCost + placeBucketCost() /*20*/)`, **return true**.
   - Else → false.

**Height semantics.**
- Landing on a solid block: the feet drop is `d = fallHeight − 1`, and the cost uses `FALL_N_BLOCKS_COST[d+1]`.
  - Default no-water limit: `unprotected <= 4`, i.e. **drop ≤ 3**.
  - Bucket limit: `unprotected <= 21`, i.e. **drop ≤ 20**.
- Landing in water: the drop is `d = fallHeight`, and the cost uses `FALL_N_BLOCKS_COST[d]`.
- The minimum Fall drop is 2. A drop of 1 is a Descend.

**Default costs, landing on solid ground, with no breaking:**
| Drop | Cost |
|---|---|
| 2 | 3.7063 + 9.4687 = **13.1750** |
| 3 | 3.7063 + 10.9117 = **14.6179** |
| 20, with bucket | 3.7063 + 25.4638 + 20 = **49.1701** |

**`updateState`** (Fall:86-169):
1. `super`; if not RUNNING, return.
2. `toDest` = rotation to the dest centre. `isWater` = the dest block's fluid is a `WaterFluid`.
3. If `dest.below()` is MAGMA and every `steppingOnBlocks` is canWalkThrough (i.e. airborne) → press SNEAK.
4. **Bucket clutch.** If `!isWater && willPlaceBucket() && feet != dest`:
   - `willPlaceBucket()` reruns `dynamicFallCost` with a fresh context and `frontBreak = 0` (Fall:79-83).
   - If there is no water bucket in the hotbar, or the dimension is the NETHER → **UNREACHABLE**.
   - If `py − dest.y < blockReachDistance && !onGround`: select the water bucket; `targetRotation = (toDest.yaw, 90)`; if `isLookingAt(dest) || isLookingAt(dest.below())` → press `CLICK_RIGHT`.
5. `openDoors(src, (dest.x, src.y, dest.z))` false → return.
6. Target = `targetRotation` (forced) if set, else `toDest` (not forced).
7. **Landing.** If `feet == dest && (py − feet.y < 0.094 || isWater)`:
   - If `isWater`:
     - With an empty bucket in the hotbar: select it. If `vy >= 0` → press `CLICK_RIGHT` (pick the water up) and return; else return.
     - Without one: if `vy >= 0` → **SUCCESS**. Otherwise keep centring.
   - Not water → **SUCCESS**.
8. **Centring.** If `|px + vx − cx| > 0.1 || |pz + vz − cz| > 0.1`: if `!onGround && |vy| > 0.4`, press SNEAK; press `MOVE_FORWARD`.
9. **Ladder avoidance.** `avoid()` scans `feet.below(i)` for i = 0..14 for a LADDER and returns its FACING.
   - If none: `avoid = src − dest`.
   - Else: `dist = |ax·(cx − ax/2 − px)| + |az·(cz − az/2 − pz)|`. If `dist < 0.6` → press `MOVE_FORWARD`; else if `!onGround`, set `SNEAK = false`.
10. If there is no bucket rotation: target the point `(cx + 0.125·avoid.x, cy, cz + 0.125·avoid.z)`, not forced.

**Other overrides:**
- `prepared`: return true if WAITING. Only break when one of the **first 4** `positionsToBreak` is not walk-through; then delegate to `super.prepared`, which handles all of them. Otherwise true ("ignore the last one which might be water") (Fall:201-213).
- `safeToCancel` = `feet == src || status != RUNNING`.

---

### 9.5 MovementDiagonal (±1, ±1; flat, or optional ascend/descend)

**Geometry.** `dir1 = src.relative(N|S)`, `dir2 = src.relative(E|W)`, both at src.y. `end = dir1.relative(dir2dir).above(dy)`.
- `positionsToBreak = {dir1, dir1.above(), dir2, dir2.above(), end, end.above()}`. These are used only for bookkeeping and the sprint check; `prepared()` always returns true, so **Diagonal never breaks blocks** (Diag:56-62, 325-327).
- `toWalkInto` = entries [0..3] that are not walk-through; `toBreak` = entries [4..5] that are not walk-through (Diag:330-357).

**Names used in `cost`:**
- Corner A column `(x, destZ)`: `pb0` = A low (x, y, destZ), `pb1` = A mid (x, y+1, destZ), A top (x, y+2, destZ).
- Corner B column `(destX, z)`: `pb2` = B low, `pb3` = B mid, B top.
- `cuttingOver1` = (x, y−1, destZ); `cuttingOver2` = (destX, y−1, z).

**`cost(context, x, y, z, destX, destZ, res)`** (Diag:116-273). A bare `return` means impossible.
1. `destIntoUpper` = dest head (destX, y+1). If not canWalkThrough → return.
2. `destInto` = dest feet (destX, y).
   - **If not canWalkThrough → ascend:** requires `allowDiagonalAscend && canWalkThrough(x, y+2, z) && canWalkOn(destInto) && canWalkThrough(destX, y+2, destZ)`, else return. `destWalkOn = destInto`.
   - **Else:** `destWalkOn` = dest floor (destX, y−1). `frostWalker = mustBeSolidToWalkOn(src floor) && canUseFrostWalker(destWalkOn)`. If `!frostWalker && !canWalkOn(destWalkOn)` → **descend:** requires `allowDiagonalDescend && canWalkOn(destX, y−2, destZ) && canWalkThrough(destX, y−1, destZ)`, else return. Then `frostWalker &= !assumeWalkOnWater`.
3. Door check. If `isBlockingDoor(src feet, x, z, destX, destZ) || isBlockingDoor(ascend ? destIntoUpper : destInto, destX, destZ, x, y)` → return.
   - Note that the second call passes **`x, y`** as `otherX, otherZ`. This is a probable bug; `z` was intended.
4. `multiplier = WALK_ONE_BLOCK_COST`.
   - destWalkOn: SOUL_SAND `+= (SS−W)/2`; else magma (allowed) `+= (SNEAK−W)/2` with `sneaking = true`; else frostWalker adds nothing; else `== Blocks.WATER` `+= walkOnWaterOnePenalty · SQRT_2`.
5. src floor (`fromDown`): climbable → return. SOUL_SAND `+= (SS−W)/2`; magma (allowed) `+= (SNEAK−W)/2` with `sneaking = true`.
6. `cuttingOver1` / `cuttingOver2`: `(!allowWalkOnMagmaBlocks && MAGMA) || isLava` → return.
7. If `isWater(src feet) || isWater(destInto)`: if ascend → return; else `multiplier = waterWalkSpeed` (it replaces everything) and `water = true`.
8. **Ascend branch** (Diag:196-219):
   - Compute `ATop, AMid, ALow, BTop, BMid, BLow` as canWalkThrough of the respective cells. Then `ALow &= !isBlockingDoor(pb0, x, destZ, destX, z)` and `BLow &= !isBlockingDoor(pb2, destX, z, x, destZ)`.
   - Return if any of:
     - neither A nor B is fully clear
     - `avoidWalkingInto(pb0) || avoidWalkingInto(pb2)`
     - `(ATop && AMid && canWalkOn(pb0))` or the same for B ("we could just ascend")
     - `(!ATop && AMid && ALow)` or the same for B ("head bonk")
   - `res.cost = multiplier·√2 + JUMP_ONE_BLOCK_COST + jumpPenalty`; `res.y = y+1`. No mining.
9. **Flat or descend branch** (Diag:220-272):
   1. `optionA = mine(pb0, false) + (isBlockingDoor(pb0, x, destZ, destX, z) ? 1 : 0)`; `optionB = mine(pb2, false) + (door ? 1 : 0)`. If both ≠ 0 → return.
   2. `optionA += mine(pb1, true)`. If both ≠ 0 → return.
   3. If `optionA == 0 && ((avoidWalkingInto(pb2) && pb2 != Blocks.WATER) || avoidWalkingInto(pb3))` → return.
   4. `optionB += mine(pb3, true)`. If both ≠ 0 → return.
   5. If `optionB == 0 && ((avoidWalkingInto(pb0) && pb0 != WATER) || avoidWalkingInto(pb1))` → return.
   6. **Edging** (`optionA != 0 || optionB != 0`): `multiplier *= (√2 − 0.001)`. If the src feet block is climbable → return.
   7. **Not edging:** if `canSprint && !water && !sneaking`, `multiplier *= SPRINT_MULTIPLIER`.
   8. `res.cost = multiplier·√2`. For descend, add `max(FALL_N_BLOCKS_COST[1], CENTER_AFTER_FALL_COST)` and set `y−1`.

   The mining cost of the blocked side is **never added**: the path edges around it instead.

**Default costs:**
| Case | Cost |
|---|---|
| Sprint flat | 4.6328·0.7692·1.4142 = **5.0400** |
| Edging | 4.6328·(1.4142−0.001)·1.4142 = **9.2591** |
| Diagonal ascend | 6.5518 + 3.1634 + 2 = **11.7152** |
| Diagonal descend (sprint) | 5.0400 + 5.6147 = **10.6547** |
| Water penalty on dest | effectively +3·√2·√2 = +6 |

**`isBlockingDoor(state, doorX, doorZ, otherX, otherZ)`** (Diag:366-385):
- Must be a `DoorBlock`. `(ox, oz) = FACING` unit vector.
- `HINGE == LEFT` → neighbour `(doorX − ox + oz, doorZ − oz − ox)`; else `(doorX − ox − oz, doorZ − oz + ox)`.
- Return `neighbour == (otherX, otherZ)`.

**`updateState`** (Diag:276-310):
1. `super`; if not RUNNING, return.
2. `feet == dest` → **SUCCESS**. Else if `!playerInValidPosition() && !(isLiquid(src) && validPositions.contains(feet.above()))` → **UNREACHABLE**.
3. Doors, for corner A = (src.x, src.y, dest.z): if `!isBlockingDoor(A, src.x, dest.z, dest.x, src.z) && (!openDoors(src, A) || !openDoors((src.x, dest.y, dest.z), dest))` → return. Same for corner B = (dest.x, src.y, src.z).
4. If `dest.y > src.y && py < src.y + 0.1 && horizontalCollision` → press **JUMP**.
5. If `sprint()` → press SPRINT. `sprint()` is false when `isLiquid(feet) && !sprintInWater`, or when any of `positionsToBreak[0..3]` is not walk-through.
6. `SNEAK = allowWalkOnMagmaBlocks && any steppingOnBlocks is MAGMA`.
7. `moveTowards(dest)`.

**Valid positions** (Diag:104-114), with `diagA = (src.x, src.y, dest.z)` and `diagB = (dest.x, src.y, src.z)`:
- Descend: `{src, dest.above(), diagA, diagB, dest, diagA.below(), diagB.below()}`
- Ascend: `{src, src.above(), diagA, diagB, dest, diagA.above(), diagB.above()}`
- Flat: `{src, dest, diagA, diagB}`

**`safeToCancel`** (Diag:65-91):
- `feet == src` → true.
- Both corner floors canWalkOn → true.
- If standing in a corner column at src.y: true if any of the 4 points `(px ± 0.25, py − 1, pz ± 0.25)` is canWalkOn.
- Otherwise true.

---

### 9.6 MovementDownward (mine the block under you and drop 1)

**Geometry.** `dest = src.below()`; `positionsToBreak = {dest}`; valid positions `{src, dest}` (Down:37-55).

**`cost(context, x, y, z)`** (Down:57-72):
1. `!allowDownward` → **COST_INF**.
2. `!canWalkOn(x, y−2, z)` (the floor under the block to be mined) → **COST_INF**.
3. `down` = src floor (x, y−1, z):
   - `LADDER` or `VINE` → return **`LADDER_DOWN_ONE_COST`** (6.6667).
   - Otherwise return **`FALL_N_BLOCKS_COST[1] + getMiningDurationTicks(down, includeFalling=false)`**. Default is 5.6147 + breaking; for example dirt by hand gives 22.61.

**Execution:**
- The break is done by `Movement.prepared()` on `dest`.
- `updateState` (Down:75-95):
  1. `super`; if not RUNNING, return.
  2. `feet == dest` → **SUCCESS**. Else `!playerInValidPosition()` → **UNREACHABLE**.
  3. `ab` = horizontal distance to the dest centre. If `numTicks++ < 10 && ab < 0.2` → return (just fall).
  4. Otherwise `moveTowards(dest)`.

---

### 9.7 MovementPillar (jump and place under yourself; or climb a ladder or water column)

**Geometry.** `dest = src.above()`; `positionsToBreak = {src.above(2) /*src head+1*/}`; `positionToPlace = src` (src feet) (Pill:44-46).

**`cost(context, x, y, z)`** (Pill:58-136). Blocks: `fromState` = src feet, `fromDown` = src floor, `srcUp` = src head, `toBreak` = src head+1, `check` = (x, y+3, z).
1. `ladder = isClimbable(src feet)`.
2. If not a ladder:
   - `isClimbable(src floor)` → **COST_INF**.
   - src floor is a bottom slab → **COST_INF**.
3. `toBreak` is a `FenceGateBlock` → **COST_INF** (issue #172).
4. If `isWater(toBreak) && isWater(src feet)` and `isWater(src head)` → return **`LADDER_UP_ONE_COST`** (8.5106). This is "ascending pillars of water".
5. `placeCost = 0`. If not a ladder:
   - `placeCost = costOfPlacingAt(src feet)`; INF → **COST_INF**.
   - If the src floor is an `AirBlock`: `placeCost += 0.1`.
6. **COST_INF** if `(isLiquid(src feet) && !canPlaceAgainst(src floor)) || (isLiquid(src floor) && !isWaterloggedSolidTop(src floor) && assumeWalkOnWater)`.
7. **COST_INF** if the src feet block is `LILY_PAD` or a `CarpetBlock` and the src floor has a fluid.
8. `hardness = getMiningDurationTicks(toBreak, includeFalling=true)`; INF → **COST_INF**.
9. If `hardness != 0`:
   - If `toBreak` is climbable → `hardness = 0`.
   - Else if `check` (y+3) is a `FallingBlock` and `(!(toBreak is FallingBlock) || !(srcUp is FallingBlock))` → **COST_INF**.
10. Return:
    - Ladder: **`LADDER_UP_ONE_COST + hardness·5`**.
    - Otherwise: **`JUMP_ONE_BLOCK_COST + placeCost + jumpPenalty + hardness`**. Default 3.1634 + 20 + 2 = **25.1634**, or 25.2634 over air.

**`updateState`** (Pill:139-231):
1. `super`; if not RUNNING, return.
2. `feet.y < src.y` → **UNREACHABLE**.
3. **Water column.** If the src feet block is water and `dest` is water:
   - Target = rotation to the dest centre (not forced).
   - If `|px − cx| > 0.2 || |pz − cz| > 0.2` → press `MOVE_FORWARD`.
   - `feet == dest` → **SUCCESS**. Return.
4. `ladder = isClimbable(src feet)`. `rotation` = rotation to the src centre. If not a ladder: target = `(current yaw, rotation.pitch)`, forced.
5. `blockIsThere = canWalkOn(src) || ladder`.
6. **Ladder:** `feet == dest` → **SUCCESS**; else `moveTowards(dest)` + **JUMP**, return.
7. **Not a ladder:**
   1. `!selectThrowawayForLocation(true, src)` → **UNREACHABLE**.
   2. Press **SNEAK**.
   3. `dist` = horizontal Euclidean distance to the dest centre; `flatMotion = √(vx² + vz²)`.
      - If `dist > 0.17`: press `MOVE_FORWARD`; target = `rotation` (yaw + pitch), forced.
      - Else if `flatMotion < 0.05`: `JUMP = (py < dest.y)`.
   4. If `!blockIsThere`:
      - If the src block is neither air nor `canBeReplaced()`: target = `reachable(src)` (forced) if present; `JUMP = false`; press `CLICK_LEFT` (mine the obstruction); `blockIsThere = false`.
      - Else if `isCrouching() && (isLookingAt(src.below()) || isLookingAt(src)) && py > dest.y + 0.1` → press **`CLICK_RIGHT`**. This places near the jump apex.
8. `feet == dest && blockIsThere` → **SUCCESS**.

**`prepared`** (Pill:234-245): if feet is src or src.below and the src floor is climbable → press SNEAK. If `isWater(dest.above())` → true (skip breaking). Otherwise `super.prepared`.

---

### 9.8 MovementParkour (jump a gap, 2..4 blocks; optional ascend and mid-air place)

**Geometry.** `dest = src + dir·dist + (ascend ? up : 0)`; `positionsToBreak = {}`; `positionToPlace = src + dir·dist + (ascend ? 0 : down)` (Park:49-54).
- The static `cost(context, src, dir)` derives `dist = |res.x − x| + |res.z − z|` and `ascend = res.y > y` (Park:56-61).
- `calculateCost` requires `res == dest` exactly (Park:223-230).
- Valid positions: `src + dir·i + up·j` for i = 0..dist, j = 0..1.

**`cost(context, x, y, z, dir, res)`** (Park:63-201). Let `(xd, zd) = dir` and `col(i) = (x + xd·i, z + zd·i)`.

*Preconditions.* Any failure means no move:
1. `allowParkour`.
2. `!allowJumpAtBuildLimit && y >= context.maxY` → none.
3. `col(1)` feet `y` must be **fullyPassable**.
4. `adj` = `col(1)` floor `y−1`. If `canWalkOn(adj)` → none ("could just traverse").
5. `avoidWalkingInto(adj) && !(adj fluid is WaterFluid)` → none ("magma sucks").
6. `col(1)` `y+1` and `y+2` must be fullyPassable. Src head+1 `(x, y+2, z)` must be fullyPassable.
7. `standingOn` = src floor. Climbable, `StairBlock` or bottom slab → none.
8. `assumeWalkOnWater && standingOn has fluid && !isWaterloggedSolidTop(standingOn)` → none.
9. Src feet has a fluid → none ("can't jump out of water").

*Max jump distance `maxJump`:*
| Condition | maxJump | Gap |
|---|---|---|
| Standing on magma (with `allowWalkOnMagmaBlocks`) | **2** | 1 block |
| Standing on soul sand | **2** | 1 block |
| `canSprint` | **4** | 3 blocks |
| Otherwise | **3** | 2 blocks |

*Forward scan.* `verifiedMaxJump = 1`. For `i = 2..maxJump`, at `col(i)`:
1. `y+1` and `y+2` must be fullyPassable, else break.
2. `destInto` = `y`. If it is **not** fullyPassable:
   - **Parkour ascend** if `i <= 3 && allowParkourAscend && canSprint && canWalkOn(destInto) && checkOvershootSafety(col(i+1), y+1)`: `res = (col(i), y+1)`, cost **`i · SPRINT_ONE_BLOCK_COST + jumpPenalty`**. Return.
   - Otherwise break.
3. `landingOn` = `y−1`. A landing is valid if `(landingOn != FARMLAND && canWalkOn(landingOn)) || (min(16, frostWalker + 2) >= i && canUseFrostWalker(landingOn))`:
   - If `checkOvershootSafety(col(i+1), y)`: `res = (col(i), y)`, cost **`costFromJumpDistance(i) + jumpPenalty`**. Return.
   - Otherwise break.
4. `y+3` must be fullyPassable, else break.
5. `verifiedMaxJump = i`.

Column coverage implied by this scan:
- Column 1: y, y+1, y+2 fullyPassable, and its floor is not walkable.
- Each intermediate gap column 2..i−1: y−1 is not a landing; y, y+1, y+2 and **y+3** are fullyPassable.
- Landing column: y+1, y+2 fullyPassable; y is fullyPassable (flat landing) or canWalkOn (ascend).

*Parkour place* (only if `allowParkourPlace`). For `i = verifiedMaxJump` down to 2, so the longest jump is preferred:
1. `toReplace` = `col(i)` at `y−1`.
2. `placeCost = costOfPlacingAt`; INF → continue.
3. `!isReplaceable` → continue.
4. `!checkOvershootSafety(col(i+1), y)` → continue.
5. For each {N, S, E, W, DOWN} neighbour of `(col(i), y−1)`: skip the one at `(destX − xd, destZ − zd)` ("we can't turn around that fast"). If `canPlaceAgainst` → `res = (col(i), y)`, cost **`costFromJumpDistance(i) + placeCost + jumpPenalty`**. Return.

**`checkOvershootSafety(bsi, x, y, z)`** = `!avoidWalkingInto(x, y, z) && !avoidWalkingInto(x, y+1, z)` (Park:203-206).

**`costFromJumpDistance`** (Park:208-219): 2 → `WALK_ONE_BLOCK_COST·2`; 3 → `WALK_ONE_BLOCK_COST·3`; 4 → `SPRINT_ONE_BLOCK_COST·4`; any other value throws.

**Cost table (jumpPenalty = 2):**
| dist (gap) | flat | flat + place (20) | ascend |
|---|---|---|---|
| 2 (1-gap) | 9.2657 + 2 = **11.2657** | **31.2657** | 2·3.5638 + 2 = **9.1276** |
| 3 (2-gap) | 13.8985 + 2 = **15.8985** | **35.8985** | 3·3.5638 + 2 = **12.6914** |
| 4 (3-gap, sprint only) | 14.2552 + 2 = **16.2552** | **36.2552** | n/a (ascend requires i ≤ 3) |

**`updateState`** (Park:252-315):
1. `super`; if not RUNNING, return.
2. `feet.y < src.y` → **UNREACHABLE** ("sorry").
3. `openDoors(src, src + dir)` false → return.
4. If `dist >= 4 || ascend` → press **SPRINT**. Parkour does not request sprint for flat dist 2 or 3.
5. If `allowWalkOnMagmaBlocks` and the block under the feet is MAGMA → press SNEAK.
6. `moveTowards(dest)`.
7. If `feet == dest`: a `VINE` or `LADDER` at dest → **SUCCESS**; else if `py − feet.y < 0.094` → **SUCCESS** (lily-pad tolerance).
8. Else if `feet != src`:
   - **If `feet == src + dir || py − src.y > 0.0001`** (over the edge or airborne):
     - **Mid-air place:** if `allowPlace && hasGenericThrowaway() && !canWalkOn(dest.below()) && !onGround && attemptToPlaceABlock(dest.below(), preferDown=true, wouldSneak=false) == READY_TO_PLACE` → press `CLICK_RIGHT`.
     - **dist check:** if `dist == 3 && !ascend`, compute `distFromStart = max(|src.x+.5 − px|, |src.z+.5 − pz|)`. If `< 0.7`, return without jumping yet.
     - Press **JUMP**.
   - **Else if `feet != dest − dir`** (on the ground somewhere else): `SPRINT = false`. If `feet == src − dir` → `moveTowards(src)`; else `moveTowards(src − dir)`. This walks back for a run-up.

**Other overrides.** `safeToCancel` = `status != RUNNING`: only cancellable before the first running tick.

---

## 10. Recalculate, cancel and success conditions outside the movement (PathExecutor)

These come from PE:197-256 and PE:348-502. They are not in the requested files but govern execution.
- **Lookahead check.** When a movement starts, PathExecutor re-runs `calculateCost` for the next `costVerificationLookahead` (5) movements. If any is `>= COST_INF` and `safeToCancel` → cancel.
- **Every tick:** `currentCost = recalculateCost()`.
  - `>= COST_INF && canCancel` → cancel.
  - If the movement was not calculated while its chunk was loaded and `currentCost − originalEstimate > maxCostIncrease` (10) → cancel.
- **Status handling.** UNREACHABLE or FAILED → cancel. SUCCESS → advance `pathPosition` and tick the next movement immediately.
- **Timeout.** `ticksOnCurrent > originalCostEstimate + movementTimeoutTicks (100)` → cancel.
- **Sprint override** (`shouldSprintNextTick`). The SPRINT that a movement requests is consumed by PathExecutor, which decides:
  - No sprint if `!allowSprint || food <= 6`.
  - **Traverse → Ascend skip** if `sprintableAscend(...)`: same direction, both floors walkable, `next.toBreakCached` empty, a 2×3 fullyPassable corridor, and no `avoidWalkingInto` at `src+3` or `next.dest+2`.
  - **Descend:** apply forceSafeMode (frost-walker case). If `safeMode() && !skipToAscend()` → no sprint.
    - Descend followed by Ascend in the same direction → skip the descend.
    - `canSprintFromDescendInto`: the next move is a Descend in the same direction, or the floor beyond dest is walkable and the next move is a Traverse in the same direction (or a Diagonal with `allowOvershootDiagonalDescend`). In that case sprint, and advance once `feet == dest`.
  - **Ascend after a same-direction Descend:** release JUMP once `py >= src.above().y − 0.07`.
  - **Fall:** an optional `overrideFall` steering.

---

## 11. Quirks to reproduce, or deliberately fix

1. **Water cost.** `waterSpeedMultiplier` defaults to **1.0f** when no Depth Strider is worn, so `waterWalkSpeed = WALK_ONE_BLOCK_COST` (4.633) instead of 9.091 (CC:188-201, 157).
2. **Diagonal door check argument.** The second `isBlockingDoor` call passes `(x, y)` instead of `(x, z)` (Diag:149).
3. **Dead UNREACHABLE branch.** `Movement.prepared` cannot reach the `somethingInTheWay → UNREACHABLE` branch (M:386-391).
4. **Traverse backplace** never checks `canPlaceAgainst(src floor)`. It only rejects soul sand, non-double slabs, `!mustBeSolidToWalkOn`, and lily pad or carpet over fluid (Trav:157-168).
5. **Water falls have no height cap** (Desc:160-180). Solid landings are capped at `maxFallHeightNoWater+1` (no bucket) or `maxFallHeightBucket+1` (with bucket), measured as `unprotectedFallHeight`.
6. **Diagonals never break blocks.** Edging around adds only the `(√2 − 0.001)` factor, never the mining cost of the blocked side. Cost-time mining is used only to decide whether a side is open.
7. **Fluid walk-through ignores the block's shape.** `canWalkThroughPosition` returns true for still top-layer water even in waterlogged non-passable blocks (for example a waterlogged fence), because the fluid branch does not check the collision shape (MH:228-243).
8. **Magma asymmetry.** Traverse only penalises magma on the **src floor**, not the dest floor. Diagonal penalises both. Ascend uses the full `SNEAK_ONE_BLOCK_COST` when stepping onto magma.
