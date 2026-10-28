"""A*: PathNode (pathing/calc/PathNode.java), BinaryHeapOpenSet (pathing/calc/openset/BinaryHeapOpenSet.java),
AbstractNodeCostSearch (pathing/calc/AbstractNodeCostSearch.java, "ANCS"), AStarPathFinder
(pathing/calc/AStarPathFinder.java, "AS"), Favoring (utils/pathing/Favoring.java) and PathCalculationResult
(api/utils/PathCalculationResult.java).

    finder = AStarPathFinder(real_start, start_x, start_y, start_z, goal, Favoring(previous_path, 0.5), ctx)
    result = finder.calculate(primary_ms=500, failure_ms=2000)
    if result.type in (PathCalculationResult.SUCCESS_TO_GOAL, PathCalculationResult.SUCCESS_SEGMENT):
        path = result.path        # post-processed, cut at the load boundary and the static cutoff

The inner loop is written for CPython: every name it touches is a local, the 22 moves are a tuple of plain
functions, nodes live in one dict keyed by context.pack(x, y, z) (Baritone's 16x8x16 cells, ANCS:48-57, buy
nothing here), and a block's flags are fetched once per search through ctx.at (context.py).
"""
import math
import time

from .costs import COST_INF
from .context import pack, unpack
from .moves import MOVES, MoveResult
from .path import Path


class PathNode:
    """PathNode.java: a position, its heuristic, the best cost found to it and where that came from."""
    __slots__ = ("x", "y", "z", "estimated_cost_to_goal", "cost", "combined_cost", "previous", "heap_position", "favor")

    def __init__(self, x, y, z, goal):
        self.previous = None                                                 # PathNode:75
        self.cost = COST_INF                                                 # PathNode:76
        h = goal.heuristic(x, y, z)                                          # PathNode:77
        if h != h:                                                           # PathNode:78-85: NaN
            raise RuntimeError(f"{goal} calculated implausible heuristic NaN at {x} {y} {z}")
        self.estimated_cost_to_goal = h
        self.combined_cost = 0.0
        self.heap_position = -1                                              # PathNode:86
        self.favor = 0.0                                                     # PathNode:68-71: 0 means not looked up yet
        self.x = x
        self.y = y
        self.z = z

    def is_open(self):
        return self.heap_position != -1                                      # PathNode:92-94

    @property
    def pos(self):
        return (self.x, self.y, self.z)

    def __repr__(self):
        return f"PathNode({self.x}, {self.y}, {self.z}, cost={self.cost:.3f}, h={self.estimated_cost_to_goal:.3f})"


class BinaryHeapOpenSet:
    """BinaryHeapOpenSet.java: a 1-based binary min-heap on combined_cost with decrease-key through the node's
    heap_position. keys[i] mirrors array[i].combined_cost so sifting compares floats next to each other."""

    def __init__(self):
        self.array = [None]                                                  # index 0 unused
        self.keys = [0.0]
        self.size = 0

    def __len__(self):
        return self.size

    def is_empty(self):
        return self.size == 0

    def insert(self, value):
        """BinaryHeapOpenSet:66-73."""
        self.array.append(None)
        self.keys.append(0.0)
        self.size += 1
        self._sift_up(self.size, value, value.combined_cost)

    def update(self, value):
        """BinaryHeapOpenSet:76-79: cost only ever goes down, so the node only ever goes up."""
        self._sift_up(value.heap_position, value, value.combined_cost)

    def _sift_up(self, index, value, cost):
        """BinaryHeapOpenSet:81-96."""
        array = self.array
        keys = self.keys
        parent = index >> 1
        while index > 1 and keys[parent] > cost:
            parent_node = array[parent]
            array[index] = parent_node
            keys[index] = keys[parent]
            parent_node.heap_position = index
            index = parent
            parent = index >> 1
        array[index] = value
        keys[index] = cost
        value.heap_position = index

    def remove_lowest(self):
        """BinaryHeapOpenSet:104-139."""
        if self.size == 0:
            raise RuntimeError("Cannot remove from empty heap")
        array = self.array
        keys = self.keys
        result = array[1]
        result.heap_position = -1
        value = array.pop()
        cost = keys.pop()
        self.size -= 1
        size = self.size
        if size == 0:
            return result
        index = 1
        child = 2
        while child <= size:
            child_cost = keys[child]
            if child < size and keys[child + 1] < child_cost:
                child += 1
                child_cost = keys[child]
            if cost <= child_cost:
                break
            child_node = array[child]
            array[index] = child_node
            keys[index] = child_cost
            child_node.heap_position = index
            index = child
            child = index << 1
        array[index] = value
        keys[index] = cost
        value.heap_position = index
        return result


class Favoring:
    """Favoring.java:39-56: a cost multiplier per position, the previous path's positions at
    backtrackCostFavoringCoefficient (0.5), everything else 1. Mob avoidance (Favoring:31-37) is not ported."""

    def __init__(self, previous_path=None, coefficient=0.5):
        self.favorings = {}
        if coefficient != 1.0 and previous_path is not None:                 # Favoring:43-46
            for x, y, z in previous_path.positions():
                self.favorings[pack(x, y, z)] = coefficient

    def is_empty(self):
        return not self.favorings                                            # Favoring:48-50

    def calculate(self, key):
        return self.favorings.get(key, 1.0)                                  # Favoring:52-54

    def add(self, x, y, z, coefficient):
        self.favorings[pack(x, y, z)] = coefficient


class PathCalculationResult:
    """PathCalculationResult.java: a type and, for the two successes, the path."""
    SUCCESS_TO_GOAL = "SUCCESS_TO_GOAL"
    SUCCESS_SEGMENT = "SUCCESS_SEGMENT"
    FAILURE = "FAILURE"
    CANCELLATION = "CANCELLATION"
    EXCEPTION = "EXCEPTION"
    TYPES = (SUCCESS_TO_GOAL, SUCCESS_SEGMENT, FAILURE, CANCELLATION, EXCEPTION)

    def __init__(self, type_, path=None, error=None):
        if type_ not in self.TYPES:
            raise ValueError(type_)
        self.type = type_
        self.path = path
        self.error = error

    @property
    def success(self):
        return self.path is not None

    def __repr__(self):
        return f"PathCalculationResult({self.type}, {self.path!r})"


class AbstractNodeCostSearch:
    """ANCS: the bookkeeping around calculate0: the node map, cancel, the best-so-far trackers and the result."""

    COEFFICIENTS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)                      # ANCS:81
    INVERSE_COEFFICIENTS = tuple(1.0 / c for c in COEFFICIENTS)              # ANCS:84-90
    MIN_DIST_PATH = 5                                                        # ANCS:95
    MIN_IMPROVEMENT = 0.01                                                   # ANCS:104

    def __init__(self, real_start, start_x, start_y, start_z, goal, ctx):
        self.real_start = tuple(real_start)                                  # ANCS:107
        self.start_x, self.start_y, self.start_z = start_x, start_y, start_z
        self.goal = goal
        self.ctx = ctx
        self.nodes = {}                                                      # ANCS:55: keyed by pack(x, y, z)
        self.start_node = None
        self.most_recent_considered = None
        self.best_so_far_nodes = [None] * len(self.COEFFICIENTS)              # ANCS:69
        self.is_finished = False
        self.cancel_requested = False
        self.num_nodes_considered = 0
        self.num_movements_considered = 0
        self.num_empty_chunk = 0

    def cancel(self):
        """ANCS:116-118: safe from another thread; the loop reads the flag every node."""
        self.cancel_requested = True

    def calculate(self, primary_ms=None, failure_ms=None):
        """ANCS:121-162. The timeouts default to the settings' primaryTimeoutMS and failureTimeoutMS."""
        if self.is_finished:
            raise RuntimeError("Path finder cannot be reused!")
        settings = self.ctx.settings
        if primary_ms is None:
            primary_ms = settings.primaryTimeoutMS
        if failure_ms is None:
            failure_ms = settings.failureTimeoutMS
        self.cancel_requested = False
        self.ctx.claim_search_caches()                                       # ANCS:126
        try:
            path = self.calculate0(primary_ms, failure_ms)                   # ANCS:128
            if path is not None:
                path = path.post_process()
            if self.cancel_requested:                                        # ANCS:129-131
                return PathCalculationResult(PathCalculationResult.CANCELLATION)
            if path is None:                                                 # ANCS:132-134
                return PathCalculationResult(PathCalculationResult.FAILURE)
            path = path.cutoff_at_loaded_chunks(self.ctx)                    # ANCS:136
            path = path.static_cutoff(self.goal)                             # ANCS:144
            if self.goal.is_in_goal(*path.get_dest()):                       # ANCS:148-152
                return PathCalculationResult(PathCalculationResult.SUCCESS_TO_GOAL, path)
            return PathCalculationResult(PathCalculationResult.SUCCESS_SEGMENT, path)
        except Exception as error:                                           # ANCS:153-156
            return PathCalculationResult(PathCalculationResult.EXCEPTION, error=error)
        finally:
            self.is_finished = True                                          # ANCS:159
            self.ctx.release_search_caches()                                 # ANCS:160

    def calculate0(self, primary_ms, failure_ms):
        raise NotImplementedError

    def get_dist_from_start_sq(self, node):
        """ANCS:174-179."""
        dx = node.x - self.start_x
        dy = node.y - self.start_y
        dz = node.z - self.start_z
        return dx * dx + dy * dy + dz * dz

    def get_node_at_position(self, x, y, z, key):
        """ANCS:194-220, with one dict in place of the cells."""
        node = self.nodes.get(key)
        if node is None:
            node = PathNode(x, y, z, self.goal)
            self.nodes[key] = node
        return node

    def path_to_most_recent_node_considered(self):
        """ANCS:222-225."""
        if self.most_recent_considered is None:
            return None
        return Path(self.real_start, self.start_node, self.most_recent_considered, 0, self.goal, self.ctx)

    def best_path_so_far(self):
        """ANCS:227-230."""
        return self.best_so_far(False, 0)

    def best_so_far(self, log_info=False, num_nodes=0):
        """ANCS:232-266: the first best-so-far node, by increasing coefficient, more than MIN_DIST_PATH from the
        start; None when even the loosest coefficient got no further."""
        if self.start_node is None:
            return None
        best_dist = 0.0
        min_dist_sq = self.MIN_DIST_PATH * self.MIN_DIST_PATH
        for i, node in enumerate(self.best_so_far_nodes):
            if node is None:
                continue
            dist = self.get_dist_from_start_sq(node)
            if dist > best_dist:
                best_dist = dist
            if dist > min_dist_sq:                                           # ANCS:245
                if log_info:
                    if self.COEFFICIENTS[i] >= 3:
                        print("Warning: cost coefficient is greater than three! Probably means that the path I "
                              "found is pretty terrible (like sneak-bridging for dozens of blocks)")
                    print(f"Path goes for {math.sqrt(dist):.1f} blocks, A* cost coefficient {self.COEFFICIENTS[i]}")
                return Path(self.real_start, self.start_node, node, num_nodes, self.goal, self.ctx)   # ANCS:255
        if log_info:
            print(f"Even with a cost coefficient of {self.COEFFICIENTS[-1]}, I couldn't get more than "
                  f"{math.sqrt(best_dist):.1f} blocks. No path found =(")
        return None

    def get_goal(self):
        return self.goal

    def get_start(self):
        return (self.start_x, self.start_y, self.start_z)

    def map_size(self):
        return len(self.nodes)


# (x offset, y offset, z offset, dynamicXZ, kind, cost function, move) per move, in Baritone's order; the loop
# calls the cost functions directly, in the three shapes Move.kind names, instead of going through Moves.apply
MOVE_TABLE = tuple((m.x_offset, m.y_offset, m.z_offset, m.dynamic_xz, m.kind, m.cost, m) for m in MOVES)


class AStarPathFinder(AbstractNodeCostSearch):
    """AS: the actual A*."""

    def __init__(self, real_start, start_x, start_y, start_z, goal, favoring, ctx):
        super().__init__(real_start, start_x, start_y, start_z, goal, ctx)
        self.favoring = favoring if favoring is not None else Favoring()
        self.log = False

    @classmethod
    def create(cls, start, goal, ctx, favoring=None, real_start=None):
        """A finder from a start position (the feet), the way PathingBehavior.createPathfinder builds one."""
        start = tuple(start)
        return cls(start if real_start is None else real_start, start[0], start[1], start[2], goal, favoring, ctx)

    def calculate0(self, primary_timeout, failure_timeout):
        """AS:56-214: the loop. Returns a Path (to the goal, or the best so far) or None."""
        ctx = self.ctx
        goal = self.goal
        settings = ctx.settings
        min_y = ctx.min_y                                                    # AS:57-58
        max_y = ctx.max_y
        sx, sy, sz = self.start_x, self.start_y, self.start_z
        nodes = self.nodes
        nodes_get = nodes.get
        start_node = self.get_node_at_position(sx, sy, sz, pack(sx, sy, sz))   # AS:59
        self.start_node = start_node
        start_node.cost = 0.0                                                # AS:60
        start_node.combined_cost = start_node.estimated_cost_to_goal         # AS:61
        open_set = BinaryHeapOpenSet()                                       # AS:62
        open_set.insert(start_node)                                          # AS:63
        coefficients = len(self.COEFFICIENTS)
        best_heuristic_so_far = [start_node.estimated_cost_to_goal] * coefficients   # AS:64-68
        best_so_far = self.best_so_far_nodes
        for i in range(coefficients):
            best_so_far[i] = start_node
        res = MoveResult()                                                   # AS:69
        start_time = time.monotonic()                                        # AS:71
        primary_timeout_time = start_time + primary_timeout / 1000.0         # AS:76-77
        failure_timeout_time = start_time + failure_timeout / 1000.0
        failing = True                                                       # AS:78
        num_nodes = 0                                                        # AS:79
        num_movements_considered = 0                                         # AS:80
        num_empty_chunk = 0                                                  # AS:81
        favoring = self.favoring
        is_favoring = not favoring.is_empty()                                # AS:82
        favor_of = favoring.calculate
        pathing_max_chunk_border_fetch = settings.pathingMaxChunkBorderFetch     # AS:84
        minimum_improvement = self.MIN_IMPROVEMENT if settings.minimumImprovementRepropagation else 0.0   # AS:85
        move_table = MOVE_TABLE                                              # AS:86
        inverse = self.INVERSE_COEFFICIENTS
        smallest_inverse = inverse[-1]
        coefficient_range = range(coefficients)
        best_max = max(best_heuristic_so_far)
        min_dist_sq = self.MIN_DIST_PATH * self.MIN_DIST_PATH
        is_loaded = ctx.is_loaded
        is_in_goal = goal.is_in_goal
        unknown = ctx._unknown
        remove_lowest = open_set.remove_lowest
        insert = open_set.insert
        update = open_set.update
        monotonic = time.monotonic
        inf = COST_INF
        while open_set.size and num_empty_chunk < pathing_max_chunk_border_fetch and not self.cancel_requested:   # AS:87
            if num_nodes & 63 == 0:                                          # AS:88: once every 64 nodes
                now = monotonic()                                            # AS:89
                if now >= failure_timeout_time or (not failing and now >= primary_timeout_time):   # AS:90-92
                    break
            current = remove_lowest()                                        # AS:99
            self.most_recent_considered = current                            # AS:100
            num_nodes += 1                                                   # AS:101
            cx = current.x
            cy = current.y
            cz = current.z
            if is_in_goal(cx, cy, cz):                                       # AS:102
                self.num_nodes_considered = num_nodes                        # AS:103-104
                self.num_movements_considered = num_movements_considered
                self.num_empty_chunk = num_empty_chunk
                if self.log:
                    print(f"Took {(monotonic() - start_time) * 1000:.0f}ms, {num_movements_considered} movements considered")
                return Path(self.real_start, start_node, current, num_nodes, goal, ctx)   # AS:106
            current_cost = current.cost
            cx4 = cx >> 4
            cz4 = cz >> 4
            for dx, dy, dz, dynamic_xz, kind, cost, move in move_table:      # AS:108
                new_x = cx + dx                                              # AS:109-110
                new_z = cz + dz
                if (new_x >> 4 != cx4 or new_z >> 4 != cz4) and not is_loaded(new_x, new_z):   # AS:111
                    if not dynamic_xz:                                       # AS:113-115: counted only when certain
                        num_empty_chunk += 1
                    continue
                # AS:118-120: no world border here
                new_y = cy + dy
                if new_y > max_y or new_y < min_y:                           # AS:121-123
                    continue
                unknown[0] = False
                num_movements_considered += 1                                # AS:126
                if kind == 0:                                                # AS:124-125 moves.apply, by kind
                    action_cost = cost(ctx, cx, cy, cz, new_x, new_z)
                    rx = new_x
                    ry = new_y
                    rz = new_z
                elif kind == 1:
                    action_cost = cost(ctx, cx, cy, cz)
                    rx = new_x
                    ry = new_y
                    rz = new_z
                else:
                    res.cost = inf                                           # AS:124 res.reset()
                    cost(ctx, cx, cy, cz, new_x, new_z, res)
                    action_cost = res.cost                                   # AS:127
                    rx = res.x
                    ry = res.y
                    rz = res.z
                if unknown[0]:
                    # departs: plan Fidelity 2. The move read an unread block: skipped and counted toward
                    # pathingMaxChunkBorderFetch, where Baritone would have read air
                    num_empty_chunk += 1
                    continue
                if action_cost >= inf:                                       # AS:128-130
                    continue
                if action_cost <= 0 or action_cost != action_cost:           # AS:131-139
                    raise RuntimeError(f"{move} from {cx} {cy} {cz} calculated implausible cost {action_cost}")
                # AS:141-143: no world border here
                if kind == 2 and not dynamic_xz and (rx != new_x or rz != new_z):   # AS:144-155
                    raise RuntimeError(f"{move} from {cx} {cy} {cz} ended at x z {rx} {rz} instead of {new_x} {new_z}")
                # AS:156-165: a static move lands on its offsets by construction
                key = (rx << 38) + (rz << 12) + ry                           # AS:166 (context.pack, inlined)
                neighbor = nodes_get(key)                                    # AS:167
                if neighbor is None:
                    neighbor = nodes[key] = PathNode(rx, ry, rz, goal)
                if is_favoring:                                              # AS:168-176: once per node, not per edge
                    favor = neighbor.favor
                    if favor == 0:
                        favor = neighbor.favor = favor_of(key)
                    action_cost *= favor
                tentative_cost = current_cost + action_cost                  # AS:177
                if neighbor.cost - tentative_cost > minimum_improvement:     # AS:178
                    neighbor.previous = current                              # AS:179
                    neighbor.cost = tentative_cost                           # AS:180
                    estimate = neighbor.estimated_cost_to_goal
                    neighbor.combined_cost = tentative_cost + estimate       # AS:181
                    if neighbor.heap_position != -1:                         # AS:182-186
                        update(neighbor)
                    else:
                        insert(neighbor)
                    # AS:187-196. The smallest of the seven heuristics is the one with the smallest 1/coefficient;
                    # when even that cannot beat the largest best-so-far, none can, and the loop is skipped
                    if best_max - (estimate + tentative_cost * smallest_inverse) > minimum_improvement:
                        changed = False
                        for i in coefficient_range:
                            heuristic = estimate + tentative_cost * inverse[i]   # AS:188
                            if best_heuristic_so_far[i] - heuristic > minimum_improvement:   # AS:189
                                best_heuristic_so_far[i] = heuristic         # AS:190
                                best_so_far[i] = neighbor                    # AS:191
                                changed = True
                                if failing:                                  # AS:192-194
                                    ddx = rx - sx
                                    ddy = ry - sy
                                    ddz = rz - sz
                                    if ddx * ddx + ddy * ddy + ddz * ddz > min_dist_sq:
                                        failing = False
                        if changed:
                            best_max = max(best_heuristic_so_far)
        self.num_nodes_considered = num_nodes                                # AS:200-201
        self.num_movements_considered = num_movements_considered
        self.num_empty_chunk = num_empty_chunk
        if self.cancel_requested:                                            # AS:202-204
            return None
        elapsed = monotonic() - start_time
        if self.log:                                                         # AS:205-208
            print(f"{num_movements_considered} movements considered")
            print(f"Open set size: {open_set.size}")
            print(f"PathNode map size: {self.map_size()}")
            print(f"{int(num_nodes / elapsed) if elapsed else 0} nodes per second")
        result = self.best_so_far(self.log, num_nodes)                       # AS:209
        if result is not None and self.log:
            print(f"Took {elapsed * 1000:.0f}ms, {num_movements_considered} movements considered")
        return result


__all__ = ["PathNode", "BinaryHeapOpenSet", "Favoring", "PathCalculationResult", "AbstractNodeCostSearch",
           "AStarPathFinder", "MOVE_TABLE", "pack", "unpack"]
