"""
Stream manager for discovering and managing LSL streams.
"""

import pylsl
from typing import Dict, List, Set


class StreamManager:
    """Manages LSL stream discovery and selection."""
    
    def __init__(self):
        self.discovered_streams: Dict[str, pylsl.StreamInfo] = {}
        self.selected_stream_uids: Set[str] = set()
        self.select_all_active = False
    
    def find_streams(self, timeout: float = 2.0) -> List[pylsl.StreamInfo]:
        """
        Discover available LSL streams.
        
        Args:
            timeout: Timeout for stream discovery in seconds
            
        Returns:
            List of discovered stream info objects
        """
        print("Looking for LSL streams...")
        streams_info_list = pylsl.resolve_streams(timeout)
        
        if not streams_info_list:
            print("No LSL streams found.")
            return []

        print(f"Found {len(streams_info_list)} streams:")
        self.discovered_streams.clear()
        
        for i, info in enumerate(streams_info_list):
            print(f"  {i+1}. Name: {info.name()}, Type: {info.type()}, UID: {info.uid()}")
            self.discovered_streams[info.uid()] = info

        if self.select_all_active:
            self.selected_stream_uids.update(self.discovered_streams.keys())
            
        return streams_info_list
    
    def select_streams(self, stream_uids: List[str]) -> None:
        """
        Select streams for recording by their UIDs.
        
        Args:
            stream_uids: List of stream UIDs to select
        """
        self.select_all_active = False
        self.selected_stream_uids.clear()
        
        for uid in stream_uids:
            if uid in self.discovered_streams:
                stream_name = self.discovered_streams[uid].name()
                print(f"Stream {stream_name} (UID: {uid}) selected for recording.")
                self.selected_stream_uids.add(uid)
            else:
                print(f"Warning: Stream UID {uid} not found among discovered streams.")
    
    def select_all_streams(self) -> int:
        """
        Select all discovered streams.
        
        Returns:
            Number of streams selected
        """
        if not self.discovered_streams:
            self.find_streams()
        
        if self.discovered_streams:
            self.select_all_active = True
            uids = list(self.discovered_streams.keys())
            self.selected_stream_uids = set(uids)
            return len(uids)
        return 0
    
    def deselect_all_streams(self) -> None:
        """Deselect all streams."""
        self.select_all_active = False
        self.selected_stream_uids.clear()
    
    def get_selected_streams(self) -> Dict[str, pylsl.StreamInfo]:
        """
        Get currently selected streams.
        
        Returns:
            Dictionary mapping UIDs to StreamInfo objects
        """
        return {
            uid: self.discovered_streams[uid] 
            for uid in self.selected_stream_uids 
            if uid in self.discovered_streams
        }


    def get_unrecorded_selected_streams(self, active_stream_uids: Set[str]) -> Dict[str, pylsl.StreamInfo]:
        return {
            uid: info
            for uid, info in self.get_selected_streams().items()
            if uid not in active_stream_uids
        }
    
    def get_stream_info(self, uid: str) -> pylsl.StreamInfo:
        """
        Get stream info by UID.
        
        Args:
            uid: Stream UID
            
        Returns:
            StreamInfo object
            
        Raises:
            KeyError: If stream UID not found
        """
        return self.discovered_streams[uid]
    
    def get_stream_list(self) -> List[Dict]:
        """
        Get list of all discovered streams with their properties.
        
        Returns:
            List of stream dictionaries
        """
        stream_list = []
        for uid, info in self.discovered_streams.items():
            stream_list.append({
                'uid': uid,
                'name': info.name(),
                'type': info.type(),
                'channels': info.channel_count(),
                'rate': info.nominal_srate(),
                'selected': uid in self.selected_stream_uids
            })
        return stream_list 