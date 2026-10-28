"""ActionCosts, ported from baritone/api/pathing/movement/ActionCosts.java (java/baritone, c55a9c5).

Costs are in ticks. Every constant is derived from a speed in blocks a second or from the fall formula, the way
Baritone derives them, so a machine's own measured speeds (calibrate.py writes them to calibration.json) give its
own costs and Baritone's numbers are the defaults. See docs/research-movements.md section 2 for the values.
"""
import json
import os

COST_INF = 1_000_000.0   # AC:51: added to other costs, so not Double.MAX_VALUE

DEFAULT_SPEEDS = {   # blocks a second, the vanilla Java numbers Baritone uses (AC:25-36)
    "walk": 4.317,
    "walk_in_water": 2.2,
    "swim": 2.86,
    "ladder_up": 2.35,
    "ladder_down": 3.0,
    "sneak": 1.3,
    "sprint": 5.612,
}


def velocity(ticks):
    """Fall speed after that many ticks, blocks a tick (AC:80-82)."""
    return (0.98 ** ticks - 1) * -3.92


def distance_to_ticks(distance):
    """Ticks to fall a distance from rest (AC:88-102)."""
    if distance == 0:
        return 0.0
    remaining, tick = distance, 0
    while True:
        fall = velocity(tick)
        if remaining <= fall:
            return tick + remaining / fall
        remaining -= fall
        tick += 1


def calibration_path():
    """calibration.json: BARITONE_BEDROCK_CALIBRATION, else the one in the repository root."""
    return os.environ.get("BARITONE_BEDROCK_CALIBRATION") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "calibration.json")


def load_calibration(path=None):
    """The calibration file as a dict, or {} when there is none. Its "speeds" hold blocks a second by the keys of
    DEFAULT_SPEEDS; the other sections belong to control.py."""
    path = path or calibration_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


class Costs:
    """The ActionCosts constants, named as in Java, built from a speed table."""

    def __init__(self, speeds=None):
        s = dict(DEFAULT_SPEEDS)
        s.update(speeds or {})
        self.speeds = s
        self.WALK_ONE_BLOCK_COST = 20 / s["walk"]                       # 4.633
        self.WALK_ONE_IN_WATER_COST = 20 / s["walk_in_water"]           # 9.091
        self.SWIM_ONE_BLOCK_COST = 20 / s["swim"]                       # 6.993
        self.WALK_ONE_OVER_SOUL_SAND_COST = self.WALK_ONE_BLOCK_COST * 2
        self.LADDER_UP_ONE_COST = 20 / s["ladder_up"]                   # 8.511
        self.LADDER_DOWN_ONE_COST = 20 / s["ladder_down"]               # 6.667
        self.SNEAK_ONE_BLOCK_COST = 20 / s["sneak"]                     # 15.385
        self.SPRINT_ONE_BLOCK_COST = 20 / s["sprint"]                   # 3.564
        self.SPRINT_MULTIPLIER = self.SPRINT_ONE_BLOCK_COST / self.WALK_ONE_BLOCK_COST   # 0.769
        self.WALK_OFF_BLOCK_COST = self.WALK_ONE_BLOCK_COST * 0.8       # 3.706: 0.5 to the edge, 0.3 to start falling
        self.CENTER_AFTER_FALL_COST = self.WALK_ONE_BLOCK_COST - self.WALK_OFF_BLOCK_COST   # 0.927
        self.COST_INF = COST_INF
        self.FALL_N_BLOCKS_COST = [distance_to_ticks(i) for i in range(4097)]
        self.FALL_1_25_BLOCKS_COST = distance_to_ticks(1.25)            # 6.234
        self.FALL_0_25_BLOCKS_COST = distance_to_ticks(0.25)            # 3.071
        # a jump rises 1.25 and falls 0.25 back onto the block above; the parabola is symmetric (AC:57-69)
        self.JUMP_ONE_BLOCK_COST = self.FALL_1_25_BLOCKS_COST - self.FALL_0_25_BLOCKS_COST   # 3.163

    @classmethod
    def from_calibration(cls, path=None):
        return cls(load_calibration(path).get("speeds"))


DEFAULT = Costs()
