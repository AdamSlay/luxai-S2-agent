from lib.evasion_utils import *
from lib.queue_builder import QueueBuilder
from lib.utils import *


def evasion_check(self, unit, target_factory, opp_units, obs):
    if unit.unit_type == "HEAVY":
        threshold = 100
        recharge_rate = 10
    else:
        threshold = 10
        recharge_rate = 1

    # Tiles of interest
    cardinal_tiles = get_cardinal_tiles(unit.pos)
    second_level_tiles = get_second_level_tiles(unit.pos)
    evasion_state = "evading"

    # Enemy units on tiles of interest
    danger_close_units, danger_close = get_opp_units_on_tiles(unit, opp_units, cardinal_tiles)
    danger_far_units, danger_far = get_opp_units_on_tiles(unit, opp_units, second_level_tiles)

    # Positions to avoid
    avoid_positions = deepcopy(list(self.occupied_next))
    avoid_these_tiles = set()

    # Find the positions that I do not want to move to, append them to avoid_positions
    for oid, opp_unit in danger_far_units.items():
        power_needed = opp_unit.power
        is_light = unit.unit_type == "LIGHT"
        opp_heavy = opp_unit.unit_type == "HEAVY"
        light_vs_heavy = is_light and opp_heavy
        if unit.power <= power_needed or light_vs_heavy:
            cards_off_limits = get_cardinal_tiles_toward(unit.pos, opp_unit.pos)
            # Don't avoid tiles that are on my factories
            cards_off_limits = remove_factory_tiles_from_group(cards_off_limits, self.my_factories)

            # append the tiles to avoid_positions
            for tile in cards_off_limits:
                avoid_positions.append((tile[0], tile[1]))
                avoid_these_tiles.add((tile[0], tile[1]))

    for oid, opp_unit in danger_close_units.items():
        # if enemy is a heavy, and you are light, avoid the tile they are on
        light_vs_heavy = unit.unit_type == "LIGHT" and opp_unit.unit_type == "HEAVY"
        if light_vs_heavy:
            avoid_positions.append((opp_unit.pos[0], opp_unit.pos[1]))
            avoid_these_tiles.add((opp_unit.pos[0], opp_unit.pos[1]))

    # # if you are out of power, recharge
    q_builder = QueueBuilder(self, unit, target_factory, obs)
    path_home = q_builder.get_path_positions(unit.pos, target_factory.pos, occupied=avoid_positions)
    cost_home = q_builder.get_path_cost(path_home)

    # if you're evading, but out of power, reset the queue so that you can recharge
    if unit.power <= cost_home + threshold and self.unit_states[unit.unit_id] == "evading":
        self.action_queue[unit.unit_id] = []

    # if there is an action in the queue
    if len(self.action_queue[unit.unit_id]) > 0:

        # and the action is a move, make sure it's safe
        if self.action_queue[unit.unit_id][0][0] == 0:
            next_dir = self.action_queue[unit.unit_id][0][1]
            next_pos = next_position(unit.pos, next_dir)

            #  if the next position is safe, then continue with the action
            if (next_pos[0], next_pos[1]) not in avoid_these_tiles:
                # if the queue is an evasion queue, but I'm not in danger anymore, then clear the queue
                if self.unit_states[unit.unit_id] == "evading" and next_dir != 0:
                    if danger_close:
                        # print(f"Step {self.step}: {unit.unit_id} was evading, but still danger close. performing next evasion action", file=sys.stderr)
                        # you were evading, your next action is safe, but still danger close,
                        # do the next action then clear rest of the queue
                        self.action_queue[unit.unit_id] = [self.action_queue[unit.unit_id][0]]
                    else:
                        # print(f"Step {self.step}: {unit.unit_id} was evading, but no longer danger close. returning None from evasion_check", file=sys.stderr)
                        # you were evading, but no longer danger close, clear the queue and stop evading
                        self.unit_states[unit.unit_id] = "idle"
                        self.action_queue[unit.unit_id] = []
                    return None

                elif not danger_close:
                    # you were not evading, and your next action is safe, so continue
                    # print(f"Step {self.step}: {unit.unit_id} was not evading, and next move is safe. returning None from evasion_check", file=sys.stderr)
                    return None

        # if the next action is not a move, then make sure there isn't danger close
        elif not danger_close:
            # print(
            #     f"Step {self.step}: {unit.unit_id} was not evading, and is not moving. returning None from evasion_check",
            #     file=sys.stderr)
            return None

    # If danger close, build evasion dance queue and return it, keep in mind avoid_these_tiles
    if danger_close:
        self.unit_states[unit.unit_id] = evasion_state

        print(f"Step {self.step}: {unit.unit_id} is evading DANGER CLOSE", file=sys.stderr)

        # if you have an action in the queue, get the next position
        if unit.unit_id in self.action_queue.keys() and len(self.action_queue[unit.unit_id]) > 0:
            next_pos = get_next_queue_position(unit, self.action_queue)
            # if your queue is not moving you, then you will need to build a new queue
            not_moving = next_pos[0] == unit.pos[0] and next_pos[1] == unit.pos[1]
        else:
            next_pos = unit.pos
            not_moving = True
        on_my_factory = (next_pos[0], next_pos[1]) in self.my_factory_tiles
        if ((next_pos[0], next_pos[1]) in avoid_these_tiles or not_moving) and not on_my_factory:
            # get the first unit in the danger_close_units, this is *probably* the enemy homer
            opp_id, opp_u = next(iter(danger_close_units.items()))
            queue = q_builder.build_evasion_dance(avoid_positions, opp_unit=opp_u)
            return queue
        else:
            # you are already moving to a safe tile, so just continue
            # print(f"Step {self.step}: {unit.unit_id} returning None from inside danger_close check", file=sys.stderr)
            return None

        # queue = q_builder.build_evasion_dance(avoid_positions)
        # return queue

    # If danger far, try to pause and then continue about your business
    if danger_far:
        print(f"Step {self.step}: {unit.unit_id} is evading DANGER FAR", file=sys.stderr)
        self.unit_states[unit.unit_id] = evasion_state

        # if you're not in the way of your own units, try to pause
        if can_stay(unit.pos, self.occupied_next) and unit.power % 3 != 0:
            direction = move_toward(unit.pos, target_factory.pos, avoid_positions, desired_direction=0)
        # otherwise, try to move out of the way while avoiding enemy units
        else:
            direction = move_toward(unit.pos, target_factory.pos, avoid_positions)
            # if you can't move out of the way while avoiding enemy units, just move out of the way
            if direction == 0:
                direction = move_toward(unit.pos, target_factory.pos, self.occupied_next)

        new_pos = next_position(unit.pos, direction)
        cost = move_cost(unit, new_pos, self.obs)

        # if you don't have enough power for the first move
        if unit.power < cost:
            queue = []
            # find out how long to wait
            charges_needed = ((cost - unit.power) // recharge_rate) + 1
            for _ in range(charges_needed + 1):
                queue.append(unit.move(0))
            return queue

        # if you have enough power for the first move, queue it up
        queue = [unit.move(direction)]
        if len(queue) > 20:
            queue = queue[:20]
        return queue
