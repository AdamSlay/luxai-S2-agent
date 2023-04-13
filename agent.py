# import numpy as np
# import sys

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
        self.factory_needs = dict()  # fid: [ice, ore]

        # dibs
        self.light_mining_dibs = dict()
        self.heavy_mining_dibs = dict()

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
                    print(f'Step {self.step}: Unit {unit.unit_id} is moving to occupied tile! {new_pos}', file=sys.stderr)
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
        self.action_queue[unit.unit_id] = queue
        self.new_queue[unit.unit_id] = queue
        self.add_nextpos_to_occnext(unit)

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

            # TODO: this check should go in some sort of unit setup function
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
        needs = []
        fid = factory.unit_id
        number_of_ice = self.factory_resources[fid][0]
        number_of_ore = self.factory_resources[fid][1]
        factory_type = self.factory_types[fid]

        if factory_type == "ice mine":
            [needs.append("ice") for _ in range(number_of_ice)]
        elif factory_type == "ore mine":
            [needs.append("ore") for _ in range(number_of_ore)]
        elif factory_type == "balanced":
            [needs.append("ice") for _ in range(number_of_ice)]
            [needs.append("ore") for _ in range(number_of_ore)]
        elif factory_type == "ice pref":
            [needs.append("ice") for _ in range(number_of_ice)]
            [needs.append("ore") for _ in range(number_of_ore)]
        elif factory_type == "ore pref":
            [needs.append("ore") for _ in range(number_of_ore)]
            [needs.append("ice") for _ in range(number_of_ice)]
        elif factory_type == "rich":
            [needs.append("ice") for _ in range(number_of_ice)]
            [needs.append("ore") for _ in range(number_of_ore)]
        elif factory_type == "resourceless":
            needs.append("ice")

        self.factory_needs[fid] = needs

    def pop_factory_needs(self, factory):
        fid = factory.unit_id
        if len(self.factory_needs[fid]) > 1:
            self.factory_needs[fid] = self.factory_needs[fid][1:]
        else:
            del self.factory_needs[fid]

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

        print(f"Step {self.step}: heavy dibs: {len(self.heavy_mining_dibs)}\n light dibs: {len(self.light_mining_dibs)}", file=sys.stderr)
        print(f"Step {self.step}: occ_next: {len(self.occupied_next)} \n"
              f"my_fact_tiles: {len(self.my_factory_tiles)} \n"
              f"opp_factory_tiles: {len(self.opp_factory_tiles)} \n"
              f"factory_centers: {len(self.my_factory_centers)} \n"
              f"factory_resources: {len(self.factory_resources)} \n"
              f"unit_states: {len(self.unit_states)} \n"
              f"factory_types: {len(self.factory_types)} \n"
              f"factory_states: {len(self.factory_states)} \n"
              f"factory_needs: {len(self.factory_needs)} \n", file=sys.stderr)

        # Occasional Factory updates
        if self.step % 10 == 0:
            self.factory_types = dict()  # Clear out the factory types from last step
            self.factory_needs = dict()  # Clear out the factory needs from last step
            for fid, factory in factories.items():
                # Update the factory's resources, these are the resources which the factory should have control over
                ice_map, ore_map = obs["board"]["ice"], obs["board"]["ore"]
                fact_ice, fact_ore = nearby_resources(factory.pos, ice_map, ore_map, all_factories)
                self.factory_resources[fid] = [fact_ice, fact_ore]
                print(f"Step {self.step}: Factory {fid} has {fact_ice} ice and {fact_ore} ore", file=sys.stderr)

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

            need_recharge = q_builder.check_need_recharge()
            state = self.unit_states[unit.unit_id]
            if need_recharge and state != "recharging" and state != "low battery":
                print(f"Step {self.step}: Unit {unit.unit_id} needs to recharge, was {self.unit_states[unit.unit_id]}", file=sys.stderr)
                q_builder.clear_mining_dibs()
                queue = q_builder.build_recharge_queue()
                self.update_queues(unit, queue)

            else:  # if you don't need to recharge
                # if you don't have a queue, build one
                if len(self.action_queue[unit.unit_id]) == 0:
                    q_builder.clear_mining_dibs()
                    # find your closest factory and initialize a QueueBuilder

                    # for now, just mine factory_needs in order, but this will be more complex later
                    if closest_factory.unit_id in self.factory_needs:
                        resource = self.factory_needs[closest_factory.unit_id][0]
                        self.pop_factory_needs(closest_factory)
                    else:
                        resource = "ice"

                    if closest_factory.cargo.water > 500:
                        resource = "ore"

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
            closest_factory = get_closest_factory(factories, unit.pos)
            q_builder = QueueBuilder(self, unit, closest_factory, obs)

            need_recharge = q_builder.check_need_recharge()
            state = self.unit_states[unit.unit_id]
            if need_recharge and state != "recharging" and state != "low battery":
                print(f"Step {self.step}: Unit {unit.unit_id} needs to recharge, was {self.unit_states[unit.unit_id]}", file=sys.stderr)
                q_builder.clear_mining_dibs()
                queue = q_builder.build_recharge_queue()
                self.update_queues(unit, queue)
            else:  # if you don't need to recharge
                # if you don't have a queue, build one
                if len(self.action_queue[unit.unit_id]) == 0:
                    q_builder.clear_mining_dibs()

                    # for now, just mine factory_needs in order, but this will be more complex later
                    if closest_factory.unit_id in self.factory_needs:
                        resource = self.factory_needs[closest_factory.unit_id][0]
                        self.pop_factory_needs(closest_factory)
                    else:
                        resource = "ice"

                    queue = q_builder.build_mining_queue(resource)
                    if queue is None:
                        queue = q_builder.build_recharge_queue()

                    # update the action queue, this adds new_pos to occupied_next
                    self.update_queues(unit, queue)
                else:
                    # if you have a queue, add the next position to occupied_next
                    self.add_nextpos_to_occnext(unit)

        for fid, factory in factories.items():
            # For now, just build a HEAVY unit if you can, soon this will go in self.factory_construct or similar
            # I'm thinking these will be the factory functions: factory_construct, factory_water, factory_state
            f_pos = (factory.pos[0], factory.pos[1])
            if f_pos not in self.occupied_next:
                if factory.can_build_heavy(game_state) and factory.unit_id in self.factory_needs:
                    queue = factory.build_heavy()
                    self.update_queues(factory, queue)

                elif factory.can_build_light(game_state) and factory.unit_id in self.factory_needs:
                    queue = factory.build_light()
                    self.update_queues(factory, queue)

        # Finalize the action queue and submit it
        finalized_actions = self.finalize_new_queue()
        return finalized_actions
