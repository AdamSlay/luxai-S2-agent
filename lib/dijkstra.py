from queue import PriorityQueue
from typing import List, Tuple


def dijkstras_path(rubble_map, start, finish, occupied_next, opp_factory_tiles, rubble_threshold=0) -> list:
    start = tuple(start)
    finish = tuple(finish)
    o_facto = [tuple(pos) for pos in opp_factory_tiles]
    unit_positions = [tuple(pos) for pos in occupied_next]
    queue = PriorityQueue()
    visited = set()
    prev = {}
    rubble_map[finish[0]][finish[1]] = 0
    queue.put((0, start))
    while not queue.empty():
        cost, node = queue.get()
        if node == finish:
            path = []
            while node != start:
                path.append(list(node))
                node = prev[node]
            path.append(list(start))
            return path[::-1]
        if node in visited:
            continue
        visited.add(node)
        for neighbor in get_neighbors(node, rubble_map):
            if neighbor in visited or neighbor in o_facto:
                continue
            elif queue.qsize() <= 5 and neighbor in unit_positions:
                continue
            move_cost = 5
            if queue.qsize() <= 2:
                neighbor_cost = move_cost
            else:
                rubble_cost = rubble_map[neighbor[0]][neighbor[1]]
                if rubble_cost < rubble_threshold:
                    rubble_cost = 0
                neighbor_cost = cost + move_cost + rubble_cost
            queue.put((neighbor_cost, neighbor))
            prev[neighbor] = node
    return []


def get_neighbors(pos: Tuple[int, int], rubble_map: List[List[int]]) -> List[Tuple[int, int]]:
    n_rows, n_cols = len(rubble_map), len(rubble_map[0])
    row, col = pos
    neighbors = []
    if row > 0:
        neighbors.append((row - 1, col))
    if row < n_rows - 1:
        neighbors.append((row + 1, col))
    if col > 0:
        neighbors.append((row, col - 1))
    if col < n_cols - 1:
        neighbors.append((row, col + 1))
    return neighbors