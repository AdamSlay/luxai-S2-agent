# import numpy as np
# import sys
from math import ceil

from lib.dijkstra import dijkstras_path
from lib.evasion import evasion_check
from lib.excavation_utils import *
from lib.factory_utils import *
from lib.utils import *
from lib.queue_builder import QueueBuilder
from lib.setup_factories import setup

from lux.kit import obs_to_game_state
from lux.config import EnvConfig


class Agent():
    def __init__(self, player: str, env_cfg: EnvConfig) -> None:
        self.player = player
        self.opp_player = "player_1" if self.player == "player_0" else "player_0"
        np.random.seed(0)
        self.env_cfg: EnvConfig = env_cfg
        self.step = 0
        self.obs = None
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

        # States
        self.unit_states = dict()  # uid: "state"
        self.factory_types = dict()  # fid: "type"
        self.factory_states = dict()  # fid: "state"

        # Tasks
        self.factory_needs_light = dict()  # fid: [ice, ore]  --> what I need
        self.factory_needs_heavy = dict()  # fid: [ice, ore]  --> what I need
        self.factory_tasks_light = dict()  # {fid: {unit_id :"task"}}  --> what I've got
        self.factory_tasks_heavy = dict()  # {fid: {unit_id :"task"}}  --> what I've got

        # dibs
        self.light_mining_dibs = dict()  # {unit_id: pos}
        self.heavy_mining_dibs = dict()  # {unit_id: [pos, pos, etc]}
        self.lichen_dibs = dict()

        # reserve power
        self.moderate_reserve_power = {"LIGHT": 15, "HEAVY": 150}
        self.low_reserve_power = {"LIGHT": 10, "HEAVY": 100}

        # Mining Adjacent and Helper
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

    def set_ore_paths(self):
        for fid, factory in self.my_factories.items():
            self.ore_paths[fid] = []
            # print(f"finding ore path for factory {fid}", file=sys.stderr)
            closest_ore = closest_resource_tile("ore", factory.pos, list(self.opp_factory_tiles), self.obs)
            if closest_ore is not None:
                ore_distance = distance_to(closest_ore, factory.pos)
                if ore_distance < 20:
                    rubble_map = self.obs['board']['rubble']
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
        for fid, factory in self.my_factories.items():
            clearing_tile = self.factory_clearing_tiles[fid]
            if clearing_tile is not None:
                rubble_map = self.obs['board']['rubble']
                ice_map = self.obs['board']['ice']
                ore_map = self.obs['board']['ore']
                resource_positions = np.column_stack(np.where((ice_map == 1) | (ore_map == 1)))
                off_limits = [pos for pos in resource_positions]
                off_limits.extend(list(self.opp_factory_tiles))
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
            self.ore_path_costs[fid] = get_path_cost(path, self.obs)

    def set_clearing_path_costs(self):
        for fid, path in self.clearing_paths.items():
            self.clearing_path_costs[fid] = get_path_cost(path, self.obs)

    def check_valid_dig(self, unit, obs):
        if self.action_queue[unit.unit_id][0][0] == 3:
            lichen_map = obs['board']['lichen_strains']

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

    def avoid_collisions(self, unit):
        state = self.unit_states[unit.unit_id]
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
                    q_builder = QueueBuilder(self, unit, [], self.obs)
                    q_builder.clear_mining_dibs()
                    q_builder.clear_lichen_dibs()
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

    def update_queues(self, unit, queue):
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
        heavies, lights = [], []
        for uid, u in units.items():

            # this check should go in some sort of unit setup function
            if uid not in self.action_queue.keys():
                self.action_queue[uid] = []

            if u.unit_type == "HEAVY":
                heavies.append(u)
            elif u.unit_type == "LIGHT":
                lights.append(u)

        self.my_heavy_units = heavies
        self.my_light_units = lights
        return heavies, lights

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

                max_excavators = 4
                cost_to_ore = self.ore_path_costs[fid]
                if cost_to_ore > 0:
                    excavators_needed = ceil(cost_to_ore / 40)
                    excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                    # if not, then I need to excavate a path to the nearest ore
                    [light_todo.append("ore path") for _ in range(excavators_needed)]

                # do I have room to grow lichen?
                free_spaces_wanted = 10
                needs_excavation, free_spaces_actual = lichen_surrounded(self.obs, factory.strain_id, self.opp_strains, self.occupied_next, free_spaces_wanted)
                primary_zone = get_orthogonal_positions(factory.pos, 1, self.my_factory_tiles, self.obs)
                zone_cost = get_total_rubble(self.obs, primary_zone)
                if (needs_excavation or zone_cost > 0) and cost_to_ore == 0:
                    if zone_cost == 0:
                        # check to see if clearing a path is necessary
                        cost_to_clearing = self.clearing_path_costs[fid]
                        if cost_to_clearing > 0:
                            excavators_needed = ceil(cost_to_clearing / 40)
                            excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                            # if not, then I need to excavate a clearing path
                            [light_todo.append("clearing path") for _ in range(excavators_needed)]
                        else:
                            excavators_needed = ceil((free_spaces_wanted - free_spaces_actual) / 2)
                            excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                            # if not, then I need to excavate edge of lichen
                            [light_todo.append("rubble") for _ in range(excavators_needed)]
                    else:
                        excavators_needed = ceil(zone_cost / 40)
                        excavators_needed = excavators_needed if excavators_needed <= max_excavators else max_excavators
                        # # if not, then I need to excavate edge of lichen
                        [light_todo.append("rubble") for _ in range(excavators_needed)]

                # if I have room to grow lichen, do I have enough water to grow lichen?
                if factory.cargo.water < 200 and number_of_ice >= 2:
                    ice_miners = number_of_ice - 1 if number_of_ice - 1 <= 3 else 3
                    # # if not, then I need to mine ice
                    [light_todo.append("ice") for _ in range(ice_miners)]

                # if enemy is growing lichen nearby, attack it
                dibbed = list(self.light_mining_dibs.values())
                dibbed.extend(list(self.heavy_mining_dibs.values()))
                opp_lichen_tile = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.obs)
                light_unit = [u for uid, u in self.my_units.items() if u.unit_type == "LIGHT"]
                light_unit = light_unit[0] if len(light_unit) > 0 else None
                if light_unit is not None and opp_lichen_tile is not None:
                    q_builder = QueueBuilder(self, light_unit, factory, self.obs)
                    path_to_lichen = q_builder.get_path_positions(light_unit.pos, opp_lichen_tile)
                    cost_to_lichen = q_builder.get_path_cost(path_to_lichen)
                    if opp_lichen_tile is not None and cost_to_lichen <= 40:
                        [light_todo.append("lichen") for _ in range(4)]

                # if I have enough water to grow lichen, do I have enough ore to build bots?
                if factory.cargo.metal < 100 and number_of_ore >= 1:
                    ore_miners = number_of_ore - 1 if number_of_ore - 1 <= 3 else 3
                    # # if not, then I need to mine ore
                    [light_todo.append("ore") for _ in range(ore_miners)]

            # if I have enough ore to build bots, does someone else need my help?
            # # if so, then I need to go help them

            # HEAVIES
            # if it's early in the game, and I'm safe on water, do I have enough ore to build bots?
            if self.step < 200 and factory.cargo.water > 100 and number_of_ore > 0:
                # # then I need to mine ore
                heavy_todo.append("ore")

            # do I have enough water to grow lichen?
            if factory.cargo.water < 2000 and number_of_ice > 0:
                # # if not, then I need to mine ice
                heavy_todo.append("ice")

            # are you dangerously low on water?
            if factory.cargo.water < 150 and number_of_ice > 1:
                emergency_ice_miners = number_of_ice - 1 if number_of_ice - 1 <= 3 else 3
                [heavy_todo.append("ice") for _ in range(emergency_ice_miners)]

            # if I don't need to excavate, do I have enough ore to build bots?
            if factory.cargo.metal < 100 and number_of_ore > 0 and self.step < 750:
                # # if not, then I need to mine ore
                heavy_todo.append("ore")

            if self.step >= 100:
                dibbed = list(self.heavy_mining_dibs.values())
                dibbed.extend(list(self.light_mining_dibs.values()))
                closest_lichen = closest_opp_lichen(self.opp_strains, factory.pos, dibbed, self.obs)
                if closest_lichen is not None:
                    # if so, then I need to attack
                    [heavy_todo.append("lichen") for _ in range(2)]

            # if I have enough water to grow lichen, am I super clogged up with rubble?
            surrounded, free_spaces = lichen_surrounded(self.obs, factory.strain_id, self.opp_strains, self.occupied_next, 10)
            if surrounded:
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

            print(f"Step {self.step}: {fid} light tasks being done: {light_tasks_being_done}", file=sys.stderr)
            print(f"Step {self.step}: {fid} heavy tasks being done: {heavy_tasks_being_done}", file=sys.stderr)
            print(f"Step {self.step}: {fid} light tasks needed: {light_tasks_needed}", file=sys.stderr)
            print(f"Step {self.step}: {fid} heavy tasks needed: {heavy_tasks_needed}", file=sys.stderr)
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
        if factory.cargo.water > 50 and game_state.real_env_steps <= 100:
            if factory.cargo.water >= 120:
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
                return
            if factory.cargo.water > 200:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 100 and game_state.real_env_steps % 3 != 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
            if factory.cargo.water > 50 and game_state.real_env_steps % 2 == 0:
                queue = factory.water()
                self.update_queues(factory, queue)
                return
        elif 750 <= game_state.real_env_steps < 980:
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
        off_limits = deepcopy(self.my_factory_tiles)
        dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
        if light:
            light_dibbed_tiles = [pos for pos in self.light_mining_dibs.values()]
            dibbed_tiles.extend(light_dibbed_tiles)

        primary_zone = get_orthogonal_positions(task_factory.pos, 1, off_limits, self.obs)
        zone_cost = get_total_rubble(self.obs, primary_zone)
        if zone_cost > 0:
            lowest_rubble_pos = get_position_with_lowest_rubble(primary_zone, dibbed_tiles, self.obs, task_factory)
            if lowest_rubble_pos is None:
                lowest_rubble_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
            queue = q_builder.build_mining_queue(resource, rubble_tile=lowest_rubble_pos)
        else:
            off_limits_or_dibbed = deepcopy(list(self.occupied_next))
            off_limits_or_dibbed.extend(dibbed_tiles)
            positions_to_clear = next_positions_to_clear(self.obs, task_factory.strain_id,
                                                         self.opp_strains,
                                                         off_limits=off_limits_or_dibbed)
            if len(positions_to_clear) > 0:
                lowest_rubble_pos = get_position_with_lowest_rubble(positions_to_clear, dibbed_tiles, self.obs, task_factory)
                if lowest_rubble_pos is None:
                    lowest_rubble_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
                queue = q_builder.build_mining_queue(resource, rubble_tile=lowest_rubble_pos)
            else:
                close_rubble_tile = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
                queue = q_builder.build_mining_queue(resource, rubble_tile=close_rubble_tile)

        return queue

    def trailblazing_task_assignment(self, q_builder, _task, task_factory, light=False):
        dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
        if light:
            light_dibbed_tiles = [pos for pos in self.light_mining_dibs.values()]
            dibbed_tiles.extend(light_dibbed_tiles)
        if _task == "ore path":
            path_to_ore = self.ore_paths[task_factory.unit_id]
            closest_path_pos = closest_rubble_tile_in_group(task_factory.pos, dibbed_tiles, path_to_ore, self.obs)
            if closest_path_pos is None:
                closest_path_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
            queue = q_builder.build_mining_queue("ore path", rubble_tile=closest_path_pos)
            return queue
        else:  # it's a clearing path
            path_to_clear = self.clearing_paths[task_factory.unit_id]
            closest_path_pos = closest_rubble_tile_in_group(task_factory.pos, dibbed_tiles, path_to_clear, self.obs)
            if closest_path_pos is None:
                closest_path_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
            queue = q_builder.build_mining_queue("clearing path", rubble_tile=closest_path_pos)
            return queue

    def mining_decision(self, task_factory, q_builder, light=False):
        last_recursion_try = False
        if light:
            factory_needs = self.factory_needs_light
            tasks_being_done = self.factory_tasks_light
        else:
            factory_needs = self.factory_needs_heavy
            tasks_being_done = self.factory_tasks_heavy

        q_builder.clear_mining_dibs()
        q_builder.clear_lichen_dibs()
        q_builder.clear_previous_task()

        # for now, just mine factory_needs in order, but this will be more complex later
        if task_factory.unit_id in factory_needs.keys():
            _task = factory_needs[task_factory.unit_id][0]
            self.pop_factory_needs(task_factory, light=light)
        else:
            print(f"Step {self.step}: No light needs for factory {task_factory.unit_id}",
                  file=sys.stderr)
            # check the other factories in order of nearness to this one and see if they need help
            # if they do, switch to helping them
            factory_in_need = self.find_closest_factory_with_needs(task_factory, factory_needs)
            if factory_in_need is not None:
                print(f"Step {self.step}: Switching to helping factory {factory_in_need.unit_id}", file=sys.stderr)
                # make factory_in_need the new task_factory
                task_factory = factory_in_need

                # if the factory in need has less than 3 workers, do the first task available
                # the idea is that the first 2 tasks are usually vital to the factory's survival
                # we have to make sure those are getting done
                if len(tasks_being_done[task_factory.unit_id]) < 3:
                    _task = factory_needs[task_factory.unit_id][0]
                    self.pop_factory_needs(task_factory, light=light)

                # if the factory in need has more than 2 workers, do the last task available
                else:
                    _task = factory_needs[task_factory.unit_id][-1]
                    self.pop_factory_needs(task_factory, light=light, reverse=True)

            else:
                # if there are no factories in need, attack
                _task = "lichen"
                last_recursion_try = True

        resources = ["rubble", "ice", "ore"]
        pathing = ["ore path", "clearing path"]
        helping = ["helper"]
        attacking = ["lichen", "aggro"]
        first_task_word = _task.split(":")[0]

        # Mining tasks
        if first_task_word in resources:
            resource = _task
            if resource == "rubble":
                queue = self.rubble_digging_task_assignment(q_builder, resource, task_factory, light=light)
            else:  # it's a resource
                queue = q_builder.build_mining_queue(resource)

            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light)

            return queue

        # Pathing tasks
        elif first_task_word in pathing:
            queue = self.trailblazing_task_assignment(q_builder, _task, task_factory, light=light)
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light)
            return queue

        elif first_task_word in helping:
            homer_id = _task.split(":")[1]  # for a helper queue this is the homer_id
            homer = self.my_units[homer_id]
            queue = q_builder.build_helper_queue(homer)
            if queue is None:
                queue = self.mining_decision(task_factory, q_builder, light=light)
            if queue is not None:
                return queue

        # Attacking tasks
        dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
        dibbed_tiles.extend([pos for pos in self.light_mining_dibs.values()])
        lichen_tile = closest_opp_lichen(self.opp_strains, q_builder.unit.pos, dibbed_tiles, self.obs, priority=True)
        if lichen_tile is not None:
            # queue = q_builder.build_mining_queue("lichen", lichen_tile=lichen_tile)
            queue = q_builder.build_attack_queue()
            if queue is None and not last_recursion_try:
                queue = self.mining_decision(task_factory, q_builder, light=light)

            return queue if queue is not None else None

        # if you made it here, you couldn't find a lichen tile
        print(
            f"Step {self.step}: Factory {task_factory.unit_id} _task {_task} is not in resources, and can't find qeueu",
            file=sys.stderr)
        return None

    def decision_tree(self, unit, factories, opp_units, obs):
        if unit.unit_type == "LIGHT":
            is_light = True
            factory_tasks = self.factory_tasks_light
        else:
            is_light = False
            factory_tasks = self.factory_tasks_heavy

        if unit.unit_id not in self.unit_states.keys():
            self.unit_states[unit.unit_id] = "idle"

        self.avoid_collisions(unit)  # make sure you aren't going to collide with a friendly unit

        # make sure you aren't about to dig up the rubble you just made by clearing opp lichen
        if len(self.action_queue[unit.unit_id]) > 0 and self.unit_states[unit.unit_id] == "attacking":
            self.check_valid_dig(unit, self.obs)

        task_factory = get_closest_factory(factories, unit.pos)
        # if you are the closest heavy to a given factory, you need to be doing tasks for that factory
        for fid, f in factories.items():
            heavy_tiles = [u.pos for u in self.my_heavy_units]
            closest_heavy = closest_tile_in_group(f.pos, [], heavy_tiles)
            if closest_heavy is not None and on_tile(unit.pos, closest_heavy):
                task_factory = f
        for factory_id, unit_tasks in factory_tasks.items():
            if unit.unit_id in unit_tasks.keys() and factory_id in factories.keys():
                task_factory = factories[factory_id]
        q_builder = QueueBuilder(self, unit, task_factory, obs)

        # make sure closest factory is not about to run dry, save it if you have ice
        closest_factory = get_closest_factory(factories, unit.pos)
        transferable, transfer_direction = q_builder.transfer_ready(home_pref=False)
        not_already_transfering = self.unit_states[unit.unit_id] != "transfering"
        if closest_factory.cargo.water < 50 and transferable and not_already_transfering:
            self.unit_states[unit.unit_id] = "transfering"
            queue, cost = q_builder.get_transfer_queue(transfer_direction, home_pref=False)
            self.update_queues(unit, queue)

        # Check for evasions now that we have come up with our final queue and any interrupts
        evasion_queue = evasion_check(self, unit, task_factory, opp_units, obs)
        if evasion_queue is not None:
            self.update_queues(unit, evasion_queue)
            return

        state = self.unit_states[unit.unit_id]

        need_recharge = False
        if state != "attacking":
            # for the love of god, just complete your attack run and worry about optimal recharging later
            need_recharge = q_builder.check_need_recharge()

        if need_recharge and state != "recharging" and state != "low battery":
            q_builder.clear_mining_dibs()
            q_builder.clear_lichen_dibs()
            queue = q_builder.build_recharge_queue()
            self.update_queues(unit, queue)
        else:  # if you don't need to recharge

            # if you don't have a queue, build one
            if len(self.action_queue[unit.unit_id]) == 0 or state == "waiting":
                queue = self.mining_decision(task_factory, q_builder, light=is_light)  # try to get a new queue

                # if you were waiting before and you're waiting now, and you have a queue, just keep waiting
                was_waiting = state == "waiting"  # was waiting to start the step
                is_waiting = self.unit_states[unit.unit_id] == "waiting"  # still waiting after decision tree
                has_a_queue = len(self.action_queue[unit.unit_id]) > 0 and self.action_queue[unit.unit_id] != []
                if was_waiting and is_waiting and has_a_queue:
                    # you were already waiting and nothing new came up
                    # so just continue waiting and add your next pos to occupied_next
                    self.add_nextpos_to_occnext(unit)
                    return

                if queue is None:
                    # Got through the whole decision tree and couldn't find a queue
                    queue = q_builder.build_waiting_queue()
                    if queue is None:
                        return

                # update the action queue, this adds new_pos to occupied_next
                self.update_queues(unit, queue)
            else:
                # if you have a queue, add the next position to occupied_next
                self.add_nextpos_to_occnext(unit)

    def act(self, step: int, obs, remainingOverageTime: int = 60):
        # initial step setup, these are the basic vars that we need to have
        self.obs = obs
        game_state = obs_to_game_state(step, self.env_cfg, obs)
        factories = game_state.factories[self.player]
        opp_factories = game_state.factories[self.opp_player]
        all_factories = factories | opp_factories
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

        # Factory updates
        self.factory_types = dict()  # Clear out the factory types from last step
        self.factory_needs = dict()  # Clear out the factory needs from last step

        print(f"Step: {self.step}: timing", file=sys.stderr)
        # if self.step == 200:
        #     print('halt')

        # Set opp_strains and paths
        if self.step == 2:
            self.set_ore_paths()
            self.set_clearing_paths()
            for fid, factory in opp_factories.items():
                self.opp_strains.append(factory.strain_id)
            for fid,factory in factories.items():
                self.my_strains.append(factory.strain_id)

        if self.step >= 2:
            self.set_ore_path_costs()
            self.set_clearing_path_costs()

        for fid, factory in factories.items():
            # Update the factory's resources, these are the resources which the factory should have control over
            ice_map, ore_map = obs["board"]["ice"], obs["board"]["ore"]
            fact_ice, fact_ore = nearby_resources(factory.pos, ice_map, ore_map, all_factories)
            self.factory_resources[fid] = [fact_ice, fact_ore]

            # then update the factory's type
            self.set_factory_type(factory)

            # then update the factory's needs
            self.define_factory_needs(factory)

        # Unit Actions
        heavies, lights = self.split_heavies_and_lights(units)

        # first get actions for heavies that were previously mining adjacent
        for unit in heavies:
            if unit.unit_id in self.unit_states.keys():
                state = self.unit_states[unit.unit_id]
                if state == "mining adjacent":
                    self.decision_tree(unit, factories, opp_units, obs)
                    self.mining_adjacent.add(unit.unit_id)

        # then get actions for heavies that weren't mining adjacent
        for unit in heavies:
            if unit.unit_id not in self.mining_adjacent:
                self.decision_tree(unit, factories, opp_units, obs)

        # first get actions for lights that were previously helping
        for unit in lights:
            if unit.unit_id in self.unit_states.keys():
                state = self.unit_states[unit.unit_id]
                if state == "helping":
                    self.decision_tree(unit, factories, opp_units, obs)
                    self.helper_treated.add(unit.unit_id)

        # then get actions for lights that weren't helping
        for unit in lights:
            if unit.unit_id not in self.helper_treated:
                self.decision_tree(unit, factories, opp_units, obs)

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
                    total_units = len(units)
                    total_factories = len(factories)
                    need_basic_lights = total_units < total_factories * 10
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
        return finalized_actions
