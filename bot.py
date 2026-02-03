import logging
from telegram import Update, Bot, ChatMemberAdministrator, ChatJoinRequest, Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument
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
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
import json
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
        # Store media groups by group_id
        self.media_groups: Dict[str, List[Message]] = defaultdict(list)
        # Track which groups are being processed
        self.processing_groups: Dict[str, bool] = {}
    
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
• Forwards single messages AND media groups (albums) from main channel
• Handles photos, videos, documents in albums with captions
• Approves join requests in bulk
• Auto-forwarding with duplicate prevention

**How to get Channel ID:**
1. Add bot to your channel as admin
2. Forward a message from channel to @username_to_id_bot
3. Or use: /getid in the channel

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
                "All posts (including media groups) from this channel will be forwarded to post channels.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error setting main channel: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    async def process_media_group(self, media_group_id: str, post_channels: List[dict], context: ContextTypes.DEFAULT_TYPE):
        """Process and forward a media group."""
        if media_group_id not in self.media_groups or not self.media_groups[media_group_id]:
            return
        
        messages = self.media_groups[media_group_id]
        logger.info(f"Processing media group {media_group_id} with {len(messages)} messages")
        
        # Sort by message_id to maintain order
        messages.sort(key=lambda x: x.message_id)
        
        # Find caption (from first message with caption)
        caption = None
        caption_entities = None
        for msg in messages:
            if msg.caption or (hasattr(msg, 'caption_entities') and msg.caption_entities):
                caption = msg.caption
                caption_entities = getattr(msg, 'caption_entities', None)
                logger.info(f"Found caption in message {msg.message_id}: {caption[:50] if caption else 'None'}")
                break
        
        # Prepare media list
        media_list = []
        for i, msg in enumerate(messages):
            try:
                if msg.photo:
                    # Get highest resolution photo
                    file_id = msg.photo[-1].file_id
                    if i == 0 and caption:
                        # Add caption to first media only
                        media_list.append(InputMediaPhoto(
                            media=file_id,
                            caption=caption,
                            caption_entities=caption_entities
                        ))
                    else:
                        media_list.append(InputMediaPhoto(media=file_id))
                    
                elif msg.video:
                    file_id = msg.video.file_id
                    if i == 0 and caption:
                        media_list.append(InputMediaVideo(
                            media=file_id,
                            caption=caption,
                            caption_entities=caption_entities
                        ))
                    else:
                        media_list.append(InputMediaVideo(media=file_id))
                    
                elif msg.document:
                    file_id = msg.document.file_id
                    if i == 0 and caption:
                        media_list.append(InputMediaDocument(
                            media=file_id,
                            caption=caption,
                            caption_entities=caption_entities
                        ))
                    else:
                        media_list.append(InputMediaDocument(media=file_id))
                        
                elif msg.audio:
                    file_id = msg.audio.file_id
                    if i == 0 and caption:
                        media_list.append(InputMediaDocument(
                            media=file_id,
                            caption=caption,
                            caption_entities=caption_entities
                        ))
                    else:
                        media_list.append(InputMediaDocument(media=file_id))
                        
            except Exception as e:
                logger.error(f"Error processing message {msg.message_id} in media group: {e}")
                continue
        
        if not media_list:
            logger.error(f"No valid media in group {media_group_id}")
            return
        
        # Forward to all post channels
        success_count = 0
        for channel in post_channels:
            try:
                # Check if any message from this group is already posted
                already_posted = False
                for msg in messages:
                    if db.is_message_posted(msg.message_id, channel["channel_id"]):
                        logger.info(f"Message {msg.message_id} already posted to {channel['channel_id']}")
                        already_posted = True
                        break
                
                if already_posted:
                    logger.info(f"Skipping channel {channel['channel_id']} - already posted")
                    continue
                
                logger.info(f"Forwarding media group to channel {channel['channel_id']}")
                
                # Send media group
                if len(media_list) == 1:
                    # For single media, use appropriate method
                    media = media_list[0]
                    if isinstance(media, InputMediaPhoto):
                        await context.bot.send_photo(
                            chat_id=channel["channel_id"],
                            photo=media.media,
                            caption=media.caption,
                            caption_entities=media.caption_entities
                        )
                    elif isinstance(media, InputMediaVideo):
                        await context.bot.send_video(
                            chat_id=channel["channel_id"],
                            video=media.media,
                            caption=media.caption,
                            caption_entities=media.caption_entities
                        )
                    elif isinstance(media, InputMediaDocument):
                        await context.bot.send_document(
                            chat_id=channel["channel_id"],
                            document=media.media,
                            caption=media.caption,
                            caption_entities=media.caption_entities
                        )
                else:
                    # For multiple media, send as group
                    await context.bot.send_media_group(
                        chat_id=channel["channel_id"],
                        media=media_list
                    )
                
                # Mark all messages as posted
                for msg in messages:
                    db.mark_message_posted(msg.message_id, channel["channel_id"])
                
                success_count += 1
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Failed to forward media group to {channel['channel_id']}: {e}")
        
        logger.info(f"Successfully forwarded media group {media_group_id} to {success_count} channels")
        
        # Clean up
        del self.media_groups[media_group_id]
        if media_group_id in self.processing_groups:
            del self.processing_groups[media_group_id]
    
    async def forward_single_message(self, message: Message, post_channels: List[dict], context: ContextTypes.DEFAULT_TYPE):
        """Forward a single message to all post channels."""
        success_count = 0
        
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
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Failed to forward to {channel['channel_id']}: {e}")
        
        if success_count > 0:
            logger.info(f"Forwarded single message {message.message_id} to {success_count} channels")
    
    async def forward_from_main_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Forward messages from main channel to post channels."""
        main_channel = db.get_main_channel()
        
        if not main_channel:
            return
        
        if str(update.channel_post.chat.id) != main_channel["channel_id"]:
            return
        
        message = update.channel_post
        post_channels = db.get_post_channels()
        
        if not post_channels:
            return
        
        logger.info(f"Received message {message.message_id} in main channel")
        
        # Check if message is part of a media group
        if hasattr(message, 'media_group_id') and message.media_group_id:
            media_group_id = message.media_group_id
            logger.info(f"Message {message.message_id} belongs to media group {media_group_id}")
            
            # Add message to media group collection
            self.media_groups[media_group_id].append(message)
            
            # If this is the first message of the group, schedule processing
            if media_group_id not in self.processing_groups:
                self.processing_groups[media_group_id] = True
                
                # Schedule processing after delay to collect all messages
                async def delayed_processing():
                    # Wait for all media group messages to arrive
                    await asyncio.sleep(3.0)  # Increased delay for better collection
                    
                    # Check if we still have this media group
                    if media_group_id in self.media_groups:
                        logger.info(f"Processing media group {media_group_id} after delay")
                        await self.process_media_group(media_group_id, post_channels, context)
                    else:
                        logger.info(f"Media group {media_group_id} already processed or removed")
                
                # Schedule the processing task
                context.application.create_task(delayed_processing())
            else:
                logger.info(f"Media group {media_group_id} is already being processed")
        else:
            # Single message (not part of media group)
            logger.info(f"Forwarding single message {message.message_id}")
            await self.forward_single_message(message, post_channels, context)
    
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
        stats_text += f"• **Total Channels:** {1 + len(post_channels)}\n"
        
        posted_count = db.posted_messages.count_documents({})
        stats_text += f"• **Messages Forwarded:** {posted_count}\n"
        
        stats_text += f"• **Active Media Groups:** {len(self.media_groups)}\n"
        stats_text += f"• **Processing Groups:** {len(self.processing_groups)}\n"
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cleanup_old_media_groups(self, context: ContextTypes.DEFAULT_TYPE):
        """Clean up old media groups that weren't processed."""
        try:
            current_time = datetime.now()
            groups_to_remove = []
            
            for group_id, messages in list(self.media_groups.items()):
                if messages:
                    first_msg_time = datetime.fromtimestamp(messages[0].date.timestamp())
                    if (current_time - first_msg_time).seconds > 30:  # 30 seconds old
                        groups_to_remove.append(group_id)
                        logger.warning(f"Removing old unprocessed media group {group_id}")
            
            for group_id in groups_to_remove:
                if group_id in self.media_groups:
                    del self.media_groups[group_id]
                if group_id in self.processing_groups:
                    del self.processing_groups[group_id]
                    
        except Exception as e:
            logger.error(f"Error in cleanup_old_media_groups: {e}")
    
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
            
            # Cleanup old media groups (every 5 minutes)
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
