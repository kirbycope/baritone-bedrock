"""Benchmark: nodes a second and path time for the planner.

    python3.11 tests/bench_planner.py [--size 256] [--seed 1]

Three runs: raw expansion rate on a flat ArrayWorld with an unreachable goal (the primary timeout stops it), the
same with dirt in the hotbar (every pillar and ascend-by-placing becomes a neighbour too), and a GoalXZ 150 blocks
away on the rolling hills, which is the plan's "200 block segment on open ground" case. Under a minute; under the
watchdog if it is not.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baritone_bedrock.context import CalculationContext   # noqa: E402
from baritone_bedrock.goals import GoalXZ                  # noqa: E402
from baritone_bedrock.astar import AStarPathFinder         # noqa: E402
from baritone_bedrock.settings import Settings             # noqa: E402
from tests.fixtures import flat_array, rolling_hills, DIRT  # noqa: E402


def run(label, world, start, goal, hotbar, primary_ms, failure_ms, settings=None):
    ctx = CalculationContext(world, settings=settings, hotbar=hotbar)
    finder = AStarPathFinder.create(start, goal, ctx)
    t = time.perf_counter()
    result = finder.calculate(primary_ms, failure_ms)
    dt = time.perf_counter() - t
    nodes = finder.num_nodes_considered
    rate = nodes / dt if dt else 0
    length = len(result.path.positions()) if result.path is not None else 0
    cost = sum(m.get_cost() for m in result.path.movements()) if result.path is not None else 0
    print(f"{label:34s} {result.type:16s} {nodes:7d} nodes {dt * 1000:7.1f} ms {rate:8.0f} nodes/s "
          f"{finder.num_movements_considered:8d} moves  path {length} positions, {cost:.1f} ticks")
    return rate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    t = time.perf_counter()
    flat = flat_array(args.size, height=63)
    rolling, heights = rolling_hills(args.size, seed=args.seed)                       # the test fixture's default
    steep, steep_heights = rolling_hills(args.size, seed=args.seed, amplitude=8, scale=28)
    print(f"worlds built in {time.perf_counter() - t:.2f} s")
    mid = args.size // 2
    far = GoalXZ(100_000, 100_000)
    # the raw-rate runs would otherwise stop at the world's edge after 50 unloaded-chunk misses
    unlimited = Settings(pathingMaxChunkBorderFetch=1 << 30)
    rates = [run("flat, empty hands, 1 s", flat, (mid, 64, mid), far, [], 1000, 1000, unlimited),
             run("flat, dirt in hand, 1 s", flat, (mid, 64, mid), far, [DIRT], 1000, 1000, unlimited)]
    sx, sz = 20, 20
    gx, gz = min(args.size - 20, sx + 150), min(args.size - 20, sz + 150)
    start = (sx, heights[sx][sz] + 1, sz)
    rates.append(run(f"rolling hills, GoalXZ {gx - sx} away", rolling, start, GoalXZ(gx, gz), [], 500, 2000))
    rates.append(run("rolling hills, dirt in hand", rolling, start, GoalXZ(gx, gz), [DIRT], 500, 2000))
    start = (sx, steep_heights[sx][sz] + 1, sz)
    rates.append(run(f"steep hills, GoalXZ {gx - sx} away, 2 s", steep, start, GoalXZ(gx, gz), [], 2000, 4000))
    rates.append(run("steep hills, dirt in hand, 2 s", steep, start, GoalXZ(gx, gz), [DIRT], 2000, 4000))
    print(f"nodes per second: flat {rates[0]:.0f} / {rates[1]:.0f}, rolling {rates[2]:.0f} / {rates[3]:.0f}, "
          f"steep {rates[4]:.0f} / {rates[5]:.0f}")


if __name__ == "__main__":
    main()
