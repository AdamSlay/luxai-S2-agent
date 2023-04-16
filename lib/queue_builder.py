# from math import floor

from lib.dijkstra import dijkstras_path
from lib.utils import *


class QueueBuilder:
    def __init__(self, agent, unit, target_factory, obs):
        self.agent = agent
        self.unit = unit
        self.target_factory = target_factory
        self.obs = deepcopy(obs)

    def build_mining_queue(self, resource: str, rubble_tile=None) -> list or None:
        self.agent.unit_states[self.unit.unit_id] = "mining"
        self.clear_mining_dibs()
        self.clear_previous_task()

        queue = []
        transfer_ready, transfer_direction = self.transfer_ready(home_pref=True)
        if transfer_ready:
            transfer_queue = self.get_transfer_queue(transfer_direction)
            queue.extend(transfer_queue)

        dibs = self.get_dibs_class()
        dibs_tiles = [tile for uid, tile in dibs.items()]

        if self.unit.unit_type == "LIGHT":
            factory_tasks_weight_class = self.agent.factory_tasks_light
            heavy_dibs = [tile for uid, tile in self.agent.heavy_mining_dibs.items()]
            dibs_tiles.extend(heavy_dibs)
        else:
            factory_tasks_weight_class = self.agent.factory_tasks_heavy

        if rubble_tile is not None:
            resource_tile = rubble_tile
            tile_amount = self.obs["board"]["rubble"][resource_tile[0]][resource_tile[1]]
            print(f"Step {self.agent.step}: Unit {self.unit.unit_id} is mining rubble at {resource_tile}, tile_amt = {tile_amount}!", file=sys.stderr)
        else:
            resource_tile = closest_resource_tile(resource, self.unit.pos, dibs_tiles, self.obs)
            tile_amount = None

        if resource_tile is None:
            print(f"{self.unit.unit_id} can't find a resource tile!", file=sys.stderr)
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
        reserve_power = self.agent.moderate_reserve_power[self.unit.unit_type]
        pathing_cost = cost_to_resource + cost_from_resource + reserve_power
        dig_allowance = 600 if self.unit.unit_type == "HEAVY" else 50
        if rubble_tile is not None:
            # if you are mining rubble, you just need enough power to clear the tile (at a minimum)
            # but if the tile has a ton of rubble, just use the regular dig allowance
            dig_rate = 20 if self.unit.unit_type == "HEAVY" else 2
            dig_cost = 60 if self.unit.unit_type == "HEAVY" else 5
            rubble_dig_allowance = ((tile_amount // dig_rate) + 1) * dig_cost
            dig_allowance = rubble_dig_allowance if rubble_dig_allowance < dig_allowance else dig_allowance

        if self.unit.power < pathing_cost + dig_allowance:
            return self.build_recharge_queue()

        if len(path_to_resource) > 1:
            moves_to_resource = self.get_path_moves(path_to_resource)
            queue.extend(moves_to_resource)

        digs = self.get_number_of_digs(self.unit.power, pathing_cost, tile_amt=tile_amount)
        queue.append(self.unit.dig(n=digs))

        factory_tasks_weight_class[self.target_factory.unit_id][self.unit.unit_id] = resource

        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_recharge_queue(self, occupied=None) -> list:
        # The point of occupied is to pass in opp_heavies or some such to avoid them in case you are super low or something
        self.agent.unit_states[self.unit.unit_id] = "recharging"
        self.clear_mining_dibs()
        self.clear_previous_task()
        queue = []

        heavies = [unit for unit in self.agent.my_heavy_units if unit.unit_id != self.unit.unit_id]
        return_tile = closest_factory_tile(self.target_factory.pos, self.unit.pos, heavies)
        pickup_amt = self.get_pickup_amt(self.target_factory)

        pos = (self.unit.pos[0], self.unit.pos[1])
        in_position = on_tile(self.unit.pos, return_tile)
        in_occupied = pos in self.agent.occupied_next
        if in_position and not in_occupied:
            queue = [self.unit.pickup(4, pickup_amt)]
            return queue
        elif in_position and in_occupied:
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
            if self.agent.unit_states[self.unit.unit_id] == "evasion recharge":
                print(f"Unit {self.unit.unit_id} is performing evasion recharge and doesn't have enough battery", file=sys.stderr)

                next_pos = path_home[1]
                cost_to_next = self.get_path_cost([next_pos])

                # if you can't even make it to the next tile, just wait
                if self.unit.power < cost_to_next:
                    cost_remaining = cost_home - self.unit.power
                    queue = self.build_low_battery_queue(cost_remaining)
                    return queue

                # if you can make it to the next tile, just go there
                next_move = self.get_path_moves([next_pos])
                queue = next_move
                return queue

            else:
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
        self.agent.unit_states[self.unit.unit_id] = "low battery"
        self.clear_mining_dibs()
        self.clear_previous_task()
        queue = []
        if self.unit.unit_type == "HEAVY":
            solar_charge = 10
        else:
            solar_charge = 1

        step = deepcopy(self.agent.step)
        power_required = deepcopy(desired_power)

        while power_required >= 0:
            queue.append(self.unit.move(0))
            if step % 50 < 30:
                power_required -= solar_charge
            step += 1
        # add an extra move to make sure you don't get stuck
        queue.append(self.unit.move(0))

        queue = truncate_actions(queue)
        return queue

    def build_evasion_dance(self, avoid_positions, opp_unit=None):
        self.clear_previous_task()
        reserve_power = self.agent.low_reserve_power[self.unit.unit_type]

        # If you barely have enough power to get home, recharge
        path_home = self.get_path_positions(self.unit.pos, self.target_factory.pos, avoid_positions)
        cost_home = self.get_path_cost(path_home)
        if self.unit.power < cost_home + reserve_power:
            self.agent.unit_states[self.unit.unit_id] = "evasion recharge"
            queue = self.build_recharge_queue(avoid_positions)
            if len(queue) == 0:
                print(f"Step {self.agent.step}: {self.unit.unit_id} couldn't find a recharge path that avoids positions", file=sys.stderr)
                queue = self.build_recharge_queue(self.agent.occupied_next)
            return queue

        light_vs_heavy = self.unit.unit_type == "LIGHT" and opp_unit.unit_type == "HEAVY"
        # is_homer = self.agent.all_unit_titles[self.unit.unit_id] == "homer"
        away_from_home = distance_to(self.unit.pos, self.target_factory.pos) > 4
        # if opp_unit is None or light_vs_heavy or (is_homer and away_from_home):
        if opp_unit is None or light_vs_heavy or away_from_home:
            # If you're in a precarious situation, retreat
            direction = move_toward(self.unit.pos, self.target_factory.pos, avoid_positions)
            if direction == 0:
                print(f"Step {self.agent.step}: {self.unit.unit_id} coulnd't find direction while avoiding positions", file=sys.stderr)
                # if you can't find a direction while avoiding threats, try to find a direction without avoiding threats
                direction = move_toward(self.unit.pos, self.target_factory.pos, self.agent.occupied_next)
            queue = [self.unit.move(direction)]

        # Otherwise, find a direction to move in then move back
        else:
            direction = move_toward(self.unit.pos, opp_unit.pos, avoid_positions)
            if direction == 0:
                direction = move_toward(self.unit.pos, self.target_factory.pos, avoid_positions)
            if direction == 0:
                direction = move_toward(self.unit.pos, self.target_factory.pos, self.agent.occupied_next)

            opposite_direction = get_opposite_direction(direction)
            sequence = [
                self.unit.move(direction),
                self.unit.move(opposite_direction)
            ]
            queue = sequence
        return queue

    def check_need_recharge(self) -> bool:
        reserve_power = self.agent.low_reserve_power[self.unit.unit_type]

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

    def clear_previous_task(self):
        uid = self.unit.unit_id
        fid = self.target_factory.unit_id
        if uid in self.agent.factory_tasks_light[fid].keys():
            del self.agent.factory_tasks_light[fid][uid]
        if uid in self.agent.factory_tasks_heavy[fid].keys():
            del self.agent.factory_tasks_heavy[fid][uid]

    def get_number_of_digs(self, power_remaining: int, total_movement_cost: int, tile_amt=None) -> int:
        if self.unit.unit_type == "HEAVY":
            dig_cost = 60
            dig_rate = 20
        else:
            dig_cost = 5
            dig_rate = 2
        number_of_digs = (power_remaining - total_movement_cost) // dig_cost
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
