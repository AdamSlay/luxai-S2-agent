# import numpy as np
# import sys
# from copy import deepcopy
from math import ceil
from random import shuffle

from lib.dijkstra import dijkstras_path
from lib.evasion import evasion_check
from lib.excavation_utils import *
from lib.factory_utils import *
from lib.utils import *
from lib.queue_builder import QueueBuilder
from lib.setup_factories import setup

from lux.kit import obs_to_game_state
from lux.config import EnvConfig


# import cProfile
# profiler = cProfile.Profile()

class Agent():
    def __init__(self, player: str, env_cfg: EnvConfig) -> None:
        self.start_icers = None
        self.player = player
        self.opp_player = "player_1" if self.player == "player_0" else "player_0"
        np.random.seed(0)
        self.env_cfg: EnvConfig = env_cfg
        self.step = 0
        self.board = None
        self.opp_strains = []  # list of strains
        self.my_strains = []

        # queues
        self.action_queue = dict()
        self.new_queue = dict()

        # occupied
        self.occupied_next = set()

        # units
        self.my_units = dict()
        self.my_heavy_units = dict()
        self.my_light_units = dict()
        self.opp_units = dict()

        # factories
        self.my_factories = dict()
        self.opp_factories = dict()
        self.my_factory_tiles = set()
        self.opp_factory_tiles = set()

        # factory resources
        self.number_of_factories = 0
        self.my_factory_centers = set()
        self.factory_resources = dict()  # fid: [ice, ore]

        # paths
        self.low_rubble_scores = np.ndarray([])
        self.ore_paths = dict()  # fid: [path]
        self.clearing_paths = dict()  # fid: [path]
        self.factory_clearing_tiles = dict()  # fid: [tile]
        self.ore_path_costs = dict()  # fid: [cost]
        self.clearing_path_costs = dict()  # fid: [cost]
        self.path_home = dict()  # uid: [path]
        self.cost_home = dict()  # uid: [cost]

        # States
        self.unit_states = dict()  # uid: "state"
        self.factory_types = dict()  # fid: "type"
        self.factory_states = dict()  # fid: "state"
        self.factory_low_charge_heavy = dict()  # fid: bool
        self.factory_low_charge_light = dict()  # fid: bool

        # Tasks
        self.factory_needs_light = dict()  # fid: [ice, ore]  --> what I need
        self.factory_needs_heavy = dict()  # fid: [ice, ore]  --> what I need
        self.factory_tasks_light = dict()  # {fid: {unit_id :"task"}}  --> what I've got
        self.factory_tasks_heavy = dict()  # {fid: {unit_id :"task"}}  --> what I've got
        self.factory_homers = dict()  # {fid: unit_id}
        self.factory_icers = dict()  # {fid: unit_id}
        self.factory_helpers = dict()  # {fid: [unit_id, unit_id, etc]}

        # dibs
        self.light_mining_dibs = dict()  # {unit_id: pos}
        self.heavy_mining_dibs = dict()
        self.lichen_dibs = dict()  # {unit_id: [pos, pos, etc]}
        self.aggro_dibs = dict()  # {unit_id: fid}

        # reserve power
        self.moderate_reserve_power = {"LIGHT": 15, "HEAVY": 150}
        self.low_reserve_power = {"LIGHT": 10, "HEAVY": 100}

        # Mining Adjacent and Helper
        self.last_state = dict()  # whatever your last state was
        self.helper_treated = set()
        self.mining_adjacent = set()

        self.factory_helper_amounts = dict()
        self.factory_adjacency_scores = dict()
        self.adjacency_scores = []

    def early_setup(self, step: int, obs, remainingOverageTime: int = 60):
        queue, factories_to_place, factory_position, low_rubble_scores, adjacency_score = setup(self, step, obs, remainingOverageTime)
        if factories_to_place > self.number_of_factories:
            self.number_of_factories = factories_to_place

        if low_rubble_scores is not None:
            self.low_rubble_scores = low_rubble_scores

        if factory_position is not None:
            x, y = factory_position[0], factory_position[1]
            self.my_factory_centers.add((x, y))

        if adjacency_score is not None:
            self.adjacency_scores.append(adjacency_score)

        return queue

    def set_factory_helper_amounts(self):
        for fid, factory in self.my_factories.items():
            if self.factory_adjacency_scores[fid] == 45:
                self.factory_helper_amounts[fid] = 3
            elif self.factory_adjacency_scores[fid] == 40:
                self.factory_helper_amounts[fid] = 2
            elif self.factory_adjacency_scores[fid] == 20:
                self.factory_helper_amounts[fid] = 2
            elif self.factory_adjacency_scores[fid] == 10:
                self.factory_helper_amounts[fid] = 1
            else:
                self.factory_helper_amounts[fid] = 0

    def update_and_assign_helpers(self):
        units = self.my_light_units.copy()
        all_units = self.my_units.copy()

        new_factory_helpers = dict()
        for fid, uids in self.factory_helpers.items():
            if fid not in self.my_factories.keys():
                continue
            for uid in uids:
                if uid in all_units.keys():
                    if fid not in new_factory_helpers.keys():
                        new_factory_helpers[fid] = []
                    new_factory_helpers[fid].append(uid)
                    units = [u for u in units if u.unit_id != uid]
        self.factory_helpers = new_factory_helpers

        for fid, factory in self.my_factories.items():
            if fid not in self.factory_helpers.keys():
                self.factory_helpers[fid] = []

            sorted_units = sorted(units, key=lambda x: distance_to(x.pos, factory.pos))
            helpers_wanted = self.factory_helper_amounts[fid]
            helpers_wanted = 2
            helpers_wanted -= len(self.factory_helpers[fid])
            if helpers_wanted > 0:
                for i in range(helpers_wanted):
                    if i < len(sorted_units):
                        unit_id = sorted_units[i].unit_id
                        self.factory_helpers[fid].append(unit_id)
                        units = [u for u in units if u.unit_id != unit_id]


    def set_factory_charge_level(self):
        for fid, factory in self.my_factories.items():
            # SETUP
            if fid not in self.factory_low_charge_light.keys():
                self.factory_low_charge_light[fid] = False
            if fid not in self.factory_low_charge_heavy.keys():
                self.factory_low_charge_heavy[fid] = False

            # LIGHT
            if factory.power < 400 and self.factory_low_charge_light[fid] is False:
                self.factory_low_charge_light[fid] = True
            elif factory.power >= 500 and self.factory_low_charge_light[fid] is True:
                self.factory_low_charge_light[fid] = False
            # HEAVY
            if factory.power < 800 and self.factory_low_charge_heavy[fid] is False:
                self.factory_low_charge_heavy[fid] = True
            elif factory.power >= 1000 and self.factory_low_charge_heavy[fid] is True:
                self.factory_low_charge_heavy[fid] = False

    def set_ore_paths(self):
        rubble_map = np.copy(self.board['rubble'])
        for fid, factory in self.my_factories.items():
            self.ore_paths[fid] = []
            # print(f"finding ore path for factory {fid}", file=sys.stderr)
            closest_ore = closest_resource_tile("ore", factory.pos, list(self.opp_factory_tiles), self.board)
            if closest_ore is not None:
                ore_distance = distance_to(closest_ore, factory.pos)
                if ore_distance < 20:
                    ore_path = dijkstras_path(rubble_map, factory.pos, closest_ore, [], list(self.opp_factory_tiles),
                                              rubble_threshold=60)
                    if len(ore_path) > ore_distance * 2:
                        ore_path = dijkstras_path(rubble_map, factory.pos, closest_ore, [],
                                                  list(self.opp_factory_tiles),
                                                  rubble_threshold=90)
                    self.ore_paths[fid] = ore_path

    def find_clearing_position(self, target_position, min_distance, max_distance):
        best_coord = None
        max_score = float('-inf')
        for i in range(self.low_rubble_scores.shape[0]):
            for j in range(self.low_rubble_scores.shape[1]):
                current_coord = np.array([i, j])
                distance = distance_to(target_position, current_coord)
                if min_distance <= distance <= max_distance and self.low_rubble_scores[i, j] > max_score:
                    max_score = self.low_rubble_scores[i, j]
                    best_coord = current_coord
        n = 5
        x, y = best_coord
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                if abs(i) + abs(j) <= n and 0 <= x + i < self.low_rubble_scores.shape[0] and 0 <= y + j < \
                        self.low_rubble_scores.shape[1]:
                    self.low_rubble_scores[x + i, y + j] = 0
        return best_coord

    def set_clearing_paths(self):
        rubble_map = np.copy(self.board['rubble'])
        ice_map = np.copy(self.board['ice'])
        ore_map = np.copy(self.board['ore'])
        resource_positions = np.column_stack(np.where((ice_map == 1) | (ore_map == 1)))
        off_limits = [pos for pos in resource_positions]
        off_limits.extend(list(self.opp_factory_tiles))
        for fid, factory in self.my_factories.items():
            clearing_tile = self.factory_clearing_tiles[fid]
            if clearing_tile is not None:
                # add all factory tiles to off limits *except* for the home factory
                my_factory_tiles = [get_factory_tiles(f.pos) for i, f in self.my_factories.items() if i != fid]
                for tiles in my_factory_tiles:
                    for tile in tiles:
                        off_limits.append(tile)

                clearing_path = dijkstras_path(rubble_map, factory.pos, clearing_tile, [], off_limits,
                                               rubble_threshold=100)
                self.clearing_paths[fid] = clearing_path
            if fid not in self.ore_paths.keys():
                self.ore_paths[fid] = []

    def set_ore_path_costs(self):
        for fid, path in self.ore_paths.items():
            self.ore_path_costs[fid] = get_path_cost(path, self.board)

    def set_clearing_path_costs(self):
        for fid, path in self.clearing_paths.items():
            self.clearing_path_costs[fid] = get_path_cost(path, self.board)

    def check_valid_dig(self, unit):
        if self.action_queue[unit.unit_id][0][0] == 3:
            lichen_map = self.board['lichen_strains']

            strain_id = lichen_map[unit.pos[0]][unit.pos[1]]
            if strain_id not in self.opp_strains:
                if len(self.action_queue[unit.unit_id]) > 1:
                    queue = self.action_queue[unit.unit_id][1:]
                    self.update_queues(unit, queue)
                else:
                    self.action_queue[unit.unit_id] = []

    def check_valid_transfer(self, unit):
        if self.action_queue[unit.unit_id][0][0] == 1:
            transfer_type = self.action_queue[unit.unit_id][0][2]
            transfer_amt = self.action_queue[unit.unit_id][0][3]
            if transfer_type == 4 and transfer_amt >= unit.power:
                self.action_queue[unit.unit_id] = []

    def pop_action_queue(self):
        new_actions = dict()
        for unit_id, queue in self.action_queue.items():

            if isinstance(queue, list):
                if len(queue) > 1:
                    # if there are more repetitions, decrement the repetitions and keep the queue
                    nq = queue[0]
                    nq[5] -= 1
                    if nq[5] == 0:
                        # if there are no more repetitions, pop the queue
                        new_actions[unit_id] = queue[1:]
                    else:
                        new_actions[unit_id] = [nq] + queue[1:]

                elif len(queue) == 1:
                    nq = queue[0]
                    nq[5] -= 1
                    if nq[5] == 0:
                        # reset action queue
                        new_actions[unit_id] = []

                        # remove from tasks
                        for fid, units in self.factory_tasks_light.items():
                            if unit_id in units.keys():
                                del self.factory_tasks_light[fid][unit_id]
                        for fid, units in self.factory_tasks_heavy.items():
                            if unit_id in units.keys():
                                del self.factory_tasks_heavy[fid][unit_id]
                    else:
                        new_actions[unit_id] = [nq]
            else:
                # it's a factory action, delete it
                continue

        self.action_queue = new_actions

    def update_occupied_next(self):
        # self.occupied_next = [f.pos for i, f in factories.items()]
        self.occupied_next = set()
        self.opp_factory_tiles = set()
        self.my_factory_tiles = set()
        opp_factory_tiles = [get_factory_tiles(f.pos) for i, f in self.opp_factories.items()]
        for tiles in opp_factory_tiles:
            for tile in tiles:
                self.occupied_next.add((tile[0], tile[1]))
                self.opp_factory_tiles.add((tile[0], tile[1]))

        my_factory_tiles = [get_factory_tiles(f.pos) for i, f in self.my_factories.items()]
        for tiles in my_factory_tiles:
            for tile in tiles:
                self.my_factory_tiles.add((tile[0], tile[1]))

        for uid, state in self.unit_states.items():
            if state == "low battery":
                if uid in self.my_units.keys():
                    unit = self.my_units[uid]
                    pos = (unit.pos[0], unit.pos[1])
                    self.occupied_next.add(pos)

    def clear_dead_units_from_memory(self):
        new_light_dibs = dict()
        for uid in self.light_mining_dibs.keys():
            if uid in self.my_units.keys():
                new_light_dibs[uid] = self.light_mining_dibs[uid]
        self.light_mining_dibs = new_light_dibs

        new_heavy_dibs = dict()
        for uid in self.heavy_mining_dibs.keys():
            if uid in self.my_units.keys():
                new_heavy_dibs[uid] = self.heavy_mining_dibs[uid]
        self.heavy_mining_dibs = new_heavy_dibs

        new_unit_states = dict()
        for uid in self.unit_states.keys():
            if uid in self.my_units.keys():
                new_unit_states[uid] = self.unit_states[uid]
        self.unit_states = new_unit_states

        new_factory_homers = dict()
        # print(f"BEFORE factory homers: {self.factory_homers}", file=sys.stderr)
        for fid, uid in self.factory_homers.items():
            if uid in self.my_units.keys() and fid in self.my_factories.keys():
                new_factory_homers[fid] = uid
        self.factory_homers = new_factory_homers
        # print(f"AFTER factory homers: {self.factory_homers}", file=sys.stderr)

        new_factory_icers = dict()
        # print(f"BEFORE factory icers: {self.factory_icers}", file=sys.stderr)
        for fid, uid in self.factory_icers.items():
            if uid in self.my_units.keys() and fid in self.my_factories.keys():
                new_factory_icers[fid] = uid
        self.factory_icers = new_factory_icers
        # print(f"AFTER factory icers: {self.factory_icers}", file=sys.stderr)

    def avoid_collisions(self, unit, state):
        if unit.unit_id in self.action_queue.keys() and state != "low battery":
            queue = self.action_queue[unit.unit_id]

            # if you have an action queue, check the next position
            if isinstance(queue, list) and len(queue) > 0:

                # if you're moving, next_pos is the next position
                if queue[0][0] == 0:
                    next_pos = next_position(unit.pos, queue[0][1])
                    new_pos = (next_pos[0], next_pos[1])
                # otherwise, next_pos is the current position
                else:
                    new_pos = (unit.pos[0], unit.pos[1])

                # if the next position is already occupied, clear the action queue
                if new_pos in self.occupied_next:
                    q_builder = QueueBuilder(self, unit, [], self.board)
                    q_builder.clear_mining_dibs()
                    q_builder.clear_lichen_dibs()
                    # clear previous task without knowing the factory
                    for fid, units in self.factory_tasks_light.items():
                        if unit.unit_id in units.keys():
                            del self.factory_tasks_light[fid][unit.unit_id]
                    for fid, units in self.factory_tasks_heavy.items():
                        if unit.unit_id in units.keys():
                            del self.factory_tasks_heavy[fid][unit.unit_id]

                    self.action_queue[unit.unit_id] = []

    def add_nextpos_to_occnext(self, unit):
        if unit.unit_id in self.action_queue.keys():
            queue = self.action_queue[unit.unit_id]
            # if you have an action queue, check the next position
            if isinstance(queue, list) and len(queue) > 0:

                # if you're moving, next_pos is the next position
                if queue[0][0] == 0:
                    next_pos = next_position(unit.pos, queue[0][1])
                    new_pos = (next_pos[0], next_pos[1])
                # otherwise, next_pos is the current position
                else:
                    new_pos = (unit.pos[0], unit.pos[1])

                # add new_pos to occupied_next
                self.occupied_next.add(new_pos)

            # if you don't have an action queue, add the current position to occupied_next
            else:
                pos = (unit.pos[0], unit.pos[1])
                self.occupied_next.add(pos)

    def remove_old_next_pos_from_occ_next(self, unit):
        if unit.unit_id in self.action_queue.keys():
            queue = self.action_queue[unit.unit_id]
            # if you have an action queue, check the next position
            if isinstance(queue, list) and len(queue) > 0:

                # if you're moving, next_pos is the next position
                if queue[0][0] == 0:
                    next_pos = next_position(unit.pos, queue[0][1])
                    old_pos = (next_pos[0], next_pos[1])
                # otherwise, next_pos is the current position
                else:
                    old_pos = (unit.pos[0], unit.pos[1])

                # remove old_pos from occupied_next
                if old_pos in self.occupied_next:
                    self.occupied_next.remove(old_pos)

    def remove_task_from_factory(self, unit):
        unit_id = unit.unit_id
        for fid, units in self.factory_tasks_light.items():
            if unit_id in units.keys():
                del self.factory_tasks_light[fid][unit_id]
        for fid, units in self.factory_tasks_heavy.items():
            if unit_id in units.keys():
                del self.factory_tasks_heavy[fid][unit_id]

    def assign_tasks_to_attackers(self, attackers, unit_type):
        factory_needs = self.factory_needs_light if unit_type == "LIGHT" else self.factory_needs_heavy
        # Sort the factories based on the number of tasks they have
        sorted_factories = sorted(factory_needs, key=lambda factory_id: len(factory_needs[factory_id]), reverse=True)

        assigned_attackers = set()
        remaining_attackers = []

        for factory_id in sorted_factories:
            if factory_id not in self.my_factories.keys():
                continue
            factory_position = self.my_factories[factory_id].pos

            # Sort the attackers based on their distance to the current factory
            sorted_attackers = sorted(attackers, key=lambda unit: distance_to(factory_position, unit.pos))

            for attacker in sorted_attackers:
                # Check if the attacker has not been assigned a task yet
                if attacker.unit_id not in assigned_attackers:
                    # Assign the task to the attacker
                    self.decision_tree(attacker, self.my_factories, self.opp_units, self.my_factories[factory_id])
                    # Add the attacker's ID to the assigned_attackers set
                    assigned_attackers.add(attacker.unit_id)
                    # Break out of the inner loop to avoid assigning more tasks to the same attacker
                    break

        # Populate the remaining_attackers list with attackers that were not assigned tasks
        for attacker in attackers:
            if attacker.unit_id not in assigned_attackers:
                remaining_attackers.append(attacker)

        return remaining_attackers

    def update_queues(self, unit, queue, new_queue=True):
        # first remove the old next position from occupied_next if it exists
        self.remove_old_next_pos_from_occ_next(unit)

        if isinstance(queue, list) and queue:
            if queue[0][0] == 0:
                new_pos = next_position(unit.pos, queue[0][1])
                new_pos = (new_pos[0], new_pos[1])
                self.occupied_next.add(new_pos)
            else:
                new_pos = (unit.pos[0], unit.pos[1])
                self.occupied_next.add(new_pos)
        elif not queue:
            new_pos = (unit.pos[0], unit.pos[1])
            self.occupied_next.add(new_pos)
        self.action_queue[unit.unit_id] = queue
        if new_queue:
            self.new_queue[unit.unit_id] = queue

    def finalize_new_queue(self):
        actions_to_submit = dict()
        for unit_id, queue in self.new_queue.items():
            if isinstance(queue, list) and queue and queue != [[]]:
                actions_to_submit[unit_id] = queue
            elif isinstance(queue, int):
                actions_to_submit[unit_id] = queue
        return actions_to_submit

    def is_it_a_helper(self, unit_id):
        for factory_id, unit_ids in self.factory_helpers.items():
            if unit_id in unit_ids:
                return True, factory_id
        return False, None

    def split_heavies_and_lights(self, units):
        helpers, adjacents = [], []
        heavies, lights = [], []
        homers, icers = [], []
        heavy_attackers, light_attackers = [], []
        for uid, u in units.items():
            is_helper, fid = self.is_it_a_helper(uid)

            # this check should go in some sort of unit setup function
            if uid not in self.action_queue.keys():
                self.action_queue[uid] = []

            if u.unit_id in self.factory_homers.values():
                homers.append(u)
                continue

            elif u.unit_id in self.factory_icers.values():
                icers.append(u)
                continue

            elif is_helper:
                helpers.append(u)
                continue

            elif uid in self.last_state.keys() and self.last_state[uid] == "attacking":
                if u.unit_type == "HEAVY":
                    heavy_attackers.append(u)
                else:
                    light_attackers.append(u)
                continue

            elif u.unit_type == "HEAVY":
                if uid in self.unit_states.keys() and self.unit_states[uid] == "mining adjacent":
                    adjacents.append(u)
                else:
                    heavies.append(u)
                continue

            elif u.unit_type == "LIGHT":
                lights.append(u)
                continue

        self.my_heavy_units = heavies
        self.my_light_units = lights
        return heavies, lights, helpers, adjacents, homers, icers, heavy_attackers, light_attackers

    def set_factory_type(self, factory):
        fid = factory.unit_id
        number_of_ice = self.factory_resources[fid][0]
        number_of_ore = self.factory_resources[fid][1]

        if number_of_ice == 0 and number_of_ore == 0:
            self.factory_types[fid] = "resourceless"

        elif number_of_ice == number_of_ore:
            self.factory_types[fid] = "balanced"

        elif number_of_ice == 0 and number_of_ore >= 1:
            self.factory_types[fid] = "ore mine"

        elif number_of_ice >= 1 and number_of_ore == 0:
            self.factory_types[fid] = "ice mine"

        elif number_of_ice >= 3 and number_of_ore >= 3:
            self.factory_types[fid] = "rich"

        elif number_of_ice > number_of_ore:
            self.factory_types[fid] = "ice pref"

        elif number_of_ice < number_of_ore:
            self.factory_types[fid] = "ore pref"

    def define_factory_needs(self, factory):
        fid = factory.unit_id
        # Initialize factory needs/tasks if they don't exist yet
        if fid not in self.factory_needs_light.keys():
            self.factory_needs_light[fid] = []
        if fid not in self.factory_needs_heavy.keys():
            self.factory_needs_heavy[fid] = []
        if fid not in self.factory_tasks_light.keys():
            self.factory_tasks_light[fid] = dict()
        if fid not in self.factory_tasks_heavy.keys():
            self.factory_tasks_heavy[fid] = dict()
        if fid not in self.factory_homers.keys():
            self.factory_homers[fid] = ''
        if fid not in self.factory_icers.keys():
            self.factory_icers[fid] = ''

        number_of_ice = self.factory_resources[fid][0]
        number_of_ore = self.factory_resources[fid][1]

        factory_type = self.factory_types[fid]
        factory_state = "basic"  # for now, all factories are basic

        if factory_state == "basic":

            # figure out all tasks that need to be done
            light_todo = []
            heavy_todo = []

            # LIGHTS
            # do I have a path to the nearest ore?
            if self.step >= 2:
                # for uid, task in self.factory_tasks_heavy[fid].items():
                #     if uid in self.unit_states.keys() and self.unit_states[uid] == "mining adjacent":
                #         # light_todo.append(f"helper:{uid}")
                #         pass
            # TODO: The helpers need to know who to help, and when to help them


                # do I have room to grow lichen?
                free_spaces_wanted = 10
                needs_excavation, free_spaces_actual = lichen_surrounded(self.board, factory.strain_id,
                                                                         self.opp_strains, self.occupied_next,
                                                                         free_spaces_wanted)
                primary_zone = get_orthogonal_positions(factory.pos, 2, self.my_factory_tiles, self.board)
                zone_cost = get_total_rubble(self.board, primary_zone)
                cost_to_clearing = self.clearing_path_costs[fid]
                cost_to_ore = self.ore_path_costs[fid]
                max_excavators = 8
                if self.step < 850:
                    if 0 < cost_to_ore <= cost_to_clearing or (cost_to_ore > 0 and cost_to_clearing == 0):
                        excavators_needed = ceil(cost_to_ore / 20)
                        excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                        # if not, then I need to excavate a path to the nearest ore
                        [light_todo.append("ore path") for _ in range(excavators_needed)]
                    elif cost_to_clearing > 0:
                        excavators_needed = ceil(cost_to_clearing / 20)
                        excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                        # if not, then I need to excavate a clearing path
                        [light_todo.append("clearing path") for _ in range(excavators_needed)]

                if needs_excavation or zone_cost > 0:
                    if zone_cost > 0:
                        excavators_needed = ceil(zone_cost / 20)
                    else:
                        excavators_needed = ceil(free_spaces_wanted - free_spaces_actual)
                    if self.step > 850:
                        excavators_needed = ceil(excavators_needed / 2)
                    excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                    # if not, then I need to excavate edge of lichen
                    [light_todo.append("rubble") for _ in range(excavators_needed)]
                else:
                    [light_todo.append("rubble") for _ in range(4)]

                non_rubble = []
                # if I have room to grow lichen, do I have enough water to grow lichen?
                if factory.cargo.water < 200 and 150 < self.step < 850:
                    ice_miners = number_of_ice if number_of_ice <= 8 else 8
                    # # if not, then I need to mine ice
                    [light_todo.append("ice") for _ in range(2)]
                    [non_rubble.append("ice") for _ in range(ice_miners - 2)]

                # if enemy is growing lichen nearby, attack it
                dibbed = list(self.light_mining_dibs.values())
                dibbed.extend(list(self.heavy_mining_dibs.values()))
                opp_lichen_tile = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.board)
                has_workers = len(self.factory_tasks_light[fid]) > 5
                if opp_lichen_tile is not None and distance_to(factory.pos, opp_lichen_tile) <= 30 and has_workers:
                    [non_rubble.append("lichen") for _ in range(1)]

                # if I have enough water to grow lichen, do I have enough ore to build bots?
                if factory.cargo.metal < 200 and 100 < self.step < 900:
                    ore_miners = number_of_ore if number_of_ore <= 10 else 10
                    # # if not, then I need to mine ore
                    [light_todo.append("ore") for _ in range(2)]
                    [non_rubble.append("ore") for _ in range(ore_miners - 2)]
                # randomize the order of tasks after rubble
                shuffle(non_rubble)
                light_todo.extend(non_rubble)

            # HEAVIES
            # I always need a homer
            if self.factory_homers[fid] == '':
                heavy_todo.append("homer")

            need_an_icer = number_of_ice > 1 or number_of_ore > 0 and self.factory_icers[fid] == ''
            if number_of_ice > 1 or number_of_ore > 0 and self.factory_icers[fid] == '':
                heavy_todo.append("icer")

            # if self.step > 100 and not need_an_icer:
            #     cost_to_clearing = self.clearing_path_costs[fid]
            #     if cost_to_clearing > 0:
            #         heavy_todo.append("clearing path")

            # if factory.cargo.ore < 200 and self.step < 850:
            #     # if not, then I need to mine ore
            #     heavy_todo.append("ore")


            # # are you dangerously low on water?
            # if factory.cargo.water < 150 and number_of_ice > 2:
            #     emergency_ice_miners = number_of_ice - 1 if number_of_ice - 1 <= 2 else 2
            #     [heavy_todo.append("ice") for _ in range(emergency_ice_miners)]

            closest_enemy = get_closest_factory(self.opp_factories, factory.pos)
            if distance_to(factory.pos, closest_enemy.pos) <= 10 and not need_an_icer:
                heavy_todo.append("aggro")

            if self.step >= 100 and not need_an_icer:
                dibbed = list(self.heavy_mining_dibs.values())
                dibbed.extend(list(self.light_mining_dibs.values()))
                closest_lichen = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.board)
                if closest_lichen is not None:
                    heavy_todo.append("lichen")

            # # if I have enough water to grow lichen, am I super clogged up with rubble?
            # surrounded, free_spaces = lichen_surrounded(self.board, factory.strain_id, self.opp_strains,
            #                                             self.occupied_next, 10)
            # if surrounded and free_spaces < 3 and self.step < 900 and not need_an_icer:
            #     # # if so, then I need to excavate either a clearing path or immediate zone
            #     heavy_todo.append("rubble")

            # figure out what tasks are already being done
            light_tasks_being_done = []
            for uid, task in self.factory_tasks_light[fid].items():
                if task:
                    light_tasks_being_done.append(task)

            heavy_tasks_being_done = []
            for uid, task in self.factory_tasks_heavy[fid].items():
                if task:
                    heavy_tasks_being_done.append(task)

            # remove tasks that are already being done from the list of tasks that need to be done
            light_tasks_needed = light_todo.copy()  # Create a copy of light_todo to avoid modifying the original list
            for task in light_tasks_being_done:
                if task in light_tasks_needed:
                    light_tasks_needed.remove(task)

            heavy_tasks_needed = heavy_todo.copy()  # Create a copy of light_todo to avoid modifying the original list
            for task in heavy_tasks_being_done:
                if task in heavy_tasks_needed:
                    heavy_tasks_needed.remove(task)

            # print(f"Step {self.step}: {fid} light tasks being done: {light_tasks_being_done}", file=sys.stderr)
            # print(f"Step {self.step}: {fid} heavy tasks being done: {heavy_tasks_being_done}", file=sys.stderr)
            # print(f"Step {self.step}: {fid} light tasks needed: {light_tasks_needed}", file=sys.stderr)
            # print(f"Step {self.step}: {fid} heavy tasks needed: {heavy_tasks_needed}", file=sys.stderr)
            if light_tasks_needed:
                self.factory_needs_light[fid] = light_tasks_needed
            elif not light_tasks_needed:
                del self.factory_needs_light[fid]
            if heavy_tasks_needed:
                self.factory_needs_heavy[fid] = heavy_tasks_needed
            elif not heavy_tasks_needed:
                del self.factory_needs_heavy[fid]

    def factory_watering(self, factory, game_state):
        # WATER
        homer_id = self.factory_homers[factory.unit_id]
        if homer_id != '':
            homer_state = self.unit_states[homer_id]
        else:
            homer_state = None

        # don't water if you are surrounded, it won't increase your power anyway
        surrounded, free_spaces = lichen_surrounded(self.board, factory.strain_id, self.opp_strains, [], 1)
        lichen_map = self.board['lichen_strains']
        factory_strain_map = np.argwhere(lichen_map == factory.strain_id)
        lichen_count = np.count_nonzero(factory_strain_map == 1)
        if surrounded and lichen_count > 1 and self.step < 700:
            return

        if factory.cargo.water > 50 and game_state.real_env_steps <= 100:
            if homer_state and homer_state == "mining adjacent":
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif game_state.real_env_steps % 3 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        if factory.cargo.water > 50 and 100 < game_state.real_env_steps < 750:
            power = factory.power
            if power > 2500:
                if game_state.real_env_steps % 4 == 0 and lichen_count > 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    return
            elif 2000 < power < 2500 and lichen_count < 30:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif homer_id and homer_state == "mining adjacent" and self.step % 8 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif factory.cargo.water > 150 and game_state.real_env_steps % 3 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif factory.cargo.water > 50 and game_state.real_env_steps % 2 == 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        if 750 <= game_state.real_env_steps < 980:
            steps_remaining = 1000 - game_state.real_env_steps
            if factory.cargo.water > steps_remaining * 6:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 400:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 200 and game_state.real_env_steps % 3 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 100 and game_state.real_env_steps % 2 == 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 50 and game_state.real_env_steps % 3 == 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        if 980 <= game_state.real_env_steps < 996:
            if factory.cargo.water > 50:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        if game_state.real_env_steps >= 996:
            if factory.cargo.water > 30:
                queue = factory.water()
                self.update_queues(factory, queue)
                return

    def pop_factory_needs(self, factory, light=True, reverse=False):
        fid = factory.unit_id
        if light:
            factory_needs = self.factory_needs_light
        else:
            factory_needs = self.factory_needs_heavy
        if len(factory_needs[fid]) > 1:
            if reverse:
                factory_needs[fid] = factory_needs[fid][::-1]
            else:
                factory_needs[fid] = factory_needs[fid][1:]
        else:
            del factory_needs[fid]

    def find_closest_factory_with_needs(self, factory, factory_needs):
        current_factory_position = factory.pos
        closest_distance = float('inf')
        closest_factory_id = None

        for factory_id, needs in factory_needs.items():
            # check if the factory has needs and that it is still alive
            if needs and factory_id in self.my_factories.keys():
                other_factory_position = self.my_factories[factory_id].pos
                distance = distance_to(current_factory_position, other_factory_position)

                if distance < closest_distance:
                    closest_distance = distance
                    closest_factory_id = factory_id

        if closest_factory_id is None:
            return None
        return self.my_factories[closest_factory_id]

    def rubble_digging_task_assignment(self, q_builder, resource, task_factory, light=False):
        off_limits = self.my_factory_tiles
        dibbed_tiles = [(pos[0], pos[1]) for pos in self.heavy_mining_dibs.values()]
        if light:
            light_dibbed_tiles = [(pos[0], pos[1]) for pos in self.light_mining_dibs.values()]
            dibbed_tiles.extend(light_dibbed_tiles)

        primary_zone = get_orthogonal_positions(task_factory.pos, 1, off_limits, self.board)
        zone_cost = get_total_rubble(self.board, primary_zone)
        if zone_cost > 0:
            lowest_rubble_pos = get_position_with_lowest_rubble(primary_zone, dibbed_tiles, self.board, task_factory)
            if lowest_rubble_pos is None:
                lowest_rubble_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.board)
            queue = q_builder.build_mining_queue(resource, rubble_tile=lowest_rubble_pos)
        else:
            off_limits_or_dibbed = list(self.occupied_next)
            off_limits_or_dibbed.extend(dibbed_tiles)
            positions_to_clear = next_positions_to_clear(self.board, task_factory.strain_id,
                                                         self.opp_strains,
                                                         off_limits=off_limits_or_dibbed)
            if len(positions_to_clear) > 0:
                lowest_rubble_pos = get_position_with_lowest_rubble(positions_to_clear, dibbed_tiles, self.board,
                                                                    task_factory)
                if lowest_rubble_pos is None:
                    lowest_rubble_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.board)
                queue = q_builder.build_mining_queue(resource, rubble_tile=lowest_rubble_pos)
            else:
                close_rubble_tile = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.board)
                if distance_to(task_factory.pos, close_rubble_tile) < 12:
                    queue = q_builder.build_mining_queue(resource, rubble_tile=close_rubble_tile)
                else:
                    queue = q_builder.build_waiting_queue(length=53)

        return queue

    def trailblazing_task_assignment(self, q_builder, _task, task_factory, light=False):
        dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
        if light:
            light_dibbed_tiles = [pos for pos in self.light_mining_dibs.values()]
            dibbed_tiles.extend(light_dibbed_tiles)
        if _task == "ore path":
            path_to_ore = self.ore_paths[task_factory.unit_id]
            closest_path_pos = closest_rubble_tile_in_group(task_factory.pos, dibbed_tiles, path_to_ore, self.board)
            if closest_path_pos is None:
                # print(f"Step {self.step}: {q_builder.unit.unit_id} is trailblazing an ore path PSYCH",file=sys.stderr)
                return None
            queue = q_builder.build_mining_queue("ore path", rubble_tile=closest_path_pos)
            return queue
        else:  # it's a clearing path
            path_to_clear = self.clearing_paths[task_factory.unit_id]
            # print(f"Step {self.step}: {q_builder.unit.unit_id} is trailblazing a clearing path {path_to_clear}")
            closest_path_pos = closest_rubble_tile_in_group(task_factory.pos, dibbed_tiles, path_to_clear, self.board)
            if closest_path_pos is None:
                # print(f"Step {self.step}: {q_builder.unit.unit_id} is trailblazing a clearing path PSYCH",file=sys.stderr)
                return None
            queue = q_builder.build_mining_queue("clearing path", rubble_tile=closest_path_pos)
            return queue

    def homer_task_assignment(self, q_builder, _task, task_factory):
        number_of_heavies = len(self.my_heavy_units)
        number_of_factories = len(self.my_factories)
        need_heavies = number_of_heavies < number_of_factories * 1.5
        enough_water = task_factory.cargo.water >= 100
        closest_ore = closest_resource_tile('ore', task_factory.pos, [], self.board)
        if self.step < 400 and need_heavies and enough_water and distance_to(task_factory.pos, closest_ore) < 13:
            return q_builder.build_mining_queue("ore")
        else:
            return q_builder.build_mining_queue("ice")

    def icer_task_assignment(self, q_builder, _task, task_factory):
        enough_water = task_factory.cargo.water >= 140
        closest_ore = closest_resource_tile('ore', task_factory.pos, [], self.board)
        if self.step < 800 and enough_water and distance_to(task_factory.pos, closest_ore) < 20:
            return q_builder.build_mining_queue("ore")
        else:
            return q_builder.build_mining_queue("ice")

    def helper_task_assignment(self, q_builder, task_factory):
        unit = q_builder.unit
        if self.factory_helpers[task_factory.unit_id].index(unit.unit_id) == 0:
            helpee_id = self.factory_homers[task_factory.unit_id]
            # print(f"Step {self.step}: {unit.unit_id} is helping {helpee_id}", file=sys.stderr)
            if helpee_id == '':
                queue = self.rubble_digging_task_assignment(q_builder, "rubble", task_factory)
                if queue is None:
                    queue = q_builder.build_waiting_queue(length=53)
                return queue
            helpee = self.my_units[helpee_id]
        else:
            helpee_id = self.factory_icers[task_factory.unit_id]
            if helpee_id == '':
                queue = self.rubble_digging_task_assignment(q_builder, "rubble", task_factory)
                if queue is None:
                    queue = q_builder.build_waiting_queue(length=53)
                return queue
            helpee = self.my_units[helpee_id]
        if helpee_id in self.unit_states.keys():
            if self.unit_states[helpee_id] == "mining adjacent":
                if helpee_id in self.heavy_mining_dibs.keys():
                    tile = self.heavy_mining_dibs[helpee_id]
                else:
                    tile = None
                return q_builder.build_helper_queue(helpee, tile)
        else:
            queue = self.rubble_digging_task_assignment(q_builder, "rubble", task_factory)
            if queue is None:
                queue = q_builder.build_waiting_queue(length=53)
            return queue
    def mining_decision(self, task_factory, q_builder, light=False, lichen_ok=True, ore_path_ok=True, clearing_path_ok=True, rubble_ok=True):
        if light:
            factory_needs = self.factory_needs_light
            tasks_being_done = self.factory_tasks_light
        else:
            factory_needs = self.factory_needs_heavy
            tasks_being_done = self.factory_tasks_heavy


        q_builder.clear_mining_dibs()
        q_builder.clear_lichen_dibs()
        q_builder.clear_previous_task()
        unit = q_builder.unit
        attacking = False

        is_helper, helper_factory_id = self.is_it_a_helper(unit.unit_id)
        if self.step < 400:
            minimum_units = 1 if not light else 5
        elif self.step < 900:
            minimum_units = 0 if not light else 5
        else:
            minimum_units = -1 if not light else 3

        # if you were just attacking, but ran out of queue, try to keep attacking.
        # if you can't, then you'll naturally recurse to the next task
        if unit.unit_id in self.factory_homers.values():
            # print(f"Step {self.step}: {unit.unit_id} is a homer {self.factory_homers}",file=sys.stderr)
            _task = "homer"

        elif unit.unit_id in self.factory_icers.values():
            # print(f"Step {self.step}: {unit.unit_id} is an icer {self.factory_icers}",file=sys.stderr)
            if self.factory_homers[task_factory.unit_id] == '':
                self.factory_icers[task_factory.unit_id] = ''
                _task = "homer"
            else:
                _task = "icer"
        elif is_helper:
            # print(f"Step {self.step}: {unit.unit_id} is a helper {self.factory_helpers}",file=sys.stderr)
            _task = "factory helper"

        elif unit.unit_id in self.last_state.keys() and self.last_state[unit.unit_id] == "attacking" and lichen_ok and self.last_state[unit.unit_id] != "icer" and unit.unit_id not in self.factory_icers.values():
            # print(f"Step {self.step}: 4.1.10.5 {self.factory_icers}", file=sys.stderr)
            attacking = True
            _task = "lichen"

        elif task_factory.unit_id in factory_needs.keys():
            tasks = factory_needs[task_factory.unit_id].copy()
            if not lichen_ok:
                tasks = [task for task in tasks if task != "lichen"]
            if not ore_path_ok:
                tasks = [task for task in tasks if task != "ore path"]
            if not clearing_path_ok:
                tasks = [task for task in tasks if task != "clearing path"]
            if not rubble_ok:
                tasks = [task for task in tasks if task != "rubble"]
            if len(tasks) > 0:
                _task = tasks[0]
                self.pop_factory_needs(task_factory, light=light)

                # don't leave for lichen if there are few units left at the factory
                if len(tasks_being_done[task_factory.unit_id]) < minimum_units and _task == "lichen" and unit.unit_type != "HEAVY":
                    # print(
                    #     f"Step {self.step}: {unit.unit_id} {task_factory.unit_id}, {tasks_being_done[task_factory.unit_id]}",
                    #     file=sys.stderr)
                    if len(tasks) > 1:
                        _task = tasks[1]
                        self.pop_factory_needs(task_factory, light=light)
                    else:
                        queue = q_builder.build_waiting_queue(length=14)
                        return queue
            else:
                # print(f"Step {self.step}: {unit.unit_id} has no tasks to do after popping factory needs",file=sys.stderr)
                queue = q_builder.build_waiting_queue(length=1)
                return queue
            #
            # else:
            #     _task = factory_needs[task_factory.unit_id][0]
            #     self.pop_factory_needs(task_factory, light=light)

        # elif len(tasks_being_done[task_factory.unit_id]) > minimum_units or attacking:
        else:
            # check the other factories in order of nearness to this one and see if they need help
            # if they do, switch to helping them
            factory_in_need = self.find_closest_factory_with_needs(task_factory, factory_needs)
            if factory_in_need is not None:
                # make factory_in_need the new task_factory
                task_factory = factory_in_need
                if lichen_ok:
                    new_factory_needs = factory_needs[task_factory.unit_id]
                else:
                    new_factory_needs = [task for task in factory_needs[task_factory.unit_id] if task != "lichen"]
                    if not new_factory_needs:
                        # print(f"Step {self.step}: {unit.unit_id} has no tasks to do after failing to find lichen",
                        #       file=sys.stderr)
                        queue = q_builder.build_waiting_queue(length=28)
                        return queue

                # if the factory in need has less than 3 workers, do the first task available
                # the idea is that the first 2 tasks are usually vital to the factory's survival
                if len(tasks_being_done[task_factory.unit_id]) < 5:
                    _task = new_factory_needs[0]
                    self.pop_factory_needs(task_factory, light=light)
                # if the factory in need has more than 2 workers, do the last task available
                else:
                    _task = new_factory_needs[-1]
                    self.pop_factory_needs(task_factory, light=light, reverse=True)

            # elif lichen_ok:
            #     _task = "lichen"

            else:
                # if there are no factories in need, wait
                queue = q_builder.build_waiting_queue(length=29)
                return queue
        # else:
        #     # if there are no factories in need, wait
        #     if unit.unit_type == "HEAVY":
        #         print(f"Step {self.step}: {unit.unit_id} is a heavy and has no tasks to do, {tasks_being_done[task_factory.unit_id]}", file=sys.stderr)
        #     queue = q_builder.build_waiting_queue(length=3)
        #     return queue

        if unit.unit_id in self.last_state.keys():
            last_state = self.last_state[unit.unit_id]
        else:
            last_state = ""

        if unit.unit_id in self.factory_icers.values() or last_state == "icer":
            _task = "icer"
        # print(f"Step {self.step}: 4.1.10.8 {self.factory_icers}", file=sys.stderr)

        helping = ["helper"]
        resources = ["rubble", "ice", "ore"]
        pathing = ["ore path", "clearing path"]
        aggro = ["aggro"]
        first_task_word = _task.split(":")[0]

        # Homer tasks
        if first_task_word == "homer" or last_state == "homer":
            queue = self.homer_task_assignment(q_builder, _task, task_factory)
            self.last_state[unit.unit_id] = "homer"
            self.factory_homers[task_factory.unit_id] = unit.unit_id
            if queue is None:
                queue = q_builder.build_waiting_queue(length=18)
            return queue

        # Icer tasks
        if (first_task_word == "icer" or last_state == "icer") and (self.factory_icers[task_factory.unit_id] == unit.unit_id or self.factory_icers[task_factory.unit_id] == ''):
            queue = self.icer_task_assignment(q_builder, _task, task_factory)
            self.last_state[unit.unit_id] = "icer"
            self.factory_icers[task_factory.unit_id] = unit.unit_id
            if queue is None:
                queue = q_builder.build_waiting_queue(length=19)
            return queue

        if _task == "icer":
            first_task_word = "lichen"

        # Mining tasks
        if first_task_word in resources:
            resource = _task
            if resource == "rubble":
                queue = self.rubble_digging_task_assignment(q_builder, resource, task_factory, light=light)
            else:  # it's a resource
                queue = q_builder.build_mining_queue(resource)
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok, ore_path_ok=ore_path_ok, clearing_path_ok=clearing_path_ok, rubble_ok=False)
                self.last_state[unit.unit_id] = self.unit_states[unit.unit_id]
            return queue

        # Pathing tasks
        if first_task_word in pathing:
            queue = self.trailblazing_task_assignment(q_builder, _task, task_factory, light=light)

            if queue is None:
                if first_task_word == "ore path":
                    queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok, ore_path_ok=False, clearing_path_ok=clearing_path_ok, rubble_ok=rubble_ok)
                else:
                    queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok, ore_path_ok=ore_path_ok, clearing_path_ok=False, rubble_ok=rubble_ok)
            self.last_state[unit.unit_id] = self.unit_states[unit.unit_id]
            return queue

        elif _task == "factory helper":
            queue = self.helper_task_assignment(q_builder, task_factory)
            # homer_id = _task.split(":")[1]  # for a helper queue this is the homer_id
            # if homer_id in self.heavy_mining_dibs.keys():
            #     tile = self.heavy_mining_dibs[homer_id]
            # else:
            #     tile = None
            # homer = self.my_units[homer_id]
            # queue = q_builder.build_helper_queue(homer, tile)
            # if queue is None:
            #     queue = q_builder.build_waiting_queue(length=16)
                # queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok, ore_path_ok=ore_path_ok, clearing_path_ok=clearing_path_ok, rubble_ok=rubble_ok)
            self.last_state[unit.unit_id] = "helping"
            return queue

        # Aggro tasks
        elif first_task_word in aggro:
            queue = q_builder.build_aggro_queue()
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok, ore_path_ok=ore_path_ok, clearing_path_ok=clearing_path_ok, rubble_ok=rubble_ok)
            self.last_state[unit.unit_id] = "aggro"
            return queue

        # Attacking tasks
        elif first_task_word == "lichen":
            dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
            dibbed_tiles.extend([pos for pos in self.light_mining_dibs.values()])
            if self.step > 900:
                lichen_tile = closest_opp_lichen(self.opp_strains, q_builder.unit.pos, dibbed_tiles, self.board, priority=False)
            else:
                lichen_tile = closest_opp_lichen(self.opp_strains, q_builder.unit.pos, dibbed_tiles, self.board, priority=True)
            if lichen_tile is not None:
                queue = q_builder.build_attack_queue(lichen_tile)
                if queue is None and len(tasks_being_done[task_factory.unit_id]) < minimum_units:
                    queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=False, ore_path_ok=ore_path_ok, clearing_path_ok=clearing_path_ok)

                if queue is not None:
                    self.last_state[unit.unit_id] = "attacking"
                    return queue

        # if you made it here, you couldn't find a lichen tile
        if unit.unit_type == "HEAVY":
            if _task == "icer":
                print(f"Step {self.step}: {unit.unit_id} has task {_task} for {task_factory.unit_id}. icers: {self.factory_icers}", file=sys.stderr)
            print(f"Step {self.step}: {unit.unit_id} couldn't find a {_task} queue, waiting", file=sys.stderr)
        return q_builder.build_waiting_queue(length=10)

    def decision_tree(self, unit, factories, opp_units, factory=None):
        if unit.unit_type == "LIGHT":
            is_light = True
            factory_tasks = self.factory_tasks_light
        else:
            is_light = False
            factory_tasks = self.factory_tasks_heavy

        if unit.unit_id not in self.unit_states.keys():
            self.unit_states[unit.unit_id] = "idle"
            state = self.unit_states[unit.unit_id]
        else:
            state = self.unit_states[unit.unit_id]
        self.avoid_collisions(unit, state)  # make sure you aren't going to collide with a friendly unit

        # make sure you aren't about to dig up the rubble you just made by clearing opp lichen
        if len(self.action_queue[unit.unit_id]) > 0 and state == "attacking":
            self.check_valid_dig(unit)

        if len(self.action_queue[unit.unit_id]) > 0 and state == "helping":
            self.check_valid_transfer(unit)

        closest_factory = get_closest_factory(factories, unit.pos)
        task_factory = closest_factory
        # if you are the closest heavy to a given factory, you need to be doing tasks for that factory
        heavy_tiles = [u.pos for u in self.my_heavy_units]
        for fid, f in factories.items():
            closest_heavy = closest_tile_in_group(f.pos, [], heavy_tiles)
            # if closest_heavy is not None and on_tile(unit.pos, closest_heavy):
            has_no_heavies = len(self.factory_tasks_heavy[fid].keys()) == 0
            if closest_heavy is not None and on_tile(unit.pos, closest_heavy) and has_no_heavies:
                task_factory = f

        # if you are a homer, keep your factory!
        if unit.unit_id in self.factory_homers.values():
            for fid, f in factories.items():
                if self.factory_homers[fid] == unit.unit_id:
                    task_factory = f

        if unit.unit_id in self.factory_icers.values():
            for fid, f in factories.items():
                if self.factory_icers[fid] == unit.unit_id:
                    task_factory = f
        is_helper, factory_id = self.is_it_a_helper(unit.unit_id)
        if is_helper:
            if factory_id in self.my_factories.keys():
                task_factory = self.my_factories[factory_id]

            if self.factory_helpers[task_factory.unit_id].index(unit.unit_id) == 0:
                helpee_id = self.factory_homers[task_factory.unit_id]
            else:
                helpee_id = self.factory_icers[task_factory.unit_id]
            if helpee_id in self.unit_states.keys() and self.unit_states[helpee_id] == "mining_adjacent" and state != "helping":
                self.action_queue[unit.unit_id] = []

        q_builder = QueueBuilder(self, unit, task_factory, self.board)

        need_recharge, path_home, cost_home = q_builder.check_need_recharge(factory=closest_factory)
        self.path_home[unit.unit_id] = path_home
        self.cost_home[unit.unit_id] = cost_home
        if state == "attacking" and need_recharge and is_light:
            queue = q_builder.build_waiting_queue(length=33)
            self.remove_old_next_pos_from_occ_next(unit)
            self.update_queues(unit, queue)

        # make sure closest factory is not about to run dry, save it if you have ice
        ice_cargo = unit.cargo.ice
        if closest_factory.cargo.water < 50 and ice_cargo > 50:
            q_builder = QueueBuilder(self, unit, closest_factory, self.board)
            not_a_homer = unit.unit_id not in self.factory_homers.values()
            not_an_icer = unit.unit_id not in self.factory_icers.values()
            if not_a_homer:
                q_builder.clear_mining_dibs()
            q_builder.clear_lichen_dibs()
            q_builder.clear_previous_task()

            closest_tile = closest_factory_tile(closest_factory.pos, unit.pos, [])
            if on_tile(unit.pos, closest_tile) and can_stay(unit.pos, list(self.occupied_next)):
                queue = [unit.transfer(0, 0, unit.cargo.ice)]
                print(f"Step {self.step}: {unit.unit_id} interrupting queue to transfer water to {task_factory.pos}, queue: {queue}", file=sys.stderr)
                self.remove_task_from_factory(unit)
                self.remove_old_next_pos_from_occ_next(unit)
                self.update_queues(unit, queue)

            elif on_tile(unit.pos, closest_tile) and not can_stay(unit.pos, list(self.occupied_next)):
                direciton = move_toward(unit.pos, closest_factory.pos, list(self.occupied_next))
                queue = [unit.move(direciton)]
                print(f"Step {self.step}: {unit.unit_id} interrupting queue to transfer water to {task_factory.pos}, queue: {queue}", file=sys.stderr)
                self.remove_task_from_factory(unit)
                self.remove_old_next_pos_from_occ_next(unit)
                self.update_queues(unit, queue)

            # elif not_already_transfering and not need_recharge:
            #     path = q_builder.get_path_positions(unit.pos, closest_tile)
            #     if not path or len(path) <= 1:
            #         queue = q_builder.build_waiting_queue(length=14)
            #     else:
            #         queue = q_builder.get_path_moves(path)
            #     print(f"Step {self.step}: {unit.unit_id} interrupting queue to transfer water to {task_factory.pos}, queue: {queue}", file=sys.stderr)
            #     self.remove_task_from_factory(unit)
            #     self.remove_old_next_pos_from_occ_next(unit)
            #     self.update_queues(unit, queue)


        # Check for evasions now that we have come up with our final queue and any interrupts
        evasion_queue = evasion_check(self, unit, task_factory, opp_units, self.board)
        if evasion_queue is not None:
            self.remove_task_from_factory(unit)
            self.remove_old_next_pos_from_occ_next(unit)
            self.update_queues(unit, evasion_queue)
            return

        else:  # if you don't need to recharge

            # if you don't have a queue, build one
            if len(self.action_queue[unit.unit_id]) == 0 or state == "waiting" or state == "solar panel" or state == "slow charging":
                queue = self.mining_decision(task_factory, q_builder, light=is_light)  # try to get a new queue

                # if you were waiting before and you're waiting now, and you have a queue, just keep waiting
                was_waiting = state == "waiting"  # was waiting to start the step
                is_waiting = self.unit_states[unit.unit_id] == "waiting"  # still waiting after decision tree

                was_solar_panel = state == "solar panel"
                is_solar_panel = self.unit_states[unit.unit_id] == "solar panel"

                was_slow_charging = state == "slow charging"
                is_slow_charging = self.unit_states[unit.unit_id] == "slow charging"

                has_a_queue = len(self.action_queue[unit.unit_id]) > 0 and self.action_queue[unit.unit_id] != []
                still_waiting = was_waiting and is_waiting and has_a_queue
                still_solar_panel = was_solar_panel and is_solar_panel and has_a_queue
                still_slow_charging = was_slow_charging and is_slow_charging and has_a_queue

                if still_waiting or still_solar_panel or still_slow_charging:
                    # you were already waiting/solar/slow and nothing new came up
                    # so just continue waiting/solar/slow and add your next pos to occupied_next
                    # update the local action queue, but don't update the new_queue
                    self.add_nextpos_to_occnext(unit)
                    return

                if queue is None:
                    # Got through the whole decision tree and couldn't find a queue
                    if unit.unit_id not in self.factory_homers.values() and unit.unit_id not in self.factory_icers.values():
                        queue = q_builder.build_waiting_queue(length=6)
                    if queue is None:
                        print(f"Step {self.step}: {unit.unit_id} couldn't find a queue, doing NONE, state: {state}", file=sys.stderr)
                        return

                # update the action queue, this adds new_pos to occupied_next
                self.update_queues(unit, queue)
            else:
                # if you have a queue, add the next position to occupied_next
                self.add_nextpos_to_occnext(unit)

    def act(self, step: int, obs, remainingOverageTime: int = 60):
        # profiler.enable()
        # initial step setup, these are the basic vars that we need to have
        game_state = obs_to_game_state(step, self.env_cfg, obs)
        self.step = game_state.real_env_steps
        # if self.step > 15:
        #     print('f')

        factories = game_state.factories[self.player]
        opp_factories = game_state.factories[self.opp_player]
        all_factories = {**factories, **opp_factories}
        units = game_state.units[self.player]
        opp_units = game_state.units[self.opp_player]
        # global vars
        self.board = obs['board']
        self.my_units = units
        self.opp_units = opp_units
        self.my_factories = factories
        self.opp_factories = opp_factories


        # functions that need to be called on each step, mainly to clean the slate from the last step
        self.new_queue = dict()  # Clear out the new queue from last step
        self.pop_action_queue()  # Then update the persistent action queue
        self.update_occupied_next()  # Update the occupied_next set
        self.clear_dead_units_from_memory()  # Clear out the dead units from the mining dibs
        self.mining_adjacent = set()  # Clear out the mining adjacent set
        self.helper_treated = set()  # Clear out the helper treated set
        self.set_factory_charge_level()  # Set the charge level of each factory

        # Factory updates
        self.factory_types = dict()  # Clear out the factory types from last step
        self.factory_needs = dict()  # Clear out the factory needs from last step

        print(f"Step {self.step}: timing", file=sys.stderr)
        # if self.step == 200:
        #     print('halt')

        # Set opp_strains and paths
        if self.step == 2:
            self.set_ore_paths()
            self.set_clearing_paths()
            for fid, factory in opp_factories.items():
                self.opp_strains.append(factory.strain_id)
            i = 0
            for fid, factory in factories.items():
                self.my_strains.append(factory.strain_id)
                self.factory_adjacency_scores[fid] = self.adjacency_scores[i]
                i += 1
            self.set_factory_helper_amounts()

        if self.step >= 2:
            self.set_ore_path_costs()
            self.set_clearing_path_costs()

        ice_map, ore_map = self.board["ice"], self.board["ore"]
        for fid, factory in factories.items():
            # Update the factory's resources, these are the resources which the factory should have control over
            fact_ice, fact_ore = nearby_resources(factory.pos, ice_map, ore_map, all_factories)
            self.factory_resources[fid] = [fact_ice, fact_ore]

            # then update the factory's type
            self.set_factory_type(factory)

            # then update the factory's needs
            self.define_factory_needs(factory)


        # Unit Actions
        if self.step >= 20:
            self.update_and_assign_helpers()
        # print(f"Step {self.step}: helper_treated: {self.factory_helpers}", file=sys.stderr)
        heavies, lights, helpers, adjacents, homers, icers, heavy_attackers, light_attackers = self.split_heavies_and_lights(units)

        for unit in homers:
            self.decision_tree(unit, factories, opp_units)

        for unit in icers:
            self.decision_tree(unit, factories, opp_units)


        # first get actions for heavies that were previously mining adjacent
        for unit in adjacents:
            self.decision_tree(unit, factories, opp_units)
            self.mining_adjacent.add(unit.unit_id)


        # first get actions for lights that were previously helping
        helper_list = [u.unit_id for u in helpers]
        # print(f"Step {self.step}: helpers: {helper_list}", file=sys.stderr)
        for unit in helpers:
            self.decision_tree(unit, factories, opp_units)
            self.helper_treated.add(unit.unit_id)


        # then get actions for heavies that weren't mining adjacent
        for unit in heavies:
            if unit.unit_id not in self.mining_adjacent:
                self.decision_tree(unit, factories, opp_units)


        for unit in heavy_attackers:
            self.decision_tree(unit, factories, opp_units)


        # then get actions for lights that weren't helping
        for unit in lights:
            if unit.unit_id not in self.helper_treated:
                self.decision_tree(unit, factories, opp_units)


        remaining_light_attackers = self.assign_tasks_to_attackers(light_attackers, "LIGHT")


        for unit in remaining_light_attackers:
            self.decision_tree(unit, factories, opp_units)

        # FACTORIES
        for fid, factory in factories.items():
            if self.step == 1:
                clearing_position = self.find_clearing_position(factory.pos, 4, 12)
                if clearing_position is not None:
                    # print(f"Step {self.step}: {factory.unit_id} is clearing {clearing_position}", file=sys.stderr)
                    self.factory_clearing_tiles[factory.unit_id] = clearing_position

            # I'm thinking these will be the factory functions: factory_construct, factory_water, factory_state
            f_pos = (factory.pos[0], factory.pos[1])
            if f_pos not in self.occupied_next:
                if factory.can_build_heavy(game_state) and factory.unit_id in self.factory_needs_heavy:
                    if self.step < 120:
                        number_of_heavies = len(self.my_heavy_units)
                        number_of_factories = len(factories)
                        if number_of_heavies < number_of_factories:
                            queue = factory.build_heavy()
                            self.update_queues(factory, queue)
                            continue
                    else:
                        queue = factory.build_heavy()
                        self.update_queues(factory, queue)
                        continue

                elif factory.can_build_light(game_state) and factory.unit_id in self.factory_needs_light:
                    if self.step < 100:
                        lights_wanted = 5
                    elif self.step < 200:
                        lights_wanted = 8
                    elif self.step < 400:
                        lights_wanted = 10
                    else:
                        lights_wanted = 20

                    light_units = len(self.my_light_units)
                    total_factories = len(factories)
                    need_basic_lights = light_units < total_factories * lights_wanted
                    if self.factory_needs_light[factory.unit_id] and need_basic_lights:
                        if self.step > 100:
                            number_of_ore = self.factory_resources[fid][1]
                            if self.step % 10 == 0:
                                if number_of_ore > 0 and self.factory_icers[fid] != '':
                                    queue = factory.build_light()
                                    self.update_queues(factory, queue)
                                    continue
                                elif number_of_ore == 0:
                                    queue = factory.build_light()
                                    self.update_queues(factory, queue)
                                    continue
                        else:
                            queue = factory.build_light()
                            self.update_queues(factory, queue)
                            continue
            self.factory_watering(factory, game_state)

        # Finalize the action queue and submit it
        finalized_actions = self.finalize_new_queue()
        # profiler.disable()
        # profiler.dump_stats('profile.txt')
        return finalized_actions
