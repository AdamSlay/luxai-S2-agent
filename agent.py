# import numpy as np
# import sys

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
        self.light_mining_dibs = dict()
        self.heavy_mining_dibs = dict()

        # reserve power
        self.moderate_reserve_power = {"LIGHT": 15, "HEAVY": 150}
        self.low_reserve_power = {"LIGHT": 10, "HEAVY": 100}

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
                        new_actions[unit_id] = []
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
            # do I have room to grow lichen?
            needs_excavation = lichen_surrounded(self.obs, factory.strain_id, self.opp_strains, self.occupied_next, 10)
            primary_zone = get_orthogonal_positions(factory.pos, 1, self.my_factory_tiles, self.obs)
            zone_cost = get_total_rubble(self.obs, primary_zone)
            if needs_excavation or zone_cost > 0:
                # # if not, then I need to excavate either a clearing path or immediate zone
                [light_todo.append("rubble") for _ in range(4)]

            # if I have room to grow lichen, do I have enough water to grow lichen?
            if factory.cargo.water < 200 and number_of_ice >= 2:
                ice_miners = number_of_ice - 1 if number_of_ice - 1 <= 3 else 3
                # # if not, then I need to mine ice
                [light_todo.append("ice") for _ in range(ice_miners)]

            # if I have enough water to grow lichen, do I have enough ore to build bots?
            if factory.cargo.metal < 100 and number_of_ore >= 1:
                ore_miners = number_of_ore - 1 if number_of_ore - 1 <= 3 else 3
                # # if not, then I need to mine ore
                [light_todo.append("ore") for _ in range(ore_miners)]

            # if I have enough ore to build bots, does someone else need my help?
            # # if so, then I need to go help them

            # HEAVIES
            # if it's early in the game, and I'm safe on water, do I have enough ore to build bots?
            if self.step < 300 and factory.cargo.water < 100 and number_of_ore > 0:
                # # then I need to mine ore
                heavy_todo.append("ore")

            # do I have enough water to grow lichen?
            if factory.cargo.water < 1000 and number_of_ice > 0:
                # # if not, then I need to mine ice
                heavy_todo.append("ice")

            # if I have enough water to grow lichen, am I super clogged up with rubble?
            if lichen_surrounded(self.obs, factory.strain_id, self.opp_strains, self.occupied_next, 10):
                # # if so, then I need to excavate either a clearing path or immediate zone
                heavy_todo.append("rubble")

            # if I don't need to excavate, do I have enough ore to build bots?
            if factory.cargo.metal < 100 and number_of_ore > 0:
                # # if not, then I need to mine ore
                heavy_todo.append("ore")

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
            print(f"Step {self.step}: {fid} light tasks needed: {light_tasks_needed}", file=sys.stderr)
            if light_tasks_needed:
                self.factory_needs_light[fid] = light_tasks_needed
            elif not light_tasks_needed:
                del self.factory_needs_light[fid]
            if heavy_tasks_needed:
                self.factory_needs_heavy[fid] = heavy_tasks_needed
            elif not heavy_tasks_needed:
                del self.factory_needs_heavy[fid]

    # TODO: This is actually for setting up the queue for a unit that needs to clear rubble
    # if lichen_surrounded(self.obs, factory.strain_id, self.opp_strains, self.occupied_next, 10):
    #     positions_to_clear = next_positions_to_clear(self.obs, factory.strain_id, self.opp_strains,
    #                                                  self.occupied_next)
    #     if len(positions_to_clear) > 0:
    #         lowest_rubble_pos = get_position_with_lowest_rubble(positions_to_clear, self.obs)
    # TODO: end of rubble clearing code
    def pop_factory_needs(self, factory, light=True):
        fid = factory.unit_id
        if light:
            factory_needs = self.factory_needs_light
        else:
            factory_needs = self.factory_needs_heavy
        if len(factory_needs[fid]) > 1:

            factory_needs[fid] = factory_needs[fid][1:]
        else:
            del factory_needs[fid]

    def light_mining_decision(self, task_factory, q_builder):
        q_builder.clear_mining_dibs()
        q_builder.clear_previous_task()

        # for now, just mine factory_needs in order, but this will be more complex later
        if task_factory.unit_id in self.factory_needs_light.keys():
            resource = self.factory_needs_light[task_factory.unit_id][0]
            self.pop_factory_needs(task_factory, light=True)
        else:
            print(f"Step {self.step}: No light needs for factory {task_factory.unit_id}",
                  file=sys.stderr)
            resource = "lichen"

        if resource == "rubble":
            off_limits = deepcopy(self.my_factory_tiles)
            dibbed_tiles = [pos for pos in self.light_mining_dibs.values()]
            heavy_dibbed_tiles = [pos for pos in self.heavy_mining_dibs.values()]
            dibbed_tiles.extend(heavy_dibbed_tiles)

            primary_zone = get_orthogonal_positions(task_factory.pos, 1, off_limits, self.obs)
            zone_cost = get_total_rubble(self.obs, primary_zone)
            if zone_cost > 0:
                lowest_rubble_pos = get_position_with_lowest_rubble(primary_zone, dibbed_tiles, self.obs)
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
                    # TODO: currently this doesn't take into account mining dibs
                    lowest_rubble_pos = get_position_with_lowest_rubble(positions_to_clear, dibbed_tiles, self.obs)
                    if lowest_rubble_pos is None:
                        lowest_rubble_pos = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
                    queue = q_builder.build_mining_queue(resource, rubble_tile=lowest_rubble_pos)
                else:
                    close_rubble_tile = closest_rubble_tile(task_factory.pos, dibbed_tiles, self.obs)
                    queue = q_builder.build_mining_queue(resource, rubble_tile=close_rubble_tile)
        elif resource == "lichen":
            queue = None
        else:
            queue = q_builder.build_mining_queue(resource)
        return queue

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

        # Factory updates
        self.factory_types = dict()  # Clear out the factory types from last step
        self.factory_needs = dict()  # Clear out the factory needs from last step

        print(f"Step: {self.step}: timing", file=sys.stderr)
        # if self.step == 100:
        #     print('f')

        # Set opp_strains
        if self.step == 2:
            for fid, factory in opp_factories.items():
                self.opp_strains.append(factory.strain_id)
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
        for unit in heavies:
            if unit.unit_id not in self.unit_states.keys():
                self.unit_states[unit.unit_id] = "idle"
            self.avoid_collisions(unit)  # make sure you aren't going to collide with a friendly unit

            closest_factory = get_closest_factory(factories, unit.pos)
            q_builder = QueueBuilder(self, unit, closest_factory, obs)

            # Check for evasions now that we have come up with our final queue and any interrupts
            evasion_queue = evasion_check(self, unit, closest_factory, opp_units, obs)
            if evasion_queue is not None:
                self.update_queues(unit, evasion_queue)
                continue

            need_recharge = q_builder.check_need_recharge()
            state = self.unit_states[unit.unit_id]
            if need_recharge and state != "recharging" and state != "low battery":
                q_builder.clear_mining_dibs()
                queue = q_builder.build_recharge_queue()
                self.update_queues(unit, queue)

            else:  # if you don't need to recharge
                # if you don't have a queue, build one
                if len(self.action_queue[unit.unit_id]) == 0:
                    q_builder.clear_mining_dibs()
                    # find your closest factory and initialize a QueueBuilder

                    # for now, just mine factory_needs in order, but this will be more complex later
                    if closest_factory.unit_id in self.factory_needs_heavy.keys():
                        resource = self.factory_needs_heavy[closest_factory.unit_id][0]
                        self.pop_factory_needs(closest_factory, light=False)
                    else:
                        resource = "ice"

                    # this was just to get more ore for testing
                    # if closest_factory.cargo.water > 500:
                    #     resource = "ore"

                    queue = q_builder.build_mining_queue(resource)
                    if queue is None:
                        queue = q_builder.build_recharge_queue()

                    # update the action queue, this adds new_pos to occupied_next
                    self.update_queues(unit, queue)
                else:
                    # if you have a queue, add the next position to occupied_next
                    self.add_nextpos_to_occnext(unit)

        for unit in lights:
            if unit.unit_id not in self.unit_states.keys():
                self.unit_states[unit.unit_id] = "idle"

            self.avoid_collisions(unit)  # make sure you aren't going to collide with a friendly unit

            task_factory = get_closest_factory(factories, unit.pos)
            for factory_id, units in self.factory_tasks_light.items():
                if unit.unit_id in units and factory_id in factories.keys():
                    task_factory = factories[factory_id]
            q_builder = QueueBuilder(self, unit, task_factory, obs)

            # Check for evasions now that we have come up with our final queue and any interrupts
            evasion_queue = evasion_check(self, unit, task_factory, opp_units, obs)
            if evasion_queue is not None:
                self.update_queues(unit, evasion_queue)
                continue

            need_recharge = q_builder.check_need_recharge()
            state = self.unit_states[unit.unit_id]

            if need_recharge and state != "recharging" and state != "low battery":
                q_builder.clear_mining_dibs()
                queue = q_builder.build_recharge_queue()
                self.update_queues(unit, queue)
            else:  # if you don't need to recharge
                # if you don't have a queue, build one
                if len(self.action_queue[unit.unit_id]) == 0:
                    queue = self.light_mining_decision(task_factory, q_builder)

                    if queue is None:
                        print(f"Step {self.step}: {unit.unit_id} has no queue, building recharge queue", file=sys.stderr)
                        # This would be attack or something
                        queue = q_builder.build_recharge_queue()

                    # update the action queue, this adds new_pos to occupied_next
                    self.update_queues(unit, queue)
                else:
                    # if you have a queue, add the next position to occupied_next
                    self.add_nextpos_to_occnext(unit)

        # FACTORIES
        for fid, factory in factories.items():
            # For now, just build a HEAVY unit if you can, soon this will go in self.factory_construct or similar
            # I'm thinking these will be the factory functions: factory_construct, factory_water, factory_state
            f_pos = (factory.pos[0], factory.pos[1])
            if f_pos not in self.occupied_next:
                if factory.can_build_heavy(game_state) and factory.unit_id in self.factory_needs_heavy:
                    queue = factory.build_heavy()
                    self.update_queues(factory, queue)
                    continue

                elif factory.can_build_light(game_state) and factory.unit_id in self.factory_needs_light:
                    queue = factory.build_light()
                    self.update_queues(factory, queue)
                    continue
            # WATER
            if factory.cargo.water > 50 and game_state.real_env_steps <= 100:
                if factory.cargo.water >= 120:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                elif game_state.real_env_steps % 3 != 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
            if factory.cargo.water > 50 and 100 < game_state.real_env_steps < 750:
                power = factory.power
                if power > 5000:
                    continue
                if factory.cargo.water > 200:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                if factory.cargo.water > 100 and game_state.real_env_steps % 3 != 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                if factory.cargo.water > 50 and game_state.real_env_steps % 2 == 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
            elif 750 <= game_state.real_env_steps < 980:
                steps_remaining = 1000 - game_state.real_env_steps
                if factory.cargo.water > steps_remaining * 6:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                if factory.cargo.water > 400:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                if factory.cargo.water > 200 and game_state.real_env_steps % 3 != 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
                elif factory.cargo.water > 50 and game_state.real_env_steps % 2 == 0:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
            elif 980 <= game_state.real_env_steps < 996:
                if factory.cargo.water > 50:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue
            elif game_state.real_env_steps >= 996:
                if factory.cargo.water > 30:
                    queue = factory.water()
                    self.update_queues(factory, queue)
                    continue

        # Finalize the action queue and submit it
        finalized_actions = self.finalize_new_queue()
        return finalized_actions
