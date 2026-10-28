# baritone-bedrock

A port of Baritone's pathing (cabaletta/baritone) for Minecraft Bedrock, in Python with no dependencies beyond the
standard library. It drives the real Bedrock client through whatever controller the caller hands it (an emulated
Xbox pad, for instance) and reads the world through its own behavior pack, the probe. Where Baritone's execution
model cannot carry over, since it sets the player's rotation and keys directly while this has a rate-limited right
stick and a pad round trip, mineflayer-pathfinder's human controller model is followed instead. The plan is in
`docs/baritone-port-plan.md`; the line-by-line reading of Baritone it is built from is in `docs/research-movements.md`
and `docs/research-settings.md`.

Phases 0 to 3 of that plan are here: the rig (the probe pack, the probe's Python end, the terrain classification
table, the controller protocol and the calibration), the planner (costs, goals, the movement set, A*, paths), the
executor (the controller model, the movements' per-tick behaviour, PathExecutor and PathingBehavior, the Navigator
facade and the obstacle course that proves it), and phase 3: sprint jumps (`sprintjump.py`, Baritone's SprintJump
simulation), the parkour movement's per-tick behaviour, and the course's parkour and forest pieces.

## Licence

LGPL-3.0, because this is a derivative work of Baritone, which is LGPL-3.0. See `LICENSE`, and `CREDITS.md` for
what comes from where (Baritone, mineflayer-pathfinder, prismarine-physics).

## Layout

```
baritone_bedrock/
  controller.py   the Controller protocol a caller implements, and NullController for tests
  probe.py        Probe (the HTTP end of the pack) and ProbeTerrain (the world as bands of classified blocks)
  world.py        the flag byte per block: KINDS, classify(), Terrain, DictWorld
  costs.py        Baritone's ActionCosts, from calibration.json when it exists
  settings.py     Baritone's settings, with its names and defaults
  calibrate.py    builds a rig by command and measures the client (see Calibration)
  goals.py        GoalXZ, GoalBlock, GoalNear, GoalGetToBlock, GoalComposite with Baritone's heuristics
  context.py      CalculationContext and ToolSet: what one search reads, snapshotted
  moves.py        the eight movements: the cost functions for the planner and tick() for the executor
  astar.py        AStarPathFinder, Favoring, PathCalculationResult
  path.py         Path, CutoffPath, SplicedPath
  control.py      Body: the controller model (pure pursuit, the turn curve, taps and toggles, look_at)
  sprintjump.py   SprintJump: the tick-by-tick hop simulation the executor plans sprint jumps with (see The executor)
  executor.py     PathExecutor and PathingBehavior (see The executor)
  navigator.py    Navigator: go, look_at, where, gather, stand_up, stop (see Navigating)
  course.py       builds and runs the obstacle course (see The obstacle course)
pack/             the probe behavior pack: manifest.json and scripts/main.js
tests/            unittest; python3.11 -m unittest discover -s tests -t .
docs/             the plan and the two research sheets
java/             the upstream clones, git-ignored
```

## Wiring it up

Two things come from the caller: a controller and a way to get the pack into the world.

### The controller

`baritone_bedrock.controller.Controller` is a `typing.Protocol` with two methods:

- `state(left=(x, y), right=(x, y), held=())` sets both sticks (each axis -1 to 1, y up) and the set of buttons
  held down, and leaves them so until the next call; `state()` lets everything go.
- `press(*names, hold=0.12, gap=0.35)` taps buttons in turn.

Button names are `a`, `b`, `x`, `y`, `lb`, `rb`, `lt`, `rt`, `ls`, `rs`, `up`, `down`, `left`, `right`, `start`
and `back`. The FTUE skill's `Pad` class already has exactly these two methods, so a `Pad()` passes straight through.
`NullController` records every call and moves nothing, for tests and dry runs. Nothing in this package moves the
player except through the controller it was given.

### The pack

`pack/` is a behavior pack (`@minecraft/server` 2.6.0, `@minecraft/server-net` 1.0.0-beta, so the world needs the
Beta APIs experiment and the server's `config/default/permissions.json` must allow `@minecraft/server-net`). Copy it
into the world's `behavior_packs/` and list its header uuid and version in `world_behavior_packs.json`. It posts to
`http://127.0.0.1:47812` on the machine running the server, which is where `Probe` listens; when nothing listens it
waits a second and tries again, so it can sit in a world unused.

The pack does nothing to the player or the world on its own. Its only writes are the commands the runner sends it
in a `/tick` answer, which the calibration uses to build its rig.

```python
from baritone_bedrock.probe import Probe, ProbeTerrain

probe = Probe()                     # 127.0.0.1:47812
probe.connected(60)                 # True once the pack has posted
me = probe.player("Kirbycope")      # the latest report, or None if none this second
terrain = ProbeTerrain(probe)       # a world.Terrain: terrain.kind(x, y, z) is a BlockKind
```

## The probe protocol

Every tick the pack POSTs `/tick`:

```
{tick, players: [...], events: [...], blocks: [...], results: [...]}
```

and the runner answers `{scan: [[dim, cx, cz, y0, y1], ...], commands: ["fill ...", {dim, cmd}, ...]}`.

A player report carries `id, name, dim, x, y, z, yaw, pitch, vx, vy, vz, ground, water, sneak, sprint, jump,
climbing, swimming, falling, fall, slot, hand, hotbar, input, health, food, look, block`. `hand` and each of the nine
`hotbar` entries are `{type, amount}` or null. `input` is `{move: {x, y}, jump, sneak}` from the player's own input
(`inputInfo`). `look` is the entity under the crosshair within six blocks, `block` the block there as
`{type, x, y, z, face}`, the game's own answer to what the player is aiming at. `Probe` adds `at` (arrival time),
`n` (its report count) and `tick` (the game tick). There is no `air`: the 2.6.0 Script API has no member for a
player's air supply.

`events` are `{type: "break" | "place" | "hurt", tick, dim, x, y, z, block, player, damage, cause}` from
`playerBreakBlock`, `playerPlaceBlock` and `entityHurt` (players only), queued and sent with the next tick.
`Probe.events()` returns those queued since the last call. `blocks` are `{dim, x, y, z, entry}` for each block a
break or place changed, applied to the stored band so the terrain stays current without a rescan. `results` are
`{cmd, ok, successCount, error}` for the commands sent in the last answer; `Probe.command(text, timeout)` queues
one, waits for its result and returns it.

Each band asked for comes back as a POST to `/chunk`:

```
{dim, cx, cz, y0, y1, height, palette, data, ms, wall, cells}
```

`data` has one character per block, `chr(33 + paletteIndex)`, at index `((x * 16) + z) * height + (y - y0)`.
`ms` is the script time the pack spent reading the band, `wall` the milliseconds from its first column to the post
and `cells` the blocks read; `Probe.scan_stats` keeps them and the course report sums them per piece. The pack reads
bands in its tick handler under a budget of script time a tick (`budgetMs` in `pack/scripts/main.js`, 25 by
default; a `budget` number in a `/tick` answer, `Probe.budget_ms`, changes it without a restart), columns of the
band at the head of the queue until the budget is gone, and posts a band the moment it is complete. A band costs
about 42 ms of script time whatever is in it (8192 `getBlock` calls at about 5 us each; the states are only read
for slabs, stairs, snow layers, doors, gates and liquids), so the budget sets the throughput: the `system.runJob`
with a yield per column this replaced delivered 25 fresh bands in 3.9 s and took the server to 17.4 ticks a second
while it ran; at 15 ms a tick the same 25 bands took 4.8 s with the server at 19.7 or better, and at 25 ms a tick
a band arrives every 85 ms or so, a 100 block corridor (about 20 bands) in under 2 s.
A palette entry is the block's typeId plus the states that change how it is walked, joined as
`typeId|state=value|...`: `minecraft:vertical_half` (slabs), `upside_down_bit` (stairs), `height` (snow layers),
`open_bit` (doors, trapdoors, gates), `liquid_depth` (water and lava) and `waterlogged`. A block that could not be
read is `?`. A band whose palette would pass 90 entries is sent as two bands of half the height; `Probe` folds them
back into one 32 level band keyed `(dim, cx, cz, y0)`. `ProbeTerrain` reads a band on a miss together with the eight
chunks round it, and reads a band holding a `?` again after four seconds.

## Terrain classification

`world.classify(entry)` turns a palette entry into a `BlockKind` once: a flag byte (`WALK_ON`, `WALK_THROUGH`,
`PASSABLE`, `PLACE_AGAINST`, `WATER`, `LAVA`, `FLOWING`, `DANGER`, `TALL`, `CLIMB`, `BOTTOM_SLAB`, `GRAVITY`,
`OPENABLE`, `BREAKABLE`, `UNKNOWN`), a hardness and the tool class that digs it fast. The flags are Baritone's
predicates (`canWalkOn`, `canWalkThrough`, `fullyPassable`, `canPlaceAgainst`, `avoidWalkingInto`) decided per
Bedrock block type, since the Script API has no collision shapes to ask. `KINDS` is a list of (pattern, flags,
hardness, tool) rows, first match wins, covering the vanilla 1.26 block list; the last row matches everything, so a
block nobody has described, in any namespace, is solid, not breakable and not a face to place against. Only the
natural terrain whitelist is `BREAKABLE`. `tests/test_world.py` pins the table.

## Navigating

```python
import sys
sys.path.insert(0, r"C:\GitHub\.claude\skills\ftue")       # or wherever the caller's Controller lives
from ftue import Pad
from baritone_bedrock import Navigator, Probe

probe = Probe()                                           # 127.0.0.1:47812; the pack must be in the world
probe.connected(60)
nav = Navigator(Pad(), probe, "Kirbycope", settings=None, log=print, say=run.say)
left = nav.go((x, None, z), within=1.5, timeout=300)     # a column: GoalXZ
left = nav.go((x, y, z), within=1.5)                      # a point: GoalNear(within)
left = nav.go(GoalGetToBlock(x, y, z))                    # any Goal
nav.look_at((x, y, z), block=(bx, by, bz), timeout=6.0, tolerance=2.0)   # or entity=<id>, or a callable point
nav.where()                                               # (x, y, z, yaw, pitch) or None
nav.gather("minecraft:dirt", 16, timeout=120)             # break the nearest exposed dirt by hand until 16 are held
nav.stand_up()                                            # out of a crouch (B toggles on this client)
nav.stop()
```

`go` returns the flat distance left (0 to `within` on arrival; more when the time ran out or the planner found no
way), or `None` when the probe lost the player (a death, a disconnect). It says what it does through `say`:
"Walking to", "Next segment", "Breaking", "Placing", "Stuck, finding another way", "Arrived". `on_tick=` takes a
callable given one dict per executor tick (feet, yaw, pitch, sticks, held, movement, status, path position), which
is what the course writes to `ticks.jsonl`. The hotbar the planner costs with is read from the probe at the start of
every segment, so a block gathered mid-walk is placeable in the next segment.

Nothing is given to the player: `allowPlace` is true only while the hotbar holds a throwaway block
(`acceptableThrowawayItems`, dirt first), and `gather` is how it gets one (walk to the nearest exposed block of
that kind in the probe's bands, break it by hand, walk over the drop, repeat).

## The executor

`executor.py` is `PathExecutor` and `PathingBehavior` ported from the Java (PE and PB in the comments), ticked once
per probe report with a new game tick: the feet against the current movement's valid positions with the back-skip
and the forward-skip, `possiblyOffPath` (2 blocks for 200 ticks, 3 at once, flat distance during a Fall), the
toBreak/toPlace/toWalkInto bookkeeping, re-costing the next `costVerificationLookahead` movements, `maxCostIncrease`,
`movementTimeoutTicks` past the original estimate, `shouldPause` while the search in progress backtracks, and
`shouldSprintNextTick` (the traverse-to-ascend skip with the early jump, the descend rules, `canSprintFromDescendInto`,
`allowOvershootDiagonalDescend`). The behavior plans a fresh path from `pathStart()`, plans the next segment from the
current path's end once `ticksRemainingInSegment` drops under `planningTickLookahead`, splices it on, snipsnaps onto
it early, discards a next segment that no longer contains the player, and cancels a search that no longer starts
where the player is; the searches run on a worker thread with the timeouts from the settings (500/2000 ms fresh,
4000/5000 planning ahead).

`moves.py`'s `tick(state)` methods are the movements' `updateState`, written for the controller model in
`control.py`:

- steering is pure pursuit (mineflayer-pathfinder's human.js): the camera turns toward a point 1.8 to 3 blocks
  along the path (clipped at the next turn or height change) through the inverse of the measured turn curve
  (`calibration.json` "turn": 0 deg/s at 0.25 deflection, 25.6 at 0.5, 150 at 1.0), with a 4 degree deadband and
  the error predicted one pad-to-probe latency ahead; the left stick points at the movement's own target relative
  to the camera, so the body strafes toward the path while the camera is still turning;
- sprint is the LS click (clicked once moving forward, clicked again when the probe's sprint goes false), jump an A
  tap of two ticks, use an LT tap, attack RT held until the probe's break event, the hotbar RB steps until the
  probe's hand matches (never the D-pad, which opens the emote wheel in the world);
- sneak is a B tap that TOGGLES, whatever options.txt says: the probe's sneak field is the truth, a tap goes out
  only while it disagrees with what the movement wants, and B is never held;
- an ascend jumps within 1.2 blocks of the step (Baritone's rule; the calibration's `step_up` says an A tap lands it
  from 1.525 on this client), or at any distance once it is pressed against the step with no forward speed;
- a pillar taps A when still and centred, then LT the calibrated `pillar.delay_ticks` (5) later while the crosshair
  is on the block below;
- a bridge is Java's backplace (Trav:322-348) with two changes for a slow camera: the sneak is asked for from the
  whole of the src block (the toggle shows two or three ticks after the tap), and once the feet have passed the
  edge the movement stays in the backplace and presses MOVE_BACK only when the camera is within 30 degrees of
  facing back, since pressed earlier it walks the player back onto src; the LT goes out once the camera is still
  and the crosshair is on the side face of the floor block, and the probe's place event confirms the block. The
  Bedrock shortcut of tapping LT with the crosshair on the top face of the block under the feet, near its edge,
  was tried on this client first: 22 taps placed nothing, so it is not used.

Sprint jumps (`settings.sprintJumping`, off in Baritone, on in the course run) are `sprintjump.py`, Baritone's
`SprintJump.java` on the same numbers (gravity 0.08, drag 0.98, jump 0.42, the 0.2 sprint-jump boost, ground
friction 0.546, air friction 0.91). They are Bedrock's numbers: the per-tick trace `calibrate --trace-jump`
recorded on this PC (calibration.json, `jump.sprint_jump_trace`) is a hop of 3.629 blocks with a takeoff step of
0.4806, an air step of 0.2863, the apex at 1.252 and the next ground tick at 0.3867, and the simulation gives
3.629, 0.4806, 0.2864 and 0.386 from the same start; every y of the flight matches to a hundredth
(`tests/test_sprintjump.py` checks the simulation against the recorded trace whenever one is present). The hops are
planned from `shouldSprintNextTick` (PE:362-366) and flown by `fly()` and
`steer()` (PE:507-535): the runway is the straight run of traverses, ascends and descends ahead with solid floors
and clear columns, the hop is simulated tick by tick, and it is taken only when every velocity margin (0.85, 1,
1.15) lands on a runway cell with apex minus landing under 3 (so a drop of 1 is hopped down, a drop of 2 is not),
with a step ahead popped from the landing when the next hop can. Two departures: a stick cannot brake the way
Baritone does (facing backward while holding W keeps the sprint; pulling a stick back drops it), so only the full
send is offered; and the A tap shows a tick later (`calibration.json` latency.jump_ticks), so the hop is required
to work from that many ground ticks ahead as well. In the air the executor steers alone (the left stick along the
line, tilted to kill sideways drift; the camera led along the line) and picks the path up at whichever movement it
comes down in.

Parkour (`settings.allowParkour`, off in Baritone "because it falls off when cornering", on in the course run) is
`MovementParkour.update_state` per Park:252-315: sprint for a dist of 4 or an ascend (a dist 2 or 3 is walked), run
at dest, jump once the feet are over the edge (a dist 3 waits for 0.7 past the src centre), walk back to src - dir
for a run-up when on the ground anywhere else, the mid-air place when `allowParkourPlace` holds, UNREACHABLE on a
fall. The one departure is the latency again: the jump is decided at where the player will be when the tap lands,
and a tick that would carry the next decision past the block's edge (0.8 from the centre) jumps now, since a
walking pace of 0.216 a tick is wider than the 0.1 between Baritone's 0.7 and the edge. `settings.maxParkourJump`
(4, Baritone's with sprint) caps the planner's dist for a client whose measured sprint jump cannot make the longest.

Every rule the port keeps carries its Java line in a comment; every departure says "departs:" and why.

## The obstacle course

```
python -m baritone_bedrock.course --build [--pieces 1,2,...] [--origin X Z] [--y 200]
python -m baritone_bedrock.course --run   [--pieces 1,2,...] [--repeat 3] [--no-sprint-jump] [--no-parkour] [--out captures/course] [--controller ftue|null] [--no-shots]
python -m baritone_bedrock.course --clear
```

Nine pieces on quartz pads 200 blocks up, each with a gold start and an emerald end marker: 100 flat blocks; a
staircase up 10 and down 10; a hill with a 3 block cliff, a walled pocket on the easy-looking side and the ramp on
the other; pits 1, 2 and 3 deep; a river 6 wide and a pond with a 1 block bank; a stone plug in a wall across a wide
pad that is cheaper to dig by hand than to walk round; a 6x6 dirt patch, a 4 block cliff to pillar and a 3 block gap
over water to bridge; the parkour piece, four legs each with its own markers (gaps of 1, 2 and 3 blocks over a pit 2
deep with no way out, then a gap of 1 onto a platform one block up: Baritone's dist 2, 3, 4 and the ascend); and a
dark oak forest strip 40 long and 21 wide, walled, under a two-deep canopy, with 2x2 trunks on a jittered grid and
leaves at feet and head height in a fifth of the cells, so no straight line through it is clear. `--build` walks
the player along the chunks it builds (a fill in an unloaded chunk does nothing), verifies each piece with
`testforblock`, and records the origin in `captures/course/layout.json`; `--run` clears any dirt the player carries,
teleports them to each start (each leg's start for piece 8), gathers 12 dirt first on piece 7, runs `Navigator.go`
to the end marker with a 5 minute cap, and writes `captures/course/<time>/report.md` (time, path positions,
segments, planner nodes and milliseconds, bands read and the pack time they cost, sprint jumps taken, parkour
landed over attempted, done or not, against the old navigator's 1.2 to 2.4 s a block), `ticks.jsonl` and a
screenshot at each end when the FTUE skill's Client can be imported. `--repeat 3` runs every leg three times for a
"3 of 3" table; the run has `sprintJumping` and `allowParkour` on unless `--no-sprint-jump` or `--no-parkour` says
otherwise. `--clear` teleports the player off the course first.

### Phase 3 results, 28 October 2026

All on this PC, the course world 200 blocks over a lake, the FTUE pad, `sprintJumping` and `allowParkour` on
(the reports are under `captures/course/20261028-12*`):

| piece | time | notes |
| --- | --- | --- |
| 1, flat 100, sprint jumps off, scanner at 25 ms | 25.9 s | 11.8 s with a search in progress; this morning, with the old scanner, 30.9 s and 17.1 s |
| 1, flat 100, sprint jumps on, scanner at 25 ms | 23.0 s | 21 hops, none short, 10.0 s with a search in progress |
| 1, flat 100, sprint jumps off, scanner at 15 ms (the default) | 28.7 s | 15.3 s with a search in progress |
| 1, flat 100, sprint jumps on, scanner at 15 ms (the default) | 27.6 s | 21 hops, 15.2 s with a search in progress |
| 2a, stairs up 10 and down 10 | 15.6 s | no hop attempted on the stairs (a hop down them lands three steps lower, past SAFE_FALL) |
| 2b, the descending half | 3.8 s | sprint kept through the descends |
| 8a, parkour dist 2 (gap 1), three runs | 4.4, 3.5, 3.6 s | landed 3 of 3 |
| 8b, parkour dist 3 (gap 2), three runs | 4.6, 4.2, 4.3 s | landed 4 of 4 (one run planned two jumps) |
| 8c, parkour dist 4 (gap 3, sprint), three runs | 3.4, 3.4, 3.1 s | landed 3 of 3 |
| 8d, parkour dist 2 up 1, three runs | 3.3, 3.2, 3.3 s | landed 3 of 3 |
| 9, dark oak forest 40 by 21 | 16.3 s | 49 positions, 209 nodes, through the undergrowth |

Nobody fell: the lowest y of every run is the pad. The planning time left on piece 1 is the band wait of a course
in the sky: the pad hangs 100 blocks over water, and Baritone's fall scan (no height cap on a water landing) reads
every band down to the lake off either side of it, a hundred-odd bands a piece; a walk on real ground has the
ground right there. It halves with the scanner at 25 ms a tick and costs the tick rate, see below.

Two things that spoiled runs before they were understood, both now visible in the course log: under 7 food the
executor never sprints (PE:357-359), so there are no hops, no dist 4 parkour and a walking pace, and the player's
hunger had reached 6 by the fourth piece 1 run (the course prints `FOOD n` at a start when that is so; the course
is a rig, `effect saturation` fixes it); and for ten or twenty seconds after a rejoin the client can ignore the pad
entirely (the sticks and the camera command stand while nothing moves), which read as a movement timeout.

The scanner numbers that set the budget: the old `runJob` scanner delivered 25 fresh bands in 3.9 s with the server
down to 17.4 ticks a second; the tick-budget scanner at 15 ms a tick takes 3.3 to 4.8 s for 25 bands (about 50 ms
of script time a band, 6 or 7 bands a second) with the server at 19.6 to 19.9; at 25 ms a tick 2.2 s but 15 to 17
ticks a second; at 30 ms 2.4 s and 14.9. The default is 15, the tick rate first; a caller that would rather have the
corridor in 2 s sets `Probe.budget_ms = 25`. A filtered pass with `Dimension.getBlocks` was tried and dropped (same
cost, unreliable counts, overshoots the budget: the comment in `pack/scripts/main.js` has the numbers).

## Calibration

`calibrate.py` measures the numbers `costs.py` and the executor read, in a quartz rig it builds by command 200
blocks up in the current world (relative to where the player stands), and writes `calibration.json`:

```
python -m baritone_bedrock.calibrate --controller ftue --out calibration.json [--player NAME] [--only speeds,jump]
```

- `speeds`: walk, sprint, sneak, walk_in_water and swim in blocks a second, over 3 s of probe positions (the keys of
  `costs.DEFAULT_SPEEDS`; ladder speeds are not measured and keep Baritone's)
- `jump`: apex height, air time in ticks, peak vertical velocity, sprint-jump and walk-jump length (lower bounds:
  the ground flag lags the takeoff a tick), and from `--trace-jump X Y Z [--yaw -90]`, run on its own on open flat
  ground with no rig, `sprint_jump_length_measured`, `walk_jump_length_measured` and `sprint_jump_trace` /
  `walk_jump_trace`: every report of the flight (x, y, z, vx, vy, vz, ground, sprint), the per-tick horizontal
  steps, the takeoff step and the air step, and whether the probe's velocity is the tick's displacement, which is
  what `sprintjump.py` is checked against (0.48 on the jump tick and about 0.29 a tick after it for a sprint,
  12 ticks in the air)
- `step_up`: the largest flat distance from a 1 block step at which an A tap still lands it, walking and sprinting,
  by bisection over 0.4 to 1.6 blocks with three trials a midpoint
- `turn`: degrees a second at right stick 0.25, 0.5 and 1.0, and the ramp to full rate in ticks
- `latency`: ticks and milliseconds from a `state()` call to the first report showing it, median of ten for a stick
  move and ten for a jump
- `break`: ticks by hand for dirt and stone, from the probe's break event, with the latency taken off
- `pillar`: the delay from an A tap to the LT tap that places a dirt block under the feet, and the window that works
- `client`, `date`, `rig` and `notes` (anything that failed or was not measured)

The rig is the one place a `give` (16 dirt for the pillar) and a gamemode change (survival while breaking and
pillaring) are allowed; both are undone, the rig is filled with air and the player teleported back afterwards.

For `--controller ftue` the FTUE skill's `Pad` is imported from `../.claude/skills/ftue` (or
`BARITONE_FTUE_DIR`) and connects to the pad host already running on this machine. The client must be in the world
with the pack before the run; the FTUE skill's `join_server` is what puts it there. `tests/test_calibrate.py` runs
the whole calibration against a fake probe simulating a player with Java's physics, so every section is checked
without a client.

Every wait in the calibrator is counted in probe ticks, never in seconds, which is what lets the fake probe drive it
synchronously and what makes the numbers it writes the numbers the executor will see through the same probe.
