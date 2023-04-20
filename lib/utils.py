from math import floor
import random
import sys

# from copy import deepcopy
import numpy as np

from scipy.ndimage import distance_transform_cdt
from scipy.spatial import KDTree


def distance_to(start: np.ndarray, finish: np.ndarray) -> int:
    # Manhattan distance between two points
    y = finish[1] - start[1]
    x = finish[0] - start[0]
    return abs(x) + abs(y)


def direction_to(start, target):
    # direction (0 = center, 1 = up, 2 = right, 3 = down, 4 = left)
    start = np.array(start)
    target = np.array(target)
    ds = target - start
    dx = ds[0]
    dy = ds[1]
    if dx == 0 and dy == 0:
        return 0
    if abs(dx) > abs(dy):
        if dx > 0:
            return 2
        else:
            return 4
    else:
        if dy > 0:
            return 3
        else:
            return 1


def get_opposite_direction(direction: int) -> int:
    #  direction (0 = center, 1 = up, 2 = right, 3 = down, 4 = left)
    if direction == 1:
        return 3
    if direction == 2:
        return 4
    if direction == 3:
        return 1
    if direction == 4:
        return 2
    return 0


def next_position(position: np.ndarray, direction: int):
    if direction == 0:  # center
        return position
    if direction == 1:  # up
        return np.array([position[0], position[1] - 1])
    elif direction == 2:  # right
        return np.array([position[0] + 1, position[1]])
    elif direction == 3:  # down
        return np.array([position[0], position[1] + 1])
    elif direction == 4:  # left
        return np.array([position[0] - 1, position[1]])
    else:
        print(f"Error: invalid direction in next_position {direction}", file=sys.stderr)
        return position


def manhattan_dist_to_nth_closest(arr, n):
    if n == 1:
        distance_map = distance_transform_cdt(1 - arr, metric='taxicab')
        return distance_map
    else:
        true_coords = np.transpose(np.nonzero(arr))  # get the coordinates of true values
        tree = KDTree(true_coords)  # build a KDTree

        # query the nearest to nth closest distances using p=1 for Manhattan distance
        dist, _ = tree.query(np.transpose(np.nonzero(~arr)), k=n, p=1)

        return np.reshape(dist[:, n - 1], arr.shape)  # reshape the result to match the input shap


def get_helper_tile(homer_pos, home_pos):
    factory_tiles = get_factory_tiles(home_pos)
    target_tile = closest_tile_in_group(homer_pos, [], list(factory_tiles))
    return target_tile


def on_tile(tile: np.ndarray, position: np.ndarray) -> bool:
    if np.all(tile == position):
        return True
    return False


def tile_adjacent(tile: np.ndarray, position: np.ndarray) -> bool:
    dist = distance_to(tile, position)
    if 0 < dist <= 1:
        return True
    return False


def can_stay(position: np.ndarray, off_limits: list) -> bool:
    for tile in off_limits:
        if tile[0] == position[0] and tile[1] == position[1]:
            return False
    return True


def move_cost(unit, pos, board) -> int:
    if unit.unit_type == "HEAVY":
        multiplier = 1
        move_cost = 40
    else:
        multiplier = 0.05
        move_cost = 2
    rubble_map = board["rubble"]
    rubble_cost = floor(rubble_map[pos[0]][pos[1]] * multiplier)
    return rubble_cost + move_cost + 1


def get_cardinal_direction(position: np.ndarray, target: np.ndarray) -> str:
    ds = target - position
    dx = ds[0]
    dy = ds[1]
    if dx == 0 and dy == 0:
        return "C"
    if dx > 0 and dy == 0:
        return "E"
    if dx < 0 and dy == 0:
        return "W"
    if dx == 0 and dy > 0:
        return "S"
    if dx == 0 and dy < 0:
        return "N"
    if dx > 0 and dy > 0:
        return "SE"
    if dx > 0 > dy:
        return "NE"
    if dx < 0 < dy:
        return "SW"
    if dx < 0 and dy < 0:
        return "NW"
    return "C"


def get_cardinal_tiles(f: np.ndarray):
    cards = np.array([np.array([f[0], f[1] + 1]),
                      np.array([f[0] + 1, f[1]]),
                      np.array([f[0], f[1] - 1]),
                      np.array([f[0] - 1, f[1]])
                      ])
    tiles = np.array([np.array([f[0], f[1]])])
    for pos in cards:
        # make sure they are within the bounds of our board
        if 0 <= pos[0] < 48 and 0 <= pos[1] < 48:
            tiles = np.append(tiles, [pos], 0)
    return tiles


def get_factory_tiles(f: np.ndarray):
    tiles = np.array([np.array([f[0], f[1]]),
                      np.array([f[0], f[1] + 1]),
                      np.array([f[0] + 1, f[1]]),
                      np.array([f[0], f[1] - 1]),
                      np.array([f[0] - 1, f[1]]),
                      np.array([f[0] + 1, f[1] + 1]),
                      np.array([f[0] - 1, f[1] + 1]),
                      np.array([f[0] + 1, f[1] - 1]),
                      np.array([f[0] - 1, f[1] - 1])
                      ])
    return tiles


def get_closest_factory(factories: dict, position: np.ndarray):
    factory_units = np.array([f for u, f in factories.items()])
    factory_tiles = np.array([f.pos for u, f in factories.items()])
    factory_distances = np.mean((factory_tiles - position) ** 2, 1)
    closest = factory_units[np.argmin(factory_distances)]
    return closest


def closest_factory_tile(factory_pos: np.ndarray, position: np.ndarray, heavies) -> np.ndarray:
    heavy_tiles = set()
    for heavy in heavies:
        heavy_tiles.add((heavy.pos[0], heavy.pos[1]))

    factory_tiles = get_factory_tiles(factory_pos)
    factory_tiles = [tile for tile in factory_tiles if (tile[0], tile[1]) not in heavy_tiles]
    if len(factory_tiles) == 0:
        return factory_pos
    factory_distances = [distance_to(position, tile) for tile in factory_tiles]
    return factory_tiles[np.argmin(factory_distances)]


def closest_resource_tile(resource: str, start: np.ndarray, off_limits: list, board):
    tile_map = np.copy(board[resource])
    for pos in off_limits:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            tile_map[x, y] = 0
    tile_locations = np.argwhere(tile_map == 1)
    if len(tile_locations) == 0:
        return None
    tile_distances = np.mean((tile_locations - start) ** 2, 1)
    target_tile = tile_locations[np.argmin(tile_distances)]
    return target_tile


def closest_rubble_tile(start: np.ndarray, off_limits: list, board):
    """Finds the closest rubble tile to the unit that is not occupied by a unit or a factory"""
    tile_map = np.copy(board["rubble"])
    for pos in off_limits:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            tile_map[x][y] = 0
    tile_locations = np.argwhere(((tile_map <= 40) & (tile_map > 0)))
    tile_distances = np.mean((tile_locations - start) ** 2, 1)
    if np.min(tile_distances) >= 20:
        tile_locations = np.argwhere(tile_map > 0)
        tile_distances = np.mean((tile_locations - start) ** 2, 1)
    target_tile = tile_locations[np.argmin(tile_distances)]
    return target_tile


def closest_rubble_tile_in_group(start: np.ndarray, off_limits: list, group: list, board):
    """Finds the closest tile in a group of tiles to the unit that is not occupied by a unit or a factory"""
    off_limits_set = set()
    for pos in off_limits:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            off_limits_set.add((x, y))
    group_set = set()
    for pos in group:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            group_set.add((x, y))
    group_set = group_set - off_limits_set
    if len(group_set) == 0:
        return None
    group_list = list(group_set)
    group_rubble_tiles = np.array(
        [np.array([f[0], f[1]]) for f in group_list if board["rubble"][f[0], f[1]] > 0])
    if len(group_rubble_tiles) == 0:
        return None
    group_distances = np.mean((group_rubble_tiles - start) ** 2, 1)
    target_tile = group_rubble_tiles[np.argmin(group_distances)]
    return target_tile


def closest_tile_in_group(start: np.ndarray, off_limits: list, group: list):
    """Finds the closest tile in a group of tiles to the unit that is not occupied by a unit or a factory"""
    off_limits_set = set()
    for pos in off_limits:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            off_limits_set.add((x, y))
    group_set = set()
    for pos in group:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            group_set.add((x, y))
    group_set = group_set - off_limits_set
    if len(group_set) == 0:
        return None
    group_list = list(group_set)
    group_tiles = np.array([np.array([f[0], f[1]]) for f in group_list])
    if len(group_tiles) == 0:
        return None
    group_distances = np.mean((group_tiles - start) ** 2, 1)
    target_tile = group_tiles[np.argmin(group_distances)]
    return target_tile


def closest_opp_lichen(opp_strains, start: np.ndarray, off_limits: list, board, priority=False, tile_amount=0, group=None):
    lichen_tiles = np.copy(board["lichen_strains"])
    lichen_amounts = np.copy(board["lichen"])
    for pos in off_limits:
        x = int(pos[0])
        y = int(pos[1])
        if x < 48 and y < 48:
            lichen_tiles[x, y] = 1000  # this is a null value for the lichen strains

    if group is not None:
        lichen_tiles = {tile: lichen_tiles[tile] for tile in group}

    if priority:
        priority_strain = find_most_common_integer(lichen_tiles, opp_strains, strain_amount=15)
        tile_locations = np.argwhere((np.isin(lichen_tiles, priority_strain) & (lichen_amounts > tile_amount)))
    else:
        tile_locations = np.argwhere((np.isin(lichen_tiles, opp_strains) & (lichen_amounts > tile_amount)))
    if len(tile_locations) == 0:
        return None
    tile_distances = [distance_to(start, tile) for tile in tile_locations]
    target_tile = tile_locations[np.argmin(tile_distances)]
    if target_tile is None:
        return None
    return np.array(target_tile)


def find_most_common_integer(lichen_strain_map, opp_strains, strain_amount=0):
    all_strains, counts = np.unique(lichen_strain_map, return_counts=True)

    # Filter the unique_elements and counts arrays based on integers_of_interest
    opp_strain_locations = np.isin(all_strains, opp_strains)
    filtered_elements = all_strains[opp_strain_locations]
    filtered_counts = counts[opp_strain_locations]
    if len(filtered_elements) == 0:
        return None

    # Find the indices of strains with counts greater than or equal to x
    indices_above_x = np.where(filtered_counts >= strain_amount)
    # Get the strains with counts greater than or equal to x
    strains_above_x = filtered_elements[indices_above_x]
    return strains_above_x.tolist()


def get_lichen_in_square(lichen_tiles, player_strains, pos, size):
    x_center, y_center = pos
    half_size = size // 2
    x_min, x_max = max(0, x_center - half_size), min(48, x_center + half_size + 1)
    y_min, y_max = max(0, y_center - half_size), min(48, y_center + half_size + 1)

    square_tiles = []
    for x in range(x_min, x_max):
        for y in range(y_min, y_max):
            if (x, y) in lichen_tiles and lichen_tiles[x, y] in player_strains:
                square_tiles.append((x, y))

    return square_tiles


def find_new_direction(position: np.ndarray, target: np.ndarray, off_limits: list) -> int:
    #  direction (0 = center, 1 = up, 2 = right, 3 = down, 4 = left)
    cardinal_dir = get_cardinal_direction(position, target)
    if cardinal_dir == "E":
        s = [3, 1]
        random.shuffle(s)
        r = [2, s[0], s[1], 4]
    elif cardinal_dir == "W":
        s = [3, 1]
        random.shuffle(s)
        r = [4, s[0], s[1], 2]
    elif cardinal_dir == "S":
        s = [2, 4]
        random.shuffle(s)
        r = [3, s[0], s[1], 1]
    elif cardinal_dir == "N":
        s = [2, 4]
        random.shuffle(s)
        r = [1, s[0], s[1], 3]
    elif cardinal_dir == "SE":
        s = [3, 2]
        random.shuffle(s)
        r = [s[0], s[1], 4, 1]
    elif cardinal_dir == "NE":
        s = [2, 1]
        random.shuffle(s)
        r = [s[0], s[1], 4, 3]
    elif cardinal_dir == "SW":
        s = [3, 4]
        random.shuffle(s)
        r = [s[0], s[1], 2, 1]
    elif cardinal_dir == "NW":
        s = [4, 1]
        random.shuffle(s)
        r = [s[0], s[1], 2, 3]
    else:
        r = list(range(1, 5))
        random.shuffle(r)

    for d in r:
        new_pos = next_position(position, d)
        pos_off_limits = False
        for pos in off_limits:
            if on_tile(pos, new_pos):
                pos_off_limits = True
                break
        if pos_off_limits:
            continue
        elif 0 <= new_pos[0] < 48 and 0 <= new_pos[1] < 48:
            return d
    return 0


def move_toward(position: np.ndarray, target: np.ndarray, off_limits: list, desired_direction=None) -> int:
    if desired_direction is not None:
        direction = desired_direction
    else:
        direction = direction_to(position, target)
    new_pos = next_position(position, direction)
    for pos in off_limits:
        if on_tile(pos, new_pos):
            direction = find_new_direction(position, target, off_limits)
            return direction
    return direction


def truncate_actions(actions):
    truncated = []
    count = 1

    for i in range(1, len(actions)):
        if np.array_equal(actions[i], actions[i - 1]):
            count += 1
        else:
            new_action = actions[i - 1].copy()
            new_action[-1] = count
            truncated.append(new_action)
            count = 1

    # Append the last action
    last_action = actions[-1].copy()
    last_action[-1] = count
    truncated.append(last_action)

    return truncated


def get_path_cost(path_positions: list, board) -> int:
    total_cost = 0
    rubble_map = board["rubble"]
    for pos in path_positions:
        rubble_cost = rubble_map[pos[0]][pos[1]]
        total_cost += rubble_cost
    return total_cost
