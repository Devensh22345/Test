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
        # Track media groups to forward them together
        self.media_groups = {}
    
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
• Instant forwarding (no batching delay)
• Media group support (albums forwarded as albums)
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
                "All posts from this channel will be instantly forwarded to post channels.",
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
    
    async def forward_single_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Forward a single message (non-media group)."""
        main_channel = db.get_main_channel()
        
        if not main_channel:
            return
        
        if str(update.channel_post.chat.id) != main_channel["channel_id"]:
            return
        
        message = update.channel_post
        post_channels = db.get_post_channels()
        
        if not post_channels:
            return
        
        # Check if this is part of a media group
        if message.media_group_id:
            # Handle as part of media group
            await self.handle_media_group(message, context)
            return
        
        # Regular single message - forward immediately
        success_count = 0
        failed_channels = []
        
        for channel in post_channels:
            try:
                if db.is_message_posted(message.message_id, channel["channel_id"]):
                    continue
                
                await context.bot.forward_message(
                    chat_id=channel["channel_id"],
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                
                db.mark_message_posted(message.message_id, channel["channel_id"])
                success_count += 1
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to forward message {message.message_id} to {channel['channel_id']}: {e}")
                failed_channels.append(channel.get('title', channel['channel_id']))
        
        if success_count > 0:
            logger.info(f"Forwarded single message {message.message_id} to {success_count} channels")
        
        if failed_channels:
            logger.warning(f"Failed to forward to: {failed_channels}")
    
    async def handle_media_group(self, message, context: ContextTypes.DEFAULT_TYPE):
        """Handle media groups (albums)."""
        media_group_id = message.media_group_id
        
        # Initialize or update media group collection
        if media_group_id not in self.media_groups:
            self.media_groups[media_group_id] = {
                'messages': [],
                'last_update': datetime.utcnow(),
                'processing': False
            }
        
        # Add message to media group
        self.media_groups[media_group_id]['messages'].append({
            'message_id': message.message_id,
            'chat_id': message.chat.id,
            'timestamp': datetime.utcnow()
        })
        self.media_groups[media_group_id]['last_update'] = datetime.utcnow()
        
        logger.info(f"Added message {message.message_id} to media group {media_group_id}. Total: {len(self.media_groups[media_group_id]['messages'])}")
        
        # Wait for 1 second to collect all messages in the media group
        # Telegram sends media group messages rapidly but not instantly
        await asyncio.sleep(1)
        
        # Check if we should process this media group
        if (not self.media_groups[media_group_id]['processing'] and 
            datetime.utcnow() - self.media_groups[media_group_id]['last_update']).seconds >= 1:
            
            self.media_groups[media_group_id]['processing'] = True
            await self.process_media_group(media_group_id, context)
    
    async def process_media_group(self, media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
        """Process a complete media group."""
        try:
            media_group = self.media_groups.get(media_group_id)
            if not media_group or not media_group['messages']:
                return
            
            messages = media_group['messages']
            post_channels = db.get_post_channels()
            
            if not post_channels:
                # Clean up
                if media_group_id in self.media_groups:
                    del self.media_groups[media_group_id]
                return
            
            logger.info(f"Processing media group {media_group_id} with {len(messages)} messages")
            
            # Forward to each post channel
            for channel in post_channels:
                try:
                    # Get message IDs that haven't been forwarded yet
                    message_ids_to_forward = []
                    for msg in messages:
                        if not db.is_message_posted(msg['message_id'], channel["channel_id"]):
                            message_ids_to_forward.append(msg['message_id'])
                    
                    if not message_ids_to_forward:
                        continue
                    
                    # Forward first message
                    first_msg = messages[0]
                    await context.bot.forward_message(
                        chat_id=channel["channel_id"],
                        from_chat_id=first_msg['chat_id'],
                        message_id=first_msg['message_id']
                    )
                    
                    # Mark first message as posted
                    db.mark_message_posted(first_msg['message_id'], channel["channel_id"])
                    
                    # Forward remaining messages as a group
                    if len(messages) > 1:
                        for msg in messages[1:]:
                            try:
                                await context.bot.forward_message(
                                    chat_id=channel["channel_id"],
                                    from_chat_id=msg['chat_id'],
                                    message_id=msg['message_id']
                                )
                                db.mark_message_posted(msg['message_id'], channel["channel_id"])
                                await asyncio.sleep(0.1)  # Small delay
                            except Exception as e:
                                logger.error(f"Failed to forward media group message {msg['message_id']}: {e}")
                    
                    logger.info(f"Forwarded media group {media_group_id} ({len(messages)} messages) to {channel['channel_id']}")
                    
                except Exception as e:
                    logger.error(f"Failed to forward media group {media_group_id} to {channel['channel_id']}: {e}")
            
            # Clean up after processing
            if media_group_id in self.media_groups:
                del self.media_groups[media_group_id]
                
            logger.info(f"Completed processing media group {media_group_id}")
            
        except Exception as e:
            logger.error(f"Error processing media group {media_group_id}: {e}")
            # Clean up on error
            if media_group_id in self.media_groups:
                del self.media_groups[media_group_id]
    
    async def forward_from_main_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Main handler for forwarding from main channel."""
        # This method will handle both single messages and media groups
        await self.forward_single_message(update, context)
    
    async def cleanup_old_media_groups(self, context: ContextTypes.DEFAULT_TYPE):
        """Clean up old media groups that weren't completed."""
        try:
            current_time = datetime.utcnow()
            groups_to_remove = []
            
            for media_group_id, data in self.media_groups.items():
                # Remove groups older than 30 seconds (should have been processed)
                if (current_time - data['last_update']).seconds > 30:
                    groups_to_remove.append(media_group_id)
                    logger.warning(f"Cleaning up old media group {media_group_id} with {len(data['messages'])} messages")
            
            for media_group_id in groups_to_remove:
                # Try to process whatever we have
                if self.media_groups[media_group_id]['messages']:
                    await self.process_media_group(media_group_id, context)
                else:
                    del self.media_groups[media_group_id]
                    
        except Exception as e:
            logger.error(f"Error in cleanup_old_media_groups: {e}")
    
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
        stats_text += f"• **Active Media Groups:** {len(self.media_groups)}\n"
        
        posted_count = db.posted_messages.count_documents({})
        stats_text += f"• **Total Messages Forwarded:** {posted_count}\n"
        
        # Show media group info
        if self.media_groups:
            stats_text += f"\n**Active Media Groups:**\n"
            for mg_id, data in list(self.media_groups.items())[:5]:  # Show first 5
                stats_text += f"• {mg_id[:8]}...: {len(data['messages'])} messages\n"
            if len(self.media_groups) > 5:
                stats_text += f"• ... and {len(self.media_groups) - 5} more\n"
        
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
        
        # Join request handler
        self.application.add_handler(ChatJoinRequestHandler(self.handle_join_request))
        
        # Message handlers - for forwarding from main channel
        self.application.add_handler(
            MessageHandler(filters.ChatType.CHANNEL, self.forward_from_main_channel)
        )
        
        # Schedule jobs
        job_queue = self.application.job_queue
        if job_queue:
            # Auto-approval job (every 24 hours)
            job_queue.run_repeating(
                self.auto_approve_old_requests,
                interval=86400,
                first=10
            )
            
            # Media group cleanup job (every 5 minutes)
            job_queue.run_repeating(
                self.cleanup_old_media_groups,
                interval=300,
                first=60
            )
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
        
        # Start the Bot
        logger.info("Starting bot with media group support...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Main function to run the bot."""
    bot = ChannelBot()
    bot.run()

if __name__ == '__main__':
    main()
