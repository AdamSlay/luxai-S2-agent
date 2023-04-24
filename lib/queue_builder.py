# from math import floor
# from copy import deepcopy
from lib.dijkstra import dijkstras_path
from lib.utils import *


class QueueBuilder:
    def __init__(self, agent, unit, target_factory, board):
        self.agent = agent
        self.unit = unit
        self.target_factory = target_factory
        self.board = board

    def build_mining_queue(self, resource: str, rubble_tile=None, lichen_tile=None) -> list or None:
        self.agent.unit_states[self.unit.unit_id] = "mining"
        self.clear_mining_dibs()
        self.clear_lichen_dibs()
        self.clear_previous_task()
        self.clear_aggro_dibs()

        # TRANSFER
        queue = []
        not_a_homer = self.unit.unit_id not in self.agent.factory_homers.values()
        not_an_icer = self.unit.unit_id not in self.agent.factory_icers.values()
        max_power = 2980 if self.unit.unit_type == "HEAVY" else 145
        transfer_ready, transfer_direction, start_postition = self.transfer_ready(home_pref=True)
        transfer_queue_cost = 0
        if transfer_ready:
            transfer_queue, transfer_queue_cost = self.get_transfer_queue(transfer_direction)
            queue.extend(transfer_queue)

        # DIBS
        dibs = self.get_dibs_class()
        dibs_tiles = [tile for uid, tile in dibs.items()]

        if self.unit.unit_type == "LIGHT":
            factory_tasks_weight_class = self.agent.factory_tasks_light
            heavy_dibs = [tile for uid, tile in self.agent.heavy_mining_dibs.items()]
            dibs_tiles.extend(heavy_dibs)
        else:
            factory_tasks_weight_class = self.agent.factory_tasks_heavy
            # if you're digging rubble or lichen, avoid the lights that are doing the same
            # you might accidentally mine a tile that a light finished off while you were moving
            # plus it avoids congestion while digging rubble/lichen
            if rubble_tile is not None or lichen_tile is not None:
                light_dibs = [tile for uid, tile in self.agent.light_mining_dibs.items()]
                dibs_tiles.extend(light_dibs)

        # TARGET TILE
        target_factory = self.target_factory
        if rubble_tile is not None:
            resource_tile = rubble_tile
            tile_amount = self.board["rubble"][resource_tile[0]][resource_tile[1]]
        elif lichen_tile is not None:
            self.agent.unit_states[self.unit.unit_id] = "attacking"
            resource_tile = lichen_tile
            tile_amount = self.board["lichen"][resource_tile[0]][resource_tile[1]]
        else:
            if self.unit.unit_type == "HEAVY" and not_a_homer and not_an_icer:
                # if you're a heavy, don't swipe a mining tile form another heavy just because you have a lower uid than them
                heavy_tiles = [u.pos for u in self.agent.my_heavy_units if u.unit_id != self.unit.unit_id]
                dibs_tiles.extend(heavy_tiles)
            resource_tile = closest_resource_tile(resource, target_factory.pos, dibs_tiles, self.board)
            tile_amount = None

        if resource_tile is None:
            # can't find a resource, return None and get back into the decision tree
            if not not_a_homer:
                print(f"Step {self.agent.step}: {self.unit.unit_id} can't find a resource tile, but is a homer, dibs{dibs}", file=sys.stderr)
            if not not_an_icer:
                print(f"Step {self.agent.step}: {self.unit.unit_id} can't find a resource tile, but is an icer, dibs{dibs}", file=sys.stderr)
            return None

        # Check if mining adjacent
        mining_adjacent = False
        home_factory_tiles = get_factory_tiles(target_factory.pos)
        factory_tile = closest_tile_in_group(resource_tile, [], home_factory_tiles)
        if tile_adjacent(resource_tile, factory_tile) and self.unit.unit_type == "HEAVY":
            mining_adjacent = True
            self.agent.unit_states[self.unit.unit_id] = "mining adjacent"

        # PATHING
        # if we are already on the resource tile, make sure you can stay
        pos = (self.unit.pos[0], self.unit.pos[1])
        resource_tuple = (resource_tile[0], resource_tile[1])
        if resource_tuple == pos and pos in self.agent.occupied_next:
            direction = move_toward(self.unit.pos, resource_tile, self.agent.occupied_next)
            # print(f"Step {self.agent.step}: {self.unit.unit_id} is already on the resource tile, but in occ_next moving {direction}", file=sys.stderr)
            queue = [self.unit.move(direction)]
            return queue

        # start position is the position after the transfer queue, it may not be different from unit.pos
        path_to_resource = self.get_path_positions(start_postition, resource_tile)
        cost_to_resource = self.get_path_cost(path_to_resource)
        if not path_to_resource:
            return None

        # avoid heavies when looking for a return tile
        heavies = [unit for unit in self.agent.my_heavy_units if unit.unit_id != self.unit.unit_id]
        return_tile = closest_factory_tile(target_factory.pos, resource_tile, heavies)

        path_from_resource = self.get_path_positions(resource_tile, return_tile)
        cost_from_resource = self.get_path_cost(path_from_resource)

        # COST
        reserve_power = self.agent.moderate_reserve_power[self.unit.unit_type]
        pathing_cost = transfer_queue_cost + cost_to_resource + cost_from_resource + reserve_power
        dig_allowance = 600 if self.unit.unit_type == "HEAVY" else 50
        dig_rate = 20 if self.unit.unit_type == "HEAVY" else 2
        dig_cost = 60 if self.unit.unit_type == "HEAVY" else 5

        if mining_adjacent:
            dig_allowance = 0  # only stop digging if you have less than reserve power

        if rubble_tile is not None:
            # if you are mining rubble, you just need enough power to clear the tile (at a minimum)
            # but if the tile has a ton of rubble, just use the regular dig allowance
            rubble_dig_allowance = ((tile_amount // dig_rate) + 1) * dig_cost
            dig_allowance = rubble_dig_allowance if rubble_dig_allowance < dig_allowance else dig_allowance

        if self.unit.power < pathing_cost + dig_allowance:
            if pathing_cost + dig_allowance > max_power:
                return None  # this task is not feasible for this unit at this time

            is_attacking = lichen_tile is not None
            return self.build_recharge_queue(factory=target_factory, attacking=is_attacking)

        if len(path_to_resource) > 1:
            if resource != "ice" and resource != "ore":
                moves_to_resource = self.get_path_moves(path_to_resource, pauses=2)
            else:
                moves_to_resource = self.get_path_moves(path_to_resource)
            queue.extend(moves_to_resource)

        # DIGS
        digs = self.get_number_of_digs(self.unit.power, pathing_cost, tile_amt=tile_amount, dig_rate=dig_rate)
        # make sure you aren't going to overfill your cargo, if you are transferring this queue, don't worry about it
        if (resource == "ice" or resource == "ore") and not transfer_ready:
            if resource == "ice":
                cargo = self.unit.cargo.ice
            else:
                cargo = self.unit.cargo.ore
            cargo_space = 1000 if self.unit.unit_type == "HEAVY" else 150
            max_digs = (cargo_space - cargo) // dig_rate
            digs = digs if digs < max_digs else max_digs

        if digs <= 0:
            return None  # this task is not feasible for this unit at this time
        if not not_a_homer and not mining_adjacent and digs > 20:
            digs = 20

        queue.append(self.unit.dig(n=digs))

        dibs[self.unit.unit_id] = resource_tile
        factory_tasks_weight_class[target_factory.unit_id][self.unit.unit_id] = resource
        if mining_adjacent:
            if target_factory.unit_id in self.agent.factory_needs_light.keys():
                self.agent.factory_needs_light[target_factory.unit_id].insert(0, f"helper:{self.unit.unit_id}")
            else:
                self.agent.factory_needs_light[target_factory.unit_id] = [f"helper:{self.unit.unit_id}"]

        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_attack_queue(self, lichen_tile):
        self.agent.unit_states[self.unit.unit_id] = "attacking"
        self.clear_mining_dibs()
        self.clear_lichen_dibs()
        self.clear_previous_task()
        self.clear_aggro_dibs()
        endgame = self.agent.step > 500

        closest_factory = get_closest_factory(self.agent.my_factories, self.unit.pos)
        target_factory = closest_factory

        enough_power = 2000 if self.unit.unit_type == "HEAVY" else 1000
        target_distance = distance_to(self.unit.pos, target_factory.pos)
        closest_distance = distance_to(self.unit.pos, closest_factory.pos)
        if target_distance > 10 and closest_distance < 5 and target_factory.power < enough_power:
            target_factory = closest_factory

        # do the loop once outside while so that you can recharge if you don't have enough to make a run

        factory_tasks_weight_class = self.agent.factory_tasks_light if self.unit.unit_type == "LIGHT" else self.agent.factory_tasks_heavy

        dibs = self.agent.lichen_dibs
        dig_cost = 60 if self.unit.unit_type == "HEAVY" else 5
        dig_rate = 100 if self.unit.unit_type == "HEAVY" else 10
        dig_allowance = 600 if self.unit.unit_type == "HEAVY" else 20
        reserve_power = self.agent.moderate_reserve_power[self.unit.unit_type]
        if self.agent.step > 800:
            dig_allowance = 1500 if self.unit.unit_type == "HEAVY" else 50
        # if self.agent.step > 940:
        #     dig_allowance = 200 if self.unit.unit_type == "HEAVY" else 10
        if self.agent.step > 950:
            dig_allowance = 60 if self.unit.unit_type == "HEAVY" else 5
            reserve_power = 0
        max_power = 2980 if self.unit.unit_type == "HEAVY" else 145
        power_remaining = self.unit.power
        lichen_amounts = self.board["lichen"]
        position = self.unit.pos
        queue = []

        # DIBBED TILES
        dibbed_lists = [pos_list for pos_list in self.agent.lichen_dibs.values()]
        dibbed_tiles = []
        for pos_list in dibbed_lists:
            dibbed_tiles.extend(pos_list)

        # PATHS AND COSTS
        path_to_lichen = self.get_path_positions(position, lichen_tile)
        if not path_to_lichen and not on_tile(self.unit.pos, lichen_tile):
            # lichen_tile = closest_opp_lichen(self.agent.opp_strains, self.unit.pos, dibbed_tiles, self.board)
            # if lichen_tile is None:
            #     print(f'Step {self.agent.step}: {self.unit.unit_id} is attacking but cant find a lichen tile',
            #           file=sys.stderr)
            #     return None
            # path_to_lichen = self.get_path_positions(position, lichen_tile)
            # if not path_to_lichen:
            print(
                f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but cant find a path {path_to_lichen}',
                file=sys.stderr)
            return None
        elif on_tile(self.unit.pos, lichen_tile) and (self.unit.pos[0], self.unit.pos[1]) in self.agent.occupied_next:
            queue = self.build_waiting_queue(length=7)
            return queue

        cost_from_lichen = 0
        return_path = []
        cost_to_lichen = self.get_path_cost(path_to_lichen)
        # if not endgame and self.unit.unit_type == "HEAVY":
        #     # first try the target factory
        #     path_from_lichen = self.get_path_positions(lichen_tile, target_factory.pos)
        #     if not path_from_lichen:
        #         # then just try the closest factory to the lichen tile
        #         target_factory = get_closest_factory(self.agent.my_factories, lichen_tile)
        #         # you don't need to care about occupied_next for the return path, because you arent actually going to use it
        #         path_from_lichen = self.get_path_positions(lichen_tile, target_factory.pos, occupied=self.agent.opp_factory_tiles)
        #         if not path_from_lichen:
        #             # if you still can't find a path, return None
        #             print(
        #                 f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but no path_from_lichen {path_from_lichen}',
        #                 file=sys.stderr)
        #             return None
        #     cost_from_lichen = self.get_path_cost(path_from_lichen)
        #     return_path = path_from_lichen
        # else:
        cost_from_lichen = self.agent.cost_home[self.unit.unit_id]
        total_cost = cost_to_lichen + cost_from_lichen + dig_allowance + reserve_power

        # CAN YOU AFFORD IT?
        if total_cost > power_remaining:
            if total_cost > max_power:
                if self.unit.power < max_power * 0.8:
                    queue = self.build_recharge_queue(factory=target_factory, attacking=True)
                    return queue
                else:
                    print(
                        f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but its too expensive: {total_cost}, trekking',
                        file=sys.stderr)
                    queue = self.build_trekking_queue(path_to_lichen, max_power=max_power)
                    return queue
                # print(
                #     f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but its too expensive: {total_cost}',
                #     file=sys.stderr)
                # return None  # it's not feasible to attack this lichen, return None and go back through decision tree
            if self.unit.unit_type == "LIGHT" or endgame:
                if distance_to(self.unit.pos, target_factory.pos) < 8:
                    queue = self.build_recharge_queue(factory=target_factory, attacking=True)
                    return queue
                return self.build_waiting_queue(length=77)

            if self.unit.unit_type == "HEAVY":
                return self.build_recharge_queue(factory=target_factory, attacking=True)

        # YES YOU CAN
        if not on_tile(self.unit.pos, lichen_tile):
            queue.extend(self.get_path_moves(path_to_lichen))

        power_remaining -= cost_to_lichen + reserve_power  # <---------------- accounting for reserve power here
        tile_amount = lichen_amounts[lichen_tile[0]][lichen_tile[1]]

        digs = self.get_number_of_digs(power_remaining, cost_from_lichen, tile_amt=tile_amount, dig_rate=dig_rate)
        if digs <= 0:
            if on_tile(self.unit.pos, lichen_tile):
                queue = self.build_waiting_queue(length=9)
                dibs[self.unit.unit_id] = [lichen_tile]
                return queue
            else:
                print(f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but cant dig. digs: {digs}',
                      file=sys.stderr)
                return queue[:20]


            # print(f'Step {self.agent.step}: {self.unit.unit_id} is attacking {lichen_tile} but cant dig digs: {digs}',
            #       file=sys.stderr)
            # # this task is not feasible for this unit at this time
            # return None

        dig_allowance -= digs * dig_cost
        power_remaining -= digs * dig_cost
        queue.append(self.unit.dig(n=digs))
        dibs[self.unit.unit_id] = [lichen_tile]

        while power_remaining > 0:
            dibbed_lists = [pos_list for pos_list in self.agent.lichen_dibs.values()]
            dibbed_tiles = []
            for pos_list in dibbed_lists:
                dibbed_tiles.extend(pos_list)
            # set position to previous lichen tile
            position = lichen_tile
            # find new lichen tile, closest to the previous lichen tile because that's where you will be
            lichen_tile = closest_opp_lichen(self.agent.opp_strains, position, dibbed_tiles, self.board)
            if lichen_tile is None:
                # if you can't find another lichen tile, break the loop
                break

            # PATHS AND COSTS
            path_to_lichen = self.get_path_positions(position, lichen_tile)
            cost_to_lichen = self.get_path_cost(path_to_lichen)
            if not endgame and self.unit.unit_type == "HEAVY":
                path_home_tile = closest_tile_in_group(lichen_tile, [], return_path)
                if path_home_tile is not None:
                    return_path = [tuple(pos) for pos in return_path]
                    path_home_tile = tuple(path_home_tile)
                    return_path_tile_index = return_path.index(path_home_tile)
                    path_home_tile = np.array(path_home_tile)
                    path_from_lichen = return_path[return_path_tile_index:]
                    path_from_lichen.reverse()
                    # you don't need to care about occupied_next for the return path, because you arent' actually going to use it
                    path_from_lichen.extend(self.get_path_positions(path_home_tile, lichen_tile, occupied=self.agent.opp_factory_tiles))
                else:
                    # print(f"Step {self.agent.step}: {self.unit.unit_id} path_home_tile is None {return_path}, lichen_tile: {lichen_tile}")
                    path_from_lichen = self.get_path_positions(lichen_tile, target_factory.pos, occupied=self.agent.opp_factory_tiles)
                cost_from_lichen = self.get_path_cost(path_from_lichen)
            total_cost = cost_to_lichen + cost_from_lichen + dig_allowance  # reserve power is already accounted for

            if total_cost > power_remaining:
                # you don't have the power to get to the next lichen tile, so break the loop
                break
            else:
                if len(path_to_lichen) > 1:
                    queue.extend(self.get_path_moves(path_to_lichen))
                power_remaining -= cost_to_lichen

                # this is the lichen amount at time of creating queue, it may change by the time you get there
                tile_amount = lichen_amounts[lichen_tile[0]][lichen_tile[1]]

                # get the number of digs necessary depending on the amount of lichen and the power remaining
                digs = self.get_number_of_digs(power_remaining, cost_to_lichen, tile_amt=tile_amount, dig_rate=dig_rate)

                # since the lichen amount may change, add extra digs if you can afford it and are a light unit
                # this will be checked for validity via agent.check_valid_dig at the time of execution
                if self.unit.unit_type == "LIGHT":
                    power_after_digs = power_remaining - (digs * dig_cost)
                    if power_after_digs >= dig_cost * 3:
                        digs += 3
                    elif power_after_digs >= dig_cost * 2:
                        digs += 3
                    elif power_after_digs >= dig_cost:
                        digs += 1

                # if you don't have any digs left, break the loop
                if digs <= 0:
                    break

                queue.append(self.unit.dig(n=digs))

                dig_allowance -= digs * dig_cost
                power_remaining -= digs * dig_cost

                dibs[self.unit.unit_id].append(lichen_tile)
                # end of loop

        # # after breaking out of loop, add path from lichen tile to factory
        # if len(path_from_lichen) > 1:
        #     queue.extend(self.get_path_moves(path_from_lichen))
        # factory_tasks_weight_class[target_factory.unit_id][self.unit.unit_id] = "lichen"
        if len(queue) > 20:
            queue = queue[:20]
        return queue

        # TODO: this is the square idea
        # lichen_tiles = np.copy(self.board["lichen_strains"])
        # for pos in dibbed_tiles:
        #     x = int(pos[0])
        #     y = int(pos[1])
        #     if x < 48 and y < 48:
        #         lichen_tiles[x, y] = 1000
        #
        # priority_strain = find_most_common_integer(lichen_tiles, self.agent.opp_strains)
        # priority_factory = self.agent.opp_factories[f'factory_{priority_strain}']
        #
        # lichen_group = get_lichen_in_square(lichen_tiles, self.agent.opp_strains, priority_factory.pos, 3)
        # if lichen_group is None:
        #     lichen_tile = closest_opp_lichen(self.agent.opp_strains, self.unit.pos, dibbed_tiles, self.board, priority=True)
        # lichen_tile = closest_opp_lichen(self.agent.opp_strains, self.unit.pos, dibbed_tiles, self.board, priority=True, group=lichen_group)

    def build_trekking_queue(self, path_positions, max_power=0):
        # get the path_positions for the amount of path that you can afford given your power
        affordable_path = self.get_path_cost(path_positions, max_power=max_power)
        if not affordable_path:
            return None
        queue = self.get_path_moves(affordable_path, pauses=5)
        return queue[:20]

    def build_aggro_queue(self, factory=None):
        self.agent.unit_states[self.unit.unit_id] = "aggro"
        self.clear_lichen_dibs()
        an_icer = self.unit.unit_id in self.agent.factory_icers.values()
        if not an_icer:
            self.clear_mining_dibs()
            self.clear_previous_task()
        self.clear_aggro_dibs()

        undibbed_factories = {fid: f for fid, f in self.agent.opp_factories.items() if fid not in self.agent.aggro_dibs.values()}
        if len(undibbed_factories) == 0:
            print(f"Step {self.agent.step}: {self.unit.unit_id} no undibbed factories", file=sys.stderr)
            return None

        if factory is None:
            undibbed_factory = get_closest_factory(undibbed_factories, self.target_factory.pos)
        else:
            undibbed_factory = factory

        mining_tile = closest_resource_tile("ice", undibbed_factory.pos, [], self.board)
        cardinal_tiles = get_cardinal_tiles(mining_tile)
        target_tile = closest_tile_in_group(self.unit.pos, self.agent.opp_factory_tiles, cardinal_tiles)
        if on_tile(self.unit.pos, target_tile) and can_stay(self.unit.pos, list(self.agent.occupied_next)):
            queue = [self.unit.move(0, n=25)]
            return queue

        path = self.get_path_positions(self.unit.pos, mining_tile)
        path_back = self.get_path_positions(mining_tile, self.target_factory.pos, occupied=self.agent.opp_factory_tiles)
        if path is None or len(path) == 0:
            print(f"Step {self.agent.step}: {self.unit.unit_id} cant find aggro path to {undibbed_factory.unit_id}", file=sys.stderr)
            return None
        if path_back is None or len(path_back) == 0:
            print(f"Step {self.agent.step}: {self.unit.unit_id} cant find aggro path back to {self.target_factory.unit_id}", file=sys.stderr)
            return None

        path_cost = self.get_path_cost(path)
        path_back_cost = self.get_path_cost(path_back)
        reserve_power = 150
        aggro_allowance = 400
        total_cost = path_cost + path_back_cost + aggro_allowance + reserve_power

        if total_cost >= self.unit.power:
            if total_cost > 2999:
                return None
            print(f"Step {self.agent.step}: {self.unit.unit_id} cant afford aggro path to {undibbed_factory.unit_id}, cost: {total_cost}, power: {self.unit.power}", file=sys.stderr)
            queue = self.build_recharge_queue()
            return queue

        self.agent.factory_tasks_heavy[self.target_factory.unit_id][self.unit.unit_id] = "aggro"
        self.agent.aggro_dibs[self.unit.unit_id] = undibbed_factory.unit_id
        queue = self.get_path_moves(path)
        queue.append(self.unit.move(0, n=25))
        return queue[:20]

    def build_recharge_queue(self, occupied=None, factory=None, slow_charge=False, attacking=False,
                             in_danger=False) -> list:
        # The point of occupied is to pass in opp_heavies or some such to avoid them in case you are super low or something
        target_factory = self.target_factory
        if factory is not None:
            target_factory = factory

        factory_low = self.agent.factory_low_charge_light[target_factory.unit_id] if self.unit.unit_type == "LIGHT" \
            else self.agent.factory_low_charge_heavy[target_factory.unit_id]

        a_homer = self.unit.unit_id in self.agent.factory_homers.values()
        an_icer = self.unit.unit_id in self.agent.factory_icers.values()
        a_helper = self.unit.unit_id in self.agent.last_state.keys() and \
                       self.agent.last_state[self.unit.unit_id] == "helping"

        self.agent.unit_states[self.unit.unit_id] = "recharging"
        an_icer = self.unit.unit_id in self.agent.factory_icers.values()
        if not a_homer and not an_icer:
            self.clear_mining_dibs()
            self.clear_previous_task()
        self.clear_lichen_dibs()
        self.clear_aggro_dibs()


        if factory_low and not a_homer and not an_icer and not a_helper and not in_danger:
            if self.unit.unit_type == "HEAVY" and not an_icer and self.unit.power > 1500:
                queue = self.solar_panel_queue(target_factory)
                if queue is not None:
                    return queue
            queue = self.build_waiting_queue(length=11)
            return queue

        elif self.unit.unit_type == "HEAVY" and not a_homer and not an_icer and not in_danger and target_factory.power < 800:
            queue = self.build_waiting_queue(length=12)
            return queue

        # elif self.unit.unit_type == "HEAVY":
        #     print(f"Step {self.agent.step}: {self.unit.unit_id} is a homer/icer and building recharge queue for {target_factory.unit_id}"
        #           f"- icers: {self.agent.factory_icers}, homers: {self.agent.factory_homers}", file=sys.stderr)

        homer_or_icer = a_homer or an_icer
        heavies = [unit for unit in self.agent.my_heavy_units if unit.unit_id != self.unit.unit_id]
        return_tile = closest_factory_tile(target_factory.pos, self.unit.pos, heavies)
        pickup_amt = self.get_pickup_amt(target_factory, homer=homer_or_icer, helper=a_helper)

        occupied_next = self.agent.occupied_next.copy()
        occupied_next.add((target_factory.pos[0], target_factory.pos[1]))

        pos = (self.unit.pos[0], self.unit.pos[1])
        in_position = on_tile(self.unit.pos, return_tile)
        in_occupied = pos in occupied_next

        if in_position and not in_occupied:
            queue = [self.unit.pickup(4, pickup_amt)]
            return queue
        elif in_position and in_occupied:
            direction = move_toward(self.unit.pos, return_tile, occupied_next)
            # print(f"Step {self.agent.step}: {self.unit.unit_id} is stuck at {self.unit.pos} and is moving {direction}", file=sys.stderr)
            queue = [self.unit.move(direction)]
            return queue

        # get path home
        if occupied is not None:
            path_home = self.get_path_positions(self.unit.pos, return_tile, occupied=occupied)
            cost_home = self.get_path_cost(path_home)
        else:
            path_home = self.get_path_positions(self.unit.pos, return_tile)
            cost_home = self.get_path_cost(path_home)
        if not path_home:
            queue = self.build_waiting_queue(length=4)
            return queue

        # if you're hovering around the factory but can't get to it, just wait
        right_next_to_factory = distance_to(self.unit.pos, target_factory.pos) < 3
        units_in_the_way = len(path_home) > 4
        if right_next_to_factory and units_in_the_way:
            queue = self.build_waiting_queue(length=5)
            return queue

        # wait for power if you don't have enough
        if self.unit.power < cost_home:
            if self.agent.unit_states[self.unit.unit_id] == "evasion recharge":
                print(f"Unit {self.unit.unit_id} is performing evasion recharge and doesn't have enough battery",
                      file=sys.stderr)

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
                closest_factory = get_closest_factory(self.agent.my_factories, self.unit.pos)
                if closest_factory.unit_id != target_factory.unit_id:
                    target_factory = closest_factory
                    path_home = self.get_path_positions(self.unit.pos, target_factory.pos)
                    cost_home = self.get_path_cost(path_home)
                if self.unit.power < cost_home:

                    if in_danger:
                        next_dir = move_toward(self.unit.pos, target_factory.pos, self.agent.occupied_next)
                        next_pos = next_position(self.unit.pos, next_dir)
                        cost_to_next = self.get_path_cost([next_pos])
                        if self.unit.power > cost_to_next:
                            queue = [self.unit.move(next_dir)]
                            return queue

                    cost_remaining = cost_home - self.unit.power
                    queue = self.build_low_battery_queue(cost_remaining)
                    return queue

        queue = []
        if len(path_home) > 1:
            # if you're not a homer, add variable waits to avoid all units charging at once
            is_heavy = self.unit.unit_type == "HEAVY"
            able_to_stay = can_stay(self.unit.pos, self.agent.occupied_next)
            if not a_homer and not an_icer and is_heavy and able_to_stay:
                id_digit = int(self.unit.unit_id[-1])
                step_digit = self.agent.step % 10
                if id_digit != step_digit:
                    # add waits until it's your turn to charge
                    waits = step_digit - id_digit if step_digit > id_digit else id_digit - step_digit
                    queue.append(self.unit.move(0, n=waits))

            moves = self.get_path_moves(path_home)
            queue.extend(moves)

        elif not can_stay(self.unit.pos, self.agent.occupied_next):
            queue = self.build_waiting_queue(length=8)
            return queue

        # pick up power once you get home
        if (pickup_amt > target_factory.power - 50 or factory_low) and not a_homer and not a_helper:
            slow_charge = True

        if slow_charge:
            self.agent.unit_states[self.unit.unit_id] = "slow charging"
            pickup_amt = pickup_amt // 10
            if pickup_amt > 0:
                if pickup_amt > 50:
                    pickup_amt = 50
                sequence = [self.unit.pickup(4, pickup_amt), self.unit.move(0)]
                [queue.extend(sequence) for _ in range(10)]
            else:
                queue = self.build_waiting_queue(length=10)
                return queue

        else:
            queue.append(self.unit.pickup(4, pickup_amt))

        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_helper_queue(self, homer, resource_tile=None):
        self.clear_mining_dibs()
        self.clear_lichen_dibs()
        self.clear_previous_task()

        self.agent.unit_states[self.unit.unit_id] = "helping"
        factory_tasks_weight_class = self.agent.factory_tasks_light
        queue = []
        can_transfer, transfer_direction, trans_pos = self.transfer_ready()
        if can_transfer:
            transfer_queue, transfer_queue_cost = self.get_transfer_queue(transfer_direction)
            queue.extend(transfer_queue)
        if resource_tile is None:
            resource_tile = homer.pos
        target_tile = get_helper_tile(resource_tile, self.target_factory.pos)
        if not on_tile(trans_pos, target_tile):
            positions_to_target = self.get_path_positions(trans_pos, target_tile)
            if len(positions_to_target) == 0:
                return None
            cost_to_target = self.get_path_cost(positions_to_target)
            if self.unit.power < cost_to_target:
                queue = self.build_low_battery_queue(cost_to_target - self.unit.power)
            moves_to_target = self.get_path_moves(positions_to_target)
            queue.extend(moves_to_target)

        else:
            for pos in self.agent.occupied_next:
                if pos[0] == target_tile[0] and pos[1] == target_tile[1]:
                    direction = move_toward(self.unit.pos, target_tile, self.agent.occupied_next)
                    queue = [self.unit.move(direction)]
                    return queue

        pickup_amt = 0
        if self.unit.power < 140:
            pickup_amt = self.get_pickup_amt(self.target_factory, homer=False, helper=True)
            queue.append(self.unit.pickup(4, pickup_amt))

        if homer.power < 1000:
            transfer_amt = (self.unit.power + pickup_amt) - 20
            if transfer_amt > 0:
                transfer_direction = direction_to(target_tile, homer.pos)
                queue.append(self.unit.transfer(transfer_direction, 4, transfer_amt))
        else:
            queue.append(self.unit.move(0))

        factory_tasks_weight_class[self.target_factory.unit_id][self.unit.unit_id] = f"helper:{homer.unit_id}"

        # Return queue
        if len(queue) > 20:
            queue = queue[:20]
        return queue

    def build_low_battery_queue(self, desired_power: int) -> list:
        self.agent.unit_states[self.unit.unit_id] = "low battery"
        self.clear_mining_dibs()
        self.clear_lichen_dibs()
        self.clear_previous_task()
        self.clear_aggro_dibs()
        queue = []
        if self.unit.unit_type == "HEAVY":
            solar_charge = 10
            queue_cost = 11
        else:
            solar_charge = 1
            queue_cost = 2

        step = self.agent.step
        power_required = desired_power + queue_cost
        waits = 0
        while power_required >= 0:
            queue.append(self.unit.move(0))
            if step % 50 < 30:
                power_required -= solar_charge
            step += 1
            waits += 1
        if waits > 100:
            print(
                f"Step {self.agent.step}: {self.unit.unit_id} is low on battery and is waiting for {len(queue) + 1} steps. "
                f"factory = {self.target_factory.unit_id}\n", file=sys.stderr)

        # add an extra move to make sure you don't get stuck
        queue.append(self.unit.move(0))
        queue = truncate_actions(queue, self.unit)
        return queue

    def solar_panel_queue(self, factory):
        # find all tiles that share a side with a factory tile
        # filter out the ones that are occupied or have resources
        # move to the closest one
        # funnel power to the factory
        charges = 33
        already_panel = self.agent.unit_states[self.unit.unit_id] == "solar panel"
        if already_panel and self.unit.unit_id in self.agent.action_queue.keys():
            if len(self.agent.action_queue[self.unit.unit_id]) > 0:
                next_move = self.agent.action_queue[self.unit.unit_id][0]
                if next_move[0] == 1:
                    charges = self.agent.action_queue[self.unit.unit_id][0][5]

        self.agent.unit_states[self.unit.unit_id] = "solar panel"
        self.clear_mining_dibs()
        self.clear_lichen_dibs()
        self.clear_previous_task()
        self.clear_aggro_dibs()

        outer_adjacent_tiles = get_outer_adjacent_tiles(factory.pos)
        occupied_or_resources = [u.pos for u in self.agent.my_heavy_units if u.unit_id != self.unit.unit_id]
        ice = self.board['ice']
        ore = self.board['ore']
        ice_positions = np.column_stack(np.where(ice == 1))
        ore_positions = np.column_stack(np.where(ore == 1))
        occupied_or_resources.extend(ice_positions)
        occupied_or_resources.extend(ore_positions)

        closest_tile = closest_tile_in_group(self.unit.pos, occupied_or_resources, outer_adjacent_tiles)
        if closest_tile is None:
            print(f"Step {self.agent.step}: {self.unit.unit_id} is stuck in solar panel queue. no good tile",
                  file=sys.stderr)
            return None

        queue = []
        cost_to_target = 0
        if on_tile(self.unit.pos, closest_tile) and (self.unit.pos[0], self.unit.pos[1]) in self.agent.occupied_next:
            print(f"Step {self.agent.step}: {self.unit.unit_id} is stuck in solar panel queue. on target but occupied",
                  file=sys.stderr)
            direction = move_toward(self.unit.pos, closest_tile, self.agent.occupied_next)
            queue.append(self.unit.move(direction))
            return queue

        if not on_tile(self.unit.pos, closest_tile):
            positions_to_target = self.get_path_positions(self.unit.pos, closest_tile)
            if not positions_to_target:
                print(f"Step {self.agent.step}: {self.unit.unit_id} is stuck in solar panel queue. no path to target",
                      file=sys.stderr)
                return None
            cost_to_target = self.get_path_cost(positions_to_target)
            if cost_to_target > self.unit.power:
                queue = self.build_waiting_queue(length=13)
                return queue
            moves_to_target = self.get_path_moves(positions_to_target)
            queue.extend(moves_to_target)

        if self.unit.power - cost_to_target > 200:
            direction = direction_to(closest_tile, factory.pos)
            queue.append(self.unit.transfer(direction, 4, 6, n=charges))

        if len(queue) == 0:
            queue = self.build_waiting_queue(length=43)
        return queue[:20]

    def build_waiting_queue(self, length=50) -> list or None:
        self.agent.unit_states[self.unit.unit_id] = "waiting"
        a_homer = self.unit.unit_id in self.agent.factory_homers.values()
        an_icer = self.unit.unit_id in self.agent.factory_icers.values()
        if not a_homer and not an_icer:
            self.clear_mining_dibs()
            self.clear_previous_task()
        self.clear_lichen_dibs()

        # do not wait on a resource tile
        closest_factory = get_closest_factory(self.agent.my_factories, self.unit.pos)
        occupied_or_resources = list(self.agent.occupied_next)
        occupied_or_resources.append(closest_factory.pos)
        ice = self.board['ice']
        ore = self.board['ore']
        ice_positions = np.column_stack(np.where(ice == 1))
        ore_positions = np.column_stack(np.where(ore == 1))
        occupied_or_resources.extend(ice_positions)
        occupied_or_resources.extend(ore_positions)

        if can_stay(self.unit.pos, occupied_or_resources):
            queue = [self.unit.move(0, n=length)]
        else:
            occupied_next = list(self.agent.occupied_next)
            occupied_next.append(closest_factory.pos)
            direction = move_toward(self.unit.pos, self.target_factory.pos, occupied_next)
            # print(f"Step {self.agent.step}: {self.unit.unit_id} is waiting but can't stay in place, moving in direction {direction}",
            #       file=sys.stderr)
            queue = [self.unit.move(direction), self.unit.move(0, n=4)]
        return queue

    def build_evasion_dance(self, avoid_positions, cost_home, opp_unit=None):
        self.clear_previous_task()
        self.clear_lichen_dibs()
        reserve_power = self.agent.low_reserve_power[self.unit.unit_type]
        attacker = self.unit.unit_id in self.agent.last_state.keys() and \
                            self.agent.last_state[self.unit.unit_id] == "attacking"
        light_attacker = self.unit.unit_type == "LIGHT" and attacker
        heavy_attacker = self.unit.unit_type == "HEAVY" and attacker and self.agent.step > 800

        # If you barely have enough power to get home, recharge
        if self.unit.power < cost_home + reserve_power:
            self.agent.unit_states[self.unit.unit_id] = "evasion recharge"
            queue = self.build_recharge_queue(occupied=avoid_positions, in_danger=True)
            if len(queue) == 0:
                queue = self.build_recharge_queue()
            if queue[0][0] == 0:  # it's a move
                if queue[0][1] == 0:  # it's a move in place
                    direction = move_toward(self.unit.pos, self.target_factory.pos, avoid_positions)
                    queue.insert(0, self.unit.move(direction))
            return queue[:20]

        light_vs_heavy = self.unit.unit_type == "LIGHT" and opp_unit.unit_type == "HEAVY"
        # is_homer = self.agent.all_unit_titles[self.unit.unit_id] == "homer"
        away_from_home = distance_to(self.unit.pos, self.target_factory.pos) > 4
        # if opp_unit is None or light_vs_heavy or (is_homer and away_from_home):
        if opp_unit is None or light_vs_heavy or away_from_home:
            # If you're in a precarious situation, retreat
            direction = move_toward(self.unit.pos, self.target_factory.pos, avoid_positions)
            if direction == 0:
                # if you can't find a direction while avoiding threats, try to find a direction without avoiding threats
                direction = move_toward(self.unit.pos, self.target_factory.pos, self.agent.occupied_next)
            queue = [self.unit.move(direction)]
            return queue

        if light_attacker or heavy_attacker:
            direction = move_toward(self.unit.pos, self.target_factory.pos, avoid_positions)
            if direction == 0:
                direction = move_toward(self.unit.pos, self.target_factory.pos, self.agent.occupied_next)

            next_pos = next_position(self.unit.pos, direction)
            cost_to_next = self.get_path_cost([next_pos])
            if cost_to_next > self.unit.power + 1:
                queue = self.build_waiting_queue(length=17)
                return queue

            queue = [self.unit.move(direction)]
            return queue

        # Otherwise, find a direction to move in then move back
        else:
            is_homer = self.unit.unit_id in self.agent.factory_homers.values()
            if is_homer:
                # If you're a homer and you're near home, attack, but move back to home
                direction = move_toward(self.unit.pos, opp_unit.pos, avoid_positions)
                next_pos = next_position(self.unit.pos, direction)
                direction_home = move_toward(next_pos, self.target_factory.pos, [])
                sequence = [
                    self.unit.move(direction),
                    self.unit.move(direction_home)
                ]
                queue = sequence
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

    def check_need_recharge(self, factory=None) -> (bool, list, list):
        if factory is None:
            factory = self.target_factory
        reserve_power = self.agent.low_reserve_power[self.unit.unit_type]

        closest_charge_tile = closest_factory_tile(factory.pos, self.unit.pos, self.agent.my_heavy_units)
        path_home = self.get_path_positions(self.unit.pos, closest_charge_tile)
        cost_home = self.get_path_cost(path_home)

        if self.unit.power <= cost_home + reserve_power:
            return True, path_home, cost_home
        return False, path_home, cost_home

    def optimal_recharge_factory(self):
        reserve_power = self.agent.low_reserve_power[self.unit.unit_type]

        optimal_factory = None
        most_power = 0
        good_candidates = {}
        for fid, factory in self.agent.my_factories.items():
            # keep track of all factories that are good candidates
            if factory.power > 2000:
                good_candidates[fid] = factory

            # find the optimal factory
            if factory.power > most_power + 2000:
                path_to_factory = self.get_path_positions(self.unit.pos, factory.pos, recharging=True)
                cost_to_factory = self.get_path_cost(path_to_factory)
                if cost_to_factory < self.unit.power + reserve_power:
                    most_power = factory.power
                    optimal_factory = factory

        # if there are multiple viable factories, choose the closest one
        if len(good_candidates) > 1:
            closest_factory = get_closest_factory(good_candidates, self.unit.pos)
            return closest_factory

        # otherwise, go with the factory with the most power that you can reach
        return optimal_factory

    # UTILITIES
    def transfer_ready(self, home_pref=False, position=None) -> tuple:
        if position is None:
            position = self.unit.pos
        if home_pref:
            charge_factory = self.target_factory
        else:
            charge_factory = get_closest_factory(self.agent.my_factories, position)
        # other_heavies = [u for u in self.agent.my_heavy_units if u.unit_id != self.unit.unit_id]
        target_factory_tile = closest_factory_tile(charge_factory.pos, position, [])
        pos = (position[0], position[1])
        ice_threshold = 100 if self.unit.unit_type == "HEAVY" else 20
        if pos in self.agent.occupied_next:
            return False, 8, position

        if on_tile(position, target_factory_tile):
            if self.unit.cargo.ice > ice_threshold or self.unit.cargo.ore > 0:
                return True, 0, self.unit.pos

        elif tile_adjacent(self.unit.pos, target_factory_tile):
            if self.unit.cargo.ice > ice_threshold or self.unit.cargo.ore > 0:
                direction = direction_to(position, target_factory_tile)
                return True, direction, self.unit.pos

        elif self.unit.cargo.ice > ice_threshold or self.unit.cargo.ore > ice_threshold:
            path = self.get_path_positions(position, target_factory_tile)
            if len(path) > 0:
                return True, path, path[-1]

        return False, 8, position

    def get_transfer_queue(self, transfer_direction, home_pref=True):
        if home_pref:
            target_factory = self.target_factory
        else:
            target_factory = get_closest_factory(self.agent.my_factories, self.unit.pos)

        queue = []
        cost = 0
        if isinstance(transfer_direction, list):  # if transfer_direction is a path
            queue.extend(self.get_path_moves(transfer_direction))
            cost = self.get_path_cost(transfer_direction)
            # direction from last position in path to its closest factory tile
            target_factory_tile = closest_factory_tile(target_factory.pos, transfer_direction[-1], [])
            transfer_direction = direction_to(transfer_direction[-1], target_factory_tile)
        if self.unit.unit_type == "LIGHT":
            if self.unit.cargo.ice > 0:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            if self.unit.cargo.ore > 0:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
        else:
            if self.unit.cargo.ice > 100 and target_factory.cargo.water < 150:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            elif self.unit.cargo.ice > 400:
                queue.append(self.unit.transfer(transfer_direction, 0, self.unit.cargo.ice))
            if self.unit.cargo.ore > 0 and target_factory.cargo.metal < 100:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
            elif self.unit.cargo.ore > 400:
                queue.append(self.unit.transfer(transfer_direction, 1, self.unit.cargo.ore))
        return queue, cost

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

    def clear_aggro_dibs(self):
        if self.unit.unit_id in self.agent.aggro_dibs.keys():
            del self.agent.aggro_dibs[self.unit.unit_id]

    def clear_lichen_dibs(self):
        if self.unit.unit_id in self.agent.lichen_dibs.keys():
            del self.agent.lichen_dibs[self.unit.unit_id]

    def clear_previous_task(self):
        uid = self.unit.unit_id
        fid = self.target_factory.unit_id
        if uid in self.agent.factory_tasks_light[fid].keys():
            del self.agent.factory_tasks_light[fid][uid]
        if uid in self.agent.factory_tasks_heavy[fid].keys():
            del self.agent.factory_tasks_heavy[fid][uid]

    def get_number_of_digs(self, power_remaining: int, total_movement_cost: int, tile_amt=None, dig_rate=None) -> int:
        if self.unit.unit_type == "HEAVY":
            dig_cost = 60
            if dig_rate is None:
                dig_rate = 20
        else:
            dig_cost = 5
            if dig_rate is None:
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

    def get_pickup_amt(self, charge_factory, homer=False, helper=False) -> int:
        if self.unit.unit_type == "LIGHT":
            if helper:
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
                power_wanted = 150 - self.unit.power
                available_power = charge_factory.power - 200
                if available_power > power_wanted:
                    pickup_amt = power_wanted
                else:
                    pickup_amt = available_power

        else:  # it's a heavy
            if homer:
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
            else:
                power_wanted = 3000 - self.unit.power
                available_power = charge_factory.power - 600
                if available_power > power_wanted:
                    pickup_amt = power_wanted
                else:
                    pickup_amt = available_power
        # print(f"Step {self.agent.step}: {self.unit.unit_id} is picking up {pickup_amt} from {charge_factory.unit_id}", file=sys.stderr)
        return pickup_amt

    def get_path_cost(self, path_positions: list, type=None, max_power=None) -> int or list:
        if type is None:
            type = self.unit.unit_type

        if type == "HEAVY":
            queue_cost = 10
            multiplier = 1
            move_cost = 20
        else:
            queue_cost = 1
            multiplier = 0.05
            move_cost = 1

        rubble_map = self.board["rubble"]

        if max_power is not None:
            path = []
            power_remaining = max_power
            for pos in path_positions:
                rubble_cost = floor(rubble_map[pos[0]][pos[1]] * multiplier)
                power_remaining -= rubble_cost + move_cost
                if power_remaining <= queue_cost:
                    break
                path.append(pos)
            return path

        total_cost = 0
        for pos in path_positions:
            rubble_cost = floor(rubble_map[pos[0]][pos[1]] * multiplier)
            total_cost += (rubble_cost + move_cost)
        return total_cost + queue_cost

    def get_occupied_next_for_group(self, group):
        occupied_next = set()
        for tile in self.agent.opp_factory_tiles:
            occupied_next.add((tile[0], tile[1]))
        # opp_factory_tiles = [get_factory_tiles(f.pos) for i, f in self.agent.opp_factories.items()]
        # for tiles in opp_factory_tiles:
        #     for tile in tiles:
        #         occupied_next.add((tile[0], tile[1]))

        for uid, u in group.items():
            if u.unit_id in self.agent.action_queue.keys():
                queue = self.agent.action_queue[u.unit_id]
                # if you have an action queue, check the next position
                if isinstance(queue, list) and len(queue) > 0:
                    # if you're moving, next_pos is the next position
                    if queue[0][0] == 0:
                        next_pos = next_position(u.pos, queue[0][1])
                        new_pos = (next_pos[0], next_pos[1])
                    # otherwise, next_pos is the current position
                    else:
                        new_pos = (u.pos[0], u.pos[1])
                    # add new_pos to occupied_next
                    occupied_next.add(new_pos)
                # if you don't have an action queue, add the current position to occupied_next
                else:
                    pos = (u.pos[0], u.pos[1])
                    occupied_next.add(pos)
        return occupied_next

    def get_path_positions(self, start: np.ndarray, finish: np.ndarray, recharging=False, occupied=None) -> list:
        rubble_map = np.copy(self.board["rubble"])
        if occupied is None:
            if self.unit.unit_type == "HEAVY":
                units = self.agent.my_units
                unit_id = self.unit.unit_id
                factory_homers = self.agent.factory_homers
                homer = unit_id in factory_homers.values()
                if homer:
                    heavies = {uid: u for uid, u in units.items() if uid in factory_homers.values() and uid != unit_id}
                    helpers_next = []
                else:
                    heavies = {uid: u for uid, u in units.items() if uid != unit_id and u.unit_type == "HEAVY"}
                    unit_states = {uid: u for uid, u in units.items() if uid in self.agent.unit_states.keys()}
                    helpers = {uid: u for uid, u in unit_states.items() if self.agent.unit_states[uid] == "helping"}
                    helpers_next = list(self.get_occupied_next_for_group(helpers))
                occupied_next = list(self.get_occupied_next_for_group(heavies))
                occupied_next.extend(helpers_next)

            else:
                occupied_next = list(self.agent.occupied_next)
        else:
            occupied_next = list(occupied)

        if recharging or self.unit.unit_type == "LIGHT":
            opp_heavy_positions = [u.pos for uid, u in self.agent.opp_units.items() if u.unit_type == "HEAVY"]
            occupied_next.extend(opp_heavy_positions)
            for pos in opp_heavy_positions:
                cardinal_tiles = get_cardinal_tiles(pos)
                occupied_next.extend(cardinal_tiles)

        opp_factory_tiles = list(self.agent.opp_factory_tiles)
        # cheap_path = dijkstras_path(rubble_map, start, finish, occupied_next, opp_factory_tiles)
        # return cheap_path
        fast_path = dijkstras_path(rubble_map, start, finish, occupied_next, opp_factory_tiles, rubble_threshold=20)
        # if fast_path is not None and cheap_path is not None:
        #     fast_cost = self.get_path_cost(fast_path)
        #     cheap_cost = self.get_path_cost(cheap_path)
        #     if fast_cost < cheap_cost:
        #         return fast_path
        #
        # if cheap_path is not None:
        #     return cheap_path
        return fast_path

    def get_path_moves(self, path_positions: list, pauses=None) -> list:
        moves = []
        for i, pos in enumerate(path_positions):
            if i < len(path_positions) - 1:
                direction = direction_to(pos, path_positions[i + 1])
                moves.append(self.unit.move(direction))
        trunc_moves = truncate_actions(moves, self.unit, pauses=pauses)
        return trunc_moves
