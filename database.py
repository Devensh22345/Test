from pymongo import MongoClient
from datetime import datetime
from config import Config

class Database:
    def __init__(self):
        self.client = MongoClient(Config.MONGO_URI)
        self.db = self.client[Config.DATABASE_NAME]
        self.channels = self.db.channels
        self.settings = self.db.settings
        self.posted_messages = self.db.posted_messages
        self.message_mappings = self.db.message_mappings  # New collection
        
    # Channel Operations (existing)
    def add_channel(self, channel_id, channel_type, title=None):
        """Add a channel to database"""
        channel_data = {
            "channel_id": str(channel_id),
            "type": channel_type,
            "title": title,
            "added_at": datetime.utcnow(),
            "is_active": True
        }
        
        self.channels.update_one(
            {"channel_id": str(channel_id)},
            {"$set": channel_data},
            upsert=True
        )
        return True
    
    def get_main_channel(self):
        """Get the main channel"""
        return self.channels.find_one({"type": "main", "is_active": True})
    
    def get_post_channels(self):
        """Get all post channels"""
        return list(self.channels.find({"type": "post", "is_active": True}))
    
    def get_channel_by_id(self, channel_id):
        """Get channel by ID"""
        return self.channels.find_one({"channel_id": str(channel_id)})
    
    def remove_channel(self, channel_id):
        """Remove a channel"""
        result = self.channels.delete_one({"channel_id": str(channel_id)})
        return result.deleted_count > 0
    
    def is_message_posted(self, message_id, channel_id):
        """Check if message is already posted"""
        return self.posted_messages.find_one({
            "message_id": message_id,
            "channel_id": str(channel_id)
        })
    
    def mark_message_posted(self, message_id, channel_id):
        """Mark message as posted"""
        self.posted_messages.insert_one({
            "message_id": message_id,
            "channel_id": str(channel_id),
            "posted_at": datetime.utcnow()
        })
    
    # NEW: Message Mapping Operations
    def add_message_mapping(self, main_message_id, post_message_id, post_channel_id, main_channel_id):
        """Store mapping between main channel message and post channel message"""
        mapping = {
            "main_message_id": main_message_id,
            "main_channel_id": str(main_channel_id),
            "post_message_id": post_message_id,
            "post_channel_id": str(post_channel_id),
            "forwarded_at": datetime.utcnow()
        }
        self.message_mappings.insert_one(mapping)
        return True
    
    def get_message_mappings_by_main(self, main_message_id, main_channel_id):
        """Get all post channel messages for a main channel message"""
        return list(self.message_mappings.find({
            "main_message_id": main_message_id,
            "main_channel_id": str(main_channel_id)
        }))
    
    def get_message_mapping_by_post(self, post_message_id, post_channel_id):
        """Get main channel message for a post channel message"""
        return self.message_mappings.find_one({
            "post_message_id": post_message_id,
            "post_channel_id": str(post_channel_id)
        })
    
    def delete_message_mapping(self, post_message_id, post_channel_id):
        """Delete message mapping"""
        result = self.message_mappings.delete_one({
            "post_message_id": post_message_id,
            "post_channel_id": str(post_channel_id)
        })
        return result.deleted_count > 0
    
    def get_old_messages(self, days=30):
        """Get messages older than X days"""
        from datetime import datetime, timedelta
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        return list(self.message_mappings.find({
            "forwarded_at": {"$lt": cutoff_date}
        }))
    
    def cleanup_old_messages(self, days=7):
        """Cleanup old message records"""
        from datetime import datetime, timedelta
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        result = self.posted_messages.delete_many({"posted_at": {"$lt": cutoff_date}})
        return result.deleted_count

# Singleton instance
db = Database()
