from lib.utils import *


def lichen_surrounded(obs, strain_id, opp_strains, off_limits, x) -> (bool, int):
    lichen_map = obs["board"]["lichen"]
    lichen_strains_map = obs["board"]["lichen_strains"]
    rubble_map = obs["board"]["rubble"]

    my_lichen_positions = np.argwhere((lichen_strains_map == strain_id) & (lichen_map > 0))
    off_limits = [tuple(pos) for pos in off_limits]

    # Check if 80% of lichen tiles are above 40, this is a good indicator that the lichen is bordering another strain
    if np.mean(lichen_map[my_lichen_positions[:, 0], my_lichen_positions[:, 1]] > 40) > 0.8:
        return True, 5

    free_spaces = 0
    for pos in my_lichen_positions:
        x, y = pos
        neighbors = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        for nx, ny in neighbors:
            is_valid_pos = 0 <= nx < 48 and 0 <= ny < 48 and (nx, ny) not in off_limits
            if is_valid_pos and rubble_map[nx, ny] == 0 and \
                    (lichen_strains_map[nx, ny] in opp_strains or lichen_strains_map[nx, ny] == -1):
                free_spaces += 1

    return (free_spaces < x), free_spaces  # Lichen is considered surrounded if there are less than x free spaces


def next_positions_to_clear(obs, strain_id, opp_strains, off_limits):
    lichen_map = obs["board"]["lichen"]
    lichen_strains_map = obs["board"]["lichen_strains"]
    rubble_map = obs["board"]["rubble"]

    my_lichen_positions = np.argwhere((lichen_strains_map == strain_id))
    off_limits = [tuple(pos) for pos in off_limits]
    positions_to_clear = []

    for pos in my_lichen_positions:
        x, y = pos
        neighbors = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]

        for nx, ny in neighbors:
            is_valid_pos = 0 <= nx < 48 and 0 <= ny < 48 and (nx, ny) not in off_limits
            if is_valid_pos and rubble_map[nx, ny] > 0:
                positions_to_clear.append((nx, ny))

    return np.array(positions_to_clear)


def get_position_with_lowest_rubble(positions_to_clear, off_limits, obs, factory):
    positions_to_clear = [(pos[0], pos[1]) for pos in positions_to_clear]
    off_limits = [(pos[0], pos[1]) for pos in off_limits]

    # Filter out off-limits positions
    filtered_positions_to_clear = [pos for pos in positions_to_clear if pos not in off_limits]
    rubble_map = obs["board"]["rubble"]
    rubble_values_raw = [rubble_map[x, y] for x, y in filtered_positions_to_clear if rubble_map[x, y] > 0]
    rubble_values_under_thirty = [[x, y] for x, y in filtered_positions_to_clear if rubble_map[x, y] <= 30]

    # try to get the closest low rubble tile
    if len(rubble_values_under_thirty) > 0:
        # passing off_limits here has no effect, the positions have already been filtered
        closest_low_rubble = closest_rubble_tile_in_group(factory.pos, off_limits, rubble_values_under_thirty, obs)
        if closest_low_rubble is not None:
            return closest_low_rubble

    # if there are no low rubble tiles, get the lowest rubble tile possible
    if len(rubble_values_raw) == 0:
        return None
    min_rubble_index = np.argmin(rubble_values_raw)
    return positions_to_clear[min_rubble_index]


def get_orthogonal_positions(center, n, off_limits, obs):
    rubble_map = obs["board"]["rubble"]
    off_limits = [tuple(pos) for pos in off_limits]
    x, y = center
    valid_positions = set()

    for dx in range(-1, 2):
        for dy in range(-1, 2):
            border_x = x + dx
            border_y = y + dy

            for ddx in range(-n, n + 1):
                for ddy in range(-n, n + 1):
                    if abs(ddx) + abs(ddy) == n:
                        new_x = border_x + ddx
                        new_y = border_y + ddy

                        if 0 <= new_x < 48 and 0 <= new_y < 48:
                            if (new_x, new_y) not in off_limits and rubble_map[new_x, new_y] > 0:
                                valid_positions.add((new_x, new_y))

    return np.array(list(valid_positions))


def get_total_rubble(obs, tiles):
    rubble_map = obs["board"]["rubble"]
    total_rubble = 0
    for tile in tiles:
        total_rubble += rubble_map[tile[0]][tile[1]]
    return total_rubble
