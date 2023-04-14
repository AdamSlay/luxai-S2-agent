import numpy as np

from lib.utils import direction_to, next_position, get_factory_tiles


def get_cardinal_tiles_toward_diagonal(pos: np.ndarray, diagonal: np.ndarray):
    if diagonal[0] > pos[0]:
        if diagonal[1] > pos[1]:
            return np.array([np.array([pos[0], pos[1] + 1]),
                             np.array([pos[0] + 1, pos[1]])
                             ])
        else:
            return np.array([np.array([pos[0], pos[1] - 1]),
                             np.array([pos[0] + 1, pos[1]])
                             ])
    else:
        if diagonal[1] > pos[1]:
            return np.array([np.array([pos[0], pos[1] + 1]),
                             np.array([pos[0] - 1, pos[1]])
                             ])
        else:
            return np.array([np.array([pos[0], pos[1] - 1]),
                             np.array([pos[0] - 1, pos[1]])
                             ])


def get_cardinal_tiles_toward(pos: np.ndarray, target: np.ndarray):
    if target[0] == pos[0] or target[1] == pos[1]:
        direction = direction_to(pos, target)
        return np.array([next_position(pos, direction)])
    else:
        return get_cardinal_tiles_toward_diagonal(pos, target)


def get_second_level_tiles(pos: np.ndarray):
    tiles = np.array([np.array([pos[0], pos[1] + 2]),
                      np.array([pos[0] + 2, pos[1]]),
                      np.array([pos[0], pos[1] - 2]),
                      np.array([pos[0] - 2, pos[1]]),
                      np.array([pos[0] + 1, pos[1] + 1]),
                      np.array([pos[0] - 1, pos[1] + 1]),
                      np.array([pos[0] + 1, pos[1] - 1]),
                      np.array([pos[0] - 1, pos[1] - 1])
                      ])
    return tiles


def remove_factory_tiles_from_group(tiles, factories):
    factory_tiles = []
    for fid, factory in factories.items():
        factory_tiles.extend(get_factory_tiles(factory.pos))
    factory_tile_tuples = [(tile[0], tile[1]) for tile in factory_tiles]

    non_factory_tiles = []
    for tile in tiles:
        if (tile[0], tile[1]) in factory_tile_tuples:
            continue
        else:
            non_factory_tiles.append(tile)
    return np.array(non_factory_tiles)


def get_next_queue_position(unit, action_queue):
    if len(action_queue) == 0:
        return unit.pos
    else:
        if action_queue[unit.unit_id][0][0] == 0:
            next_dir = action_queue[unit.unit_id][0][1]
            return next_position(unit.pos, next_dir)
        else:
            return unit.pos


def get_opp_units_on_tiles(unit, opp_units, tiles):
    units_on_tiles = dict()
    tiles = [(tile[0], tile[1]) for tile in tiles]
    in_danger = False
    for uid, u in opp_units.items():
        if (u.pos[0], u.pos[1]) in tiles and (u.unit_type == "HEAVY" or unit.unit_type == "LIGHT"):
            units_on_tiles[uid] = u
            in_danger = True
    return units_on_tiles, in_danger
