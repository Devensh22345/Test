import logging
from telegram import Update, Bot, ChatMemberAdministrator, ChatJoinRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatJoinRequestHandler
)
from telegram.constants import ParseMode
import asyncio
from datetime import datetime
from config import Config
from database import db

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ChannelBot:
    def __init__(self):
        self.application = None
        self.batch_size = 100  # Forward 100 messages at once
        self.forwarding_queue = []  # Queue for batch forwarding
        self.is_processing = False
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send help message."""
        help_text = """
🤖 **Channel Management Bot**

**Commands:**
• `/add <channel_id>` - Add a channel as post channel
• `/main <channel_id>` - Set main channel (only one main channel allowed)
• `/approve <channel_id>` - Approve all pending join requests in a channel
• `/list` - List all registered channels
• `/remove <channel_id>` - Remove a channel
• `/stats` - Show bot statistics

**Features:**
• Batch forwarding of 100 messages at once
• Automatic join request approval
• MongoDB database for tracking

**Required Bot Permissions:**
- In Main Channel: Post messages permission
- In Post Channels: Admin with "Invite Users via Link" permission
        """
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add a channel as post channel."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a channel ID.\nUsage: /add <channel_id>")
            return
        
        channel_id = context.args[0]
        
        try:
            bot = context.bot
            chat = await bot.get_chat(channel_id)
            
            bot_member = await chat.get_member(bot.id)
            if not isinstance(bot_member, ChatMemberAdministrator):
                await update.message.reply_text(
                    f"❌ Bot is not admin in channel: {chat.title}\n"
                    "Please make bot admin with all permissions first."
                )
                return
            
            if not bot_member.can_invite_users:
                await update.message.reply_text(
                    f"⚠️ Warning: Bot doesn't have 'Invite Users' permission in {chat.title}\n"
                    "Join request approval may not work.\n\n"
                    "Still adding channel..."
                )
            
            db.add_channel(channel_id, "post", chat.title)
            
            await update.message.reply_text(
                f"✅ Successfully added as Post Channel!\n"
                f"**Channel:** {chat.title}\n"
                f"**ID:** `{channel_id}`\n"
                f"**Type:** Post Channel",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error adding channel: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    async def set_main_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set main channel."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a channel ID.\nUsage: /main <channel_id>")
            return
        
        channel_id = context.args[0]
        
        try:
            bot = context.bot
            chat = await bot.get_chat(channel_id)
            
            bot_member = await chat.get_member(bot.id)
            if not isinstance(bot_member, ChatMemberAdministrator):
                await update.message.reply_text(
                    f"❌ Bot needs to be admin in: {chat.title}\n"
                    "Please add bot as admin first."
                )
                return
            
            existing_main = db.get_main_channel()
            if existing_main:
                db.channels.update_one(
                    {"channel_id": existing_main["channel_id"]},
                    {"$set": {"type": "post"}}
                )
            
            db.add_channel(channel_id, "main", chat.title)
            
            await update.message.reply_text(
                f"✅ Main Channel Set Successfully!\n"
                f"**Channel:** {chat.title}\n"
                f"**ID:** `{channel_id}`\n\n"
                "All posts from this channel will be forwarded to post channels in batches of 100.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error setting main channel: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    async def get_all_pending_requests(self, bot: Bot, channel_id: str):
        """Get all pending join requests for a channel."""
        try:
            all_requests = []
            offset = None
            
            while True:
                result = await bot.get_chat_join_requests(
                    chat_id=channel_id,
                    limit=100,
                    offset=offset
                )
                
                if not result.join_requests:
                    break
                
                all_requests.extend(result.join_requests)
                
                if not result.next_offset:
                    break
                
                offset = result.next_offset
            
            return all_requests
            
        except Exception as e:
            logger.error(f"Error getting join requests: {e}")
            return []
    
    async def approve_single_request(self, bot: Bot, channel_id: str, user_id: int):
        """Approve a single join request."""
        try:
            await bot.approve_chat_join_request(
                chat_id=channel_id,
                user_id=user_id
            )
            return True
        except Exception as e:
            logger.error(f"Failed to approve user {user_id}: {e}")
            return False
    
    async def approve_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Approve all pending join requests in a channel."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a channel ID.\nUsage: /approve <channel_id>")
            return
        
        channel_id = context.args[0]
        
        try:
            bot = context.bot
            
            status_msg = await update.message.reply_text("⏳ Fetching pending join requests...")
            
            chat = await bot.get_chat(channel_id)
            bot_member = await chat.get_member(bot.id)
            
            if not isinstance(bot_member, ChatMemberAdministrator):
                await status_msg.edit_text(
                    f"❌ Bot is not admin in channel: {chat.title}\n"
                    "Please add bot as admin first."
                )
                return
            
            if not bot_member.can_invite_users:
                await status_msg.edit_text(
                    f"❌ Bot doesn't have 'Invite Users' permission in {chat.title}\n"
                    "Cannot approve join requests."
                )
                return
            
            await status_msg.edit_text("📋 Fetching all pending join requests...")
            pending_requests = await self.get_all_pending_requests(bot, channel_id)
            
            if not pending_requests:
                await status_msg.edit_text(
                    f"✅ No pending join requests found in: {chat.title}\n"
                    f"All requests are already approved or no requests pending."
                )
                return
            
            total_requests = len(pending_requests)
            await status_msg.edit_text(f"✅ Found {total_requests} pending requests\n⏳ Approving now...")
            
            approved_count = 0
            failed_count = 0
            
            for i, join_request in enumerate(pending_requests, 1):
                user = join_request.user
                
                if i % 10 == 0:
                    progress = f"Processing {i}/{total_requests}\nApproved: {approved_count}"
                    await status_msg.edit_text(f"⏳ {progress}")
                
                success = await self.approve_single_request(bot, channel_id, user.id)
                
                if success:
                    approved_count += 1
                else:
                    failed_count += 1
                
                await asyncio.sleep(0.5)
            
            report = f"📊 **Approval Report for {chat.title}**\n\n"
            report += f"✅ **Successfully Approved:** {approved_count}/{total_requests}\n"
            
            if failed_count > 0:
                report += f"❌ **Failed:** {failed_count}\n"
            
            remaining_requests = await self.get_all_pending_requests(bot, channel_id)
            if remaining_requests:
                report += f"\n\n⚠️ **Note:** {len(remaining_requests)} requests still pending.\n"
                report += "Some requests might have been made after we started processing."
            
            await status_msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Approved {approved_count} join requests in channel {channel_id}")
            
        except Exception as e:
            logger.error(f"Error in approve_requests: {e}")
            error_msg = f"❌ Error: {str(e)}"
            if "CHAT_ADMIN_REQUIRED" in str(e):
                error_msg = "❌ Bot needs admin privileges with 'Invite Users' permission."
            elif "not found" in str(e).lower():
                error_msg = "❌ Channel not found or bot is not a member."
            
            try:
                await update.message.reply_text(error_msg)
            except:
                pass
    
    async def handle_join_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new join requests."""
        join_request = update.chat_join_request
        logger.info(f"New join request from {join_request.from_user.id} in channel {join_request.chat.id}")
    
    async def auto_approve_old_requests(self, context: ContextTypes.DEFAULT_TYPE):
        """Auto-approve requests (scheduled job)."""
        try:
            post_channels = db.get_post_channels()
            
            for channel in post_channels:
                channel_id = channel["channel_id"]
                
                try:
                    bot = context.bot
                    pending_requests = await self.get_all_pending_requests(bot, channel_id)
                    
                    if not pending_requests:
                        continue
                    
                    approved_count = 0
                    for join_request in pending_requests:
                        success = await self.approve_single_request(bot, channel_id, join_request.user.id)
                        if success:
                            approved_count += 1
                        await asyncio.sleep(0.5)
                    
                    if approved_count > 0:
                        logger.info(f"Auto-approved {approved_count} requests in {channel_id}")
                        
                except Exception as e:
                    logger.error(f"Error auto-approving in {channel_id}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in auto_approve_old_requests: {e}")
    
    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all registered channels."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        main_channel = db.get_main_channel()
        post_channels = db.get_post_channels()
        
        response = "📊 **Registered Channels**\n\n"
        
        if main_channel:
            response += f"🏠 **Main Channel:**\n"
            response += f"• {main_channel.get('title', 'Unknown')}\n"
            response += f"  ID: `{main_channel['channel_id']}`\n\n"
        else:
            response += "❌ No main channel set\n\n"
        
        if post_channels:
            response += f"📢 **Post Channels ({len(post_channels)}):**\n"
            for i, channel in enumerate(post_channels, 1):
                response += f"{i}. {channel.get('title', 'Unknown')}\n"
                response += f"   ID: `{channel['channel_id']}`\n"
        else:
            response += "❌ No post channels added"
        
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
    
    async def remove_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove a channel from database."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a channel ID.\nUsage: /remove <channel_id>")
            return
        
        channel_id = context.args[0]
        channel = db.get_channel_by_id(channel_id)
        
        if not channel:
            await update.message.reply_text(f"❌ Channel `{channel_id}` not found in database.")
            return
        
        db.remove_channel(channel_id)
        await update.message.reply_text(
            f"✅ Channel removed successfully!\n"
            f"**Title:** {channel.get('title', 'Unknown')}\n"
            f"**ID:** `{channel_id}`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def forward_messages_batch(self, context: ContextTypes.DEFAULT_TYPE):
        """Forward a batch of messages from queue."""
        if not self.forwarding_queue or self.is_processing:
            return
        
        self.is_processing = True
        
        try:
            # Get a batch of messages to forward (up to batch_size)
            batch = self.forwarding_queue[:self.batch_size]
            
            if not batch:
                return
            
            logger.info(f"Processing batch of {len(batch)} messages")
            
            main_channel = db.get_main_channel()
            post_channels = db.get_post_channels()
            
            if not main_channel or not post_channels:
                self.forwarding_queue = []
                return
            
            # Forward to each post channel in parallel
            tasks = []
            for message_data in batch:
                message_id = message_data['message_id']
                source_chat_id = message_data['source_chat_id']
                
                for channel in post_channels:
                    # Check if already forwarded
                    if not db.is_message_posted(message_id, channel["channel_id"]):
                        task = self.forward_single_message(
                            context.bot,
                            source_chat_id,
                            message_id,
                            channel["channel_id"]
                        )
                        tasks.append(task)
            
            # Execute all forwarding tasks concurrently
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Process results
                success_count = sum(1 for r in results if r is True)
                failed_count = sum(1 for r in results if isinstance(r, Exception))
                
                logger.info(f"Batch completed: {success_count} successful, {failed_count} failed")
            
            # Remove processed messages from queue
            self.forwarding_queue = self.forwarding_queue[self.batch_size:]
            
        except Exception as e:
            logger.error(f"Error in forward_messages_batch: {e}")
        finally:
            self.is_processing = False
            
            # If there are more messages in queue, schedule next batch
            if self.forwarding_queue:
                await asyncio.sleep(1)  # Short delay before next batch
                context.application.create_task(self.forward_messages_batch(context))
    
    async def forward_single_message(self, bot: Bot, source_chat_id: int, message_id: int, target_channel_id: str):
        """Forward a single message to a channel."""
        try:
            await bot.forward_message(
                chat_id=target_channel_id,
                from_chat_id=source_chat_id,
                message_id=message_id
            )
            
            db.mark_message_posted(message_id, target_channel_id)
            return True
            
        except Exception as e:
            logger.error(f"Failed to forward message {message_id} to {target_channel_id}: {e}")
            return e
    
    async def forward_from_main_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Queue messages from main channel for batch forwarding."""
        main_channel = db.get_main_channel()
        
        if not main_channel:
            return
        
        if str(update.channel_post.chat.id) != main_channel["channel_id"]:
            return
        
        message = update.channel_post
        post_channels = db.get_post_channels()
        
        if not post_channels:
            return
        
        # Add message to forwarding queue
        self.forwarding_queue.append({
            'message_id': message.message_id,
            'source_chat_id': message.chat.id,
            'timestamp': datetime.utcnow()
        })
        
        logger.info(f"Message {message.message_id} added to queue. Queue size: {len(self.forwarding_queue)}")
        
        # If queue reaches batch size or this is the first message in queue, start processing
        if len(self.forwarding_queue) >= self.batch_size or len(self.forwarding_queue) == 1:
            if not self.is_processing:
                context.application.create_task(self.forward_messages_batch(context))
    
    async def flush_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manually flush the forwarding queue (admin command)."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        queue_size = len(self.forwarding_queue)
        
        if queue_size == 0:
            await update.message.reply_text("📭 Forwarding queue is empty.")
            return
        
        await update.message.reply_text(f"⏳ Flushing {queue_size} messages from queue...")
        
        # Trigger batch processing
        if not self.is_processing:
            context.application.create_task(self.forward_messages_batch(context))
            await update.message.reply_text(f"✅ Started processing {queue_size} messages.")
        else:
            await update.message.reply_text(f"⏳ Already processing messages. {queue_size} in queue.")
    
    async def queue_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show forwarding queue status (admin command)."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        status_text = "📊 **Forwarding Queue Status**\n\n"
        status_text += f"• **Queue Size:** {len(self.forwarding_queue)} messages\n"
        status_text += f"• **Batch Size:** {self.batch_size} messages\n"
        status_text += f"• **Currently Processing:** {'Yes' if self.is_processing else 'No'}\n"
        status_text += f"• **Post Channels:** {len(db.get_post_channels())}\n"
        
        if self.forwarding_queue:
            oldest = self.forwarding_queue[0]['timestamp']
            newest = self.forwarding_queue[-1]['timestamp']
            status_text += f"• **Oldest Message:** {oldest.strftime('%Y-%m-%d %H:%M:%S')}\n"
            status_text += f"• **Newest Message:** {newest.strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        status_text += "\n**Commands:**\n"
        status_text += "• `/flush` - Process all queued messages immediately\n"
        status_text += "• `/queue` - Show this status\n"
        
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        main_channel = db.get_main_channel()
        post_channels = db.get_post_channels()
        
        stats_text = "📈 **Bot Statistics**\n\n"
        stats_text += f"• **Main Channel:** {1 if main_channel else 0}\n"
        stats_text += f"• **Post Channels:** {len(post_channels)}\n"
        stats_text += f"• **Forwarding Queue:** {len(self.forwarding_queue)} messages\n"
        stats_text += f"• **Batch Size:** {self.batch_size} messages\n"
        
        posted_count = db.posted_messages.count_documents({})
        stats_text += f"• **Total Messages Forwarded:** {posted_count}\n"
        
        stats_text += f"• **Currently Processing:** {'Yes' if self.is_processing else 'No'}\n"
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Log errors."""
        logger.error(f"Exception while handling an update: {context.error}")
    
    def run(self):
        """Start the bot."""
        # Create Application
        self.application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Command handlers
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("add", self.add_channel))
        self.application.add_handler(CommandHandler("main", self.set_main_channel))
        self.application.add_handler(CommandHandler("approve", self.approve_requests))
        self.application.add_handler(CommandHandler("list", self.list_channels))
        self.application.add_handler(CommandHandler("remove", self.remove_channel))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("queue", self.queue_status))
        self.application.add_handler(CommandHandler("flush", self.flush_queue))
        
        # Join request handler
        self.application.add_handler(ChatJoinRequestHandler(self.handle_join_request))
        
        # Message handlers - for forwarding from main channel
        self.application.add_handler(
            MessageHandler(filters.ChatType.CHANNEL, self.forward_from_main_channel)
        )
        
        # Schedule auto-approval job (every 24 hours)
        job_queue = self.application.job_queue
        if job_queue:
            job_queue.run_repeating(
                self.auto_approve_old_requests,
                interval=86400,
                first=10
            )
        
        # Schedule queue processing job (every 5 minutes for any remaining messages)
        if job_queue:
            job_queue.run_repeating(
                lambda ctx: self.forward_messages_batch(ctx) if self.forwarding_queue and not self.is_processing else None,
                interval=300,
                first=60
            )
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
        
        # Start the Bot
        logger.info("Starting bot with batch forwarding...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Main function to run the bot."""
    bot = ChannelBot()
    bot.run()

if __name__ == '__main__':
    main()
