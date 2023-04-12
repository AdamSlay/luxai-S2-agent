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
        self.factory_states = dict()  # fid: "state"

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
            if state == "low_battery":
                if uid in self.my_units.keys():
                    unit = self.my_units[uid]
                    self.occupied_next.add(unit.pos)

    def avoid_collisions(self, unit):
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

                # if the next position is already occupied, clear the action queue
                if new_pos in self.occupied_next:
                    print(f'Step {self.step}: Unit {unit.unit_id} is moving to occupied tile!', file=sys.stderr)
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
                x, y = new_pos[0], new_pos[1]
                self.occupied_next.add((x, y))
            else:
                x, y = unit.pos[0], unit.pos[1]
                self.occupied_next.add((x, y))
        elif not queue:
            x, y = unit.pos[0], unit.pos[1]
            self.occupied_next.add((x, y))
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

    def act(self, step: int, obs, remainingOverageTime: int = 60):
        # initial step setup, these are the basic vars that we need to have
        game_state = obs_to_game_state(step, self.env_cfg, obs)
        factories = game_state.factories[self.player]
        opp_factories = game_state.factories[self.opp_player]
        all_factories = factories | opp_factories
        units = game_state.units[self.player]
        opp_units = game_state.units[self.opp_player]

        # global vars
        self.step += 1
        self.my_units = units
        self.opp_units = opp_units
        self.my_factories = factories
        self.opp_factories = opp_factories

        # functions that need to be called on each step, mainly to clean the slate from the last step
        self.new_queue = dict()  # Clear out the new queue from last step
        self.pop_action_queue()  # Then update the persistent action queue
        self.update_occupied_next()  # Update the occupied_next set,

        # Factory Actions
        for fid, factory in factories.items():
            # Update the factory's resources, these are the resources which the factory should have control over
            ice_map, ore_map = obs["board"]["ice"], obs["board"]["ore"]
            fact_ice, fact_ore = nearby_resources(factory.pos, ice_map, ore_map, all_factories)
            self.factory_resources[fid] = [fact_ice, fact_ore]
            print(f"Factory {fid} has {fact_ice} ice and {fact_ore} ore", file=sys.stderr)

            # For now, just build a HEAVY unit if you can, soon this will go in self.factory_construct or similar
            # I'm thinking these will be the factory functions: factory_construct, factory_water, factory_state
            if factory.can_build_heavy(game_state):
                queue = factory.build_heavy()
                self.update_queues(factory, queue)

        # Unit Actions
        heavies, lights = self.split_heavies_and_lights(units)
        for unit in heavies:
            self.avoid_collisions(unit)  # make sure you aren't going to collide with a friendly unit

            # if you don't have a queue, build one
            if len(self.action_queue[unit.unit_id]) == 0:
                # find your closest factory
                closest_factory = get_closest_factory(factories, unit.pos)
                # for now, just mine ice
                q_builder = QueueBuilder(self, unit, closest_factory, obs)
                resource = "ice"
                queue = q_builder.build_mining_queue(resource)
                # update the action queue, this adds new_pos to occupied_next
                self.update_queues(unit, queue)
            else:
                # if you have a queue, add the next position to occupied_next
                self.add_nextpos_to_occnext(unit)

        # Finalize the action queue and submit it
        finalized_actions = self.finalize_new_queue()
        return finalized_actions
