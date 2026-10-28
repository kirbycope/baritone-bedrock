"""Paths: Path (pathing/calc/Path.java), CutoffPath (pathing/path/CutoffPath.java), SplicedPath
(pathing/path/SplicedPath.java), with IPath's defaults (api/pathing/calc/IPath.java) and PathBase's two cutoffs
(utils/pathing/PathBase.java).

A path is a list of positions, (x, y, z) tuples, and the list of Movements between them: movements()[i] goes from
positions()[i] to positions()[i + 1]. A Path straight out of A* has no movements until post_process() assembles
them (Path.java:146-165); the search does that before handing the path back.
"""
from .costs import COST_INF
from .moves import MOVES
from .settings import Settings


class PathBase:
    """IPath's default methods and PathBase's cutoffs."""
    settings = None     # the Settings the cutoffs read (Baritone reads globals); set by the subclasses

    def positions(self):
        raise NotImplementedError

    def movements(self):
        raise NotImplementedError

    def get_goal(self):
        raise NotImplementedError

    def get_num_nodes_considered(self):
        raise NotImplementedError

    @property
    def num_nodes_considered(self):
        return self.get_num_nodes_considered()

    def length(self):
        """IPath:66-68: the number of positions."""
        return len(self.positions())

    def __len__(self):
        return self.length()

    def get_src(self):
        """IPath:81-83."""
        return self.positions()[0]

    def get_dest(self):
        """IPath:89-92."""
        return self.positions()[-1]

    def ticks_remaining_from(self, path_position):
        """IPath:99-107: the cached costs of the movements from an index on."""
        total = 0.0
        for movement in self.movements()[path_position:]:
            total += movement.get_cost()
        return total

    def post_process(self):
        raise NotImplementedError("only a Path straight out of the search is post-processed")

    def cutoff_at_loaded_chunks(self, ctx):
        """PathBase.java:30-43: with cutoffAtLoadBoundary (on here, plan Fidelity 3), cut at the first position
        whose column is not loaded. As written, that position is the last one kept (CutoffPath(this, i) includes
        position i), so a cut path can end on the unloaded column."""
        settings = ctx.settings if ctx is not None else self._settings()
        if not settings.cutoffAtLoadBoundary:                                # PathBase:32-34
            return self
        for i, (x, _y, z) in enumerate(self.positions()):                   # PathBase:36-41
            if not ctx.is_loaded(x, z):
                return CutoffPath(self, i)
        return self

    def static_cutoff(self, destination):
        """PathBase.java:45-58: drop the tail of a long path that does not reach the goal."""
        settings = self._settings()
        minimum = settings.pathCutoffMinimumLength                           # PathBase:47
        if self.length() < minimum:                                          # PathBase:48-50
            return self
        if destination is None or destination.is_in_goal(*self.get_dest()): # PathBase:51-53
            return self
        factor = settings.pathCutoffFactor                                   # PathBase:54
        new_length = int((self.length() - minimum) * factor) + minimum - 1   # PathBase:55
        return CutoffPath(self, new_length)                                  # PathBase:56

    def sanity_check(self):
        """IPath:139-166."""
        path = self.positions()
        movements = self.movements()
        if self.get_src() != path[0]:
            raise RuntimeError("Start node does not equal first path element")
        if self.get_dest() != path[-1]:
            raise RuntimeError("End node does not equal last path element")
        if len(path) != len(movements) + 1:
            raise RuntimeError("Size of path array is unexpected")
        seen_so_far = set()
        for i in range(len(path) - 1):
            src = path[i]
            dest = path[i + 1]
            movement = movements[i]
            if src != movement.src:
                raise RuntimeError("Path source is not equal to the movement source")
            if dest != movement.dest:
                raise RuntimeError("Path destination is not equal to the movement destination")
            if src in seen_so_far:
                raise RuntimeError("Path doubles back on itself, making a loop")
            seen_so_far.add(src)

    def _settings(self):
        return self.settings if self.settings is not None else Settings()

    def __repr__(self):
        p = self.positions()
        return f"{type(self).__name__}({p[0]} -> {p[-1]}, {len(p)} positions)"


class Path(PathBase):
    """Path.java: built backward from the end node's `previous` chain."""

    def __init__(self, real_start, start_node, end_node, num_nodes, goal, ctx):
        self.end = (end_node.x, end_node.y, end_node.z)                      # Path:73
        self.num_nodes = num_nodes                                           # Path:74
        self._movements = []                                                 # Path:75
        self.goal = goal                                                     # Path:76
        self.ctx = ctx                                                       # Path:77
        self.settings = ctx.settings
        self.verified = False
        current = end_node                                                   # Path:79
        temp_path = []
        temp_nodes = []
        while current is not None:                                           # Path:82-86
            temp_nodes.append(current)
            temp_path.append((current.x, current.y, current.z))
            current = current.previous
        # Path:88-101: if the player stands somewhere other than where A* started and A* gave no movements, add a
        # fake node so one movement can be built to the single position in the path (pull request #4519)
        start_node_pos = (start_node.x, start_node.y, start_node.z)
        real_start = tuple(real_start)
        if real_start != start_node_pos and start_node_pos == self.end:
            self.start = real_start                                          # Path:94
            fake_node = type(start_node)(real_start[0], real_start[1], real_start[2], goal)   # Path:95
            fake_node.cost = 0.0                                             # Path:96
            temp_nodes.append(fake_node)                                     # Path:97
            temp_path.append(real_start)                                     # Path:98
        else:
            self.start = start_node_pos                                      # Path:100
        temp_path.reverse()                                                  # Path:104-105
        temp_nodes.reverse()
        self.path = temp_path
        self.nodes = temp_nodes

    def get_goal(self):
        return self.goal

    def assemble_movements(self):
        """Path.java:114-127: a movement per pair of positions; True when one became impossible."""
        if not self.path or self._movements:
            raise RuntimeError("Path must not be empty")
        for i in range(len(self.path) - 1):
            cost = self.nodes[i + 1].cost - self.nodes[i].cost               # Path:119
            move = self.run_backwards(self.path[i], self.path[i + 1], cost)  # Path:120
            if move is None:                                                 # Path:121-125
                return True
            self._movements.append(move)
        return False

    def run_backwards(self, src, dest, cost):
        """Path.java:129-144: the first Move whose movement from src ends at dest, its cost overridden to the
        smaller of its calculated cost and the node cost difference (favoring may have skewed the latter)."""
        for move in MOVES:
            movement = move.apply0(self.ctx, src)                            # Path:131
            if movement.dest == dest:                                        # Path:132
                movement.override(min(movement.calculate_cost(self.ctx), cost))   # Path:135-136
                return movement
        return None                                                          # Path:142

    def post_process(self):
        """Path.java:146-165."""
        if self.verified:
            raise RuntimeError("Path must not be verified twice")
        self.verified = True
        failed = self.assemble_movements()                                   # Path:151
        for movement in self._movements:                                     # Path:152
            movement.check_loaded_chunk(self.ctx)
        if failed:                                                           # Path:154: a movement became impossible
            res = CutoffPath(self, len(self._movements))
            if len(res.movements()) != len(self._movements):
                raise RuntimeError("Path has wrong size after cutoff")
            return res
        self.sanity_check()                                                  # Path:162
        return self

    def movements(self):
        if not self.verified:                                                # Path:169-173
            raise RuntimeError("Path not yet verified")
        return self._movements

    def positions(self):
        return self.path

    def get_num_nodes_considered(self):
        return self.num_nodes

    def get_src(self):
        return self.start

    def get_dest(self):
        return self.end


class CutoffPath(PathBase):
    """CutoffPath.java: a prefix (or a slice) of another path."""

    def __init__(self, prev, last_position_to_include, *, first_position_to_include=0):
        first, last = first_position_to_include, last_position_to_include
        self.path = list(prev.positions()[first:last + 1])                  # CutoffPath:40
        self._movements = list(prev.movements()[first:last])                 # CutoffPath:41
        self.num_nodes = prev.get_num_nodes_considered()                     # CutoffPath:42
        self.goal = prev.get_goal()                                          # CutoffPath:43
        self.settings = prev.settings
        self.sanity_check()                                                  # CutoffPath:44

    def get_goal(self):
        return self.goal

    def movements(self):
        return self._movements

    def positions(self):
        return self.path

    def get_num_nodes_considered(self):
        return self.num_nodes


class SplicedPath(PathBase):
    """SplicedPath.java: two segments joined where the first ends and the second begins."""

    def __init__(self, path, movements, num_nodes_considered, goal, settings=None):
        self.path = path                                                     # SplicedPath:38-43
        self._movements = movements
        self.num_nodes = num_nodes_considered
        self.goal = goal
        self.settings = settings
        self.sanity_check()

    def get_goal(self):
        return self.goal

    def movements(self):
        return self._movements

    def positions(self):
        return self.path

    def get_num_nodes_considered(self):
        return self.num_nodes

    def length(self):
        return len(self.path)                                                # SplicedPath:67-69

    @staticmethod
    def try_splice(first, second, allow_overlap_cutoff=False):
        """SplicedPath.java:72-105: None unless the second path starts where the first ends. If the second
        contains an earlier position of the first, the first is cut there when allowed, otherwise refused."""
        if second is None or first is None:                                  # SplicedPath:73-75
            return None
        if first.get_dest() != second.get_src():                             # SplicedPath:76-78
            return None
        second_pos = set(second.positions())                                 # SplicedPath:79
        first_position_in_second = -1
        first_positions = first.positions()
        for i in range(first.length() - 1):                                  # SplicedPath:81-86: the last may (must) overlap
            if first_positions[i] in second_pos:
                first_position_in_second = i
                break
        if first_position_in_second != -1:                                   # SplicedPath:87-93
            if not allow_overlap_cutoff:
                return None
        else:
            first_position_in_second = first.length() - 1
        position_in_second = second.positions().index(first_positions[first_position_in_second])   # SplicedPath:94
        if not allow_overlap_cutoff and position_in_second != 0:             # SplicedPath:95-97
            raise RuntimeError("Paths to be spliced are overlapping incorrectly")
        positions = list(first_positions[:first_position_in_second + 1])    # SplicedPath:98-101
        movements = list(first.movements()[:first_position_in_second])
        positions.extend(second.positions()[position_in_second + 1:second.length()])      # SplicedPath:103
        movements.extend(second.movements()[position_in_second:second.length() - 1])      # SplicedPath:104
        return SplicedPath(positions, movements, first.get_num_nodes_considered() + second.get_num_nodes_considered(),
                           first.get_goal(), getattr(first, "settings", None))              # SplicedPath:105


__all__ = ["PathBase", "Path", "CutoffPath", "SplicedPath", "COST_INF"]
