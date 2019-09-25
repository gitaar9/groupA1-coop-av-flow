from copy import deepcopy

import numpy as np
from gym.spaces import Box

from flow.core import rewards
from flow.envs import BottleneckEnv
from flow.envs.multiagent import MultiEnv

MAX_LANES = 4  # base number of largest number of lanes in the network
EDGE_LIST = ["1", "2", "3", "4", "5"]  # Edge 1 is before the toll booth
EDGE_BEFORE_TOLL = "1"  # Specifies which edge number is before toll booth
TB_TL_ID = "2"
EDGE_AFTER_TOLL = "2"  # Specifies which edge number is after toll booth
NUM_TOLL_LANES = MAX_LANES

TOLL_BOOTH_AREA = 10  # how far into the edge lane changing is disabled
RED_LIGHT_DIST = 50  # how close for the ramp meter to start going off

EDGE_BEFORE_RAMP_METER = "2"  # Specifies which edge is before ramp meter
EDGE_AFTER_RAMP_METER = "3"  # Specifies which edge is after ramp meter
NUM_RAMP_METERS = MAX_LANES

RAMP_METER_AREA = 80  # Area occupied by ramp meter

MEAN_NUM_SECONDS_WAIT_AT_FAST_TRACK = 3  # Average waiting time at fast track
MEAN_NUM_SECONDS_WAIT_AT_TOLL = 15  # Average waiting time at toll

BOTTLE_NECK_LEN = 280  # Length of bottleneck
NUM_VEHICLE_NORM = 20

# Keys for RL experiments
ADDITIONAL_RL_ENV_PARAMS = {
    # velocity to use in reward functions
    "target_velocity": 30,
    # if an RL vehicle exits, place it back at the front
    # "add_rl_if_exit": True,
}


class BottleneckMultiAgentEnv(MultiEnv, BottleneckEnv):
    """BottleneckMultiAgentEnv.

      Environment used to train vehicles to effectively pass through a
      bottleneck.

      States
          An observation is the edge position, speed, lane, and edge number of
          the AV, the distance to and velocity of the vehicles
          in front and behind the AV for all lanes.

      Actions
          The action space 1 value for acceleration and 1 for lane changing

      Rewards
          The reward is the average speed of the edge the agent is currently in combined with the speed of
          the agent. With some discount for lane changing.

      Termination
          A rollout is terminated once the time horizon is reached.
      """

    def __init__(self, env_params, sim_params, network, simulator='traci'):
        """Initialize BottleneckAccelEnv."""
        for p in ADDITIONAL_RL_ENV_PARAMS.keys():
            if p not in env_params.additional_params:
                raise KeyError(
                    'Environment parameter "{}" not supplied'.format(p))

        super().__init__(env_params, sim_params, network, simulator)
        # self.add_rl_if_exit = env_params.get_additional_param("add_rl_if_exit")
        # self.num_rl = deepcopy(self.initial_vehicles.num_rl_vehicles)
        # Following didn't work since simulation is initially empty (w/o vehicles,
        # which seems to be special case scenario):
        self.rl_id_list = deepcopy(self.initial_vehicles.get_rl_ids())
        # self.rl_id_list = [('rl_' + str(i)) for i in range(network.vehicles.num_rl_vehicles)]
        self.max_speed = self.k.network.max_speed()

    @property
    def observation_space(self):
        """See class definition."""
        num_obs = 4 * MAX_LANES * self.scaling + 4

        return Box(low=0, high=1, shape=(num_obs,), dtype=np.float32)

    def get_key(self, tpl):
        return tpl[0]

    def get_label(self, veh_id):
        if 'rl' in veh_id:
            return 2.
        elif 'human' in veh_id:
            return 1.
        return 0.

    def get_state(self):
        """See class definition."""
        obs = {}
        features_per_car = 3

        #for id_label in self.k.vehicle.get_ids():
        #    print('ID: ' + id_label + ' x: ' + str(self.k.vehicle.get_x_by_id(id_label)) + ' edge: ' +
        #          self.k.vehicle.get_edge(veh_id=id_label))

        for veh_id in self.k.vehicle.get_rl_ids():

            edge = self.k.vehicle.get_edge(veh_id)
            lane = self.k.vehicle.get_lane(veh_id)
            veh_x_pos = self.k.vehicle.get_x_by_id(veh_id)
                                                                        # Infos the car stores about itsels:
            self_representation = [veh_x_pos,                           # Car's current x-position along the road
                                   self.k.vehicle.get_speed(veh_id),    # Car's current speed
                                   self.k.network.speed_limit(edge)]    # How fast car may drive (reference value)

            print('Self:')
            print(self_representation)

            others_representation = []                                  # Representation of surrounding vehicles

            ### Headway ###

            if lane-1 < 0:
                # Lane to left/right does not exist. Pad values with -1.'s
                others_representation.extend([-1.] * features_per_car)

            # Sorted by lane index if not mistaken...
            leading_cars_dist = self.k.vehicle.get_lane_headways(veh_id, error=1000)
            leading_cars_ids = self.k.vehicle.get_lane_leaders(veh_id)
            leading_cars_labels = [self.get_label(leading_id) for leading_id in leading_cars_ids]
            leading_cars_speed = self.k.vehicle.get_speed(leading_cars_ids, error=1000)

            leading_cars_lanes = self.k.vehicle.get_lane(leading_cars_ids)

            headway_cars_map = list(zip(leading_cars_lanes,  # (lane1, (id, dist, speed)), (lane2, (id2,...))
                                        zip(leading_cars_labels,
                                            leading_cars_dist,
                                            leading_cars_speed)))

            print('Zipped headway:')
            print(headway_cars_map)

            for element in headway_cars_map:
                if element[0] in range(lane-1, lane+1):                 # If front-vehicles' lanes are inside range, add
                    others_representation.extend(element[1])            # Add [idX, distX, speedX] to representation
                elif element[0] == -1001:
                    # There's no car. Append by -1.'s
                    others_representation.extend([0., 1000, float(self.k.network.max_speed())])

            if lane+1 > self.k.network.num_lanes(edge):
                # Lane to left/right does not exist. Pad values with -1.'s
                others_representation.extend([-1.] * features_per_car)

            print('Headway ids:')
            print(leading_cars_ids)

            ### Tailway ###

            if lane - 1 < 0:
                # Lane to left/right does not exist. Pad values with -1.'s
                others_representation.extend([-1.] * features_per_car)

            # Sorted by lane index if not mistaken...
            following_cars_dist = self.k.vehicle.get_lane_tailways(veh_id, error=-1000)
            following_cars_ids = self.k.vehicle.get_lane_followers(veh_id)
            following_cars_labels = [self.get_label(following_id) for following_id in following_cars_ids]
            following_cars_speed = self.k.vehicle.get_speed(following_cars_ids, error=-1000)

            following_cars_lanes = self.k.vehicle.get_lane(following_cars_ids)

            tailway_cars_map = list(zip(following_cars_lanes,  # (lane1, (id, dist, speed)), (lane2, (id2,...))
                                        zip(following_cars_labels,
                                            following_cars_dist,
                                            following_cars_speed)))

            print('Zipped tailway:')
            print(tailway_cars_map)

            for element in tailway_cars_map:
                if element[0] in range(lane - 1, lane + 1):   # If front-vehicles' lanes are inside range, add
                    others_representation.extend(element[1])  # Add [idX, distX, speedX] to representation
                elif element[0] == -1001:
                    # There's no car. Append by -1.'s
                    others_representation.extend([0., 1000, float(self.k.network.max_speed())])

            if lane + 1 > self.k.network.num_lanes(edge):
                # Lane to left/right does not exist. Pad values with -1.'s
                others_representation.extend([-1.] * features_per_car)

            # FIXME: EXTEND ALL LANES TO others_... AND THEN CROP THE PART CENTERED AROUND THE OWN CARS LANE!
            #  IF 4 lanes and that results in 8 records, then crop to get the subset of records only centered
            #  around the own cars lane after concatennation. Otherwise, there's no indication where the absent car
            #  was supposed to be. Could be in any position, theoretically


            print('Following ids:')
            print(following_cars_ids)

            print('Others:')
            print(others_representation)

            # Merge two lists and transform to array
            self_representation.extend(others_representation)
            observation_arr = np.asarray(self_representation, dtype=float)

            print('Merged:')
            print(self_representation)

            print('Arr:')
            print(observation_arr)

            obs[veh_id] = observation_arr  # Assign representation about self and surrounding cars to car's observation

        print('#####STATES#####')
        print(obs)
        return obs

    def compute_reward(self, rl_actions, **kwargs):
        #"""See class definition."""
        return_rewards = {}
        # in the warmup steps, rl_actions is None
        #if rl_actions:
        #    # for rl_id, actions in rl_actions.items():
        #    for rl_id in self.k.vehicle.get_rl_ids():
        #
        #        reward = 0
        #        # If there is a collision all agents get no reward
        #        if not kwargs['fail']:
        #            # Reward desired velocity in own edge
        #            edge_num = self.k.vehicle.get_edge(rl_id)
        #            reward += rewards.desired_velocity(self, fail=kwargs['fail'], edge_list=[edge_num])
        #
        #            # Reward own speed
        #            reward += self.k.vehicle.get_speed(rl_id) * 0.1
        #
        #            # Punish own lane changing
        #            if rl_id in rl_actions:
        #                reward -= abs(rl_actions[rl_id][1])
        #
        #        return_rewards[rl_id] = reward

        """Outflow rate over last ten seconds normalized to max of 1."""

        # FIXME: change to RL outflow rate, later
        reward = self.k.vehicle.get_outflow_rate(10 * self.sim_step) / (2000.0 * self.scaling)
        print('Reward: ' + str(reward) + ' after ' + str(self.sim_step) + ' sim steps.')

        # This reward applies to all vehicles.
        # TODO: maybe augment reward computation later to introduce car-specific terms.
        #  Main focus shall remain RL outflow rate; Policies will be learned to reach that goal eventually
        for rl_id in self.k.vehicle.get_rl_ids():
            return_rewards[rl_id] = reward

        return return_rewards

    @property
    def action_space(self):
        """See class definition."""
        max_decel = self.env_params.additional_params["max_decel"]
        max_accel = self.env_params.additional_params["max_accel"]

        lb = [-abs(max_decel), -1]  # * self.num_rl
        ub = [max_accel, 1]  # * self.num_rl

        return Box(np.array(lb), np.array(ub), dtype=np.float32)

    def _apply_rl_actions(self, rl_actions):
        """
        See parent class.
        """

        """See class definition."""
        # in the warmup steps, rl_actions is None
        if rl_actions:
            for rl_id, actions in rl_actions.items():
                acceleration = actions[0]
                direction = actions[1]

                self.k.vehicle.apply_acceleration(rl_id, acceleration)

                if self.time_counter <= self.env_params.additional_params[
                    'lane_change_duration'] + self.k.vehicle.get_last_lc(rl_id):
                    # direction = round(np.random.normal(loc=direction, scale=0.2))  # Exploration rate of 0.2 is random
                    direction = max(-1, min(round(direction), 1))                 # Clamp between -1 and 1
                    self.k.vehicle.apply_lane_change(str(rl_id), direction)

    # def additional_command(self):
    #     """See parent class.
    #
    #     Define which vehicles are observed for visualization purposes.
    #     """
    #     super().additional_command()
    #     for rl_id in self.k.vehicle.get_rl_ids():
    #         # leader
    #         lead_id = self.k.vehicle.get_leader(rl_id)
    #         if lead_id:
    #             self.k.vehicle.set_observed(lead_id)
    #         # follower
    #         follow_id = self.k.vehicle.get_follower(rl_id)
    #         if follow_id:
    #             self.k.vehicle.set_observed(follow_id)

    # def additional_command(self):
    #     """Reintroduce any RL vehicle that may have exited in the last step.
    #
    #     This is used to maintain a constant number of RL vehicle in the system
    #     at all times, in order to comply with a fixed size observation and
    #     action space.
    #     """
    #     super().additional_command()
    #     # if the number of rl vehicles has decreased introduce it back in
    #     num_rl = self.k.vehicle.num_rl_vehicles
    #     if num_rl != len(self.rl_id_list) and self.add_rl_if_exit:
    #         # find the vehicles that have exited
    #         diff_list = list(
    #             set(self.rl_id_list).difference(self.k.vehicle.get_rl_ids()))
    #         for rl_id in diff_list:
    #             # distribute rl cars evenly over lanes
    #             lane_num = self.rl_id_list.index(rl_id) % \
    #                        MAX_LANES * self.scaling
    #             # reintroduce it at the start of the network
    #             try:
    #                 self.k.vehicle.add(
    #                     veh_id=rl_id,
    #                     edge='1',
    #                     type_id=str('rl'),
    #                     lane=str(lane_num),
    #                     pos="0",
    #                     speed="max")
    #             except Exception:
    #                 pass