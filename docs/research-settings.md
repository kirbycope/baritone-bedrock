# Research report: pathfinding settings in Baritone and mineflayer-pathfinder

Both repos are untouched clones. Baritone is at HEAD `c55a9c5` (origin cabaletta/baritone). mineflayer-pathfinder is at HEAD `b07a419` "createHuman: cap the wait for the head to settle (#376)" (origin PrismarineJS/mineflayer-pathfinder).

**Corrections to the requested key list:**
- **`allowJumpAt256`** is a deprecated no-op. The real setting is `allowJumpAtBuildLimit`.
- **`cutoffAtLoadedChunks`** does not exist. The setting is `cutoffAtLoadBoundary`.
- **"sprintJump" family:** the settings are `sprintJumping` and `sprintJumpingDiagonals`. Related: `headHitters` and `headHittersDiagonal`.
- **`allowBreakAnyway`** exists as requested.
- **"parkour" settings** are `allowParkour`, `allowParkourPlace`, `allowParkourAscend` and `allowJumpAtBuildLimit`.

---

# PART A: Baritone

Files (absolute paths):
- **S** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\api\java\baritone\api\Settings.java` (1745 lines, read in full)
- **PE** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\main\java\baritone\pathing\path\PathExecutor.java`
- **SJ** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\main\java\baritone\pathing\path\SprintJump.java`
- **PB** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\main\java\baritone\behavior\PathingBehavior.java`
- **CC** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\main\java\baritone\pathing\movement\CalculationContext.java`
- **AC** = `C:\GitHub\.claude\skills\ftue\java\baritone\src\api\java\baritone\api\pathing\movement\ActionCosts.java`

All settings are `public final Setting<T> name = new Setting<>(default)`. Line numbers point at the declaration. Javadoc is quoted verbatim, and `<p>` paragraph breaks are joined with " / ".

## A1. Every `settings().` key used by PathExecutor, SprintJump and PathingBehavior

There are no other access patterns (such as a local `settings` variable) in these three files.

| File:line | Key | What it does there |
|---|---|---|
| PE:202 | costVerificationLookahead | When a movement starts, it re-costs the next `value-1` movements. If any is COST_INF and the movement is `safeToCancel`, it cancels ("a future movement has become impossible"). |
| PE:216 | maxCostIncrease | If `!movement.calculatedWhileLoaded()` and `currentCost - originalEstimate > value` (and cancellable), it cancels. |
| PE:246 | movementTimeoutTicks | If `ticksOnCurrent > originalCostEstimate + value`, it cancels ("This movement has taken too long"). |
| PE:357 | allowSprint | Sprint is decided per tick. It is false if the setting is off or `foodLevel <= 6`. |
| PE:362 | sprintJumping | Tries `SprintJump.plan(...)` when the setting is on and sneak is not forced. On success it steers with forward+jump. |
| PE:509 | sprintJumping | `fly()` keeps steering mid-air while `!onGround`, not in water/lava/climbable, `flight.ticks < MAX_TICKS (40)` and the setting is on. |
| PE:387 | headHitters | When the movement requested sprint, it calls `headHitJump(current)`. |
| PE:554 | headHittersDiagonal | Head-hitting on MovementDiagonal is disabled when this is false. |
| PE:403 | allowPlace | In frost-walker descend logic, `couldPlaceInstead = allowPlace && hasGenericThrowaway && next is MovementParkour`. |
| PE:666 | sprintAscends | Gates `sprintableAscend()`, which is used for traverse→ascend skipping (PE:371) and ascend sprinting (PE:464). |
| PE:711 | allowOvershootDiagonalDescend | `canSprintFromDescendInto`: sprinting from a descend into a MovementDiagonal is allowed only if this is true. |
| PE:757 | maxPathHistoryLength | `cutIfTooLong()` runs when `pathPosition > value`. |
| PE:758 | pathHistoryCutoffAmount | Number of movements trimmed from the path start by `CutoffPath`. |
| SJ:77 | sprintJumpingDiagonals | `plan()` returns null for diagonal runs when this is false. |
| PB:160 | disconnectOnArrival | On AT_GOAL it calls `ctx.world().disconnect()`. |
| PB:206 | splicePath | `current = current.trySplice(next)` every tick. |
| PB:225 | planningTickLookahead | Starts planning the next segment when `ticksRemainingInSegment(false) < value`. The code comment there says "7.5 seconds". |
| PB:490-491 | primaryTimeoutMS, failureTimeoutMS | Used when `current == null` (fresh path). |
| PB:493-494 | planAheadPrimaryTimeoutMS, planAheadFailureTimeoutMS | Used when planning ahead (`current != null`). |
| PB:558 | simplifyUnloadedYCoord | `createPathfinder`: a goal with a render pos in an unloaded chunk is replaced by `GoalXZ`. |

**Hard-coded execution constants in PathExecutor:**
- `MAX_MAX_DIST_FROM_PATH = 3` (PE:50): more than 3 from the path cancels immediately (PE:144-148).
- `MAX_DIST_FROM_PATH = 2` (PE:51).
- `MAX_TICKS_AWAY = 200` (PE:60, javadoc: "equal to 10 seconds… must be at least 5.5s (110 ticks)"): more than 2 from the path for more than 200 ticks cancels (PE:133-143). MovementFall uses flat distance instead (PE:311-313).
- Pauses with keys cleared if the next movement's dest chunk is not loaded (PE:189-196).
- `shouldPause()` pauses if the in-progress plan's best path contains the player's feet, i.e. a backtrack (PE:223-227, 274-305).
- A movement status of UNREACHABLE/FAILED cancels; SUCCESS advances (PE:228-239).

**SprintJump constants (SJ:44-51):**
- `GROUND_FRICTION = 0.6*0.91`, `AIR_FRICTION = 0.91`, `AIR_ACCEL = 0.026*0.98`, `JUMP_BOOST = 0.2`
- `JUMP = 0.42`, `GRAVITY = 0.08`, `SAFE_FALL = 3`, `EPS = 1e-6`
- `FLAT_PACE = 0.28`, `SLOW_PACE = 0.15`, `MAX_RUNWAY = 10`, `MAX_TICKS = 40`
- `MARGINS = {0.85, 1, 1.15}` (velocity robustness margins)

**SprintJump preconditions (SJ:75-81):** on ground, sprinting, not in water/lava/climbable, feet exactly on the path node, no Jump Boost / Slow Falling / Levitation, vanilla jump strength / gravity / safe-fall attributes, solid floor (friction 0.6, not magma, full collision box), and y+3 clear.

**SprintJump runway (SJ:86-102):** consecutive same-direction Traverse/Ascend/Descend (or flat Diagonal) with no blocks to break or place. It needs a runway of at least 3 cells and lateral offset ≤ 0.2 / lateral velocity ≤ 0.08 (SJ:114). It simulates vanilla air physics tick by tick and only jumps if the landing is on a path cell with apex−landing < 3 (SJ:266-275).

**CalculationContext snapshot (CC:110-172).** All movement-cost settings are snapshotted per path calculation:
- `hasThrowaway = allowPlace && hasGenericThrowaway` (CC:118)
- `hasWaterBucket = allowWaterBucketFall && water bucket in hotbar && not Nether` (CC:119)
- `canSprint = allowSprint && food > 6` (CC:120)
- `placeBlockCost = blockPlacementPenalty` (CC:141)
- allowBreak / allowBreakAnyway / allowParkour / allowParkourPlace / allowJumpAtBuildLimit / allowParkourAscend / assumeWalkOnWater (CC:142-148)
- `allowFallIntoLava = false` (CC:149, "Super secret internal setting for ElytraBehavior")
- allowDiagonalDescend / allowDiagonalAscend / allowDownward (CC:151-153)
- `minFallHeight = 3` hard-coded (CC:154)
- maxFallHeightNoWater / maxFallHeightBucket (CC:155-156)
- allowSwimming lowers the water cost to SWIM_ONE_BLOCK_COST when canSprint (CC:158-161)
- blockBreakAdditionalPenalty, backtrackCostFavoringCoefficient, jumpPenalty, walkOnWaterOnePenalty, allowWalkOnMagmaBlocks (CC:163-167)

Place-in-fluid checks are in `costOfPlacingAt` (CC:269-274). The break gate `!allowBreak && !allowBreakAnyway.contains(block)` returns COST_INF (CC:279).

**Cost units (AC, in ticks):**
- WALK_ONE_BLOCK_COST = 20/4.317 ≈ 4.633 (AC:25)
- SPRINT_ONE_BLOCK_COST = 20/5.612 ≈ 3.564 (AC:36)
- WALK_ONE_IN_WATER_COST ≈ 9.091 (AC:26)
- SWIM_ONE_BLOCK_COST ≈ 6.993 (AC:31)
- SNEAK_ONE_BLOCK_COST ≈ 15.385 (AC:35)
- COST_INF = 1000000 (AC:51)

## A2. Settings.java: every pathfinding or path-execution setting

### Break, place and inventory permissions
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 63 | allowBreak | `true` | "Allow Baritone to break blocks" |
| 68 | allowBreakAnyway | `new ArrayList<>()` (empty `List<Block>`) | "Blocks that baritone will be allowed to break even with allowBreak set to false" |
| 73 | allowSprint | `true` | "Allow Baritone to sprint" |
| 78 | allowPlace | `true` | "Allow Baritone to place blocks" |
| 83 | allowPlaceInFluidsSource | `true` | "Allow Baritone to place blocks in fluid source blocks" |
| 88 | allowPlaceInFluidsFlow | `true` | "Allow Baritone to place blocks in flowing fluid" |
| 93 | allowInventory | `false` | "Allow Baritone to move items in your inventory to your hotbar" |
| 98 | ticksBetweenInventoryMoves | `1` | "Wait this many ticks between InventoryBehavior moving inventory items" |
| 103 | inventoryMoveOnlyIfStationary | `false` | "Come to a halt before doing any inventory moves. Intended for anticheat such as 2b2t" |
| 111 | assumeExternalAutoTool | `false` | "Disable baritone's auto-tool at runtime, but still assume that another mod will provide auto tool functionality / Specifically, path calculation will still assume that an auto tool will run at execution time, even though Baritone itself will not do that." |
| 116 | autoTool | `true` | "Automatically select the best available tool" |
| 913 | itemSaver | `false` | "Stop using tools just before they are going to break." |
| 918 | itemSaverThreshold | `10` | "Durability to leave on the tool when using itemSaver" |
| 924 | preferSilkTouch | `false` | "Always prefer silk touch tools over regular tools. This will not sacrifice speed, but it will always prefer silk touch tools over other tools of the same speed. This includes always choosing ANY silk touch tool over your hand." |
| 1443 | useSwordToMine | `true` | "Use sword to mine." |
| 374 | considerPotionEffects | `true` | "For example, if you have Mining Fatigue or Haste, adjust the costs of breaking blocks accordingly." (used at ToolSet.java:80) |

### Cost tuning
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 124 | blockPlacementPenalty | `20D` | "It doesn't actually take twenty ticks to place a block, this cost is so high because we want to generally conserve blocks which might be limited. / Decrease to make Baritone more often consider paths that would require placing blocks" |
| 131 | blockBreakAdditionalPenalty | `2D` | "This is just a tiebreaker to make it less likely to break blocks if it can avoid it. For example, fire has a break cost of 0, this makes it nonzero, so all else being equal it will take an otherwise equivalent route that doesn't require it to put out fire." |
| 136 | jumpPenalty | `2D` | "Additional penalty for hitting the space bar (ascend, diagonal ascend, pillar, or parkour) because it uses hunger" |
| 141 | walkOnWaterOnePenalty | `3D` | "Walking on water uses up hunger really quick, so penalize it" |
| 264 | avoidBreakingMultiplier | `.1` | "this multiplies the break speed, if set above 1 it's \"encourage breaking\" instead" (applied to blocksToAvoidBreaking, ToolSet.java:194) |
| 458 | costHeuristic | `3.563` | "This is the big A* setting. As long as your cost heuristic is an *underestimate*, it's guaranteed to find you the best path. 3.5 is always an underestimate, even if you are sprinting. If you're walking only (with allowSprint off) 4.6 is safe. Any value below 3.5 is never worth it. It's just more computation to find the same path, guaranteed. (specifically, it needs to be strictly slightly less than ActionCosts.WALK_ONE_BLOCK_COST, which is about 3.56) / Setting it at 3.57 or above with sprinting, or to 4.64 or above without sprinting, will result in faster computation, at the cost of a suboptimal path. Any value above the walk / sprint cost will result in it going straight at its goal, and not investigating alternatives, because the combined cost / heuristic metric gets better and better with each block, instead of slightly worse. / Finding the optimal path is worth it, so it's the default." (lines 443-457). A grep finds it consumed only in `GoalXZ.java:115` and `GoalAxis.java:42`. |
| 472 | backtrackCostFavoringCoefficient | `0.5` | "Set to 1.0 to effectively disable this feature / @see Issue #18" |
| 1126 | breakCorrectBlockPenaltyMultiplier | `10d` | builder only: "Multiply the cost of breaking a block that's correct in the builder's schematic by this coefficient" |
| 1131 | placeIncorrectBlockPenaltyMultiplier | `2d` | builder only: "Multiply the cost of placing a block that's incorrect…" |

### Movement-type toggles and assumptions
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 148 | strictLiquidCheck | `false` | "Don't allow breaking blocks next to liquids. / Enable if you have mods adding custom fluid physics." |
| 162 | assumeWalkOnWater | `false` | "Allow Baritone to assume it can walk on still water just like any other block. This functionality is assumed to be provided by a separate library that might have imported Baritone. / Note: This will prevent some usage of the frostwalker enchantment, like pillaring up from water." |
| 167 | assumeWalkOnLava | `false` | "If you have Fire Resistance and Jesus then I guess you could turn this on lol" |
| 172 | assumeStep | `false` | "Assume step functionality; don't jump on an Ascend." (used at MovementAscend.java:204) |
| 181 | assumeSafeWalk | `false` | "Assume safe walk functionality; don't sneak on a backplace traverse. / Warning: if you do something janky like sneak-backplace from an ender chest, if this is true it won't sneak right click, it'll just right click, which means it'll open the chest instead of placing against it. That's why this defaults to off." |
| 188 | allowJumpAtBuildLimit | `false` | "If true, parkour is allowed to make jumps when standing on blocks at the maximum height, so player feet is y=256 / Defaults to false because this fails on constantiam. Please let me know if this is ever disabled. Please." (used at MovementParkour.java:67) |
| 193-195 | allowJumpAt256 | `false`, `@Deprecated @JavaOnly` | "Just here so mods that use the API don't break. Does nothing." |
| 202 | allowParkourAscend | `true` | "This should be monetized it's so good / Defaults to true, but only actually takes effect if allowParkour is also true" |
| 211 | allowDiagonalDescend | `false` | "Allow descending diagonally / Safer than allowParkour yet still slightly unsafe, can make contact with unchecked adjacent blocks, so it's unsafe in the nether. / For a generic \"take some risks\" mode I'd turn on this one, parkour, and parkour place." |
| 218 | allowDiagonalAscend | `false` | "Allow diagonal ascending / Actually pretty safe, much safer than diagonal descend tbh" |
| 225 | allowDownward | `true` | "Allow mining the block directly beneath its feet / Turn this off to force it to make more staircases and less shafts" |
| 339 | avoidUpdatingFallingBlocks | `true` | "If this setting is true, Baritone will never break a block that is adjacent to an unsupported falling block. / I.E. it will never trigger cascading sand / gravel falls" |
| 347 | allowVines | `false` | "Enables some more advanced vine features. They're honestly just gimmicks and won't ever be needed in real pathing scenarios. And they can cause Baritone to get trapped indefinitely in a strange scenario. / Almost never turn this on lol" |
| 353 | allowWalkOnBottomSlab | `true` | "Slab behavior is complicated, disable this for higher path reliability. Leave enabled if you have bottom slabs everywhere in your base." |
| 362 | allowParkour | `false` | "You know what it is / But it's very unreliable and falls off when cornering like all the time so. / It also overshoots the landing pretty much always (making contact with the next block over), so be careful" |
| 369 | allowParkourPlace | `false` | "Actually pretty reliable. / Doesn't make it any more dangerous compared to just normal allowParkour th" |
| 596 | allowOvershootDiagonalDescend | `true` | "Is it okay to sprint through a descend followed by a diagonal? The player overshoots the landing, but not enough to fall off. And the diagonal ensures that there isn't lava or anything that's !canWalkInto in that space, so it's technically safe, just a little sketchy. / Note: this is *not* related to the allowDiagonalDescend setting, that is a completely different thing." |
| 846 | allowSwimming | `false` | "Actually swim through water instead of bobbing along the bottom of it / Swimming works the same way it does for a player: hold sprint (ctrl) while in water to enter the swim state, then steer with yaw and pitch towards the goal. Since sprinting is what keeps the swim state alive, this requires {@link #allowSprint} and enough hunger to sprint; without it, baritone falls back to the normal walk-on-the-bottom behavior. / Water traversal is also costed at swim speed when this is on, unless depth strider makes walking the bottom the faster option." |
| 1618 | allowWalkOnMagmaBlocks | `false` | "Sneak when magma blocks are under feet" |

### Falls and water
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 154 | allowWaterBucketFall | `true` | "Allow Baritone to fall arbitrary distances and place a water bucket beneath it. Reliability: questionable." |
| 581 | maxFallHeightNoWater | `3` | "How far are you allowed to fall onto solid ground (without a water bucket)? 3 won't deal any damage. But if you just want to get down the mountain quickly and you have Feather Falling IV, you might set it a bit higher, like 4 or 5." |
| 587 | maxFallHeightBucket | `20` | "How far are you allowed to fall onto solid ground (with a water bucket)? It's not that reliable, so I've set it below what would kill an unarmored player (23)" |
| 833 | sprintInWater | `true` | "Continue sprinting while in water" |

### Execution: sprint, jump, traverse, breaking
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 379 | sprintAscends | `true` | "Sprint and jump a block early on ascends wherever possible" |
| 386 | headHitters | `false` | "Sprint jump in 1x2 corridors and whenever walking under a low ceiling, bonking our head on it. / The sprint jump speed boost applies before we hit the ceiling, making this faster than just sprinting." |
| 392 | headHittersDiagonal | `true` | "Also sprint jump under low ceilings on flat diagonal movements when {@link #headHitters} is enabled. Enabled by default. Disable this to limit head hitters to straight movements." |
| 398 | sprintJumping | `false` | "Sprint jump along straight stretches of path, up single block steps and down small hills. Every jump is simulated first and only happens if it lands back on the path without fall damage." |
| 403 | sprintJumpingDiagonals | `true` | "Also sprint jump along flat diagonal stretches of path when {@link #sprintJumping} is enabled." |
| 410 | overshootTraverse | `true` | "If we overshoot a traverse and end up one block beyond the destination, mark it as successful anyway. / This helps with speed exceeding 20m/s" |
| 415 | pauseMiningForFallingBlocks | `true` | "When breaking blocks for a movement, wait until all falling blocks have settled before continuing" |
| 420 | rightClickSpeed | `4` | "How many ticks between right clicks are allowed. Default in game is 4" |
| 430 | blockReachDistance | `4.5f` | "Block reach distance" (read via IPlayerController.java:59; creative uses 5.0F) |
| 436 | blockBreakSpeed | `6` | "How many ticks between breaking a block and starting to break the next block. Default in game is 6 ticks. Values under 1 will be clamped. The delay only applies to non-instant (1-tick) breaks." |
| 929 | walkWhileBreaking | `true` | "Don't stop walking forward when you need to break blocks in your way" |
| 615 | movementTimeoutTicks | `100` | "If a movement takes this many ticks more than its initial cost estimate, cancel it" |
| 540 | maxCostIncrease | `10D` | "If a movement's cost increases by more than this amount between calculation and execution (due to changes in the environment / world), cancel and recalculate" |
| 547 | costVerificationLookahead | `5` | "Stop 5 movements before anything that made the path COST_INF. For example, if lava has spread across the path, don't walk right up to it then recalculate, it might still be spreading lol" |

### Looking and rotations
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 425 | randomLooking113 | `2d` | "How many degrees to randomize the yaw every tick. Set to 0 to disable" |
| 441 | randomLooking | `0.01d` | "How many degrees to randomize the pitch and yaw every tick. Set to 0 to disable" |
| 782 | freeLook | `true` | "Move without having to force the client-sided rotations" |
| 787 | blockFreeLook | `false` | "Break and place blocks without having to force the client-sided rotations. Requires {@link #freeLook}." |
| 792 | elytraFreeLook | `true` | "Automatically elytra fly without having to force the client-sided rotations." |
| 797 | smoothLook | `false` | "Forces the client-sided yaw rotation to an average of the last {@link #smoothLookTicks} of server-sided rotations." |
| 802 | elytraSmoothLook | `false` | "Same as {@link #smoothLook} but for elytra flying." |
| 807 | smoothLookTicks | `5` | "The number of ticks to average across for {@link #smoothLook};" |
| 813 | remainWithExistingLookDirection | `true` | "When true, the player will remain with its existing look direction as often as possible. Although, in some cases this can get it stuck, hence this setting to disable that behavior." |
| 821 | antiCheatCompatibility | `true` | "Will cause some minor behavioral differences to ensure that Baritone works on anticheats. / At the moment this will silently set the player's rotations when using freeLook so you're not sprinting in directions other than forward, which is picken up by more \"advanced\" anticheats like AAC, but not NCP." |

### A*, timeouts, planning and segments
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 465 | pathingMaxChunkBorderFetch | `50` | "The maximum number of times it will fetch outside loaded or cached chunks before assuming that pathing has reached the end of the known area, and should therefore stop." (preceded by the comment at 460: "a bunch of obscure internal A* settings that you probably don't want to change") |
| 524 | minimumImprovementRepropagation | `true` | "Don't repropagate cost improvements below 0.01 ticks. They're all just floating point inaccuracies, and there's no point." |
| 534 | cutoffAtLoadBoundary | `false` | "After calculating a path (potentially through cached chunks), artificially cut it off to just the part that is entirely within currently loaded chunks. Improves path safety because cached chunks are heavily simplified. / This is much safer to leave off now, and makes pathing more efficient. More explanation in the issue. / @see Issue #114" |
| 552 | pathCutoffFactor | `0.9` | "Static cutoff factor. 0.9 means cut off the last 10% of all paths, regardless of chunk load state" (PathBase.java:54) |
| 557 | pathCutoffMinimumLength | `30` | "Only apply static cutoff for paths of at least this length (in terms of number of movements)" (PathBase.java:47) |
| 562 | planningTickLookahead | `150` | "Start planning the next path once the remaining movements tick estimates sum up to less than this value" |
| 567 | pathingMapDefaultSize | `1024` | "Default size of the Long2ObjectOpenHashMap used in pathing" |
| 574 | pathingMapLoadFactor | `0.75f` | "Load factor coefficient for the Long2ObjectOpenHashMap used in pathing / Decrease for faster map operations, but higher memory usage" |
| 622 | primaryTimeoutMS | `500L` | "Pathing ends after this amount of time, but only if a path has been found / If no valid path (length above the minimum) has been found, pathing continues up until the failure timeout" |
| 627 | failureTimeoutMS | `2000L` | "Pathing can never take longer than this, even if that means failing to find any path at all" |
| 634 | planAheadPrimaryTimeoutMS | `4000L` | "Planning ahead while executing a segment ends after this amount of time, but only if a path has been found / If no valid path (length above the minimum) has been found, pathing continues up until the failure timeout" |
| 639 | planAheadFailureTimeoutMS | `5000L` | "Planning ahead while executing a segment can never take longer than this, even if that means failing to find any path at all" |
| 644 | slowPath | `false` | "For debugging, consider nodes much much slower" |
| 649 | slowPathTimeDelayMS | `100L` | "Milliseconds between each node" |
| 654 | slowPathTimeoutMS | `40000L` | "The alternative timeout number when slowPath is on". Replaces both primary and failure timeouts (AStarPathFinder.java:76-77). |
| 939 | splicePath | `true` | "When a new segment is calculated that doesn't overlap with the current one, but simply begins where the current segment ends, splice it on and make a longer combined path. If this setting is off, any planned segment will not be spliced and will instead be the \"next path\" in PathingBehavior, and will only start after this one ends. Turning this off hurts planning ahead, because the next segment will exist even if it's very short. / @see #planningTickLookahead" |
| 944 | maxPathHistoryLength | `300` | "If we are more than 300 movements into the current path, discard the oldest segments, as they are no longer useful" |
| 949 | pathHistoryCutoffAmount | `50` | "If the current path is too long, cut off this many movements from the beginning." |
| 605 | simplifyUnloadedYCoord | `true` | "If your goal is a GoalBlock in an unloaded chunk, assume it's far enough away that the Y coord doesn't matter yet, and replace it with a GoalXZ to the same place before calculating a path. Once a segment ends within chunk load range of the GoalBlock, it will go back to normal behavior of considering the Y coord. The reasoning is that if your X and Z are 10,000 blocks away, your Y coordinate's accuracy doesn't matter at all until you get much much closer." |

### Avoidance
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 482 | avoidance | `false` | "Toggle the following 4 settings / They have a noticeable performance impact, so they default off / Specifically, building up the avoidance map on the main thread before pathing starts actually takes a noticeable amount of time, especially when there are a lot of mobs around, and your game jitters for like 200ms while doing so" |
| 489 | mobSpawnerAvoidanceCoefficient | `2.0` | "Set to 1.0 to effectively disable this feature / Set below 1.0 to go out of your way to walk near mob spawners" |
| 494 | mobSpawnerAvoidanceRadius | `16` | "Distance to avoid mob spawners." |
| 501 | mobAvoidanceCoefficient | `1.5` | "Set to 1.0 to effectively disable this feature / Set below 1.0 to go out of your way to walk near mobs" |
| 506 | mobAvoidanceRadius | `8` | "Distance to avoid mobs." |

All five are consumed in `Avoidance.java:63-79`.

### Block and item lists
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 230-235 | acceptableThrowawayItems | `[DIRT, COBBLESTONE, NETHERRACK, STONE]` (as items) | "Blocks that Baritone is allowed to place (as throwaway, for sneak bridging, pillaring, etc.)" |
| 240-242 | blocksToAvoid | `[TRIPWIRE]` | "Blocks that Baritone will attempt to avoid (Used in avoidance)" (MovementHelper.java:171, PrecomputedData.java:80) |
| 247-249 | blocksToDisallowBreaking | `[]` ("Leave Empty by Default") | "Blocks that Baritone is not allowed to break" |
| 254-259 | blocksToAvoidBreaking | `[CRAFTING_TABLE, FURNACE, CHEST, TRAPPED_CHEST]` | "blocks that baritone shouldn't break, but can if it needs to." |

### Caching and world data
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 610 | repackOnAnyBlockChange | `true` | "Whenever a block changes, repack the whole chunk that it's in" |
| 670 | chunkCaching | `true` | "The big one. Download all chunks in simplified 2-bit format and save them for better very-long-distance pathing." |
| 681 | pruneRegionsFromRAM | `true` | "On save, delete from RAM any cached regions that are more than 1024 blocks away from the player / Temporarily disabled / Temporarily reenabled / @see Issue #248" |
| 688 | chunkPackerQueueMaxSize | `2000` | "The chunk packer queue can never grow to larger than this, if it does, the oldest chunks are discarded / The newest chunks are kept, so that if you're moving in a straight line quickly then stop, your immediate render distance is still included" |
| 828 | pathThroughCachedOnly | `false` | "Exclusively use cached chunks for pathing / Never turn this on" (BlockStateInterface.java:87) |
| 1312 | cachedChunksExpirySeconds | `-1L` | "Cached chunks … expire and are deleted after this number of seconds -1 to disable / I would highly suggest leaving this setting disabled (-1)…" (long rationale at 1293-1311) |

### Goal and arrival behaviour with pathing effect
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 512 | rightClickContainerOnArrival | `true` | "When running a goto towards a container block (chest, ender chest, furnace, etc), right click and open it once you arrive." |
| 518 | enterPortal | `true` | "When running a goto towards a nether portal block, walk all the way into the portal instead of stopping one block before." (GetToBlockProcess.java:252) |
| 854 | blacklistClosestOnFailure | `true` | "When GetToBlockProcess or MineProcess fails to calculate a path, instead of just giving up, mark the closest instance of that block as \"unreachable\" and go towards the next closest. …" |
| 1213 | cancelOnGoalInvalidation | `true` | "Cancel the current path if the goal has changed, and the path originally ended in the goal but doesn't anymore. / Currently only runs when either MineBehavior or FollowBehavior is active. …" |
| 1218 | axisHeight | `120` | "The \"axis\" command (aka GoalAxis) will go to a axis, or diagonal axis, at this Y level." |
| 1223 | disconnectOnArrival | `false` | "Disconnect from the server upon arriving at your goal" |
| 1269 / 1274 / 1279 / 1284 | followOffsetDistance / followOffsetDirection / followRadius / followTargetMaxDistance | `0D` / `0F` / `3` / `0` | Follow goal geometry: "The actual GoalNear is set this distance away…", "…in this direction… degrees", "The radius (for the GoalNear) of how close to your target position you actually have to be", "The maximum distance to the entity you're following" |
| 1290 | disableCompletionCheck | `false` | "Turn this on if your exploration filter is enormous, you don't want it to check if it's done, and you are just fine with it just hanging on completion" |
| 693 | backfill | `false` | "Fill in blocks behind you" |

**Goal-selection settings for exploring and mining** (affect where Baritone paths, not how):
- exploreForBlocks 995 `true`
- worldExploringChunkOffset 1002 `0`
- exploreChunkSetMinimumSize 1007 `10`
- exploreMaintainY 1014 `64` ("-1 to disable")
- mineGoalUpdateInterval 955 `5`
- maxCachedWorldScanCount 960 `10`
- mineMaxOreLocationsCount 967 `64`
- minYLevelWhileMining 973 `0`
- maxYLevelWhileMining 978 `2031`
- legitMine 1231 `false`
- legitMineYLevel 1236 `-59`
- forceInternalMining 1256 `true`
- mineScanDroppedItems 1185 `true`
- mineDropLoiterDurationMSThanksLouca 1193 `250L`

### Mining and builder settings from the requested list (not path costs)
| Line | Setting | Default | Javadoc |
|---|---|---|---|
| 983 | allowOnlyExposedOres | `false` | "This will only allow baritone to mine exposed ores, can be used to stop ore obfuscators on servers that use them." |
| 990 | allowOnlyExposedOresDistance | `1` | "When allowOnlyExposedOres is enabled this is the distance around to search. / It is recommended to keep this value low, as it dramatically increases calculation times." |
| 271-273 | buildIgnoreBlocks | `[]` | "A list of blocks to be treated as if they're air. / If a schematic asks for air at a certain position, and that position currently contains a block on this list, it will be treated as correct." |
| 314-316 | okIfAir | `[]` | "A list of blocks to become air / If a schematic asks for a block on this list, only air will be accepted at that location (and nothing on buildIgnoreBlocks)" |
| 1116 | okIfWater | `false` | "Override builder's behavior to not attempt to correct blocks that are currently water" |
| 1099 | breakFromAbove | `false` | "Allow standing above a block while mining it, in BuilderProcess / Experimental" |
| 1106 | goalBreakFromAbove | `false` | "As well as breaking from above, set a goal to up and to the side of all blocks to break. / Never turn this on without also turning on breakFromAbove." |

Other builder settings, all schematic-only: buildSkipBlocks 280, buildValidSubstitutes 291, buildSubstitutes 307, buildIgnoreExisting 321, buildIgnoreDirection 326, buildIgnoreProperties 331, buildInLayers 1047, layerOrder 1054, layerHeight 1059, startAtLayer 1065, skipFailedLayers 1070, buildOnlySelection 1075, buildRepeat 1080, buildRepeatCount 1085, buildRepeatSneaky 1092, mapArtMode 1111, incorrectSize 1121, schematicOrientationX/Y/Z 1136-1146, buildSchematicRotation 1158, buildSchematicMirror 1168, schematicFallbackExtension 1174, builderTickScanRadius 1180, distanceTrim 1198.

### Elytra (separate nether-pathfinder system)
Defaults only:
- elytraSimulationTicks 1479 `20`
- elytraPitchRange 1485 `25`
- elytraFireworkSpeed 1490 `1.2`
- elytraFireworkSetbackUseDelay 1496 `15`
- elytraMinimumAvoidance 1504 `0.2`
- elytraConserveFireworks 1509 `false`
- elytraAutoJump 1530 `false`
- elytraNetherSeed 1536 `146008555100680L`
- elytraPredictTerrain 1542 `false`
- elytraAutoSwap 1547 `true`
- elytraMinimumDurability 1552 `5`
- elytraMinFireworksBeforeLanding 1557 `5`
- elytraAllowEmergencyLand 1562 `true`
- elytraTimeBetweenCacheCullSecs 1567 `TimeUnit.MINUTES.toSeconds(3)`
- elytraCacheCullDistance 1572 `5000`
- elytraAllowLandOnNetherFortress 1577 `false`
- elytraTermsAccepted 1582 `false`
- elytraAllowTightSpaces 1593 `false`
- elytraAllowAboveRoof 1598 `false`
- elytraUseCache 1603 `true`
- elytraAllowAboveBuildLimit 1608 `true`
- elytraLongDistanceThreshold 1613 `500`

### Render-only settings (noted, not detailed)
- renderPath 719 `true`
- renderPathAsLine 724 `false`
- renderGoal 729
- renderGoalAnimated 735
- renderSelectionBoxes 740
- renderGoalIgnoreDepth 745
- renderGoalXZBeacon 751
- renderSelectionBoxesIgnoreDepth 756
- renderPathIgnoreDepth 761
- **pathRenderLineWidthPixels 766 `5F`**
- goalRenderLineWidthPixels 771 `3F`
- fadePath 777 `false`
- renderCachedChunks 867
- cachedChunksOpacity 873
- yLevelBoxSize 1353
- color* 1358-1413
- selection* 1418-1438
- elytraRender* 1514-1525

---

# PART B: mineflayer-pathfinder

Files (absolute paths):
- **MV** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\movements.js`
- **IX** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\index.js`
- **PH** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\physics.js`
- **AS** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\astar.js`
- **GT** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\goto.js`
- **MO** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\move.js`
- **GL** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\goals.js`
- **HU** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\lib\human.js`
- **RM** = `C:\GitHub\.claude\skills\ftue\java\mineflayer-pathfinder\readme.md`

## B1. Movements class: every configurable field (constructor MV:19-115)

| Line | Field | Default | Meaning and where used |
|---|---|---|---|
| 23 | canDig | `true` | `safeToBreak` returns false when off (MV:257) |
| 24 | digCost | `1` | Multiplier: `laborCost = (1 + 3*digTime/1000) * digCost` (MV:300); digTime is in ms with the best tool, enchants and effects |
| 25 | placeCost | `1` | Added per placed block in jumpUp/forward/up (MV:337, 347, 388, 558) |
| 26 | liquidCost | `1` | Added if the current node is liquid, in forward and diagonal moves (MV:402, 453) |
| 27 | entityCost | `1` | Multiplied by the entity-intersection count at cells walked or landed in (MV:289, 294, 466, 507, 527, 578, 596, 608, 617) |
| 29 | dontCreateFlow | `true` | Do not break a block if the block above or any of 4 horizontal neighbours is liquid (MV:261-268) |
| 30 | dontMineUnderFallingBlock | `true` | Do not break if the block above `canFall` (gravityBlocks) or an entity is above (MV:270-275) |
| 31 | allow1by1towers | `true` | Gates pillaring in `getMoveUp` (MV:546) |
| 32 | allowFreeMotion | `false` | Executor straight-line follow for entity goals (IX:431) |
| 33 | allowParkour | `true` | Gates `getMoveParkourForward` in `getNeighbors` (MV:654-656) |
| 34 | allowSprinting | `true` | Parkour max gap `maxD = allowSprinting ? 4 : 2` (MV:586); executor sprint branches (IX:624, 627) |
| 35 | allowEntityDetection | `true` | `getNumEntitiesAt` returns 0 when false (MV:213); collision index built per path (IX:86-91) |
| 37 | entitiesToAvoid | `new Set()` | Entity names weighted 100 per cell instead of 1 (MV:180, 190) |
| 38 | passableEntities | `Set(lib/passableEntities.json)` | 28 names (falling_block, tnt, item, arrow, experience_orb, …) ignored by the collision index (MV:181) |
| 39 | interactableBlocks | `Set(lib/interactable.json)` | 134 block names. Placing against these uses sneak (IX:563-565); also the source for `openable` |
| 41-47 | blocksCantBreak | `Set{chest.id}` ∪ every `!block.diggable` | Checked in `safeToBreak` (MV:277) |
| 49-53 | blocksToAvoid | `{fire, cobweb (or legacy web), lava}` | Makes `block.safe` false (MV:237) |
| 55-61 | liquids | `{water, lava}` ∪ `{seagrass, tall_seagrass, kelp, kelp_plant, bubble_column}` if present | `block.liquid` = in set or `isWaterlogged === true` (MV:240) |
| 63-65 | gravityBlocks | `{sand, gravel}` | `block.canFall` (MV:242) |
| 67-69 | climbables | `{ladder}` (vine commented out at MV:69) | Makes the block `safe` (MV:237) and lets `getMoveUp` climb without placing (MV:545). The readme (RM:301) says "Currently unused", but the code does use it. |
| 70, 94-96 | emptyBlocks | Blocks whose `shapes.length === 0` | Used in IX:83 for the start-node y offset |
| 72-77 | replaceables | `{air, cave_air?, void_air?, water, lava}` | `block.replaceable` = in set and not physical (MV:239) |
| 79-81 | **scafoldingBlocks** (one 'f') | `[dirt item id, cobblestone item id]` | Counted for `remainingBlocks` (MV:144-154, IX:84); used for placement (IX:543) |
| 84, 91 | fences | Blocks with `shapes[0][4] > 1` (taller than 1) | Treated as non-physical so they are not walked on (MV:238) |
| 85, 93 | carpets | Blocks with `shapes[0][4] < 0.1` | Count as `safe` to walk in (MV:237) |
| 86, 98-103 | openable | interactableBlocks whose name contains "gate" and not "iron" | Fence gates only (MV:243) |
| 105 | canOpenDoors | `false` | Comment: "Causes issues. Probably due to none paper servers." When true, forward moves through an openable push a `useOne` toPlace entry (MV:395-396) |
| 107 | exclusionAreasStep | `[]` | `(block)=>number` functions summed into step cost (MV:126-133, used at 288, 371, 599, 606, 616). Non-empty also disables path shortcutting (IX:202) |
| 108 | exclusionAreasBreak | `[]` | Summed (MV:135-142). `safeToBreak` requires `< 100` (MV:277); also added at MV:332, 342, 383 |
| 109 | exclusionAreasPlace | `[]` | Summed (MV:117-124), added at MV:335, 345, 386, 556 |
| 111 | maxDropDown | `4` | `getLandingBlock`: a physical landing is allowed only if `node.y - landY <= maxDropDown` (MV:476) |
| 112 | infiniteLiquidDropdownDistance | `true` | If false, drops whose landing is more than maxDropDown below are rejected (MV:496). Physical landings are already capped in `getLandingBlock`, so this effectively limits only liquid landings, which `getLandingBlock` returns at any depth (MV:474). |
| 114 | entityIntersections | `{}` | `'x,y,z' -> weight` index, rebuilt by `updateCollisionIndex` (MV:176-202) and cleared by `clearCollisionIndex` (MV:167) |

**Block classification, `getBlock` (MV:222-249):**
- `safe = (boundingBox==='empty' || climbable || carpet) && !blocksToAvoid`
- `physical = boundingBox==='block' && !fence`
- `height` = max shape top
- An unloaded block returns all-false (MV:224-234).

**Break cost, `safeOrBreak` (MV:286-303):** returns 100 (impassable) if the block is not safe and cannot be broken. The A* prunes any move whose cost exceeds 100 (`if (cost > 100) return` throughout).

**Move base costs and rules:**
- forward `1` (MV:370)
- jumpUp `2` (MV:311). Max step height `1.2` (MV:353, 457, 607).
- diagonal `Math.SQRT2` (MV:408). There is no diagonal jump-up: `if (y === 1) return` (MV:415). Both side cells must be `safe` (MV:424, 434).
- dropDown `1` (MV:490). Never drops into a liquid at feet level (MV:505).
- down `1` (MV:515)
- up `1` + placeCost (MV:539). It cannot jump-place from a half block (MV:554). The toPlace entry has `jump: true` (MV:557).
- parkour `1` (MV:575)

**Parkour (MV:567-627):** checks gaps `d = 2..maxD`. Outcomes are forward (same y), up (+1, never at d=4, MV:605) or down (−1). The Move is created with `parkour=true` (MO:4,10). The feasibility check is **purely geometric** (ceiling/floor clearance); physics.js is not called at planning time. `Move.parkour` is never read by the executor (grep shows no consumer outside move.js and movements.js).

**Neighbour order, `getNeighbors` (MV:645-669):** for each of 4 cardinal directions: forward, jumpUp, dropDown, parkour (if allowParkour). Then 4 diagonals, then down, then up.

## B2. index.js: pathfinder settings and constants

| Line | Property | Default | Notes |
|---|---|---|---|
| IX:38 | `bot.pathfinder.thinkTimeout` | `5000` ms | Total A* budget (AS:73) |
| IX:39 | `bot.pathfinder.tickTimeout` | `40` ms | "amount of thinking per tick (max 50 ms)". AS:34 also defaults to 40. The readme (RM:96) wrongly says 50 ms. |
| IX:40 | `bot.pathfinder.searchRadius` | `-1` | "-1: don't limit the search". In A*, `maxCost = startNode.h + searchRadius` (AS:51); nodes with `g+h > maxCost` are pruned (AS:95). |
| IX:41 | `bot.pathfinder.enablePathShortcut` | `false` | "disabled by default as it can cause bugs in specific configurations". Not documented in the readme. |
| IX:42 | `bot.pathfinder.LOSWhenPlacingBlocks` | `true` | Enables `moveToEdge`/`returningPos` behaviour when placing. Not documented in the readme. |

**`getPathFromTo` options (IX:69-101):**
- `optimizePath` defaults to `true`
- `resetEntityIntersects` defaults to `true`
- `timeout`, `tickTimeout`, `searchRadius` and `startMove` are also accepted.

The start node is the floored position, +1 y when standing on a non-full solid block (IX:79-84), and `remainingBlocks = countScaffoldingItems()`. It is a generator that keeps calling `compute()` while the status is `'partial'` (IX:96-100). `getPathTo` (IX:62-67) takes only the first yield and stores `astarContext`.

**`path.status` values (from AS):**
- **`'partial'`**: this `compute()` call exceeded `tickTimeout` (AS:70-71). Returns the path to `bestNode`, the lowest-h node (AS:112). Executor effect: sets `astartTimedout = true` (IX:458, 479). Each later physicsTick resumes `astarContext.compute()`, runs postProcessPath and pathFromPlayer (trims passed nodes), emits `path_update` and replaces `path` (IX:452-459). The bot walks the partial path meanwhile.
- **`'timeout'`**: total time exceeded `thinkTimeout` (AS:73-74). Returns the best-node path. `astartTimedout` becomes false. goto() rejects with `Timeout` "Took to long to decide path to goal!" (GT:27-28).
- **`'success'`**: goal `isEnd` popped (AS:77-78). goto() resolves immediately if the path is empty (GT:29-30), otherwise on `goal_reached`.
- **`'noPath'`**: open heap exhausted (AS:121). Returns the best-node path. goto() rejects with `NoPath` (GT:25-26).

**Re-planning rules (`monitorMovement`, which runs on every `physicsTick`, IX:168):**
- **Initial plan:** when `path.length === 0`, a goal is set, the bot is not at the goal and `!pathUpdated`, it calls `getPathTo` and sets `pathUpdated = true` (IX:466-482).
- **When `pathUpdated` resets:** only `resetPath()` clears it (IX:131). So if a non-success (timeout or noPath best-node) path is walked to its end without reaching the goal, the bot does not re-plan until some reset reason fires.
- **Stuck detection** is skipped while `path` is empty (it sits after `if (path.length === 0) return`, IX:485-487).

**Reset triggers and their `path_reset` reasons:**
- `path_reset` is emitted only if `!stopPathing && path.length > 0` (IX:123). `resetPath` stops digging, releases locks, clears the collision index, clears controls unless `clearStates=false`, and calls `stop()` if `stopPathing` (IX:122-139).
- `goal_updated`: `setGoal` (IX:141-146)
- `movements_updated`: `setMovements` (IX:148-151)
- `block_updated` (clearStates=false): a block type changed near the path (IX:408-413). "Near" (`isPositionNearPath`, IX:247-293) means within dx ≤ 1, dy ≤ 2, dz ≤ 1 of a node, or of the segment for shortcut paths.
- `chunk_loaded` (clearStates=false): a loaded chunk is 4-adjacent to an A*-visited chunk (IX:415-427)
- `goal_moved` (clearStates=false): `stateGoal.hasChanged()` (IX:447-448). For GoalFollow, this is the entity moving outside `rangeSq` (GL:356-367).
- `stop()` is called when `!stateGoal.isValid()` (IX:445-446).
- `dig_error` (IX:504), `no_scaffolding_blocks` (IX:545), `place_error` (IX:573)
- **`stuck`**: `performance.now() - lastNodeTime > 3500` ms (IX:641-645). `lastNodeTime` is refreshed when the path is empty (IX:467), after a dig completes (IX:507), after a place completes (IX:578) and on node arrival (IX:591).

**`stop()`:** sets `stopPathing` (IX:161-166). It takes effect at the next node arrival (IX:592-595) or inside `resetPath` (IX:138). It emits `path_stop` and calls `fullStop()` (IX:400-406).

**`fullStop()` (IX:340-355):** clears controls, zeroes horizontal velocity, and snaps x/z to the block centre if more than 0.2 off.

**Free motion (IX:431-443):** applies only if `allowFreeMotion` is on and the goal has `.entity` (GoalFollow). If `physics.canStraightLine([target.position])` passes, it looks at target+1.6 y and holds forward while `distSq > rangeSq`, else clears controls. It returns early and skips normal path following.

**Return-to-centre after placing (IX:461-464):** when LOSWhenPlacingBlocks is on and `returningPos` is set, `moveToBlock` walks back until within 0.2 of the block centre (IX:387-398).

**Digging state machine (IX:493-521):**
1. Triggered if `digging` or `nextPoint.toBreak.length > 0`. It starts only when `!digging && onGround`.
2. Sets `digging = true`, shifts one `toBreak` entry, picks `bestHarvestTool` (IX:44-60, minimum digTime) and calls `fullStop()`.
3. Equips the tool if one exists, then `bot.dig(block, true)`. On error: `resetPath('dig_error')`. Then it refreshes `lastNodeTime` and sets `digging = false`.
4. It returns every tick while digging, so no movement happens.

**Placing state machine (IX:524-584):**
1. On first entry: `placing = true`, `placingBlock = toPlace.shift()`, `fullStop()`.
2. `useOne` entries (gate open): `activateBlock` under `lockUseBlock`, then shift the next entry (IX:532-542).
3. Gets a scaffolding item, or `resetPath('no_scaffolding_blocks')`.
4. If LOSWhenPlacingBlocks is on and the block goes at foot−1 with `dy === 0` (a bridge), `moveToEdge` runs: sneak + back, look pitch −1.421 rad away from the edge, until within 0.4 of the edge point (IX:357-385, 548-550).
5. For `jump` entries (pillar): hold jump and place only when `placingBlock.y + 1 < bot y` (IX:552-555).
6. Equip under `lockEquipItem`. Sneak if the reference block is interactable. `placeBlock(refBlock, (dx,dy,dz))` under `lockPlaceBlock`. On success: un-sneak and set `returningPos` if the entry has `returnPos`. On error: `resetPath('place_error')`. Finally `placing = false` and `lastNodeTime` is refreshed (IX:556-582).

**Node arrival and steering (IX:586-639):**
- Arrival means `|dx| ≤ 0.35`, `|dz| ≤ 0.35`, `|dy| < 1` (IX:589). On arrival it shifts the path. If that empties the path it emits `goal_reached` (non-dynamic goals, checking floored pos or pos+1), then `fullStop`. If the next node needs dig or place, it calls `fullStop` and returns (IX:609-612).
- Steering: `bot.look(atan2(-dx,-dz), 0)`, forward = true, jump = false (IX:617-619).

**Sprint and jump rule (IX:621-639):** the first true branch wins.
1. `bot.entity.isInWater` → jump on, sprint off
2. `allowSprinting && physics.canStraightLine(path, true)` → sprint, no jump
3. `allowSprinting && physics.canSprintJump(path)` → sprint + jump
4. `physics.canStraightLine(path)` → walk
5. `physics.canWalkJump(path)` → walk + jump
6. otherwise → forward off, sprint off

**`postProcessPath` (IX:170-215):**
- Snaps each node to a standing position using `getPositionOnTopOf` (the average centre of the highest collision shapes, IX:314-334).
- Stops at the first node with toBreak or non-`useOne` toPlace.
- Liquid, doorway (openable) and descending-ladder/vine nodes are centred at the floor (IX:183-188).
- If `enablePathShortcut` is on and there are no step exclusions, it drops intermediate nodes when `|Δy| ≤ 0.5`, there is no dig/place, and `physics.canStraightLineBetween(lastNode, node)` passes (IX:202-214).

**`pathFromPlayer` (IX:217-245):** drops path nodes already passed; the nearest node before any dig/place, advanced by one if between nodes or already reached.

**Goal heuristics (no cost-heuristic multiplier):**
- GoalBlock: `distanceXZ + |dy|` (GL:38-43)
- `distanceXZ = |dx−dz| + min(dx,dz)·√2` (GL:375-379)
- GoalNear/GoalFollow: `isEnd` is `dist² ≤ rangeSq` (GL:67-71, 349-354)

## B3. lib/physics.js: what it simulates and how it is used

- **Constructor (PH:3-7):** world adapter `getBlock: pos => bot.blockAt(pos, false)`.
- **`simulateUntil(goal, controller=()=>{}, ticks=1, state=null)` (PH:17-39):** this is pathfinder's own loop, not a prismarine-physics `simulateUntil`. It builds a `prismarine-physics` `PlayerState` from a copy of the bot's current controlState (forward/back/left/right/jump/sprint/sneak). Each tick it calls `controller(state, i)`, then `bot.physics.simulatePlayer(state, world)`. It returns early if `state.isInLava` or `goal(state)`, otherwise the final state.
- **`getController(nextPoint, jump, sprint, jumpAfter=0)` (PH:110-120):** each simulated tick sets yaw = `atan2(-dx, -dz)` toward the point, `forward = true`, `jump = jump && tick >= jumpAfter`, `sprint = sprint`.
- **`getReached(path)` (PH:103-108):** within 0.35 in x and z and less than 1 in y of `path[0]`, the same tolerance as executor arrival.
- **`canStraightLine(path, sprint=false)` (PH:49-68):**
  - Simulates up to **200 ticks** of no-jump walking or sprinting toward `path[0]`. If reached, it returns true.
  - Otherwise, if a jump at tick 0 would reach the node (`canSprintJump(path,0)` / `canWalkJump(path,0)`), it returns **false**, so the executor falls through to the jump branch.
  - Otherwise, if a delayed jump at tick i = 1..6 reaches the node, it returns **true** (keep walking now, jump later).
  - Otherwise it returns false.
- **`canSprintJump(path, jumpAfter=0)` / `canWalkJump` (PH:91-101):** 20 ticks with jump held (from `jumpAfter`), with or without sprint. They return `reached(state)`.
- **`canStraightLineBetween(n1, n2)` (PH:70-89):** places the simulated player at n1 and sprints toward n2 for `floor(5 × distance)` ticks. Reached means within 0.15 xz radius, `|dy| < 0.001`, and onGround or inWater. It is used only for path shortcutting (IX:208).
- **`simulateUntilNextTick()` (PH:41-43) and `simulateUntilOnGround(ticks=5)` (PH:45-47):** defined but not used by index.js (only the test at `test\internalTest.js:743-767` exercises `simulateUntil`).

**Relationship to movements.js:** movements.js does **not** import physics.js. Parkour, jump-up and drop feasibility at planning time are decided by block geometry only (MV:567-627, step limit 1.2, maxDropDown). The physics simulation is used only at execution time, every tick:
- picking walk / sprint / jump controls (IX:624-634)
- free-motion line-of-travel checks (IX:433)
- the optional path-shortcut pass (IX:208)

A parkour gap therefore gets jumped because the straight-line simulation fails (the bot would fall) while the 20-tick sprint-jump or walk-jump simulation lands within 0.35/0.35/1 of the next node.

## B4. Extra in this checkout: `createHuman` (lib/human.js, 569 lines)

Documented at RM:146-191. It is an alternative pure-pursuit walking controller that uses the pathfinder only for planning (`getPathFromTo` sliced per tick, HU:129).

**Default Movements for it (HU:90-101):**
- `canDig = false`
- `allow1by1towers = false`
- `allowParkour = false`
- `allowSprinting = true`
- `scafoldingBlocks = []`
- `maxDropDown = 4` (the comment calls this "a three-block fall: the deepest that costs no health")

**Own GoalBlock (HU:104-119):** `isEnd` matches the goal's block column within ±1 y. The heuristic is `hypot + |dy|`.

**Setback guard defaults (HU:61):** `{ trip: 3, window: 2500, hold: 8000, quiet: 3000 }`.

**Other constants:**
- Stuck handling: no progress for more than 1500 ms triggers a jump retry, at most every 1200 ms, before failing with `'stuck'` (HU:353-359).
- walkTo timeout is `60000` ms (HU:468).
- `SENS = 0.15°` rotation quantisation (HU:18).

**Personality ranges (HU:36-46):**
- basePitch: 6 ± 5° gaussian, clamped to −5..16°
- turnTime: 0.75-1.3
- sprints: 80% chance
- sprintDelay: 0 (25% of the time) or 0.1-0.8 s
- jumpRate: 0 (40%) or 0.3-1.1 per second
- lookAhead: 1.8-3.0 blocks
- stopRadius: 0.25-0.5
- reaction: max(0.08, 0.25 + 0.1·gauss) s
- glanceRate: 0 (35%) or 1/(5-12) per second
- strafe: 0 (50%) or 0.2-0.6
- deadband: 4-12°

## Discrepancies worth knowing
1. Baritone `allowJumpAt256` does nothing (S:190-195). Use `allowJumpAtBuildLimit` (S:188).
2. Baritone has no `cutoffAtLoadedChunks`. It is `cutoffAtLoadBoundary` (S:534).
3. Baritone `costHeuristic` is read only by `GoalXZ` and `GoalAxis` (per grep), not by the other goal types.
4. mineflayer readme says the `tickTimeout` default is 50 ms (RM:96), but the code uses 40 (IX:39, AS:34).
5. mineflayer readme says `climbables` is unused (RM:301), but the code uses it (MV:237, 545).
6. mineflayer field name is `scafoldingBlocks` (typo is canonical, MV:79).
7. `enablePathShortcut` and `LOSWhenPlacingBlocks` are not in the readme's Properties section.
8. mineflayer `Move.parkour` is set but never consumed by the executor.
9. After a non-success path ends short of the goal, mineflayer does not re-plan on its own until a `resetPath` reason fires, because `pathUpdated` is cleared only in `resetPath` (IX:131) and stuck detection is skipped on an empty path (IX:485).
