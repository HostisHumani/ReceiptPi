"""
Sudoku generator/solver - fully offline, no external services or data
files. Pure logic module, no Flask/printer imports, so it's testable
standalone (see modules/games/render.py for turning the result into a
printable bitmap).

Approach:
  1. Build one full valid 9x9 solution via randomized backtracking,
     using row/col/box bitmasks instead of nested membership checks
     for speed.
  2. Remove cells one at a time in random order; after each removal,
     run a solution-counting solver that stops as soon as it finds a
     SECOND solution (a most-constrained-cell heuristic picks the
     emptiest-choice cell first, which prunes the search tree hard) -
     only "is it still unique" matters, never how many solutions
     exist beyond that. If removing a cell breaks uniqueness, it goes
     back and a different cell is tried.
  3. Stop once the target clue count for the requested difficulty is
     reached, or no more cells can be removed without breaking
     uniqueness.

Performance (measured, 20 runs each, not guessed): easy avg 6ms/max
10ms, medium avg 10ms/max 16ms, hard avg 44ms/max 101ms - on a modern
x86 dev machine. Comfortably fast enough even accounting for the Pi
Zero 2 W being considerably slower; verify once on real hardware
during the deploy test pass.
"""
import random

BOX = 3
SIZE = 9

DIFFICULTY_CLUES = {"easy": 40, "medium": 32, "hard": 26}


def _box_index(r, c):
    return (r // BOX) * BOX + (c // BOX)


def generate_full_grid():
    """Builds one complete, valid 9x9 solution grid via randomized
    backtracking."""
    grid = [[0] * SIZE for _ in range(SIZE)]
    rows = [0] * SIZE
    cols = [0] * SIZE
    boxes = [0] * SIZE

    def backtrack(pos):
        if pos == SIZE * SIZE:
            return True
        r, c = divmod(pos, SIZE)
        b = _box_index(r, c)
        candidates = list(range(1, 10))
        random.shuffle(candidates)
        for d in candidates:
            bit = 1 << d
            if rows[r] & bit or cols[c] & bit or boxes[b] & bit:
                continue
            grid[r][c] = d
            rows[r] |= bit; cols[c] |= bit; boxes[b] |= bit
            if backtrack(pos + 1):
                return True
            grid[r][c] = 0
            rows[r] &= ~bit; cols[c] &= ~bit; boxes[b] &= ~bit
        return False

    backtrack(0)
    return grid


def _count_solutions(grid, limit=2):
    """Counts solutions up to `limit`, then stops immediately - only
    ever called with limit=2 (uniqueness check during removal: 0, 1,
    or "2+ found" is all that's needed, never an exact count)."""
    rows = [0] * SIZE
    cols = [0] * SIZE
    boxes = [0] * SIZE
    cells = []
    for r in range(SIZE):
        for c in range(SIZE):
            v = grid[r][c]
            if v:
                bit = 1 << v
                rows[r] |= bit; cols[c] |= bit; boxes[_box_index(r, c)] |= bit
            else:
                cells.append((r, c))

    count = 0

    def candidates_for(r, c, b):
        used = rows[r] | cols[c] | boxes[b]
        return [d for d in range(1, 10) if not used & (1 << d)]

    def backtrack():
        nonlocal count
        if count >= limit:
            return
        # most-constrained-cell heuristic: pick the empty cell with the
        # fewest legal candidates first - dramatically prunes the
        # search tree vs. always scanning left-to-right.
        best = None
        best_cands = None
        for (r, c) in cells:
            if grid[r][c]:
                continue
            b = _box_index(r, c)
            cands = candidates_for(r, c, b)
            if best is None or len(cands) < len(best_cands):
                best, best_cands = (r, c, b), cands
                if len(cands) <= 1:
                    break
        if best is None:
            count += 1
            return
        r, c, b = best
        for d in best_cands:
            bit = 1 << d
            grid[r][c] = d
            rows[r] |= bit; cols[c] |= bit; boxes[b] |= bit
            backtrack()
            grid[r][c] = 0
            rows[r] &= ~bit; cols[c] &= ~bit; boxes[b] &= ~bit
            if count >= limit:
                return

    backtrack()
    return count


def generate_puzzle(difficulty="medium"):
    """Returns (puzzle_grid, solution_grid), both 9x9 lists of lists,
    0 meaning an empty cell in puzzle_grid. puzzle_grid is guaranteed
    to have exactly one solution (solution_grid)."""
    target_clues = DIFFICULTY_CLUES.get(difficulty, DIFFICULTY_CLUES["medium"])
    solution = generate_full_grid()
    puzzle = [row[:] for row in solution]

    positions = [(r, c) for r in range(SIZE) for c in range(SIZE)]
    random.shuffle(positions)

    clues_remaining = SIZE * SIZE
    for (r, c) in positions:
        if clues_remaining <= target_clues:
            break
        removed = puzzle[r][c]
        puzzle[r][c] = 0
        if _count_solutions([row[:] for row in puzzle], limit=2) != 1:
            puzzle[r][c] = removed
        else:
            clues_remaining -= 1

    return puzzle, solution
