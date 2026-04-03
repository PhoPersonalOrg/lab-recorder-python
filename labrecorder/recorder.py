"""
Main Lab Recorder class.
"""

import os
import threading
import time
from typing import Dict, List, Optional

import pylsl

from .remote_control import RemoteControlServer
from .streams import AcquisitionManager, StreamManager
from .utils import Config
from .xdf import SimpleXDFWriter


class LabRecorder:
    """
    Main Lab Recorder class that coordinates all recording functionality.
    """

    def __init__(self, filename: str = "recording.xdf", enable_remote_control: bool = True, remote_control_port: int = 22345, config_file: Optional[str] = None):
        self.config = Config(config_file)
        self.filename = filename
        self.is_recording_flag = False
        self.xdf_writer: Optional[SimpleXDFWriter] = None

        self.stream_manager = StreamManager()
        self.acquisition_manager = AcquisitionManager(
            self._on_data_received,
            clock_reset_callback=self._on_clock_reset,
            max_samples_per_pull=self.config.get("recording.max_samples_per_pull", None),
        )

        self._data_buffers: Dict[str, List] = {}
        self.buffer_lock = threading.Lock()
        self.writer_thread: Optional[threading.Thread] = None
        self.boundary_thread: Optional[threading.Thread] = None
        self.watch_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()

        cfg_enable = self.config.get("remote_control.enabled", None)
        cfg_port = self.config.get("remote_control.port", None)
        final_enable_remote = bool(cfg_enable) if cfg_enable is not None else enable_remote_control
        final_remote_port = int(cfg_port) if cfg_port is not None else remote_control_port

        cfg_filename = self.config.get("filename", None)
        if (filename is None or filename == "recording.xdf") and cfg_filename:
            self.filename = cfg_filename

        self.remote_control_server: Optional[RemoteControlServer] = None
        if final_enable_remote:
            self.remote_control_server = RemoteControlServer(self, final_remote_port)

        self.stream_inlets: Dict[str, pylsl.StreamInlet] = {}
        self.stream_ids: Dict[str, int] = {}
        self.offset_threads: Dict[str, threading.Thread] = {}
        self.offset_stop_events: Dict[str, threading.Event] = {}


    def find_streams(self, timeout: float = 2.0) -> List[pylsl.StreamInfo]:
        return self.stream_manager.find_streams(timeout)


    def select_streams_to_record(self, stream_uids: List[str]) -> None:
        self.stream_manager.select_streams(stream_uids)


    def start_recording(self, filename: Optional[str] = None, streams: Optional[List[pylsl.StreamInfo]] = None) -> None:
        if self.is_recording_flag:
            raise RuntimeError("Recording is already in progress")

        selected_streams = self._resolve_selected_streams(streams)
        if not selected_streams:
            raise RuntimeError("No streams selected for recording")

        if filename is not None:
            self.filename = filename

        self._ensure_output_directory()
        self._reset_runtime_state()
        self.xdf_writer = SimpleXDFWriter(self.filename)
        self.xdf_writer.open()
        print(f"XDF file {self.filename} opened for writing.")

        self._setup_recording_streams(selected_streams)

        self.is_recording_flag = True
        self.shutdown_event.clear()
        for stream_uid in list(self.stream_inlets.keys()):
            self._start_offset_thread(stream_uid)

        self.acquisition_manager.start_all()
        self.writer_thread = threading.Thread(target=self._writer_thread_func, daemon=True)
        self.writer_thread.start()
        self.boundary_thread = threading.Thread(target=self._boundary_thread_func, daemon=True)
        self.boundary_thread.start()

        if self.config.get("streams.watch_for_new_streams", True):
            self.watch_thread = threading.Thread(target=self._watch_thread_func, daemon=True)
            self.watch_thread.start()

        print(f"Recording started for {len(self.stream_inlets)} streams.")


    def stop_recording(self) -> None:
        if not self.is_recording_flag:
            return

        print("Stopping recording...")
        self.is_recording_flag = False
        self.shutdown_event.set()
        self._stop_background_threads()
        self.acquisition_manager.stop_all()

        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=10.0)

        for uid, inlet in list(self.stream_inlets.items()):
            try:
                inlet.close_stream()
                print(f"Closed LSL inlet for stream {uid}.")
            except Exception as e:
                print(f"Error closing inlet for stream {uid}: {e}")

        if self.xdf_writer:
            for uid in list(self.stream_ids.keys()):
                try:
                    self.xdf_writer.write_stream_footer(uid)
                except Exception as e:
                    print(f"Error writing footer for stream {uid}: {e}")
            self.xdf_writer.close()
            self.xdf_writer = None

        self._reset_runtime_state()
        print(f"Recording saved to {self.filename}")


    def start_remote_control_server(self) -> bool:
        if self.remote_control_server:
            return self.remote_control_server.start()
        return False


    def stop_remote_control_server(self) -> None:
        if self.remote_control_server:
            self.remote_control_server.stop()


    def cleanup(self) -> None:
        if self.is_recording_flag:
            self.stop_recording()
        self.stop_remote_control_server()


    def is_recording(self) -> bool:
        return self.is_recording_flag


    def has_selected_streams(self) -> bool:
        return bool(self.stream_manager.selected_stream_uids)


    def select_all_streams(self) -> int:
        count = self.stream_manager.select_all_streams()
        if self.is_recording_flag:
            self._attach_newly_selected_streams()
        return count


    def deselect_all_streams(self) -> None:
        self.stream_manager.deselect_all_streams()


    def update_streams(self) -> int:
        streams = self.find_streams(self.config.get("streams.timeout", 2.0))
        if self.is_recording_flag:
            self._attach_newly_selected_streams()
        return len(streams)


    def set_filename(self, filename: str) -> None:
        if self.is_recording_flag:
            raise RuntimeError("Cannot change filename while recording")
        self.filename = filename


    def get_status(self) -> Dict:
        return {
            "recording": self.is_recording_flag,
            "filename": self.filename,
            "selected_streams": len(self.stream_manager.selected_stream_uids),
            "available_streams": len(self.stream_manager.discovered_streams),
            "active_streams": len(self.stream_inlets),
        }


    def get_stream_list(self) -> List[Dict]:
        return self.stream_manager.get_stream_list()


    def _resolve_selected_streams(self, streams: Optional[List[pylsl.StreamInfo]]) -> Dict[str, pylsl.StreamInfo]:
        if streams is not None:
            if isinstance(streams, list):
                return {self._get_stream_key(stream_info): stream_info for stream_info in streams}
            return streams
        return self.stream_manager.get_selected_streams()


    def _ensure_output_directory(self) -> None:
        try:
            out_dir = os.path.dirname(self.filename)
            if out_dir and not os.path.isdir(out_dir):
                os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create output directory for {self.filename}: {e}")


    def _reset_runtime_state(self) -> None:
        self.stream_inlets.clear()
        self.stream_ids.clear()
        self.offset_threads.clear()
        self.offset_stop_events.clear()
        self._data_buffers.clear()


    def _setup_recording_streams(self, selected_streams: Dict[str, pylsl.StreamInfo]) -> None:
        for uid, stream_info in selected_streams.items():
            if uid in self.stream_inlets:
                continue
            try:
                self._setup_single_stream(uid, stream_info)
            except Exception as e:
                print(f"Failed to set up stream {stream_info.name()} (UID: {uid}): {e}")
                raise


    def _setup_single_stream(self, uid: str, stream_info: pylsl.StreamInfo) -> None:
        inlet = pylsl.StreamInlet(
            stream_info,
            max_buflen=self.config.get("recording.buffer_size", 360),
            recover=self.config.get("streams.recover", True),
        )
        self._open_inlet(inlet, stream_info.name())

        inlet_info = self._get_inlet_info(inlet, stream_info)
        xdf_stream_id = self.xdf_writer.add_stream(inlet_info, stream_key=uid)
        self.stream_inlets[uid] = inlet
        self.stream_ids[uid] = xdf_stream_id
        self.acquisition_manager.add_stream(uid, inlet_info, inlet)

        first_sample, first_timestamp_lsl = inlet.pull_sample(timeout=1.0)
        if first_timestamp_lsl is not None:
            first_timestamp_local = pylsl.local_clock()
            initial_offset = first_timestamp_local - first_timestamp_lsl
            collection_time = first_timestamp_local - initial_offset
            self.xdf_writer.write_clock_offset(uid, collection_time, initial_offset)
            print(f"Stream {stream_info.name()}: Initial LSL ts: {first_timestamp_lsl:.4f}, Local ts: {first_timestamp_local:.4f}, Offset: {initial_offset:.4f}")
            if first_sample is not None:
                with self.buffer_lock:
                    self._data_buffers.setdefault(uid, []).append(([first_sample], [first_timestamp_lsl]))

        if self.is_recording_flag:
            self._start_offset_thread(uid)


    def _open_inlet(self, inlet: pylsl.StreamInlet, stream_name: str) -> None:
        open_stream = getattr(inlet, "open_stream", None)
        if callable(open_stream):
            try:
                open_stream(5.0)
                print(f"Opened LSL inlet for stream: {stream_name}")
            except Exception as e:
                print(f"Warning: delayed subscription for stream {stream_name}: {e}")
        else:
            print(f"Opened LSL inlet for stream: {stream_name}")


    def _get_inlet_info(self, inlet: pylsl.StreamInlet, fallback_info: pylsl.StreamInfo) -> pylsl.StreamInfo:
        info_method = getattr(inlet, "info", None)
        if callable(info_method):
            try:
                return info_method()
            except Exception:
                return fallback_info
        return fallback_info


    def _start_offset_thread(self, stream_uid: str) -> None:
        if stream_uid in self.offset_threads:
            return
        stop_event = threading.Event()
        thread = threading.Thread(target=self._offset_thread_func, args=(stream_uid, stop_event), daemon=True)
        self.offset_stop_events[stream_uid] = stop_event
        self.offset_threads[stream_uid] = thread
        thread.start()


    def _offset_thread_func(self, stream_uid: str, stop_event: threading.Event) -> None:
        interval = float(self.config.get("recording.clock_sync_interval", 5.0))
        while not self.shutdown_event.is_set() and not stop_event.wait(interval):
            self._sample_clock_offset(stream_uid)


    def _sample_clock_offset(self, stream_uid: str) -> None:
        if self.xdf_writer is None or stream_uid not in self.stream_inlets:
            return
        inlet = self.stream_inlets[stream_uid]
        try:
            offset = inlet.time_correction(2.0)
            local_time = pylsl.local_clock()
            collection_time = local_time - offset
            self.xdf_writer.write_clock_offset(stream_uid, collection_time, offset)
        except Exception as e:
            print(f"Timeout in time correction query for stream {stream_uid}: {e}")


    def _stop_background_threads(self) -> None:
        for stop_event in self.offset_stop_events.values():
            stop_event.set()
        for thread in list(self.offset_threads.values()):
            if thread.is_alive():
                thread.join(timeout=5.0)
        self.offset_threads.clear()
        self.offset_stop_events.clear()

        if self.boundary_thread and self.boundary_thread.is_alive():
            self.boundary_thread.join(timeout=5.0)
        self.boundary_thread = None

        if self.watch_thread and self.watch_thread.is_alive():
            self.watch_thread.join(timeout=5.0)
        self.watch_thread = None


    def _boundary_thread_func(self) -> None:
        interval = float(self.config.get("recording.boundary_interval", 10.0))
        next_boundary = time.monotonic() + interval
        while not self.shutdown_event.is_set():
            time.sleep(0.5)
            if self.xdf_writer is not None and time.monotonic() >= next_boundary:
                self.xdf_writer.write_boundary_chunk()
                next_boundary = time.monotonic() + interval


    def _watch_thread_func(self) -> None:
        interval = float(self.config.get("streams.discovery_interval", 5.0))
        timeout = float(self.config.get("streams.timeout", 2.0))
        while not self.shutdown_event.wait(interval):
            try:
                self.find_streams(timeout)
                self._attach_newly_selected_streams()
            except Exception as e:
                print(f"Warning: stream watch update failed: {e}")


    def _attach_newly_selected_streams(self) -> None:
        new_streams = self.stream_manager.get_unrecorded_selected_streams(set(self.stream_inlets.keys()))
        if not new_streams:
            return
        print(f"Attaching {len(new_streams)} newly discovered streams.")
        for uid, stream_info in new_streams.items():
            try:
                self._setup_single_stream(uid, stream_info)
            except Exception as e:
                print(f"Warning: could not attach stream {stream_info.name()} ({uid}): {e}")


    def _get_stream_key(self, stream_info: pylsl.StreamInfo) -> str:
        uid = stream_info.uid()
        if uid:
            return uid
        source_id = stream_info.source_id()
        if source_id:
            return source_id
        return stream_info.name()


    def _on_data_received(self, stream_uid: str, samples: List, timestamps: List) -> None:
        with self.buffer_lock:
            self._data_buffers.setdefault(stream_uid, []).append((samples, timestamps))


    def _on_clock_reset(self, stream_uid: str) -> None:
        print(f"Clock reset detected for stream {stream_uid}. Writing boundary and new offset.")
        if self.xdf_writer is not None:
            self.xdf_writer.write_boundary_chunk()
        self._sample_clock_offset(stream_uid)


    def _writer_thread_func(self) -> None:
        print("XDF Writer thread started.")
        while self.is_recording_flag or any(self._data_buffers.values()):
            data_written = False
            for uid, buffer in list(self._data_buffers.items()):
                chunks_to_write = []
                with self.buffer_lock:
                    if buffer:
                        chunks_to_write.extend(buffer)
                        buffer.clear()
                if chunks_to_write:
                    data_written = True
                    for samples_chunk, timestamps_chunk in chunks_to_write:
                        try:
                            self.xdf_writer.write_samples(uid, samples_chunk, timestamps_chunk)
                        except Exception as e:
                            print(f"Error writing samples for stream UID {uid} to XDF: {e}")
            if not data_written:
                if self.is_recording_flag:
                    time.sleep(0.05)
                elif not any(self._data_buffers.values()):
                    break
        print("XDF Writer thread finished.")
