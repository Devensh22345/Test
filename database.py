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
        
    # Channel Operations
    def add_channel(self, channel_id, channel_type, title=None):
        """Add a channel to database"""
        channel_data = {
            "channel_id": str(channel_id),
            "type": channel_type,  # "main" or "post"
            "title": title,
            "added_at": datetime.utcnow(),
            "is_active": True
        }
        
        # Update if exists, insert if not
        self.channels.update_one(
            {"channel_id": str(channel_id)},
            {"$set": channel_data},
            upsert=True
        )
        return True
    
    def get_main_channel(self):
        """Get the main channel"""
        return self.channels.find_one({"type": "main", "is_active": True})


    def add_join_request(self, channel_id, user_id, request_date=None):
        """Store join request in database."""
        self.db.join_requests.insert_one({
            "channel_id": str(channel_id),
            "user_id": user_id,
            "request_date": request_date or datetime.utcnow(),
            "approved": False,
            "approved_at": None
        })
        
    def mark_request_approved(self, channel_id, user_id):
        """Mark join request as approved."""
        self.db.join_requests.update_one(
            {"channel_id": str(channel_id), "user_id": user_id},
            {"$set": {"approved": True, "approved_at": datetime.utcnow()}}
        )
    
    
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
    
    def cleanup_old_messages(self, days=7):
        """Cleanup old message records"""
        from datetime import datetime, timedelta
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        result = self.posted_messages.delete_many({"posted_at": {"$lt": cutoff_date}})
        return result.deleted_count

# Singleton instance
db = Database()
