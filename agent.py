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

        # dibs
        self.light_mining_dibs = dict()  # {unit_id: pos}
        self.heavy_mining_dibs = dict()
        self.lichen_dibs = dict()  # {unit_id: [pos, pos, etc]}

        # reserve power
        self.moderate_reserve_power = {"LIGHT": 15, "HEAVY": 150}
        self.low_reserve_power = {"LIGHT": 10, "HEAVY": 100}

        # Mining Adjacent and Helper
        self.last_state = dict()  # whatever your last state was
        self.helper_treated = set()
        self.mining_adjacent = set()

    def early_setup(self, step: int, obs, remainingOverageTime: int = 60):
        queue, factories_to_place, factory_position, low_rubble_scores = setup(self, step, obs, remainingOverageTime)
        if factories_to_place > self.number_of_factories:
            self.number_of_factories = factories_to_place

        if low_rubble_scores is not None:
            self.low_rubble_scores = low_rubble_scores

        if factory_position is not None:
            x, y = factory_position[0], factory_position[1]
            self.my_factory_centers.add((x, y))

        return queue

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
            if factory.power < 600 and self.factory_low_charge_heavy[fid] is False:
                self.factory_low_charge_heavy[fid] = True
            elif factory.power >= 800 and self.factory_low_charge_heavy[fid] is True:
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
        for fid, uid in self.factory_homers.items():
            if uid in self.my_units.keys() and fid in self.my_factories.keys():
                new_factory_homers[fid] = uid
        self.factory_homers = new_factory_homers

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

    def split_heavies_and_lights(self, units):
        helpers, adjacents = [], []
        heavies, lights = [], []
        homers = []
        for uid, u in units.items():

            # this check should go in some sort of unit setup function
            if uid not in self.action_queue.keys():
                self.action_queue[uid] = []
            if u.unit_id in self.factory_homers.values():
                homers.append(u)
            elif u.unit_type == "HEAVY":
                if uid in self.unit_states.keys() and self.unit_states[uid] == "mining adjacent":
                    adjacents.append(u)
                else:
                    heavies.append(u)
            elif u.unit_type == "LIGHT":
                if uid in self.unit_states.keys() and self.unit_states[uid] == "helping":
                    helpers.append(u)
                else:
                    lights.append(u)

        self.my_heavy_units = heavies
        self.my_light_units = lights
        return heavies, lights, helpers, adjacents, homers

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

        number_of_ice = self.factory_resources[fid][0]
        number_of_ore = self.factory_resources[fid][1]

        factory_type = self.factory_types[fid]
        factory_state = "basic"  # for now, all factories are basic

        # TODO: make this a switch statement
        if factory_state == "basic":
            # TODO: make this a function

            # figure out all tasks that need to be done
            light_todo = []
            heavy_todo = []

            # LIGHTS
            # do I have a path to the nearest ore?
            if self.step >= 2:
                for uid, task in self.factory_tasks_heavy[fid].items():
                    if uid in self.unit_states.keys() and self.unit_states[uid] == "mining adjacent":
                        light_todo.append(f"helper:{uid}")

                max_excavators = 8
                cost_to_ore = self.ore_path_costs[fid]
                if cost_to_ore > 0:
                    excavators_needed = ceil(cost_to_ore / 20)
                    excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                    # if not, then I need to excavate a path to the nearest ore
                    [light_todo.append("ore path") for _ in range(excavators_needed)]

                # do I have room to grow lichen?
                free_spaces_wanted = 10
                needs_excavation, free_spaces_actual = lichen_surrounded(self.board, factory.strain_id,
                                                                         self.opp_strains, self.occupied_next,
                                                                         free_spaces_wanted)
                primary_zone = get_orthogonal_positions(factory.pos, 2, self.my_factory_tiles, self.board)
                zone_cost = get_total_rubble(self.board, primary_zone)
                cost_to_clearing = self.clearing_path_costs[fid]
                if (needs_excavation or zone_cost > 0) and cost_to_ore == 0:
                    if zone_cost == 0:
                        # check to see if clearing a path is necessary
                        if cost_to_clearing > 0:
                            excavators_needed = ceil(cost_to_clearing / 20)
                            excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                            # if not, then I need to excavate a clearing path
                            [light_todo.append("clearing path") for _ in range(excavators_needed)]
                        else:
                            excavators_needed = ceil(free_spaces_wanted - free_spaces_actual)
                            excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                            # if not, then I need to excavate edge of lichen
                            [light_todo.append("rubble") for _ in range(excavators_needed)]
                    else:
                        excavators_needed = ceil(zone_cost / 20)
                        excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                        # # if not, then I need to excavate edge of lichen
                        [light_todo.append("rubble") for _ in range(excavators_needed)]
                non_rubble =[]
                # if I have room to grow lichen, do I have enough water to grow lichen?
                if factory.cargo.water < 400 and self.step > 150:
                    ice_miners = number_of_ice if number_of_ice <= 8 else 8
                    # # if not, then I need to mine ice
                    [light_todo.append("ice") for _ in range(2)]
                    [non_rubble.append("ice") for _ in range(ice_miners - 2)]

                # if enemy is growing lichen nearby, attack it
                dibbed = list(self.light_mining_dibs.values())
                dibbed.extend(list(self.heavy_mining_dibs.values()))
                opp_lichen_tile = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.board)
                if opp_lichen_tile is not None and distance_to(factory.pos, opp_lichen_tile) <= 30:
                    [non_rubble.append("lichen") for _ in range(4)]

                # if I have enough water to grow lichen, do I have enough ore to build bots?
                if factory.cargo.metal < 100 and self.step > 100:
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

            if self.step > 100:
                cost_to_clearing = self.clearing_path_costs[fid]
                if cost_to_clearing > 0:
                    heavy_todo.append("clearing path")

            if number_of_ice > 1:
                heavy_todo.append("ice")

            # are you dangerously low on water?
            if factory.cargo.water < 150 and number_of_ice > 2:
                emergency_ice_miners = number_of_ice - 1 if number_of_ice - 1 <= 2 else 2
                [heavy_todo.append("ice") for _ in range(emergency_ice_miners)]

            if len(self.my_heavy_units) < len(self.my_factories) * 2 and factory.cargo.ore < 200:
                # if not, then I need to mine ore
                heavy_todo.append("ore")

            if self.step >= 100:
                dibbed = list(self.heavy_mining_dibs.values())
                dibbed.extend(list(self.light_mining_dibs.values()))
                closest_lichen = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.board)
                if closest_lichen is not None and distance_to(factory.pos, closest_lichen) <= 35:
                    # if so, then I need to attack
                    # [heavy_todo.append("lichen") for _ in range(2)]
                    heavy_todo.append("lichen")

            # if I have enough water to grow lichen, am I super clogged up with rubble?
            surrounded, free_spaces = lichen_surrounded(self.board, factory.strain_id, self.opp_strains,
                                                        self.occupied_next, 10)
            if surrounded and free_spaces < 3:
                # # if so, then I need to excavate either a clearing path or immediate zone
                heavy_todo.append("rubble")

            # if I have enough ore to build bots, does someone else need my help?
            # # if so, then I need to go help them

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
            if power > 5000:
                if game_state.real_env_steps % 4 == 0:
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
        elif 750 <= game_state.real_env_steps < 980:
            steps_remaining = 1000 - game_state.real_env_steps
            if factory.cargo.water > steps_remaining * 6:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif factory.cargo.water > 400:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif factory.cargo.water > 200 and game_state.real_env_steps % 3 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            elif factory.cargo.water > 50 and game_state.real_env_steps % 2 == 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        elif 980 <= game_state.real_env_steps < 996:
            if factory.cargo.water > 50:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        elif game_state.real_env_steps >= 996:
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
                queue = q_builder.build_mining_queue(resource, rubble_tile=close_rubble_tile)

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
                closest_path_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.board)
            queue = q_builder.build_mining_queue("ore path", rubble_tile=closest_path_pos)
            return queue
        else:  # it's a clearing path
            path_to_clear = self.clearing_paths[task_factory.unit_id]
            closest_path_pos = closest_rubble_tile_in_group(task_factory.pos, dibbed_tiles, path_to_clear, self.board)
            if closest_path_pos is None:
                closest_path_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.board)
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

    def mining_decision(self, task_factory, q_builder, light=False, lichen_ok=True):
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

        # if you were just attacking, but ran out of queue, try to keep attacking.
        # if you can't, then you'll naturally recurse to the next task
        if unit.unit_id in self.factory_homers.values():
            _task = "homer"

        # if unit.unit_id in self.homer_helpers.keys():
        #     _task = "helper"

        elif unit.unit_id in self.last_state.keys() and self.last_state[unit.unit_id] == "attacking" and lichen_ok:
            _task = "lichen"
            self.last_state[unit.unit_id] = "recursing"

        elif task_factory.unit_id in factory_needs.keys() and not lichen_ok:
            tasks_no_lichen = [task for task in factory_needs[task_factory.unit_id] if task != "lichen"]
            if len(tasks_no_lichen) > 0:
                _task = tasks_no_lichen[0]
                self.pop_factory_needs(task_factory, light=light)
            else:
                print(f"Step {self.step}: {unit.unit_id} has no tasks to do after failing to find lichen",
                      file=sys.stderr)
                queue = q_builder.build_waiting_queue(length=1)
                return queue

        elif task_factory.unit_id in factory_needs.keys() and lichen_ok:
            _task = factory_needs[task_factory.unit_id][0]
            self.pop_factory_needs(task_factory, light=light)

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
                        print(f"Step {self.step}: {unit.unit_id} has no tasks to do after failing to find lichen",
                              file=sys.stderr)
                        queue = q_builder.build_waiting_queue(length=28)
                        return queue

                # if the factory in need has less than 3 workers, do the first task available
                # the idea is that the first 2 tasks are usually vital to the factory's survival
                if len(tasks_being_done[task_factory.unit_id]) < 3:
                    _task = new_factory_needs[0]
                    self.pop_factory_needs(task_factory, light=light)
                # if the factory in need has more than 2 workers, do the last task available
                else:
                    _task = new_factory_needs[-1]
                    self.pop_factory_needs(task_factory, light=light, reverse=True)

            else:
                # if there are no factories in need, wait
                queue = q_builder.build_waiting_queue(length=28)
                return queue

        homer = ["homer"]
        helping = ["helper"]
        resources = ["rubble", "ice", "ore"]
        pathing = ["ore path", "clearing path"]
        attacking = ["lichen", "aggro"]
        first_task_word = _task.split(":")[0]

        # Homer tasks
        if first_task_word in homer:
            queue = self.homer_task_assignment(q_builder, _task, task_factory)
            self.last_state[unit.unit_id] = "homer"
            self.factory_homers[task_factory.unit_id] = unit.unit_id
            return queue

        # Mining tasks
        if first_task_word in resources:
            resource = _task
            if resource == "rubble":
                queue = self.rubble_digging_task_assignment(q_builder, resource, task_factory, light=light)
            else:  # it's a resource
                queue = q_builder.build_mining_queue(resource)

            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok)
                self.last_state[unit.unit_id] = self.unit_states[unit.unit_id]
            return queue

        # Pathing tasks
        elif first_task_word in pathing:
            queue = self.trailblazing_task_assignment(q_builder, _task, task_factory, light=light)
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok)
            self.last_state[unit.unit_id] = self.unit_states[unit.unit_id]
            return queue

        elif first_task_word in helping:
            homer_id = _task.split(":")[1]  # for a helper queue this is the homer_id
            homer = self.my_units[homer_id]
            queue = q_builder.build_helper_queue(homer)
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=lichen_ok)
            self.last_state[unit.unit_id] = "helping"
            return queue

        # Attacking tasks
        dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
        dibbed_tiles.extend([pos for pos in self.light_mining_dibs.values()])
        lichen_tile = closest_opp_lichen(self.opp_strains, q_builder.unit.pos, dibbed_tiles, self.board, priority=True)
        if lichen_tile is not None and distance_to(lichen_tile, task_factory.pos) <= 30:
            queue = q_builder.build_attack_queue(lichen_tile)
            if queue is None:
                # TODO: aggro queue for heavies
                queue = self.mining_decision(task_factory, q_builder, light=light, lichen_ok=False)

            self.last_state[unit.unit_id] = self.unit_states[unit.unit_id]
            if queue is not None:
                return queue

        # if you made it here, you couldn't find a lichen tile
        return q_builder.build_waiting_queue(length=3)

    def decision_tree(self, unit, factories, opp_units):
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
        for factory_id, unit_tasks in factory_tasks.items():
            if unit.unit_id in unit_tasks.keys() and factory_id in factories.keys():
                task_factory = factories[factory_id]

        # if you are a homer, keep your factory!
        if unit.unit_id in self.factory_homers.values():
            for fid, f in factories.items():
                if self.factory_homers[fid] == unit.unit_id:
                    task_factory = f

        q_builder = QueueBuilder(self, unit, task_factory, self.board)

        need_recharge, path_home, cost_home = q_builder.check_need_recharge()
        self.path_home[unit.unit_id] = path_home
        self.cost_home[unit.unit_id] = cost_home

        # if need_recharge and state != "recharging" and state != "low battery" and state != "solar charging" and state != "waiting":
        #     q_builder.clear_mining_dibs()
        #     q_builder.clear_lichen_dibs()
        #     queue = q_builder.build_recharge_queue()
        #     print(f"Step {self.step}: {unit.unit_id} interrupting queue to recharge", file=sys.stderr)
        #     self.remove_task_from_factory(unit)
        #     self.remove_old_next_pos_from_occ_next(unit)
        #     self.update_queues(unit, queue)

        # make sure closest factory is not about to run dry, save it if you have ice
        # home_pref=False results in closest_factory
        transferable, transfer_direction, trans_pos = q_builder.transfer_ready(home_pref=False)
        ice_cargo = unit.cargo.ice
        not_already_transfering = state != "transferring"
        if closest_factory.cargo.water < 50 and transferable and not_already_transfering and not need_recharge and ice_cargo > 50:
            state = "transferring"
            q_builder = QueueBuilder(self, unit, closest_factory, self.board)
            not_a_homer = unit.unit_id not in self.factory_homers.values()
            if not_a_homer:
                q_builder.clear_mining_dibs()
            q_builder.clear_lichen_dibs()
            q_builder.clear_previous_task()
            queue, cost = q_builder.get_transfer_queue(transfer_direction, home_pref=True)
            print(f"Step {self.step}: {unit.unit_id} interrupting queue to transfer water to {task_factory.pos}", file=sys.stderr)
            self.remove_task_from_factory(unit)
            self.remove_old_next_pos_from_occ_next(unit)
            self.update_queues(unit, queue)

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
        self.board = obs['board']
        game_state = obs_to_game_state(step, self.env_cfg, obs)
        factories = game_state.factories[self.player]
        opp_factories = game_state.factories[self.opp_player]
        all_factories = {**factories, **opp_factories}
        units = game_state.units[self.player]
        opp_units = game_state.units[self.opp_player]
        # global vars
        self.step = game_state.real_env_steps
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
            for fid, factory in factories.items():
                self.my_strains.append(factory.strain_id)

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
        heavies, lights, helpers, adjacents, homers = self.split_heavies_and_lights(units)

        for unit in homers:
            self.decision_tree(unit, factories, opp_units)

        # first get actions for heavies that were previously mining adjacent
        for unit in adjacents:
            self.decision_tree(unit, factories, opp_units)
            self.mining_adjacent.add(unit.unit_id)

        # first get actions for lights that were previously helping
        for unit in helpers:
            self.decision_tree(unit, factories, opp_units)
            self.helper_treated.add(unit.unit_id)

        # then get actions for heavies that weren't mining adjacent
        for unit in heavies:
            if unit.unit_id not in self.mining_adjacent:
                self.decision_tree(unit, factories, opp_units)

        # then get actions for lights that weren't helping
        for unit in lights:
            if unit.unit_id not in self.helper_treated:
                self.decision_tree(unit, factories, opp_units)

        # FACTORIES
        for fid, factory in factories.items():
            if self.step == 1:
                clearing_position = self.find_clearing_position(factory.pos, 8, 15)
                if clearing_position is not None:
                    # print(f"Step {self.step}: {factory.unit_id} is clearing {clearing_position}", file=sys.stderr)
                    self.factory_clearing_tiles[factory.unit_id] = clearing_position

            # I'm thinking these will be the factory functions: factory_construct, factory_water, factory_state
            f_pos = (factory.pos[0], factory.pos[1])
            if f_pos not in self.occupied_next:
                if factory.can_build_heavy(game_state) and factory.unit_id in self.factory_needs_heavy:
                    queue = factory.build_heavy()
                    self.update_queues(factory, queue)
                    continue

                elif factory.can_build_light(game_state) and factory.unit_id in self.factory_needs_light:
                    if self.step < 100:
                        lights_wanted = 6
                    elif self.step < 200:
                        lights_wanted = 8
                    elif self.step < 400:
                        lights_wanted = 10
                    else:
                        lights_wanted = 20

                    light_units = len(self.my_light_units)
                    total_factories = len(factories)
                    need_basic_lights = light_units < total_factories * lights_wanted
                    have_plenty_of_heavies = len(self.my_heavy_units) > total_factories * 2
                    if self.factory_needs_light[factory.unit_id] and (need_basic_lights or have_plenty_of_heavies):
                        if self.step > 200:
                            if self.step % 10 == 0:
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
