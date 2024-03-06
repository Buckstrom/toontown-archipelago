import random
from typing import List, Any, Dict

from toontown.archipelago.apclient.ap_client_enums import APClientEnums
from toontown.archipelago.definitions import locations
from toontown.archipelago.definitions.util import ap_location_name_to_id
from toontown.archipelago.packets.serverbound.status_update_packet import StatusUpdatePacket
from toontown.archipelago.util.net_utils import NetworkPlayer, NetworkSlot, ClientStatus
from toontown.archipelago.packets.clientbound.clientbound_packet_base import ClientBoundPacketBase


# Sent to clients when the connection handshake is successfully completed.
class ConnectedPacket(ClientBoundPacketBase):

    def __init__(self, json_data):
        super().__init__(json_data)

        # Your team number. See NetworkPlayer for more info on team number.
        self.team: int = self.read_raw_field('team', ignore_missing=True)

        # Your slot number on your team. See NetworkPlayer for more info on the slot number.
        self.slot: int = self.read_raw_field('slot', ignore_missing=True)

        # List denoting other players in the multiworld, whether connected or not.
        self.players: List[NetworkPlayer] = self.read_raw_field('players', ignore_missing=True)

        # Contains ids of remaining locations that need to be checked. Useful for trackers, among other things.
        self.missing_locations: List[int] = self.read_raw_field('missing_locations', ignore_missing=True)

        # Contains ids of all locations that have been checked. Useful for trackers, among other things.
        # Location ids are in the range of ± 2^53-1.
        self.checked_locations: List[int] = self.read_raw_field('checked_locations', ignore_missing=True)

        # Contains a json object for slot related data, differs per game. Empty if not required.
        # Not present if slot_data in Connect is false.
        self.slot_data: Dict[str, Any] = self.read_raw_field('slot_data', ignore_missing=True)

        # maps each slot to a NetworkSlot information.
        self.slot_info: Dict[str, NetworkSlot] = self.read_raw_field('slot_info', ignore_missing=True)

        # Number of hint points that the current player has.
        self.hint_points: int = self.read_raw_field('hint_points', ignore_missing=True)

    def get_slot_info(self, slot: int) -> NetworkSlot:
        return self.slot_info[str(slot)]

    # Creates a dict mapping slot ID -> network slot object for later retrieval
    def update_client_slot_cache(self, client):

        # Clear the cache, and populate it
        client.slot = self.slot
        client.slot_id_to_slot_name.clear()
        for id_string, network_slot in self.slot_info.items():
            client.slot_id_to_slot_name[int(id_string)] = network_slot

    def handle_first_time_player(self, av):

        #  Reset stats
        av.newToon()

        # Set their max HP
        av.b_setMaxHp(self.slot_data.get('starting_hp', 15))
        av.b_setHp(av.getMaxHp())

        # Set their starting money
        av.b_setMoney(self.slot_data.get('starting_money', 50))

        # Set their starting gag xp multiplier
        av.b_setBaseGagSkillMultiplier(self.slot_data.get('starting_gag_xp_multiplier', 2))

    # Given the option defined in the YAML for RNG generation and the seed of the AP playthrough
    # Return a new modified seed based on what option was chosen in the YAML
    #     option_global = 0
    #     option_slot_name = 1
    #     option_unique = 2
    #     option_wild = 3
    def handle_seed_generation_type(self, av, seed, option):

        option_global = 0
        option_slot_name = 1
        option_unique = 2
        option_wild = 3

        # No change
        if option == option_global:
            return seed

        # Use slot name
        if option == option_slot_name:
            return f"{seed}-{self.get_slot_info(self.slot).name}"

        # Use Toon ID
        if option == option_unique:
            return f"{seed}-{av.doId}"

        # Make something up
        if option == option_wild:
            return random.randint(1, 2**32)

        # An incorrect value was given, default to global
        return self.handle_seed_generation_type(av, seed, option_global)

    def handle_yaml_settings(self, av):

        # Update the value used for seeding any RNG elements that we want to be consistent based on this AP seed
        new_seed = self.slot_data.get('seed', random.randint(1, 2**32))
        rng_option = self.slot_data.get('seed_generation_type', 'global')
        new_seed = self.handle_seed_generation_type(av, new_seed, rng_option)
        av.setSeed(new_seed)

    def handle(self, client):
        self.debug(f"Successfully connected to the Archipelago server as {self.get_slot_info(self.slot).name}"
              f" playing {self.get_slot_info(self.slot).game}")

        # Store any information in the cache that we may need later
        self.update_client_slot_cache(client)

        # We have a valid connection, set client state to connected
        client.state = APClientEnums.CONNECTED

        client.av.b_setName(client.slot_name)

        # Is this this toon's first time? If so reset the toon's stats and initialize their settings from their YAML
        if len(self.checked_locations) == 0:
            self.handle_first_time_player(client.av)

        self.handle_yaml_settings(client.av)

        # Send all checks that may have been obtained while disconnected
        toonCheckedLocations = client.av.getCheckedLocations()
        if len(toonCheckedLocations) > 0:
            client.av.archipelago_session.sync()

        # Tell AP we are playing
        status_packet = StatusUpdatePacket()
        status_packet.status = ClientStatus.CLIENT_GOAL if client.av.winConditionSatisfied() else ClientStatus.CLIENT_PLAYING
        client.send_packet(status_packet)

        # Scout some locations that we need to display
        client.av.scoutLocations(locations.SCOUTING_REQUIRED_LOCATIONS)

        # Login location rewarding
        new_game = ap_location_name_to_id(locations.STARTING_NEW_GAME_LOCATION)
        track_one_check = ap_location_name_to_id(locations.STARTING_TRACK_ONE_LOCATION)
        track_two_check = ap_location_name_to_id(locations.STARTING_TRACK_TWO_LOCATION)
        client.av.addCheckedLocation(new_game)
        client.av.addCheckedLocation(track_one_check)
        client.av.addCheckedLocation(track_two_check)
        client.av.hintPoints = self.hint_points