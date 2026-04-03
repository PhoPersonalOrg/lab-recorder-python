"""
Data acquisition threads for LSL streams.
"""

import time
import threading
import pylsl
from typing import Callable, Optional


class AcquisitionThread:
    """Thread for acquiring data from a single LSL stream."""
    
    def __init__(self, stream_uid: str, stream_info: pylsl.StreamInfo, inlet: pylsl.StreamInlet, data_callback: Callable, clock_reset_callback: Optional[Callable] = None, max_samples_per_pull: Optional[int] = None):
        """
        Initialize acquisition thread.
        
        Args:
            stream_uid: Unique identifier for the stream
            stream_info: LSL StreamInfo object
            inlet: LSL StreamInlet for data acquisition
            data_callback: Callback function for data (stream_uid, samples, timestamps)
        """
        self.stream_uid = stream_uid
        self.stream_info = stream_info
        self.inlet = inlet
        self.data_callback = data_callback
        self.clock_reset_callback = clock_reset_callback
        
        self.thread = None
        self.running = False
        self.last_timestamp = None
        
        # Calculate max samples per pull based on sampling rate
        self.max_samples_per_pull = max_samples_per_pull or self._calculate_max_samples()
        
    def _calculate_max_samples(self) -> int:
        """Calculate optimal number of samples to pull at once."""
        nominal_rate = self.stream_info.nominal_srate()
        if nominal_rate > 0:
            max_samples = int(nominal_rate)
        else:
            max_samples = 100  # For irregular rate streams
            
        if max_samples == 0:
            max_samples = 1
        elif max_samples > 500:
            max_samples = 500  # Cap to avoid huge chunks
            
        return max_samples
    
    def start(self) -> None:
        """Start the acquisition thread."""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._acquisition_loop, daemon=True)
        self.thread.start()
        print(f"Started acquisition for {self.stream_info.name()} (UID: {self.stream_uid})")
    
    def stop(self) -> None:
        """Stop the acquisition thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        print(f"Stopped acquisition for {self.stream_info.name()} (UID: {self.stream_uid})")
    
    def _acquisition_loop(self) -> None:
        """Main acquisition loop running in the thread."""
        stream_name = self.stream_info.name()
        
        try:
            while self.running:
                samples, timestamps = self.inlet.pull_chunk(
                    timeout=0.1, 
                    max_samples=self.max_samples_per_pull
                )
                
                if timestamps:
                    self.last_timestamp = timestamps[-1]
                    self.data_callback(self.stream_uid, samples, timestamps)
                elif self.inlet.was_clock_reset():
                    print(f"Clock reset detected for stream {stream_name}. Re-evaluating sync.")
                    if self.clock_reset_callback is not None:
                        self.clock_reset_callback(self.stream_uid)
                else:
                    time.sleep(0.001)
                    
        except Exception as e:
            print(f"Error in acquisition thread for {stream_name}: {e}")
        finally:
            print(f"Acquisition thread for {stream_name} finished.")


class AcquisitionManager:
    """Manages multiple acquisition threads."""
    
    def __init__(self, data_callback: Callable, clock_reset_callback: Optional[Callable] = None, max_samples_per_pull: Optional[int] = None):
        """
        Initialize acquisition manager.
        
        Args:
            data_callback: Callback function for data (stream_uid, samples, timestamps)
        """
        self.data_callback = data_callback
        self.clock_reset_callback = clock_reset_callback
        self.max_samples_per_pull = max_samples_per_pull
        self.acquisition_threads = {}
        self.buffer_lock = threading.Lock()
        self.running = False
        
    def add_stream(self, stream_uid: str, stream_info: pylsl.StreamInfo, inlet: pylsl.StreamInlet) -> None:
        """
        Add a stream for acquisition.
        
        Args:
            stream_uid: Stream unique identifier
            stream_info: LSL StreamInfo object
            inlet: LSL StreamInlet for data acquisition
        """
        thread = AcquisitionThread(stream_uid, stream_info, inlet, self.data_callback, clock_reset_callback=self.clock_reset_callback, max_samples_per_pull=self.max_samples_per_pull)
        self.acquisition_threads[stream_uid] = thread
        if self.running:
            thread.start()
        
    def start_all(self) -> None:
        """Start acquisition for all streams."""
        self.running = True
        for thread in self.acquisition_threads.values():
            thread.start()
            
    def stop_all(self) -> None:
        """Stop acquisition for all streams."""
        self.running = False
        for thread in self.acquisition_threads.values():
            thread.stop()
        self.acquisition_threads.clear()


    def stop_stream(self, stream_uid: str) -> None:
        thread = self.acquisition_threads.pop(stream_uid, None)
        if thread is not None:
            thread.stop()
        
    def get_last_timestamps(self) -> dict:
        """Get last timestamp for each stream."""
        return {
            uid: thread.last_timestamp 
            for uid, thread in self.acquisition_threads.items()
            if thread.last_timestamp is not None
        } 