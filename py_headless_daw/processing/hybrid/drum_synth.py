from copy import copy, deepcopy
from dataclasses import dataclass
from typing import List, Optional, cast, Dict, Union

import numpy as np

from py_headless_daw.dsp_utils.drum_synth.drum_synth_generator import DrumSynthGenerator
from py_headless_daw.dsp_utils.drum_synth.drum_synth_generator_config import DrumSynthGeneratorConfig
from py_headless_daw.project.having_parameters import HavingParameters
from py_headless_daw.project.plugins.drum_synth_plugin import DrumSynthPlugin
from py_headless_daw.schema.dto.time_interval import TimeInterval
from py_headless_daw.schema.events.event import Event
from py_headless_daw.schema.events.midi_event import MidiEvent
from py_headless_daw.schema.events.parameter_value_event import ParameterValueEvent
from py_headless_daw.schema.processing_strategy import ProcessingStrategy


@dataclass
class Hit:
    data: np.ndarray
    start_sample_in_hit: int
    start_sample_in_buffer: int
    sample_length: int


class DrumSynth(ProcessingStrategy):
    MAX_HIT_LENGTH_SAMPLES = 44100 * 10

    def __init__(self, plugin: DrumSynthPlugin):

        self._cached_sound: Optional[np.ndarray] = None
        self._cached_sound_length_samples: int = 0

        self._unfinished_hits: List[Hit] = []

        # we will use this one as something that holds parameters,
        # tracks parameter changes, and generates generators
        self._plugin: DrumSynthPlugin = deepcopy(plugin)

        super().__init__()

    def render(self, interval: TimeInterval, stream_inputs: List[np.ndarray], stream_outputs: List[np.ndarray],
               event_inputs: List[List[Event]], event_outputs: List[List[Event]]):
        all_events = self._flatten_event_inputs(event_inputs)
        all_events = sorted(all_events, key=lambda event: event.sample_position)

        parameter_value_events = [e for e in all_events if isinstance(e, ParameterValueEvent)]
        parameter_value_events = cast(List[ParameterValueEvent], parameter_value_events)

        midi_on_events = [e for e in all_events if isinstance(e, MidiEvent) and e.is_note_on()]
        midi_on_events = cast(List[MidiEvent], midi_on_events)

        # next silence pc warnings in casts above
        # convert note ons to hits

        for event in all_events:
            if isinstance(event, ParameterValueEvent):
                self._cached_sound = None
                self._cached_sound_length_samples = 0

                event = cast(ParameterValueEvent, event)
                self._plugin.set_parameter_value(event.parameter_id, event.value)

        new_hits = self._convert_note_events_to_new_hits()

        all_hits = [*new_hits, *self._unfinished_hits]
        self._unfinished_hits = self._apply_hits_to_inputs(stream_inputs, all_hits)

    def _convert_events_to_new_hits(self, events) -> List[Hit]:
        pass

    def _apply_hits_to_inputs(self, stream_outputs: List[np.ndarray], hits: List[Hit]) -> List[Hit]:
        """
        renders all hits to outputs, returns a list hits to be passed to the next iteration

        :param stream_outputs: List of buffers to apply hits to
        :param hits: all hits to apply to current buffers
        :returns: a list of hits to be dealt with on the next iteration
        :rtype: List[Hit]
        """

        hits_for_next_iteration: List[Hit] = []

        for hit in hits:
            for stream_output in stream_outputs:
                hit_for_next = self._apply_one_hit_to_one_output(hit, stream_output)
                if hit_for_next is not None:
                    hits_for_next_iteration.append(hit_for_next)

        return hits_for_next_iteration

    def _apply_one_hit_to_one_output(self, hit: Hit, stream_output: np.ndarray) -> Optional[Hit]:

        buffer_length = len(stream_output)
        remaining_samples_in_hit = hit.sample_length - hit.start_sample_in_hit
        patch_start_in_output = hit.start_sample_in_buffer
        patch_end_in_output = min(buffer_length, patch_start_in_output + remaining_samples_in_hit)

        patch_length = patch_end_in_output - patch_start_in_output

        patch_start_in_hit = hit.start_sample_in_hit
        patch_end_in_hit = patch_start_in_hit + patch_length

        patched = stream_output[patch_start_in_output:patch_end_in_output]
        patch = hit.data[patch_start_in_hit:patch_end_in_hit]

        np.add(patch, patched, out=patched)

        if patch_start_in_hit + patch_length < hit.sample_length:
            res = copy(hit)
            res.start_sample_in_hit += patch_length
            return res
        else:
            return None

    def _initialize_cache(self):
        # synthesize data to cached_sound
        # detect how long the actual data is until it reliable drops to zero
        self._cached_sound_length_samples = 44100 * 2
        pass

    def _apply_parameter_changes(self, values: Dict[str, Union[str, float]]):
        if len(values):
            self._cached_sound = None
