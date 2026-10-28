"""The controller the caller supplies: the only thing in this package that moves the player.

Baritone sets the player's rotation and keys directly (baritone/utils/PlayerMovementInput.java); here the player is
a real Bedrock client holding an Xbox pad, so the executor steers with sticks and taps buttons through this
protocol, the way mineflayer-pathfinder's createHuman (java/mineflayer-pathfinder/lib/human.js:56) drives a bot
through its control-state gestures rather than teleporting its view.

The FTUE skill's Pad (C:/GitHub/.claude/skills/ftue/ftue.py, class Pad) already satisfies the protocol: its
state(left, right, held) and press(*names, hold, gap) have exactly these signatures, so a caller passes a Pad()
through unchanged. Anything else with the same two methods will do: a vgamepad wrapper, a ViGEm bridge on another
machine, or NullController below in tests.
"""
from typing import Protocol, Sequence, Tuple, runtime_checkable

# the button names every Controller understands; a Controller may accept more, never fewer
BUTTONS = ("a", "b", "x", "y", "lb", "rb", "lt", "rt", "ls", "rs", "up", "down", "left", "right", "start", "back")

Stick = Tuple[float, float]   # (x, y), each -1.0 to 1.0, y up (forward) on the left stick, pitch up on the right


@runtime_checkable
class Controller(Protocol):
    def state(self, left: Stick = (0.0, 0.0), right: Stick = (0.0, 0.0), held: Sequence[str] = ()) -> None:
        """Set both sticks and the set of buttons held down, and leave them so until the next call. The executor
        calls this every tick it steers; state() with no arguments lets everything go. held names come from
        BUTTONS: "a" jump, "b" sneak (held, since toggle crouch is off on a gamepad), "rt" attack or break,
        "lt" use or place, "ls" the sprint click."""

    def press(self, *names: str, hold: float = 0.12, gap: float = 0.35) -> None:
        """Tap each named button in turn: down for hold seconds, then gap seconds before the next. A name may carry
        its own hold as "lt:0.3", and names joined by "+" go down together ("b+lt")."""


class NullController:
    """A Controller that moves nothing and remembers every call, for tests and dry runs. held is the current set
    of buttons down, left and right the sticks as last set."""

    def __init__(self):
        self.calls = []
        self.left, self.right, self.held = (0.0, 0.0), (0.0, 0.0), frozenset()
        self.pressed = []

    def state(self, left=(0.0, 0.0), right=(0.0, 0.0), held=()):
        self.left, self.right, self.held = (float(left[0]), float(left[1])), (float(right[0]), float(right[1])), frozenset(held)
        self.calls.append(("state", self.left, self.right, tuple(sorted(self.held))))

    def press(self, *names, hold=0.12, gap=0.35):
        self.pressed.extend(names)
        self.calls.append(("press", tuple(names), hold, gap))
