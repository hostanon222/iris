import json
import os
from datetime import datetime
from typing import Dict, List, Any
import logging

logger = logging.getLogger('iris')

class DataManager:
    def __init__(self):
        self.data_dir = 'data'
        self.gallery_data_file = os.path.join(self.data_dir, 'gallery_data.json')
        self.queue_data_file = os.path.join(self.data_dir, 'art_queue.json')
        
        # Create data directory if it doesn't exist
        os.makedirs(self.data_dir, exist_ok=True)
        
    def save_gallery_item(self, item: Dict[str, Any]):
        """Save a new item to the gallery"""
        try:
            # Load existing data
            gallery_data = self.load_gallery_data()
            
            # Add new item
            gallery_data.append(item)
            
            # Save updated data
            with open(self.gallery_data_file, 'w') as f:
                json.dump(gallery_data, f, indent=2)
                
            logger.info(f"Saved new gallery item: {item['id']}")
            
        except Exception as e:
            logger.error(f"Error saving gallery item: {e}")
            raise
    
    def load_gallery_data(self) -> List[Dict[str, Any]]:
        """Load gallery data from file"""
        try:
            if os.path.exists(self.gallery_data_file):
                with open(self.gallery_data_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading gallery data: {e}")
            return []
    
    def save_queue_state(self, queue_data: List[Dict[str, Any]]):
        """Save current art queue state"""
        try:
            with open(self.queue_data_file, 'w') as f:
                json.dump(queue_data, f, indent=2)
            logger.info("Saved art queue state")
        except Exception as e:
            logger.error(f"Error saving queue state: {e}")
    
    def load_queue_state(self) -> List[Dict[str, Any]]:
        """Load art queue state"""
        try:
            if os.path.exists(self.queue_data_file):
                with open(self.queue_data_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading queue state: {e}")
            return [] 