from copy import deepcopy
from math import floor
import sys

from lux.kit import obs_to_game_state
from lib.utils import manhattan_dist_to_nth_closest, closest_resource_tile, distance_to
from lib.factory_utils import my_turn_to_place_factory
from lib.setup_utils import *


def setup(self, step: int, obs, remainingOverageTime: int = 60):
    if step == 0:
        return dict(faction="TheBuilders", bid=10), 0, None, None
    else:
        game_state = obs_to_game_state(step, self.env_cfg, obs)
        water_left = game_state.teams[self.player].water
        metal_left = game_state.teams[self.player].metal

        factories_to_place = game_state.teams[self.player].factories_to_place
        if factories_to_place > self.number_of_factories:
            number_of_factories = factories_to_place
        else:
            number_of_factories = self.number_of_factories

        # These are the weights for the different resources dependent on the total number of factories
        if number_of_factories == 5:
            ice_weight_profile = [[], [1, 0.1, 0.01, 0.001], [1, 0.1, 0.001, 0.001], [1, 0.2, 0.01, 0.001],
                                  [1, 0.2, 0.01, 0.001], [1, 0.4, 0.01, 0.001]]
            ore_weight_profile = [[], [0.1, 0.01, 0.001, 0], [0.4, 0.1, 0.01, 0.001], [0.4, 0.2, 0.1, 0.01],
                                  [1, 0.4, 0.2, 0.1], [1, 0.1, 0.01, 0.001]]
        elif number_of_factories == 4:
            ice_weight_profile = [[], [1, 0.1, 0.01, 0.001], [1, 0.1, 0.01, 0.001], [1, 0.2, 0.01, 0.001],
                                  [1, 0.4, 0.01, 0.001]]
            ore_weight_profile = [[], [0.4, 0.01, 0.001, 0], [0.4, 0.2, 0.1, 0.01], [1, 0.4, 0.2, 0.1],
                                  [1, 0.1, 0.01, 0.001]]
        elif number_of_factories == 3:
            ice_weight_profile = [[], [1, 0.1, 0.01, 0.001], [1, 0.2, 0.01, 0.001], [1, 0.4, 0.01, 0.001]]
            ore_weight_profile = [[], [0.4, 0.2, 0.1, 0.01], [1, 0.4, 0.2, 0.1], [1, 0.1, 0.01, 0.001]]
        else:
            ice_weight_profile = [[], [1, 0.1, 0.01, 0.001], [1, 0.4, 0.01, 0.001]]
            ore_weight_profile = [[], [0.4, 0.2, 0.1, 0.01], [1, 0.1, 0.01, 0.001]]

        # Is it my turn to place a factory?
        my_turn_to_place = my_turn_to_place_factory(game_state.teams[self.player].place_first, step)

        if factories_to_place > 0 and my_turn_to_place:
            ice = deepcopy(obs["board"]["ice"])
            ore = deepcopy(obs["board"]["ore"])
            factory_centers = self.my_factory_centers

            # If there are no resources left, don't mask anything
            ice_masked = mask_resource_near_factories(ice, factory_centers)
            total_ice_tiles = np.sum(ice_masked)
            if total_ice_tiles > 1:
                ice = ice_masked
            ore_masked = mask_resource_near_factories(ore, factory_centers)
            total_ice_tiles = np.sum(ice_masked)
            if total_ice_tiles > 1:
                ore = ore_masked

            # Find adjacency scores for each tile

            adjacency_score_map = create_score_map(ice, ore)

            # Find the closest resource to each tile
            ice_distances = [manhattan_dist_to_nth_closest(ice, i) for i in range(1, 5)]
            ore_distances = [manhattan_dist_to_nth_closest(ore, i) for i in range(1, 5)]

            # Set ice weights based on number of factories
            ICE_WEIGHTS = np.array(ice_weight_profile[factories_to_place])
            weighted_ice_dist = np.sum(np.array(ice_distances) * ICE_WEIGHTS[:, np.newaxis, np.newaxis], axis=0)

            # Set ore weights based on number of factories
            ORE_WEIGHTS = np.array(ore_weight_profile[factories_to_place])
            weighted_ore_dist = np.sum(np.array(ore_distances) * ORE_WEIGHTS[:, np.newaxis, np.newaxis], axis=0)

            # How heavy should we prefer ice to ore?
            # ice_pref_profile = [[], [], [0, 3, 2], [0, 4, 3, 2], [0, 4, 4, 3, 2], [0, 5, 4, 4, 3, 2]]
            ice_pref_profile = [[], [], [0, 5, 4], [0, 7, 5, 4], [0, 7, 7, 5, 4], [0, 10, 8, 6, 5, 4]]
            ICE_PREFERENCE = ice_pref_profile[number_of_factories][factories_to_place]

            # Create a vignette of high rubble tiles around the outside of the map
            # This discourages placing factories near the edge of the map, but doesn't completely rule it out
            rubble = obs["board"]["rubble"]
            mask = np.ones(rubble.shape, dtype=bool)
            mask[4:-4, 4:-4] = False
            low_rubble = (rubble < 25) & ~mask
            low_rubble_no_vignette = (rubble == 0)

            # Valid spawn locations based on where other factories are
            valid_spawn_mask = obs["board"]["valid_spawns_mask"]

            # Call it
            update_low_rubble_map(low_rubble, valid_spawn_mask, steps=4)
            update_low_rubble_map(low_rubble_no_vignette, valid_spawn_mask, steps=4)

            # Count up the areas of low rubble for scoring
            low_rubble_scores = np.zeros_like(low_rubble, dtype=float)
            for i in range(low_rubble.shape[0]):
                for j in range(low_rubble.shape[1]):
                    low_rubble_scores[i, j] = count_region_cells(low_rubble, (i, j), min_dist=0, max_dist=8,
                                                                 exponent=0.9)

            low_rubble_novignette_scores = np.zeros_like(low_rubble, dtype=float)
            for i in range(low_rubble.shape[0]):
                for j in range(low_rubble.shape[1]):
                    low_rubble_novignette_scores[i, j] = count_region_cells(low_rubble, (i, j), min_dist=0, max_dist=8,
                                                                            exponent=0.9)
            distance_score = (weighted_ice_dist * ICE_PREFERENCE + weighted_ore_dist)
            inverted_distance_score = np.max(distance_score) - distance_score
            combined_score = inverted_distance_score * obs["board"]["valid_spawns_mask"]
            overall_score = (low_rubble_scores + adjacency_score_map + combined_score * 7) * obs["board"][
                "valid_spawns_mask"]

            best_loc = np.argmax(overall_score)
            x, y = np.unravel_index(best_loc, (48, 48))
            spawn_loc = (x, y)

            loc = np.array([x, y])
            ore_tile = closest_resource_tile("ore", loc, [], obs)
            m, w = 150, 150  # metal, water
            if ore_tile is not None:
                if distance_to(loc, ore_tile) < 10 and metal_left % 150 != 0:
                    m = 140
                    w = 140
            if metal_left < 150:
                m = metal_left
            if water_left < 150:
                w = water_left

            factory_pos = np.array([x, y])
            try:
                print(f"Overall Score: {floor(overall_score[x, y])}, "
                      f"distance score: {floor(distance_score[x, y])},  "
                      f"adjacency score: {floor(adjacency_score_map[x, y])}, "
                      f"low rubble score: {floor(low_rubble_scores[x, y])}",
                      file=sys.stderr)
            except:
                pass

            return dict(spawn=spawn_loc, metal=m,
                        water=w), factories_to_place, factory_pos, low_rubble_novignette_scores
        return dict(), 0, None, None