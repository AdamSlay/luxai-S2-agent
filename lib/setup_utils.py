import numpy as np


# Mask out the resources near my factories, I don't want to compete with myself
def mask_resource_near_factories(resource_map, factories, n=2):
    masked_ice_map = resource_map.copy()

    for factory in factories:
        x, y = factory[0], factory[1]
        for dx in range(-n, n + 1):
            for dy in range(-n, n + 1):
                if abs(dx) + abs(dy) <= n:
                    for fx in range(-1, 2):  # Loop through factory tiles
                        for fy in range(-1, 2):
                            i, j = x + dx + fx, y + dy + fy
                            if 0 <= i < resource_map.shape[0] and 0 <= j < resource_map.shape[1]:
                                masked_ice_map[i, j] = 0
    return masked_ice_map


def score_tile(ice_adj, ore_adj):
    if 1 < ice_adj < 4 and 0 < ore_adj <= 2:
        return 35
    elif ice_adj == 1 and 0 < ore_adj <= 2:
        return 30
    elif 1 < ice_adj <= 3 and ore_adj == 0:
        return 15
    elif ice_adj == 1 and ore_adj == 0:
        return 10
    else:
        return 0


def create_score_map(ice_tiles, ore_tiles):
    width, height = ice_tiles.shape
    score_map = np.zeros((height, width), dtype=np.int32)

    for row in range(2, height - 2):
        for col in range(2, width - 2):
            surrounding_ice = [
                ice_tiles[row - 2, col - 1], ice_tiles[row - 2, col], ice_tiles[row - 2, col + 1],
                ice_tiles[row - 1, col - 2], ice_tiles[row - 1, col + 2],
                ice_tiles[row, col - 2], ice_tiles[row, col + 2],
                ice_tiles[row + 1, col - 2], ice_tiles[row + 1, col + 2],
                ice_tiles[row + 2, col - 1], ice_tiles[row + 2, col], ice_tiles[row + 2, col + 1]
            ]
            ice_adj = np.sum(surrounding_ice)

            surrounding_ore = [
                ore_tiles[row - 2, col - 1], ore_tiles[row - 2, col], ore_tiles[row - 2, col + 1],
                ore_tiles[row - 1, col - 2], ore_tiles[row - 1, col + 2],
                ore_tiles[row, col - 2], ore_tiles[row, col + 2],
                ore_tiles[row + 1, col - 2], ore_tiles[row + 1, col + 2],
                ore_tiles[row + 2, col - 1], ore_tiles[row + 2, col], ore_tiles[row + 2, col + 1]
            ]
            ore_adj = np.sum(surrounding_ore)

            score = score_tile(ice_adj, ore_adj)
            score_map[row, col] = score

    return score_map


# This function creates a mask around each factory that will be set to high rubble
# This will discourage placing factories too close to each other
def expand_mask(valid_spawns, steps):
    expanded_mask = np.zeros_like(valid_spawns)

    for x in range(valid_spawns.shape[0]):
        for y in range(valid_spawns.shape[1]):
            if valid_spawns[x, y] == 0:
                for dx in range(-steps, steps + 1):
                    for dy in range(-steps, steps + 1):
                        if abs(dx) + abs(dy) <= steps:
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < valid_spawns.shape[0] and 0 <= ny < valid_spawns.shape[1]:
                                expanded_mask[nx, ny] = 0

    return expanded_mask


# Update the low rubble map to include the expanded mask
def update_low_rubble_map(low_rubble_map, valid_spawns_mask, steps=4):
    expanded_mask = expand_mask(valid_spawns_mask, steps)
    low_rubble_map[expanded_mask == 1] = 1


def count_region_cells(array, start, min_dist=2, max_dist=np.inf, exponent=1.0):
    def dfs(array, loc):
        distance_from_start = abs(loc[0] - start[0]) + abs(loc[1] - start[1])
        if not (0 <= loc[0] < array.shape[0] and 0 <= loc[1] < array.shape[1]):
            return 0
        if (not array[loc]) or visited[loc]:
            return 0
        if not (min_dist <= distance_from_start <= max_dist):
            return 0

        visited[loc] = True

        count = 1.0 * exponent ** distance_from_start
        count += dfs(array, (loc[0] - 1, loc[1]))
        count += dfs(array, (loc[0] + 1, loc[1]))
        count += dfs(array, (loc[0], loc[1] - 1))
        count += dfs(array, (loc[0], loc[1] + 1))

        return count

    visited = np.zeros_like(array, dtype=bool)
    return dfs(array, start)

