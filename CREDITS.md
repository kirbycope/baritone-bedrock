# Credits

baritone-bedrock is a port, and these are the projects it is ported from. Each is cloned under the git-ignored
`java/` folder while working (see README.md) and nothing of theirs is committed here.

## Baritone

- Who: cabaletta and the Baritone contributors
- Where: https://github.com/cabaletta/baritone (commit `c55a9c5`, October 2026)
- Licence: LGPL-3.0. This repository is a derivative work of Baritone and carries the same licence (LICENSE).
- What is ported: the planner (`AStarPathFinder`, the best-so-far trackers, `Favoring`, the goals), the executor
  (`PathExecutor`, `PathingBehavior`, the per-movement `updateState` rules), the costs (`ActionCosts`, the mining
  duration, the penalties and settings with their names and defaults), and the predicates of `MovementHelper` and
  `PrecomputedData` that `baritone_bedrock/world.py` turns into a flag byte per block. Where a rule is ported, the
  Java file and line are cited beside it; `docs/research-movements.md` and `docs/research-settings.md` are the
  line-by-line reading the port works from.

## mineflayer-pathfinder

- Who: PrismarineJS and the mineflayer-pathfinder contributors
- Where: https://github.com/PrismarineJS/mineflayer-pathfinder (commit `b07a419`)
- Licence: MIT
- What is used: the human controller model of `lib/human.js` (`createHuman`: pure pursuit at a look-ahead of 1.8 to
  3 blocks, a steering deadband, coasting stops), which is how a rate-limited right stick follows a Baritone path,
  and the parkour geometry of `lib/movements.js`. `baritone_bedrock/controller.py` and the executor's steering are
  written to it.

## prismarine-physics

- Who: PrismarineJS and the prismarine-physics contributors
- Where: https://github.com/PrismarineJS/prismarine-physics
- Licence: MIT
- What is used: the player physics constants (jump velocity, gravity, drag, the walk, sprint and sneak speeds)
  that the fall formula, the fake probe in `tests/test_calibrate.py`, the simulated player in
  `tests/test_executor.py` and the calibration's expectations are checked against.

## Elsewhere in the tree

- `pack/`, the probe behavior pack, is this repository's own, written against Mojang's `@minecraft/server` 2.6.0 and
  `@minecraft/server-net` 1.0.0-beta Script API typings.
- `baritone_bedrock/probe.py` grew out of the `Probe` and `ProbeTerrain` classes of the FTUE skill's `nav.py` on
  this machine, by the same author.
