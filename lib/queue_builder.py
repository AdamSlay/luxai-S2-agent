from math import floor

from lib.dijkstra import dijkstras_path
from lib.utils import *


class QueueBuilder:
    def __init__(self, agent, unit, target_factory, obs):
        self.agent = agent
        self.unit = unit
        self.target_factory = target_factory
        self.obs = deepcopy(obs)

    def build_mining_queue(self, resource: str) -> list or None:
        self.agent.unit_states[self.unit.unit_id] = "mining"
        self.clear_mining_dibs()
        queue = []
        transfer_ready, transfer_direction = self.transfer_ready(home_pref=True)
        if transfer_ready:
            transfer_queue = self.get_transfer_queue(transfer_direction)
            queue.extend(transfer_queue)

        dibs = self.get_dibs_class()
        dibs_tiles = [tile for uid, tile in dibs.items()]

        if self.unit.unit_type == "LIGHT":
            heavy_dibs = [tile for uid, tile in self.agent.heavy_mining_dibs.items()]
            dibs_tiles.extend(heavy_dibs)
        dibs_tiles = self.agent.occupied_next

        resource_tile = closest_resource_tile(resource, self.unit.pos, dibs_tiles, self.obs)
        if resource_tile is None:
            print(f"Unit {self.unit.unit_id} can't find a resource tile!", file=sys.stderr)
            # can't find a resource, return None and get back into the decision tree
            return None
        dibs[self.unit.unit_id] = resource_tile

        # path to and cost to resource
        path_to_resource = self.get_path_positions(self.unit.pos, resource_tile)
        cost_to_resource = self.get_path_cost(path_to_resource)

        # if we are already on the resource tile, make sure you can stay
        pos = (self.unit.pos[0], self.unit.pos[1])
        if len(path_to_resource) <= 1 and pos in self.agent.occupied_next:
            direction = move_toward(self.unit.pos, resource_tile, self.agent.occupied_next)
            queue = [self.unit.move(direction)]
            return queue

        # avoid heavies when looking for a return tile
        heavies = [unit for unit in self.agent.my_heavy_units if unit.unit_id != self.unit.unit_id]
        return_tile = closest_factory_tile(self.target_factory.pos, resource_tile, heavies)

        # path from and cost from resource
        path_from_resource = self.get_path_positions(resource_tile, return_tile)
        cost_from_resource = self.get_path_cost(path_from_resource)

        # total cost
        pathing_cost = cost_to_resource + cost_from_resource
        dig_allowance = 600 if self.unit.unit_type == "HEAVY" else 50

        if self.unit.power < pathing_cost + dig_allowance:
            print(
                f"Step {self.agent.step}: Unit {self.unit.unit_id} doesn't have enough power to get to resource and back!",
                file=sys.stderr)
            # not enough power to get there and back, return None and get back into the decision tree
            return self.build_recharge_queue()

        if len(path_to_resource) > 1:
            moves_to_resource = self.get_path_moves(path_to_resource)
            queue.extend(moves_to_resource)

        digs = self.get_number_of_digs(self.unit.power, pathing_cost)
        queue.append(self.unit.dig(n=digs))

        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_recharge_queue(self, occupied=None) -> list:
        # The point of occupied is to pass in opp_heavies or some such to avoid them in case you are super low or something
        self.agent.unit_states[self.unit.unit_id] = "recharging"
        self.clear_mining_dibs()
        queue = []

        heavies = [unit for unit in self.agent.my_heavy_units if unit.unit_id != self.unit.unit_id]
        return_tile = closest_factory_tile(self.target_factory.pos, self.unit.pos, heavies)
        pickup_amt = self.get_pickup_amt(self.target_factory)

        pos = (self.unit.pos[0], self.unit.pos[1])
        in_position = on_tile(self.unit.pos, return_tile)
        in_occupied = pos in self.agent.occupied_next
        if in_position and not in_occupied:
            # print(f"Step {self.agent.step}: Unit {self.unit.unit_id} is on the return tile and not occupied!",
            #       file=sys.stderr)
            queue = [self.unit.pickup(4, pickup_amt)]
            return queue
        elif in_position and in_occupied:
            # print(f"Step {self.agent.step}: Unit {self.unit.unit_id} is on the return tile and occupied!",
            #       file=sys.stderr)
            direction = move_toward(self.unit.pos, return_tile, self.agent.occupied_next)
            queue = [self.unit.move(direction)]
            return queue

        # get path home
        if occupied is not None:
            path_home = self.get_path_positions(self.unit.pos, return_tile, occupied)
        else:
            path_home = self.get_path_positions(self.unit.pos, return_tile)

        # wait for power if you don't have enough
        cost_home = self.get_path_cost(path_home)
        if self.unit.power < cost_home:
            cost_remaining = cost_home - self.unit.power
            queue = self.build_low_battery_queue(cost_remaining)
            return queue

        if len(path_home) > 1:
            moves = self.get_path_moves(path_home)
            queue.extend(moves)

        # pick up power once you get home
        queue.append(self.unit.pickup(4, pickup_amt))
        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_low_battery_queue(self, desired_power: int) -> list:
        print(f"Step {self.agent.step}: Unit {self.unit.unit_id} is low on power! Former state: {self.agent.unit_states[self.unit.unit_id]}", file=sys.stderr)
        self.agent.unit_states[self.unit.unit_id] = "low battery"
        self.clear_mining_dibs()
        queue = []
        if self.unit.unit_type == "HEAVY":
            solar_charge = 10
        else:
            solar_charge = 1

        step = deepcopy(self.agent.step)
        power_required = deepcopy(desired_power)

        while power_required > 0:
            queue.append(self.unit.move(0))
            if step % 50 < 30:
                power_required -= solar_charge
            step += 1

        queue = truncate_actions(queue)
        return queue

    def check_need_recharge(self) -> bool:
        if self.unit.unit_type == "HEAVY":
            reserve_power = 160
        else:
            reserve_power = 15

        closest_charge_tile = closest_factory_tile(self.target_factory.pos, self.unit.pos, self.agent.my_heavy_units)
        path_home = self.get_path_positions(self.unit.pos, closest_charge_tile)
        cost_home = self.get_path_cost(path_home)

        if self.unit.power <= cost_home + reserve_power:
            return True
        return False

    # UTILITIES
    def transfer_ready(self, home_pref=False, position=None) -> tuple:
        if position is None:
            position = self.unit.pos
        if home_pref:
            charge_factory = self.target_factory
        else:
            charge_factory = get_closest_factory(self.agent.my_factories, position)

        target_factory_tile = closest_factory_tile(charge_factory.pos, position, self.agent.my_heavy_units)
        pos = (position[0], position[1])

        if pos in self.agent.occupied_next:
            print(f"Step {self.agent.step}: {self.unit.unit_id} is trying to transfer but is blocked", file=sys.stderr)
            return False, 8

        if on_tile(position, target_factory_tile):
            if self.unit.cargo.ice > 0 or self.unit.cargo.ore > 0:
                return True, 0

        elif tile_adjacent(self.unit.pos, target_factory_tile):
            if self.unit.cargo.ice > 0 or self.unit.cargo.ore > 0:
                direction = direction_to(position, target_factory_tile)
                return True, direction
        return False, 8

    def get_transfer_queue(self, transfer_direction):
        queue = []
        if self.unit.unit_type == "LIGHT":
            if self.unit.cargo.ice > 0:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            if self.unit.cargo.ore > 0:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
        else:
            if self.unit.cargo.ice > 200 > self.target_factory.cargo.water:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            elif self.unit.cargo.ice > 900:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            if self.unit.cargo.ore > 0 and self.target_factory.cargo.metal < 100:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
            elif self.unit.cargo.ore > 500:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
        return queue

    def get_dibs_class(self):
        if self.unit.unit_type == "HEAVY":
            dibs_weight_class = self.agent.heavy_mining_dibs
        else:
            dibs_weight_class = self.agent.light_mining_dibs

        return dibs_weight_class

    def clear_mining_dibs(self):
        if self.unit.unit_type == "HEAVY":
            dibs_weight_class = self.agent.heavy_mining_dibs
        else:
            dibs_weight_class = self.agent.light_mining_dibs

        if self.unit.unit_id in dibs_weight_class.keys():
            del dibs_weight_class[self.unit.unit_id]

    def get_number_of_digs(self, power_remaining: int, cost_to_resource: int, tile_amt=None) -> int:
        if self.unit.unit_type == "HEAVY":
            reserve_power = 100
            dig_cost = 60
            dig_rate = 20
        else:
            reserve_power = 10
            dig_cost = 5
            dig_rate = 2
        number_of_digs = (power_remaining - cost_to_resource - reserve_power) // dig_cost
        if tile_amt is not None:
            if tile_amt % dig_rate == 0:
                ex_digs = (tile_amt // dig_rate)
            else:
                ex_digs = (tile_amt // dig_rate) + 1
            if number_of_digs > ex_digs:
                number_of_digs = ex_digs
        return number_of_digs

    def get_pickup_amt(self, charge_factory) -> int:
        if self.unit.unit_type == "LIGHT":
            if charge_factory.power > 150:
                pickup_amt = 150 - self.unit.power
            else:
                if 150 - self.unit.power > charge_factory.power - 50:
                    if charge_factory.power - 50 > 0:
                        pickup_amt = charge_factory.power - 50
                    else:
                        pickup_amt = 0
                else:
                    pickup_amt = 150 - self.unit.power
            if pickup_amt < 0:
                pickup_amt = 0
        else:
            if charge_factory.power <= 500:
                pickup_amt = charge_factory.power - 50
            elif charge_factory.power <= 1000:
                pickup_amt = charge_factory.power - 200
            elif charge_factory.power <= 2500:
                pickup_amt = charge_factory.power - 500
            elif charge_factory.power <= 3500:
                pickup_amt = charge_factory.power - 1000
            else:
                pickup_amt = 3000 - self.unit.power
        return pickup_amt

    def get_path_cost(self, path_positions: list) -> int:
        if self.unit.unit_type == "HEAVY":
            multiplier = 1
            move_cost = 20
        else:
            multiplier = 0.05
            move_cost = 1

        total_cost = 0
        rubble_map = self.obs["board"]["rubble"]
        for pos in path_positions:
            rubble_cost = floor(rubble_map[pos[0]][pos[1]] * multiplier)
            total_cost += (rubble_cost + move_cost)
        return total_cost

    def get_path_positions(self, start: np.ndarray, finish: np.ndarray, recharging=False, occupied=None) -> list:
        rubble_map = self.obs["board"]["rubble"]
        if occupied is None:
            occupied_next = list(deepcopy(self.agent.occupied_next))
        else:
            occupied_next = list(occupied)

        if recharging:
            opp_unit_positions = [u.pos for uid, u in self.agent.opp_units.items() if u.unit_type == "HEAVY"]
            occupied_next.extend(opp_unit_positions)
        if self.unit.unit_type == "LIGHT":
            opp_heavy_positions = [u.pos for uid, u in self.agent.opp_units.items() if u.unit_type == "HEAVY"]
            occupied_next.extend(opp_heavy_positions)
            for pos in opp_heavy_positions:
                cardinal_tiles = get_cardinal_tiles(pos)
                occupied_next.extend(cardinal_tiles)

        opp_factory_tiles = list(self.agent.opp_factory_tiles)
        cheap_path = dijkstras_path(rubble_map, start, finish, occupied_next, opp_factory_tiles)
        # fast_path = dijkstras_path(rubble_map, start, finish, occupied_next, opp_factory_tiles, rubble_threshold=30)
        # if fast_path is not None and cheap_path is not None:
        #     fast_cost = self.get_path_cost(fast_path)
        #     cheap_cost = self.get_path_cost(cheap_path)
        #     if fast_cost < cheap_cost:
        #         return fast_path

        return cheap_path

    def get_path_moves(self, path_positions: list) -> list:
        moves = []
        for i, pos in enumerate(path_positions):
            if i < len(path_positions) - 1:
                direction = direction_to(pos, path_positions[i + 1])
                moves.append(self.unit.move(direction))
        trunc_moves = truncate_actions(moves)
        return trunc_moves
