# baritone-bedrock: the plan

`C:\GitHub\baritone-bedrock` (GitHub `kirbycope/baritone-bedrock`): a port of Baritone's pathing
(cabaletta/baritone, `c55a9c5`, October 2026) for Minecraft Bedrock, as its own repository. It drives the real
Bedrock client through whatever controller the caller hands it (the FTUE runner's emulated Xbox pad) and reads the
world through its own behavior pack, the probe. It knows nothing about Cobblemon or the FTUE: the FTUE skill is
its first consumer. Where Baritone's execution model cannot carry over (it sets the player's rotation and keys
directly, we have a rate-limited right stick and a 50 ms pad round trip), mineflayer-pathfinder's `createHuman`
controller (PrismarineJS/mineflayer-pathfinder, `b07a419`) is the model: pure pursuit, a steering deadband,
coasting stops.

Licence: LGPL-3.0, because it is a derivative of Baritone, which is LGPL-3.0. `CREDITS.md` names Baritone (the
planner, the executor, the costs), mineflayer-pathfinder (MIT: the human controller model and the parkour
geometry) and prismarine-physics (MIT: the physics constants checked against).

The sources are cloned under `java/` (git-ignored, per the ports rule): `java/baritone`, `java/mineflayer-pathfinder`,
`java/prismarine-physics`, `java/mineflayer`. The two spec sheets the build works from are
`docs/research-movements.md` (every movement's checks, costs and per-tick behaviour, with file:line) and
`docs/research-settings.md` (every setting with its default and javadoc, plus mineflayer-pathfinder's).

## Why the current navigator is slow

`nav.py` is a grid A* with a 40 block window that returns the node nearest the goal when the goal is outside the
window, walks two thirds of it, and plans again. Measured on the gym tour: 200 blocks took 4 to 8 minutes, the
whole tour 33 to 40 minutes, against about 36 s per 200 blocks at sprint speed. The causes, all of which Baritone
solved:

1. **No cost model.** Every step costs 1, a jump 2, water 8. Baritone costs every move in ticks from the game's
   own physics (`ActionCosts`: walk 4.633 ticks a block, sprint 3.564, jump 1 block 3.163 extra, a 3 block fall
   about 9 ticks with the walk off), so the planner prefers a sprintable straight over a staircase on its own.
2. **"Nearest to the goal" as the partial path.** Baritone keeps seven best-so-far candidates by
   `estimatedCostToGoal + cost / {1.5, 2, 2.5, 3, 4, 5, 10}` and takes the first that gets more than 5 blocks from
   the start (`AbstractNodeCostSearch.bestSoFar`). Ours took the node with the smallest heuristic, which on a
   hill is a dead end against the slope, twice over (the two walls).
3. **No memory between segments.** Baritone's `Favoring` halves the cost of every position on the previous path
   (`backtrackCostFavoringCoefficient 0.5`), and `PathingBehavior` plans the next segment from the current
   segment's end while it is walked, splicing it on (`splicePath`). Ours planned from wherever the player stood,
   with the result discarded if the player had drifted a block.
4. **Open-loop execution.** `follow` steers at a look-ahead point and calls a move stuck after 2.5 s. Baritone's
   `PathExecutor` checks every tick that the feet are in the current movement's valid positions, skips forward
   or back along the path when they are not, re-costs the next five movements, and times a movement out at its
   own cost estimate plus 100 ticks.
5. **No sprint policy, no block interaction.** Sprinting was a click on a six-step straight. Baritone sprints by
   default and decides per movement pair (traverse into ascend jumps a block early, descend into traverse keeps
   running). It breaks blocks in the way, pillars and bridges with throwaway blocks, and digs down.

## What moves from the FTUE skill, and what stays there

Moves into baritone-bedrock, generalised: the probe pack (`probe_pack/`, which becomes the repository's `pack/`),
`Probe` (the HTTP end in Python), `ProbeTerrain`, and `Navigator` with its `go`, `look_at`, `where`, `say` and
`log` API. Stays in the FTUE skill: the pad and pad host (the controller is the caller's), the bridge, `in_world`,
`stand_up`, the playbook and its steps. The skill's `nav.py` becomes a shim that imports `baritone_bedrock`, and
`prepare_world` copies the pack from the repository instead of from the skill. `walk_legs` in the playbook goes
away: `go` takes the real goal.

## Architecture

```
C:\GitHub\baritone-bedrock\
  README.md              what it is, how a caller wires a controller and a world, the API, the settings
  CREDITS.md             Baritone (LGPL-3.0), mineflayer-pathfinder (MIT), prismarine-physics (MIT)
  LICENSE                LGPL-3.0
  pyproject.toml         package baritone_bedrock, no dependencies beyond the standard library
  .github/workflows/tests.yml   the unit tests on push and pull request
  baritone_bedrock/
    __init__.py          Navigator, Probe, goals, Settings (the public names)
    controller.py        the Controller protocol a caller implements: state(left, right, held), press(...)
    probe.py             Probe: /tick and /chunk server, events queue, inventory and held item, commands out
    world.py             Terrain: palette bands from the probe decoded to per-block flag bytes; the block
                         classification table (Bedrock typeId and states -> flags, hardness, tool class)
    costs.py             ActionCosts, read from calibration.json with Baritone's numbers as defaults
    settings.py          Baritone's setting names and defaults
    goals.py             GoalXZ, GoalBlock, GoalNear, GoalGetToBlock, GoalComposite (Baritone's semantics)
    moves.py             the movement set: cost functions for the planner, one class per movement
    astar.py             AStarPathFinder: binary heap, best-so-far, timeouts, Favoring, loaded-chunk fetch limit
    path.py              Path, CutoffPath, SplicedPath, staticCutoff, cutoffAtLoadedChunks
    executor.py          PathExecutor (per tick) and PathingBehavior (segments, plan ahead, splice)
    control.py           the controller model: steering (pure pursuit, deadband), jump, sneak, sprint, use,
                         attack, hotbar, with the measured latencies and turn rates
    sprintjump.py        phase 3: the SprintJump simulation on Bedrock numbers
    calibrate.py         measures the numbers costs.py and control.py read (see Calibration)
    course.py            builds the obstacle course and runs the navigator over it (see Proving it)
  pack/                  the probe behavior pack (manifest.json, scripts/main.js), added to a world beside the
                         game's packs; the only thing of ours that runs inside the game
  tests/                 unittest: synthetic worlds for every movement rule, A* on fixtures, path splicing
  docs/                  this plan and the two research sheets
  java/                  the upstream clones, git-ignored
```

The caller supplies two things: a `Controller` (the FTUE's `Pad` already has `state` and `press`) and a way to
get the pack into the world (the FTUE's `prepare_world`). The probe answers each `/tick` with `{scan, commands}`,
so the calibration rig and the obstacle course are built by commands the pack runs, and the repository needs
nothing else from the host: no bridge, no MCP server.

Everything the planner asks of the world is a bit test on a flag byte, the way `PrecomputedData` makes
Baritone's predicates a table lookup per block state.

## The probe, version 2

The pack keeps posting `/tick` every tick and answering with the chunks wanted. Changes:

1. **Palette bands.** A band is `{dim, cx, cz, y0, y1, palette: ["minecraft:stone", "minecraft:oak_slab|top",
   ...], data}` where `data` has one character per block indexing the palette (`chr(33 + i)`); a band with more
   than 90 distinct entries is sent as two bands of half the height. A palette entry is the typeId plus the states
   that change how a block is walked: `vertical_half` (slabs), `upside_down_bit` (stairs), `height` (snow
   layers), `open_bit` (doors, trapdoors, gates), `liquid_depth` (flowing water), `waterlogged`. The runner
   classifies entries, so every block type is known exactly and a new block only ever needs a Python table row.
2. **Player fields.** Added to each player report: `sneak`, `sprint`, `jump` (`isSneaking`, `isSprinting`,
   `isJumping`), `climbing`, `swimming`, `falling`, `fall` (`fallDistance`), `slot` (`selectedSlotIndex`),
   `hand` (the held item's typeId and amount), `hotbar` (the nine slots' typeIds and amounts), `input`
   (`inputInfo.getMovementVector()` and the Jump and Sneak button states), `air`, `health`, `food`.
3. **Events.** `world.afterEvents.playerBreakBlock` and `playerPlaceBlock` (position, typeId) and
   `entityHurt` on the player (cause, damage) are queued and sent with the next `/tick`, so the executor knows the
   tick a block went or appeared and when the player took damage, without a rescan.
4. **Block updates.** The pack re-sends a band's palette index for one block when a break or place event changes
   it, so the runner's terrain stays current along the path without re-reading the band.

Nothing in the pack moves the player or touches the world: the controller stays the only actuator.

## Terrain classification (world.py)

A flag byte per block, computed once per palette entry:

| bit | meaning | Baritone predicate it answers |
| --- | --- | --- |
| WALK_ON | a full-height solid top you can stand on | `canWalkOn` |
| WALK_THROUGH | the player's body can be in it | `canWalkThrough` |
| PASSABLE | nothing to collide with at all | `fullyPassable` |
| PLACE_AGAINST | a face a block can be placed against | `canPlaceAgainst` |
| WATER, LAVA, FLOWING | liquids, and whether the water is flowing | `isWater`, `isLava`, `possiblyFlowing` |
| DANGER | fire, campfire, cactus, magma, sweet berry bush, powder snow, wither rose, dripstone | `avoidWalkingInto` |
| TALL | fences, walls, closed gates: 1.5 high, not walkable over or through | the fence checks |
| CLIMB | ladders and vines | ladder costs |
| BOTTOM_SLAB | a bottom slab or an upright stair: walkable up onto without a jump | `isBottomSlab` |
| GRAVITY | sand, gravel, concrete powder: never mined under, never mined when above another | `avoidBreaking` |
| OPENABLE | wooden doors, trapdoors, fence gates | door handling (phase 3) |

Plus per entry: `hardness` and `tool` (which tool class digs it fast) for the break cost, and `BREAKABLE`, which
is true only for the natural terrain whitelist (dirt, grass, sand, gravel, stone and its variants, deepslate,
logs, leaves, snow, clay, netherrack, soul sand, end stone, mushrooms, ice is excluded as Baritone excludes it).
Everything else is never broken: gyms, the Trainer Association, villages and anything a player built stay as
they are, which is stricter than Baritone's `blocksToAvoidBreaking` and the right default for a test runner.

Snow layers keep the rule already in the probe: height 3 or more is WALK_ON, less is WALK_THROUGH.

Unknown blocks: Baritone reads an unloaded chunk as air and skips any move whose destination chunk is not loaded.
We have no "air" fallback to lean on, so a `?` block fails every predicate, a move into one is skipped and counted
toward `pathingMaxChunkBorderFetch`, and a band holding `?` is re-read after four seconds as now.

## Costs (costs.py)

Baritone's `ActionCosts` ported as written, every value derived from a speed or the fall formula
(`distanceToTicks`), with the speeds coming from `calibration.json` when it exists:

- `WALK_ONE_BLOCK_COST = 20 / walk_speed` (4.317 blocks a second by default)
- `SPRINT_ONE_BLOCK_COST = 20 / sprint_speed` (5.612)
- `WALK_ONE_IN_WATER_COST`, `SWIM_ONE_BLOCK_COST`, `SNEAK_ONE_BLOCK_COST`, `LADDER_UP/DOWN`, `WALK_OFF_BLOCK_COST`,
  `CENTER_AFTER_FALL_COST`, `FALL_N_BLOCKS_COST[n]`, `JUMP_ONE_BLOCK_COST`, `COST_INF = 1000000`
- the penalties as settings with Baritone's defaults: `blockPlacementPenalty 20`, `blockBreakAdditionalPenalty 2`,
  `jumpPenalty 2`, `walkOnWaterOnePenalty 3`, `backtrackCostFavoringCoefficient 0.5`, `costHeuristic 3.563`
- break cost: Baritone's `getMiningDurationTicks` (hardness, best tool in the hotbar, the correct-tool and
  wrong-tool multipliers, plus the additional penalty), with the hardness table in world.py. Bedrock's break
  formula is the Java one for vanilla blocks.

Water is costed as Baritone meant, not as it is written: `waterSpeedMultiplier` returns 1.0 without Depth Strider,
so Baritone's water costs the same as land (research-movements.md, quirk 1). Ours is `WALK_ONE_IN_WATER_COST`
(9.09 a block) until the calibration measures swimming, since the player's swim is what the executor does there.

No turn cost: a heading change would need the heading in the search state, which Baritone does not carry, and
its heuristic already keeps paths straight. The camera's turn rate is the executor's problem (pursuit look-ahead).

## The movement set (moves.py)

Baritone's `Moves` enum in full: Downward, Pillar, Traverse x4, Ascend x4, Descend/Fall x4 (one move, the
result decides), Diagonal x4, Parkour x4. Each movement has `cost(world, x, y, z) -> (dest, cost)` on the planner
side, ported line for line from `docs/research-movements.md`, and `tick(state) -> status` on the executor side,
written for the controller:

| movement | planner (Baritone's rules) | executor on a controller |
| --- | --- | --- |
| Traverse | dest feet and head walkable, floor walkable, else place the floor (sneak back-place) or break what is in the way; water, soul sand, ladder cases | pursuit toward dest centre, sprint when the policy says; with blocks to break: `look_at` the block (the probe's crosshair answer), RT held until the break event, then walk; with a floor to place: B held, face the src floor's far face with the probe's `block.face`, LT, then walk |
| Diagonal | both side cells walkable, no danger beside, no ascend (Baritone forbids diagonal ascend by default and we keep that), descend variant off | pursuit as traverse, with the look-ahead clipped at the corner |
| Ascend | dest floor walkable or placeable, three blocks of head room, jump cost plus `jumpPenalty` | pursuit, A tapped when the edge is within the calibrated jump-in distance and the heading error is under 15 degrees; with a traverse before it and `sprintAscends`, the jump a block early at sprint |
| Descend | one down, floor walkable, not into water or danger; `safeMode` when the next block over is not walkable | walk off toward dest; sprint kept when the next movement continues straight (`canSprintFromDescendInto`) |
| Fall | the Descend scan continued down to `maxFallHeightNoWater 3`, or into water | walk off, wait for `ground`, then the next movement; off-path judged by flat distance while falling |
| Downward | dig the block under the feet (natural block only), not into liquid or a gravity block | look straight down, RT held until the break event, land |
| Pillar | place under the feet while jumping (needs a throwaway block in the hotbar), head room above | look down, A tap, LT when `vy < 0` after the apex (calibrated delay), repeat; verified by the place event |
| Parkour (phase 3) | gap 2, 3 or 4 with sprint, the column checks, ascend and descend variants, `allowParkourPlace` | the SprintJump simulation on Bedrock numbers decides the takeoff tick; sprint held through the run-up |

Status values are Baritone's: PREPPING (breaking or placing), WAITING, RUNNING, SUCCESS, UNREACHABLE (FAILED exists
but no movement sets it). Each movement's valid positions (src, dest, and the cells a diagonal sweeps) are what the
executor checks the feet against.

The numbers the executors keep from Baritone's `updateState` methods, so the port can be checked against the
sheet rather than tuned by feel:

- Traverse: SUCCESS at dest, or one or two blocks past it in the same direction (`overshootTraverse`). Bridging is
  Bedrock's, not Java's: a block placed near or at the feet goes into the gap ahead at floor level, so the executor
  stands at the edge, looks down at the edge of its own floor block and taps LT; no sneak-and-walk-backward
  backplace and no facing away (the user, 28 October 2026). The planner's costs stay Baritone's.
- Ascend: UNREACHABLE if the feet drop below src; SUCCESS at dest or one past; jump when the flat distance to the
  step is 1.2 or less and the sideways offset 0.2 or less, never while sideways speed exceeds 0.1 a tick; after
  10 ticks of failed placement, step back.
- Descend: aim one block past dest for the first 20 ticks, then at dest; SUCCESS at dest (or one past) once the
  feet are within 0.5 of its height; `safeMode` aims 83 percent of the way and stops when the block beyond is
  dangerous or `skipToAscend` holds.
- Fall: SUCCESS when the feet are at dest and within 0.094 of its height (or in water with vertical speed back to
  zero); centre on dest with forward while airborne; sneak while falling faster than 0.4 a tick if the dest is
  magma. No bucket, so no bucket clutch and `maxFallHeightNoWater 3` is the hard limit.
- Downward: wait 10 ticks within 0.2 of centre before steering; the break is `prepared()`'s.
- Pillar: sneak held; forward until within 0.17 of centre; jump when flat speed is under 0.05 and the feet are
  below dest; place when crouching, the crosshair on src or the src floor, and the feet more than 0.1 above dest's
  floor; SUCCESS at dest once the block is there.
- Diagonal: SUCCESS at dest; UNREACHABLE when the feet leave the valid positions; jump on a horizontal collision
  when ascending; sprint unless a corner block is not walk-through.
- Parkour (phase 3): sprint only for distance 4 or an ascend; on a 3 jump wait until 0.7 from the src centre; walk
  back to `src - dir` for a run-up when off the line.

## The planner (astar.py, goals.py, path.py)

- `AStarPathFinder.calculate(primary_ms, failure_ms)`: Baritone's loop. Nodes in a dict keyed by packed position,
  a binary heap with decrease-key, `MIN_IMPROVEMENT 0.01`, the seven best-so-far trackers, `MIN_DIST_PATH 5`,
  the time check every 64 nodes, and `pathingMaxChunkBorderFetch 50` unknown-chunk misses before the search
  stops. Timeouts: 500 ms primary and 2000 ms failure for a fresh path, 4000 and 5000 when planning ahead.
- Goals with Baritone's heuristics: `GoalXZ` (straight plus diagonal times `costHeuristic`), `GoalYLevel`
  (`JUMP_ONE_BLOCK_COST` up, `FALL_N_BLOCKS_COST[2] / 2` down), `GoalBlock`, `GoalNear`, `GoalGetToBlock`,
  `GoalComposite`. `Navigator.go(point, within)` builds `GoalXZ` when the point has no height, else `GoalNear`.
  A goal in an unloaded chunk becomes `GoalXZ` until a segment ends within loaded range (`simplifyUnloadedYCoord`).
- `Favoring`: the previous path's positions at 0.5 cost.
- `Path.postProcess` rebuilds the movements from the nodes and cuts the path at the first movement that became
  impossible; `staticCutoff` drops the last 10 percent of any path of 30 movements or more that does not reach
  the goal; `cutoffAtLoadedChunks` trims at the first position in an unread band.
- Search area: the bands the probe has, plus a fetch of the 3x3 chunk neighbourhood on a miss, as now. A miss
  past 50 counts ends the search, as in Baritone; the plan-ahead picks it up as the player moves and the server
  loads more.

Python budget: the current nav does 30,000 expansions in about a second. With flag bytes and a packed key the
target is 50,000 nodes a second, which makes Baritone's 500 ms primary budget worth 25,000 nodes, enough for a
200 block segment on open ground and far past it on the hills that beat the old one.

## The executor (executor.py, control.py)

`PathingBehavior.tick()` runs once per probe tick (20 Hz) in its own thread while the playbook waits on `go`:

1. `PathExecutor.on_tick`: the feet against the current movement's valid positions, the back-skip and the
   forward-skip, `possiblyOffPath` at 2 blocks for 200 ticks or 3 blocks at once, cost re-verification of the next
   five movements, `movementTimeoutTicks 100` past the movement's own estimate, then the movement's `tick`.
2. Segments: a fresh path from `pathStart()` (the feet, or the block the player is sneaking off, or the block
   below when falling); planning ahead from the current path's end once the remaining ticks fall under
   `planningTickLookahead 150`; `trySplice` when the next segment starts where this one ends;
   `snipsnapifpossible` when a movement ends on the next segment; discard a next segment that no longer contains
   the player.
3. The sprint policy from `PathExecutor.shouldSprintNextTick`: traverse requests sprint; traverse into ascend
   sprints and jumps early when `sprintableAscend` holds; descend into straight traverse or descend keeps it;
   never in `safeMode`.

`control.py` is the only module that knows about sticks:

- **Steering** is pure pursuit at a look-ahead of 1.8 to 3 blocks along the path (human.js), clipped at the next
  turn or height change (the existing rule), with the right stick as a rate command: `turn = clamp(error /
  turn_span)` where `turn_span` comes from the measured degrees per second at full deflection, a deadband of
  4 degrees, and the camera's smoothing latency compensated by aiming at where the error will be one round trip
  later. The left stick points at the pursuit point relative to the camera, so the body can move toward the
  path while the camera is still swinging: a stick-driven player strafes, Baritone's cannot.
- **Sprint**: LS click once moving forward; the probe's `sprint` says whether it took, and a click is repeated
  after a collision cancels it. `ctrl_sprintonmovement` is off in this client, so nothing sprints on its own.
- **Jump**: an A tap of 2 ticks. Auto-jump is off on gamepad in this client (`ctrl_autojump_gamepad:0`), so
  every step up is a planned jump.
- **Sneak**: B held, since toggle crouch is off on gamepad (`ctrl_togglecrouch_gamepad:0`); `stand_up` becomes
  "release B" and the probe's `sneak` confirms it.
- **Use and attack**: LT tap to place, RT held to break; both only after `look_at` reports the probe's crosshair
  on the wanted block or face.
- **Hotbar**: RB steps until the probe's `hand` is the wanted item (a throwaway block, the best tool for a block),
  from the `hotbar` field, never by reading the screen.
- **Latency model**: every command is stamped; the measured pad-to-probe delay (expected 60 to 120 ms, two to
  three ticks) is what the jump-in distance and the pillar place delay are expressed in.

## Calibration (calibrate.py)

Run once per machine and client build, in a flat quartz rig the script builds by command at y 200 in the
current test world (the runner's own fixture, cleared afterward), writing `calibration.json`:

- walk, sprint, sneak and swim speeds (blocks a second, from the probe's positions over 3 s)
- jump apex height and air time; the distance from a 1 block step at which an A tap lands it, sprinting and
  walking; sprint-jump length
- right stick: degrees per second at 0.25, 0.5 and 1.0 deflection, and the ramp (`ctrl_smoothrotationspeed`)
- pad-to-probe latency: command stamp to the first tick that shows the change, for a stick move and a jump
- break time by hand and with a stone pickaxe for dirt and stone, against the table
- pillar: the delay from the A tap to the LT tap that places the block under the feet

The costs and the control constants read this file; Baritone's constants are the defaults when it is absent.

## Proving it (course.py)

An obstacle course built by command in a test world, each piece a numbered pad with a start and an end marker:

1. 100 blocks of flat ground (sprint and straight-line steering)
2. a staircase up 10 and down 10, one block steps (ascend and descend timing)
3. a hill with a 3 block cliff and a wall around the easy way (the two-wall case: best-so-far and favoring)
4. a 1 block, 2 block and 3 block pit (fall limits)
5. a river 6 wide (swim) and a pond with a one-block bank
6. a 1x2 tunnel through a hill that is cheaper to dig than to go round (break)
7. a 4 block cliff with no way up (pillar) and a 3 block gap over a drop (bridge)
8. gaps of 2, 3 and 4 over a 2 block drop (parkour, phase 3)
9. a dark oak forest strip with a dense canopy (the Saffron case)

`python nav/course.py --build` makes it, `--run` runs the navigator over every piece and writes
`captures/course/<time>/report.md` with the time, the path length, the planner's node count and milliseconds,
and whether the piece was finished, with a screenshot at each end. The old navigator's times on the same course
are the baseline. The gym tour is the end-to-end check: walk the 200 blocks to each overworld gym with the
new navigator and compare with run 6's 4 to 8 minutes per gym.

## Settings

A `Settings` dataclass with Baritone's names and defaults, so the research sheet is the documentation:
`allowBreak True`, `allowPlace True`, `allowSprint True`, `allowParkour False` (on in phase 3),
`allowParkourPlace False`, `allowDiagonalDescend False`, `allowDiagonalAscend False`, `allowDownward True`,
`maxFallHeightNoWater 3`, `allowWaterBucketFall False` (no bucket), `sprintAscends True`,
`allowOvershootDiagonalDescend True`, `walkWhileBreaking False` (the controller breaks standing), timeouts and
penalties as listed above. `allowPlace` and `allowBreak` are true only when the hotbar actually holds a
throwaway block or a tool: `hasThrowaway` is read from the probe, as Baritone reads the inventory.

## Phases and agents

0. **Rig** (one agent): probe v2, `world.py` with the classification table and tests, `calibrate.py` and the
   first `calibration.json` on this PC. The client has to be in a world for the calibration run; the FTUE's
   `join_server` puts it there.
1. **Planner** (one agent, after the `world.py` interface is fixed): `costs.py`, `goals.py`, `moves.py` (cost
   side), `astar.py`, `path.py`, with unittest fixtures for every rule in the movement sheet and a benchmark of
   nodes a second on a real band dump.
2. **Executor** (one agent, after 0 and 1): `control.py`, the movement `tick` methods, `executor.py`,
   `course.py --build`, and the course run for pieces 1 to 7. Each piece is fixed until it passes.
3. **Parkour and sprint jumps** (one agent): `sprintjump.py` on Bedrock numbers, Parkour movements, piece 8.
4. **Integration** (me): the FTUE skill's `nav.py` becomes a shim over `baritone_bedrock` (imported from
   `C:\GitHub\baritone-bedrock` when it is not installed, the way the skill finds its sibling repositories),
   `prepare_world` copies `pack/` from the repository, the playbook's `walk_legs` and `approach` use `go` with real
   goals, the tour and the FTUE rerun, SKILL.md and the mirrors (`.agents/skills/ftue`, `.github/skills/ftue`)
   updated, the skill's `probe_pack/` removed. The repository gets its README, CREDITS, LICENSE, CI and first
   release once the course passes.

Every agent brief says: read `docs/research-movements.md` and `docs/research-settings.md` first, port the rule
as written and cite the Java line in a comment where the port departs from it, run long jobs under the watchdog,
keep the package free of anything Cobblemon or FTUE, and leave the controller as the only thing that moves the
player.

## Fidelity: where the port departs from Baritone on purpose

1. Water costs 9.09 a block, not Baritone's accidental 4.63 (above).
2. Unknown blocks are not air (above).
3. `cutoffAtLoadBoundary` on: Baritone caches chunks it has seen, we have only what the probe has read.
4. `walkWhileBreaking` off: the controller breaks standing still, then walks. Baritone's `pitch 26` walk-and-break
   needs instant rotation.
5. Only the natural-terrain whitelist is breakable; Baritone breaks anything with a finite hardness.
6. No frost walker, no water bucket, no potion effects, no offhand, no inventory moves (`allowInventory` is off in
   Baritone too): the hotbar is the inventory.
7. Baritone's `Movement.update` water handling (bob with JUMP below `dest.y + 0.6`, the swim state via sprint) is
   kept, with the sprint-in-water on the controller being the LS click Bedrock uses to swim.
8. Everything else is ported as written, and the research sheet's quirks 2, 4, 6 and 8 are reproduced rather than
   fixed, so a path here is the path Baritone would take.

## Gathering: the player gets its own blocks

Decided 28 October 2026: nothing is given. A player punches dirt and trees for blocks, and getting and crafting
them is part of the first session anyway. So:

- `allowPlace` is true only while the hotbar holds a throwaway block (`acceptableThrowawayItems`, with
  `minecraft:dirt` first), read from the probe, as Baritone reads the inventory. Without one the planner routes
  around, as it would for a player with empty hands.
- `Navigator.gather(kind, count, timeout)` (phase 2): walk to the nearest exposed block of that kind within reach
  of the probe's bands, break it by hand (dirt is 17 ticks, Baritone's own number), walk over the drop, repeat
  until the hotbar holds `count`. `go` calls it before a walk when the planner's best partial path ended at a
  cliff or water it could have pillared or bridged, and when a path's movement needs a block and there is none.
  Timeboxed and reported, never silent.
- Tools come from the game's own crafting: the FTUE already crafts at a crafting table for the Trainer Card, and a
  wooden pickaxe (logs, planks, sticks, table, pickaxe) is the same UI. That stays in the consumer: the
  navigator's `Provisioner` hook asks the caller for a tool class and carries on without it when the caller has
  none. Costs then use whatever is in the hotbar, so stone by hand costs 152 ticks and the planner avoids it
  unless it saves more.

## Open decisions

1. **Parkour** (phase 3, 28 October 2026): `MovementParkour.update_state` is ported with the takeoff decided a
   pad latency ahead, `sprintjump.py` is Baritone's SprintJump without the brake (a stick cannot face backward and
   keep the sprint), and course pieces 8 and 9 are built for them; the course run turns `allowParkour` and
   `sprintJumping` on (`--no-parkour`, `--no-sprint-jump` turn them off) and the report's parkour column is the
   proof. Before that it stayed off until phase 3 proved it on the course; Baritone ships it off for the same reason
   ("falls off when cornering like all the time").
