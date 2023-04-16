from lib.utils import *


def my_turn_to_place_factory(place_first: bool, step: int):
    if place_first:
        if step % 2 == 1:
            return True
    else:
        if step % 2 == 0:
            return True
    return False


def nearby_resources(center, ice_map, ore_map, factories, distance=30):
    ice_tiles = np.argwhere(ice_map == 1)
    ore_tiles = np.argwhere(ore_map == 1)
    factory_positions = [f.pos for uid, f in factories.items() if f.pos[0] != center[0] and f.pos[1] != center[1]]

    ice_count = 0
    for tile in ice_tiles:
        dist_to_center = distance_to(center, tile)
        if dist_to_center < distance:
            is_closest = True
            for other_factory in factory_positions:
                if distance_to(other_factory, tile) < dist_to_center:
                    is_closest = False
                    break
            if is_closest:
                ice_count += 1

    ore_count = 0
    for tile in ore_tiles:
        dist_to_center = distance_to(center, tile)
        if dist_to_center < distance:
            is_closest = True
            for other_factory in factory_positions:
                if distance_to(other_factory, tile) < dist_to_center:
                    is_closest = False
                    break
            if is_closest:
                ore_count += 1

    return ice_count, ore_count
