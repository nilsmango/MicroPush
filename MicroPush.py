# MicroPush

from __future__ import with_statement
import Live
from _Framework.ControlSurface import ControlSurface
from _Framework.MixerComponent import MixerComponent
from _Framework.TransportComponent import TransportComponent
from _Framework.SessionComponent import SessionComponent
from _Framework.EncoderElement import *
from _Framework.ButtonElement import ButtonElement
from _Framework.SliderElement import SliderElement
from _Framework.InputControlElement import MIDI_NOTE_TYPE, MIDI_NOTE_ON_STATUS, MIDI_NOTE_OFF_STATUS, MIDI_CC_TYPE
from _Framework.DeviceComponent import DeviceComponent
from ableton.v2.base import listens, liveobj_valid, liveobj_changed
import time, threading


mixer, transport, session_component = None, None, None
quantize_grid_value = 5
quantize_strength_value = 1.0
swing_amount_value = 0.0



class MicroPush(ControlSurface):

    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        with self.component_guard():
            global mixer
            global transport
            global session_component
            track_count = 8
            return_count = 24  # Maximum of 12 Sends and 12 Returns
            mixer = MixerComponent(track_count, return_count)
            transport = TransportComponent()
            session_component = SessionComponent()
            # set up undo redo
            self._last_can_redo = self.song().can_redo
            self._last_can_undo = self.song().can_undo
            self._setup_undo_redo()
            self._initialize_buttons()
            self._update_mixer_and_tracks()
            self._set_selected_track_implicit_arm()
            self._send_selected_track_index(self.song().view.selected_track)
            self._on_selected_track_changed.subject = self.song().view
            # track = self.song().view.selected_track
            # track.view.add_selected_device_listener(self._on_selected_device_changed)
            self.song().add_tracks_listener(self._on_tracks_changed)  # hier für return tracks: .add_return_tracks_listener()
            # self.song().view.add_selected_scene_listener(self._on_selected_scene_changed)
            self._setup_device_control()
            self._register_clip_listeners()
            self._periodic_execution()


    # def _on_selected_device_changed(self):
    #     self.log_message("device changed!!")

    def _setup_device_control(self):
        self._device = DeviceComponent()
        self._device.name = 'Device_Component'
        device_controls = []
        for index in range(8):
            control = EncoderElement(MIDI_CC_TYPE, index, 20, Live.MidiMap.MapMode.absolute)
            control.name = 'Ctrl_' + str(index)
            device_controls.append(control)
        self._device.set_parameter_controls(device_controls)
        nav_left_button = ButtonElement(1, MIDI_CC_TYPE, 0, 33)
        nav_right_button = ButtonElement(1, MIDI_CC_TYPE, 0, 32)
        self._device.set_bank_nav_buttons(nav_left_button, nav_right_button)
        self._on_device_changed.subject = self._device
        self.set_device_component(self._device)
        # Register button listeners for navigation buttons
        nav_left_button.add_value_listener(self._on_nav_button_pressed)
        nav_right_button.add_value_listener(self._on_nav_button_pressed)



    def _on_nav_button_pressed(self, value):
        if value:
            self._on_device_changed()

    @subject_slot('device')
    def _on_device_changed(self):
        if liveobj_valid(self._device):
            device = self._device.device()  # Retrieve the Device object
            # get and send name of bank and device
            selected_track = self.song().view.selected_track
            selected_device = selected_track.view.selected_device
            # device_name = selected_device.name
            available_devices = selected_track.devices
            # find index of device
            selected_device_index = self._find_device_index(selected_device, available_devices)
            # self.log_message("Selected Device Index: {}".format(selected_device_index))
            # bank names etc.
            bank_name = self._device._bank_name
            bank_names_list = ','.join(str(name) for name in self._device._parameter_bank_names())
            # sending sysex of bank name, device name, bank names
            self._send_sys_ex_message(bank_name, 0x6D)
            self._send_sys_ex_message(bank_names_list, 0x5D)
            # sending the index instead of name for device.
            self._send_sys_ex_message(selected_device_index, 0x4D)
            # Get all available devices of the selected track
            available_devices = [device.name for device in selected_track.devices]
            available_devices_string = ','.join(available_devices)
            # self.log_message("devices: {}".format(available_devices))
            self._send_sys_ex_message(available_devices_string, 0x01)

            if hasattr(device, 'parameters') and device.parameters:
                # TODO: make this prettier!
                parameter_names = [control.mapped_parameter().name if control.mapped_parameter() else ""
                                for control in self._device._parameter_controls]
                parameter_names = [name for name in parameter_names if name]  # Remove empty names
                if parameter_names:
                    # self.log_message("Parameter Names: {}".format(parameter_names))
                    # send a MIDI SysEx message with the names
                    self._send_parameter_names(parameter_names)
                else:
                    self.log_message("No parameter names found in the device controls.")
            else:
                self.log_message("Device has no parameters.")
        else:
            self.log_message("Invalid device.")

    def _find_device_index(self, device, device_list):
        for index, d in enumerate(device_list):
            if device == d:
                return str(index)
        return "not found"  # Device not found

    def _send_parameter_names(self, parameter_names):
        name_string = ','.join(parameter_names)
        self._send_sys_ex_message(name_string, 0x7D)

    def _send_sys_ex_message(self, name_string, manufacturer_id):
        status_byte = 0xF0  # SysEx message start
        # parameter names: 0x7D, bank name: 0x6D
        device_id = 0x01  
        data = name_string.encode('ascii')
        end_byte = 0xF7  # SysEx message end
        sys_ex_message = (status_byte, manufacturer_id, device_id) + tuple(data) + (end_byte, )
        self._send_midi(sys_ex_message)

    def _initialize_buttons(self):
        transport.set_play_button(ButtonElement(1, MIDI_CC_TYPE, 0, 118))
        transport.set_stop_button(ButtonElement(1, MIDI_CC_TYPE, 0, 117))
        transport.set_metronome_button(ButtonElement(1, MIDI_CC_TYPE, 0, 58))
        session_component.set_stop_all_clips_button(ButtonElement(1, MIDI_NOTE_TYPE, 15, 96))
        capture_button = ButtonElement(True, MIDI_NOTE_TYPE, 15, 100)
        capture_button.add_value_listener(self._capture_button_value)
        quantize_button = ButtonElement(True, MIDI_NOTE_TYPE, 15, 99)
        quantize_button.add_value_listener(self._quantize_button_value)
        # duplicate the active clip to a free slot
        duplicate_button = ButtonElement(True, MIDI_NOTE_TYPE, 15, 98)
        duplicate_button.add_value_listener(self._duplicate_button_value)
        # duplicate scene
        duplicate_scene_button = ButtonElement(True, MIDI_NOTE_TYPE, 15, 95)
        duplicate_scene_button.add_value_listener(self._duplicate_scene_button_value)
        # a session recording button
        sesh_record_button = ButtonElement(1, MIDI_CC_TYPE, 0, 119)
        sesh_record_button.add_value_listener(self._sesh_record_value)
        # quantize grid size button
        quantize_grid_button = ButtonElement(1, MIDI_CC_TYPE, 1, 0)
        quantize_grid_button.add_value_listener(self._quantize_grid_value)
        # quantize strength
        quantize_strength_button = ButtonElement(1, MIDI_CC_TYPE, 1, 1)
        quantize_strength_button.add_value_listener(self._quantize_strength_value)
        # swing percentage button
        swing_amount_button = ButtonElement(1, MIDI_CC_TYPE, 1, 2)
        swing_amount_button.add_value_listener(self._swing_amount_value)
        # # periodic check
        # periodic_check_button = ButtonElement(1, MIDI_NOTE_TYPE, 15, 97)
        # periodic_check_button.add_value_listener(self._periodic_check)
        # redo button
        redo_button = ButtonElement(1, MIDI_NOTE_TYPE, 15, 102)
        redo_button.add_value_listener(self._redo_button_value)
        # undo button
        undo_button = ButtonElement(1, MIDI_NOTE_TYPE, 15, 101)
        undo_button.add_value_listener(self._undo_button_value)
        # device selection
        device_selection_button = ButtonElement(1, MIDI_CC_TYPE, 1, 3)
        device_selection_button.add_value_listener(self._select_device_by_index)
        # track selection
        track_selection_button = ButtonElement(1, MIDI_CC_TYPE, 1, 4)
        track_selection_button.add_value_listener(self._select_track_by_index)
        # return and master track selection
        return_track_selection_button = ButtonElement(1, MIDI_CC_TYPE, 1, 5)
        return_track_selection_button.add_value_listener(self._select_return_track_by_index)
        # scene launch
        scene_launch_button = ButtonElement(1, MIDI_CC_TYPE, 1, 14)
        scene_launch_button.add_value_listener(self._fire_scene)
        # clip / scene select
        clip_scene_select_button = ButtonElement(1, MIDI_CC_TYPE, 1, 15)
        clip_scene_select_button.add_value_listener(self._select_clip_scene)
        # scene delete
        scene_delete_button = ButtonElement(1, MIDI_CC_TYPE, 1, 16)
        scene_delete_button.add_value_listener(self._delete_scene)

    def _setup_undo_redo(self):
        can_redo = self.song().can_redo
        can_undo = self.song().can_undo
        if can_redo:
            midi_event_bytes = (0x90 | 0x02, 0x02, 0x64)
            self._send_midi(midi_event_bytes)
        if can_undo:
            midi_event_bytes = (0x80 | 0x02, 0x02, 0x64)
            self._send_midi(midi_event_bytes)

    def _periodic_execution(self):
        # Do something here
        self._periodic_check()
        threading.Timer(0.3, self._periodic_execution).start()

    def _periodic_check(self):
        self._update_clip_slots()
        can_redo = self.song().can_redo
        can_undo = self.song().can_undo
        if can_redo != self._last_can_redo:
            self._last_can_redo = can_redo
            if can_redo:
                midi_event_bytes = (0x90 | 0x02, 0x02, 0x64)
                self._send_midi(midi_event_bytes)
            else:
                midi_event_bytes = (0x80 | 0x02, 0x02, 0x64)
                self._send_midi(midi_event_bytes)

        if can_undo != self._last_can_undo:
            self._last_can_undo = can_undo
            if can_undo:
                midi_event_bytes = (0x90 | 0x02, 0x00, 0x64)
                self._send_midi(midi_event_bytes)
            else:
                midi_event_bytes = (0x80 | 0x02, 0x00, 0x64)
                self._send_midi(midi_event_bytes)
        # TODO: clean this whole thing up, never works I think and the next line is for what?
        self.first_periodic_check = False

    def _redo_button_value(self, value):
        if value != 0:
            song = self.song()
            if song.can_redo:
                song.redo()
                # self._periodic_check()

    def _undo_button_value(self, value):
        if value != 0:
            song = self.song()
            if song.can_undo:
                song.undo()
                # self._periodic_check()

    def _sesh_record_value(self, value):
        if value != 0:
            record = self.song().session_record
            if record == False:
                self.song().session_record = True
            else:
                self.song().session_record = False

    def _capture_button_value(self, value):
        if value != 0:
            self.song().capture_midi()

    def _quantize_grid_value(self, value):
        global quantize_grid_value
        quantize_grid_value = value

    def _quantize_strength_value(self, value):
        global quantize_strength_value
        quantize_strength_value = value / 100.0

    def _swing_amount_value(self, value):
        global swing_amount_value
        # 100% swing amount did strange things, so I went down to 10% max
        swing_amount_value = value / 1000.0

    def _quantize_button_value(self, value):
        if value != 0:
            clip = self.song().view.detail_clip
            if clip:
                # need to set the swing amount first (0.00-1.00)
                self.song().swing_amount = swing_amount_value
                # grid (int 1 == 1/4, 2 == 1/8, 5 == 1/16, 8 = 1/32), strength (0.50 == 50%)
                clip.quantize(quantize_grid_value, quantize_strength_value)

    def _duplicate_button_value(self, value):
        if value != 0:
            self._duplicate_clip()
    
    def _duplicate_scene_button_value(self, value):
        if value != 0:
            song = self.song()
            selected_scene = song.view.selected_scene
            all_scenes = song.scenes
            current_index = list(all_scenes).index(selected_scene)
            song.duplicate_scene(current_index)

    def _duplicate_clip(self):
        selected_track = self.song().view.selected_track

        if selected_track is None:
            return

        song = self.song()
        selected_scene = song.view.selected_scene
        all_scenes = song.scenes
        current_index = list(all_scenes).index(selected_scene)

        duplicated_id = selected_track.duplicate_clip_slot(current_index)

        duplicated_slot = self.song().scenes[duplicated_id]

        if self.song().view.highlighted_clip_slot.is_playing:
            # move to the duplicated clip_slot
            self.song().view.selected_scene = duplicated_slot

            if not self.song().view.highlighted_clip_slot.is_playing:
                # force legato ensures that the playing-position of the duplicated
                # loop is continued from the previous clip
                self.song().view.highlighted_clip_slot.fire(force_legato=True)
        else:
            self.song().view.selected_scene = duplicated_slot

    @subject_slot('selected_track')
    def _on_selected_track_changed(self):
        selected_track = self.song().view.selected_track
        if selected_track and selected_track.has_midi_input:
            self._set_selected_track_implicit_arm()
        self._set_other_tracks_implicit_arm()
        # send new index of selected track
        self._send_selected_track_index(selected_track)
        self._on_selected_scene_changed()
        # TODO: this part doesn't seem to work? how can I make this work with master and return?
        device_to_select = selected_track.view.selected_device
        if device_to_select == None and len(selected_track.devices) > 0:
            device_to_select = selected_track.devices[0]
        if device_to_select != None:
            self.song().view.select_device(device_to_select)
        self._device_component.set_device(device_to_select)

    def _send_selected_track_index(self, selected_track):
        track_list = self.song().tracks
        track_index = self._find_track_index(selected_track, track_list)
        self._send_sys_ex_message(track_index, 0x03)
        if track_index == "not found":
            return_tracks_list = self.song().return_tracks
            return_track_index = self._find_track_index(selected_track, return_tracks_list)
            if return_track_index == "not found":
                return_track_index = str(len(return_tracks_list))
            self._send_sys_ex_message(return_track_index, 0x08)
        else:
            self._send_sys_ex_message("none selected", 0x08)

    def _find_track_index(self, track, track_list):
        for index, t in enumerate(track_list):
            if track == t:
                return str(index)
        return "not found" # Track not found

    def _select_device_by_index(self, value):
        # self.log_message("Setting new device Index: {}".format(value))
        device_to_select = self.song().view.selected_track.devices[value]
        self.song().view.select_device(device_to_select)

    def _select_track_by_index(self, track_index):
        # self.log_message("Getting track: {}".format(track_index))
        song = self.song()
        if track_index >= 0 and track_index < len(song.tracks):
            song.view.selected_track = song.tracks[track_index]
        else:
            self.log_message("Invalid track index: {}".format(track_index))

    def _select_return_track_by_index(self, track_index):
        song = self.song()
        if track_index < len(song.return_tracks):
            return_track = song.return_tracks[track_index] 
            song.view.selected_track = return_track
        else:
            master_track = song.master_track 
            song.view.selected_track = master_track

    def _set_selected_track_implicit_arm(self):
        selected_track = self.song().view.selected_track
        if selected_track:
            selected_track.implicit_arm = True
        else:
            self.song().tracks[0].implicit_arm = True

    def _set_other_tracks_implicit_arm(self):
        for track in self.song().tracks:
            if track != self.song().view.selected_track:
                track.implicit_arm = False

    def _on_tracks_changed(self):
        self._update_mixer_and_tracks()
        self._register_clip_listeners()
        self._update_clip_slots()

    # Updating names and number of tracks
    def _update_mixer_and_tracks(self):
        # tracks = self.song().tracks
        # # send track names
        # track_names = ",".join([track.name for track in tracks])
        # self._send_sys_ex_message(track_names, 0x02)
        track_names = []
        track_colors = []

        for track in self.song().tracks:
            # track names
            track_names.append(track.name)

            # track colors
            color = track.color
            red = (color >> 16) & 255
            green = (color >> 8) & 255
            blue = color & 255
            color_string = "({},{},{})".format(red, green, blue)
            track_colors.append(color_string)


        # send track names
        track_names_string = ",".join(track_names)
        self._send_sys_ex_message(track_names_string, 0x02)

        # send track colors
        track_colors_string = "-".join(track_colors)
        self._send_sys_ex_message(track_colors_string, 0x04)

        return_track_names = []
        return_track_colors = []
        
        for return_track in self.song().return_tracks:
            return_track_names.append(return_track.name)

            color = return_track.color
            red = (color >> 16) & 255
            green = (color >> 8) & 255
            blue = color & 255
            color_string = "({},{},{})".format(red, green, blue)
            return_track_colors.append(color_string)
        
        # add master track color to the mix:
        master_track = self.song().master_track
        master_channel_color = master_track.color
        red = (master_channel_color >> 16) & 255
        green = (master_channel_color >> 8) & 255
        blue = master_channel_color & 255
        color_string = "({},{},{})".format(red, green, blue)
        return_track_colors.append(color_string)

        # send return track names
        return_track_names_string = ",".join(return_track_names)
        self._send_sys_ex_message(return_track_names_string, 0x06)

        # send return track colors + master track
        track_colors_string = "-".join(return_track_colors)
        self._send_sys_ex_message(track_colors_string, 0x07)


        # Channels
        for index, track in enumerate(self.song().tracks):
            strip = mixer.channel_strip(index)
            
            # Configure strip controls for each channel track
            
            # VolumeSlider control
            volume_slider = SliderElement(MIDI_CC_TYPE, index, 7)  # Replace 7 with the appropriate MIDI CC number for volume control
            strip.set_volume_control(volume_slider)
            
            # Send1Knob control
            send1_knob = EncoderElement(MIDI_CC_TYPE, index, 40, Live.MidiMap.MapMode.absolute)
            
            
            # Send2Knob control
            send2_knob = EncoderElement(MIDI_CC_TYPE, index, 41, Live.MidiMap.MapMode.absolute)  # Replace 48 with the appropriate MIDI CC number for Send B control
            strip.set_send_controls((send1_knob, send2_knob,))
            
            # Pan
            pan_knob = EncoderElement(MIDI_CC_TYPE, index, 42, Live.MidiMap.MapMode.absolute)
            strip.set_pan_control(pan_knob)

            # TrackMuteButton control
            mute_button = ButtonElement(1, MIDI_CC_TYPE, index, 44)
            strip.set_mute_button(mute_button)

            # Solo button control
            solo_button = ButtonElement(1, MIDI_CC_TYPE, index, 43)
            strip.set_solo_button(solo_button)
            
            # Other strip controls can be configured similarly
            # strip.set_arm_button(...)
            # strip.set_shift_button(...)
        
        # Master / channel 7 cc 127
        mixer.master_strip().set_volume_control(SliderElement(MIDI_CC_TYPE, 0, 127))
        mixer.set_prehear_volume_control(EncoderElement(MIDI_CC_TYPE, 0, 126, Live.MidiMap.MapMode.absolute))
        mixer.master_strip().set_pan_control(EncoderElement(MIDI_CC_TYPE, 0, 125, Live.MidiMap.MapMode.absolute))

        # Return Tracks
        for index, returnTrack in enumerate(self.song().return_tracks):
            strip = mixer.return_strip(index)

            # VolumeSlider
            return_volume_slider = SliderElement(MIDI_CC_TYPE, index, 8)  # Replace 7 with the appropriate MIDI CC number for return track volume control
            strip.set_volume_control(return_volume_slider)

            # TrackMuteButton control
            mute_button = ButtonElement(1, MIDI_CC_TYPE, index, 10)
            strip.set_mute_button(mute_button)

            # Solo button control
            solo_button = ButtonElement(1, MIDI_CC_TYPE, index, 9)
            strip.set_solo_button(solo_button)

            # Send1Knob control
            send1_knob = EncoderElement(MIDI_CC_TYPE, index, 11, Live.MidiMap.MapMode.absolute)  # Replace 40 with the appropriate MIDI CC number for Send A control
            
            
            # Send2Knob control
            send2_knob = EncoderElement(MIDI_CC_TYPE, index, 12, Live.MidiMap.MapMode.absolute)  # Replace 48 with the appropriate MIDI CC number for Send B control
            strip.set_send_controls((send1_knob, send2_knob,))

            # Pan
            pan_knob = EncoderElement(MIDI_CC_TYPE, index, 13, Live.MidiMap.MapMode.absolute)
            strip.set_pan_control(pan_knob)

    # clipSlots
    def _register_clip_listeners(self):
        for track in self.song().tracks:
            for clip_slot in track.clip_slots:

                if clip_slot == None:
                    continue
                # do this to ignore return-tracks
                # if not clip_slot.has_stop_button:
                #     continue

                if not clip_slot.has_clip_has_listener(self._on_clip_has_clip_changed):
                    clip_slot.add_has_clip_listener(self._on_clip_has_clip_changed)

                if not clip_slot.is_triggered_has_listener(self._on_clip_playing_status_changed):
                    clip_slot.add_is_triggered_listener(self._on_clip_playing_status_changed)

                # if clip_slot.has_clip:
                #     if not clip_slot.clip.playing_position_has_listener(self._on_playing_position_changed):
                #         clip_slot.clip.add_playing_position_listener(self._on_playing_position_changed)


                                #     # if not clip_slot.playing_status_has_listener(self._on_clip_playing_status_changed):
                #     #     # self.log_message("adding a playing status listener")
                #     #     clip_slot.clip.add_playing_status_listener(self._on_clip_playing_status_changed)
 

    def _unregister_clip_listeners(self):
        for track in self.song().tracks:
            for clip_slot in track.clip_slots:
                clip_slot.remove_is_triggered_listener(self._on_clip_playing_status_changed)
                clip_slot.remove_has_clip_listener(self._on_clip_has_clip_changed)
                # if clip_slot.has_clip:
                #     # clip_slot.clip.remove_playing_status_listener(self._on_clip_playing_status_changed)
                #     clip_slot.clip.remove_playing_position_listener(self._on_playing_position_changed)

    # def _on_playing_position_changed(self):
    #     # self.log_message("trying to log the playing position")
    #     self._update_clip_slots()

    def _on_clip_playing_status_changed(self):
        # self.log_message("clip playing status changed")
        self._update_clip_slots()

    def _on_clip_has_clip_changed(self):
        # self.log_message("has clip status changed")
        self._update_clip_slots()

    def _update_clip_slots(self):
        track_clips = []

        for track in self.song().tracks:
            # track clip slots
            clip_slots = []
            for clip_slot in track.clip_slots:
                clip_data = {
                    'hasClip': clip_slot.has_clip,
                    'isPlaying': clip_slot.is_playing,
                    'isRecording': clip_slot.is_recording,
                    'isTriggered': clip_slot.is_triggered
                }
                has_clip_value = 1 if clip_data['hasClip'] else 0
                is_playing_value = 1 if clip_data['isPlaying'] else 0
                is_recording_value = 1 if clip_data['isRecording'] else 0
                is_triggered_value = 1 if clip_data['isTriggered'] else 0
                # if clip_slot.has_clip:
                #     playing_position = clip_slot.clip.playing_position
                #     length = clip_slot.clip.length
                #     self.log_message("playing: {} triggering {}".format(is_playing_value, is_triggered_value))
                # else:
                #     playing_position = 0.0
                #     length = 0.0
                
                clip_string = "{}{}{}{}".format(has_clip_value, is_playing_value, is_recording_value, is_triggered_value)
                clip_slots.append(clip_string)
            clip_slots_string = "-".join(clip_slots)
            track_clips.append(clip_slots_string)
            
        # send track clips
        track_clips_string = "/".join(track_clips)
        self._send_sys_ex_message(track_clips_string, 0x05)

    def handle_sysex(self, message):
        # start stop clip
        if len(message) >= 2 and message[1] == 9:
            values = self.extract_values_from_sysex_message(message)
            if len(values) == 3:
                self._fire_clip(values[0], values[1], values[2])
        # delete clip
        if len(message) >= 2 and message[1] == 10:
            values = self.extract_values_from_sysex_message(message)
            if len(values) == 2:
                self._delete_clip(values[0], values[1])
        # copy paste clip
        if len(message) >= 2 and message[1] == 11:
            values = self.extract_values_from_sysex_message(message)
            if len(values) == 4:
                self._copy_paste_clip(values[0], values[1], values[2], values[3])



    def extract_values_from_sysex_message(self, message):
        # Extract the values from the SysEx message based on the message format
        # Replace this with your own logic to extract the desired values
        # For example, if your message is [0xF0, 0x09, value1, value2, ..., 0xF7]
        # you can extract values starting from index 2: values = message[2:-1]
        values = message[2:-1]
        return values

    def _fire_clip(self, fire, track_index, clip_index):
        track = self.song().tracks[track_index]
        clip_slot = track.clip_slots[clip_index]
        if fire == 1:
            if clip_slot.is_playing:
                clip_slot.stop()
            else:
                clip_slot.set_fire_button_state(1)
        # else:
            # create new clip

    def _delete_clip(self, track_index, clip_index):
        track = self.song().tracks[track_index]
        clip_slot = track.clip_slots[clip_index]
        clip_slot.delete_clip()

    def _copy_paste_clip(self, from_track, from_clip, to_track, to_clip):
        tracks = self.song().tracks

        copy_track = tracks[from_track]
        copy_clip_slot = copy_track.clip_slots[from_clip]

        paste_track = tracks[to_track]
        paste_clip_slot = paste_track.clip_slots[to_clip]

        copy_clip_slot.duplicate_clip_to(paste_clip_slot)

    def _fire_scene(self, value):
        scenes = self.song().scenes
        if value < len(scenes):
            scene = scenes[value]
            scene.fire()

    def _select_clip_scene(self, value):
        scenes = self.song().scenes
        if value < len(scenes):
            self.song().view.selected_scene = scenes[value]
        track = self.song().view.selected_track
        if value < len(track.clip_slots):
            self.song().view.highlighted_clip_slot = track.clip_slots[value]
        self._send_selected_clip_slot(value)

    def _delete_scene(self, value):
        self.song().delete_scene(value)

    def _on_selected_scene_changed(self):
        selected_scene = self.song().view.selected_scene
        scenes_list = self.song().scenes
        new_index = self._find_track_index(selected_scene, scenes_list)
        self._send_selected_clip_slot(new_index)

    def _send_selected_clip_slot(self, clip_index):
        self._send_sys_ex_message(str(clip_index), 0x10)

    def disconnect(self):
        capture_button.remove_value_listener(self._capture_button_value)
        quantize_button.remove_value_listener(self._quantize_button_value)
        duplicate_button.remove_value_listener(self._duplicate_button_value)
        duplicate_scene_button.remove_value_listener(self._duplicate_scene_button_value)
        sesh_record_button.remove_value_listener(self._sesh_record_value)
        redo_button.remove_value_listener(self._redo_button_value)
        undo_button.remove_value_listener(self._undo_button_value)
        # periodic_check_button.remove_value_listener(self._periodic_check)
        self.song().remove_tracks_listener(self._on_tracks_changed)
        # self.song().view.remove_selected_track_listener(self._on_selected_track_changed)
        self._unregister_clip_listeners()
        self.remove_midi_listener(self._midi_listener)
        # self.song().view.remove_selected_scene_listener(self._on_selected_scene_changed)
        super(MicroPush, self).disconnect()
