import logging
import re
from telegram import Update, Bot, ChatMemberAdministrator, ChatJoinRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatJoinRequestHandler,
    ConversationHandler
)
from telegram.constants import ParseMode
import asyncio
from datetime import datetime, timedelta
from config import Config
from database import db

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states for /delold command
WAITING_FOR_LINK, WAITING_FOR_CONFIRMATION = range(2)

class ChannelBot:
    def __init__(self):
        self.application = None
        self.delold_data = {}  # Store temporary data for /delold
    
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
• `/delold` - Delete old messages using post link

**Auto Features:**
• Auto-forward messages from main to post channels
• Auto-delete messages in post channels when deleted in main channel
• Auto-approve join requests (scheduled)

**How to get Channel ID:**
1. Add bot to your channel as admin
2. Forward a message from channel to @username_to_id_bot
3. Or use: /getid in the channel

**Required Bot Permissions:**
- In Main Channel: Post messages permission + Delete messages
- In Post Channels: Admin with all permissions
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
            
            # Check if bot has delete permission (for auto-delete feature)
            if not bot_member.can_delete_messages:
                await update.message.reply_text(
                    f"⚠️ Warning: Bot doesn't have 'Delete Messages' permission in {chat.title}\n"
                    "Auto-delete feature will not work.\n\n"
                    "Still adding channel..."
                )
            
            # Add to database
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
            
            # Check delete permission for auto-delete
            if not bot_member.can_delete_messages:
                await update.message.reply_text(
                    f"⚠️ Warning: Bot doesn't have 'Delete Messages' permission in {chat.title}\n"
                    "Auto-delete from main channel will not work."
                )
            
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
                "All posts from this channel will be forwarded to post channels.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error setting main channel: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    # ... (approve_requests, get_all_pending_requests, approve_join_request methods remain same)
    
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
        
        success_count = 0
        failed_channels = []
        
        for channel in post_channels:
            try:
                if db.is_message_posted(message.message_id, channel["channel_id"]):
                    continue
                
                # Forward the message
                forwarded_msg = await context.bot.forward_message(
                    chat_id=channel["channel_id"],
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                
                # Store mapping between original and forwarded message
                db.add_message_mapping(
                    main_message_id=message.message_id,
                    post_message_id=forwarded_msg.message_id,
                    post_channel_id=channel["channel_id"],
                    main_channel_id=main_channel["channel_id"]
                )
                
                db.mark_message_posted(message.message_id, channel["channel_id"])
                success_count += 1
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Failed to forward to {channel['channel_id']}: {e}")
                failed_channels.append(channel.get('title', channel['channel_id']))
        
        if success_count > 0:
            logger.info(f"Forwarded message {message.message_id} to {success_count} channels")
        
        if failed_channels:
            logger.warning(f"Failed to forward to: {failed_channels}")
    
    async def handle_deleted_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle when a message is deleted in main channel - delete in post channels."""
        main_channel = db.get_main_channel()
        
        if not main_channel:
            return
        
        deleted_message = update.deleted_message or update.message
        
        # Check if message was deleted from main channel
        if str(deleted_message.chat.id) == main_channel["channel_id"]:
            logger.info(f"Message {deleted_message.message_id} deleted in main channel")
            
            # Get all post channel mappings for this message
            mappings = db.get_message_mappings_by_main(
                deleted_message.message_id,
                main_channel["channel_id"]
            )
            
            if not mappings:
                return
            
            deleted_count = 0
            failed_count = 0
            
            for mapping in mappings:
                try:
                    # Delete the message in post channel
                    await context.bot.delete_message(
                        chat_id=mapping["post_channel_id"],
                        message_id=mapping["post_message_id"]
                    )
                    
                    # Remove from database
                    db.delete_message_mapping(
                        mapping["post_message_id"],
                        mapping["post_channel_id"]
                    )
                    
                    deleted_count += 1
                    logger.info(f"Deleted message {mapping['post_message_id']} in channel {mapping['post_channel_id']}")
                    
                except Exception as e:
                    logger.error(f"Failed to delete message in channel {mapping['post_channel_id']}: {e}")
                    failed_count += 1
                
                await asyncio.sleep(0.3)
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} messages from post channels")
    
    # NEW: /delold command implementation
    async def delold_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the /delold conversation."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return ConversationHandler.END
        
        await update.message.reply_text(
            "📝 **Delete Message by Link**\n\n"
            "Please send me the **post link** you want to delete.\n\n"
            "Example: `https://t.me/channel_username/123`\n"
            "or: `https://t.me/c/1234567890/123`\n\n"
            "Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return WAITING_FOR_LINK
    
    async def delold_process_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process the Telegram message link."""
        link = update.message.text.strip()
        
        # Parse Telegram message link
        pattern1 = r'https://t\.me/(c/)?(\d+)/(\d+)'
        pattern2 = r'https://t\.me/(\w+)/(\d+)'
        
        chat_id = None
        message_id = None
        
        # Try pattern 1: t.me/c/1234567890/123
        match = re.search(pattern1, link)
        if match:
            chat_id = f"-100{match.group(2)}"  # Convert to channel format
            message_id = int(match.group(3))
        else:
            # Try pattern 2: t.me/channel_username/123
            match = re.search(pattern2, link)
            if match:
                username = match.group(1)
                message_id = int(match.group(2))
                
                try:
                    # Get chat by username
                    chat = await context.bot.get_chat(f"@{username}")
                    chat_id = str(chat.id)
                except Exception as e:
                    await update.message.reply_text(f"❌ Cannot find channel with username @{username}")
                    return WAITING_FOR_LINK
        
        if not chat_id or not message_id:
            await update.message.reply_text(
                "❌ Invalid link format.\n"
                "Please send a valid Telegram message link.\n"
                "Example: https://t.me/channel_username/123\n"
                "or: https://t.me/c/1234567890/123\n\n"
                "Type /cancel to cancel."
            )
            return WAITING_FOR_LINK
        
        # Check if bot is admin in this channel
        try:
            chat = await context.bot.get_chat(chat_id)
            bot_member = await chat.get_member(context.bot.id)
            
            if not isinstance(bot_member, ChatMemberAdministrator):
                await update.message.reply_text(
                    f"❌ Bot is not admin in channel: {chat.title}\n"
                    "Cannot delete messages."
                )
                return WAITING_FOR_LINK
            
            if not bot_member.can_delete_messages:
                await update.message.reply_text(
                    f"❌ Bot doesn't have 'Delete Messages' permission in: {chat.title}\n"
                    "Cannot delete messages."
                )
                return WAITING_FOR_LINK
            
            # Try to get the message to confirm it exists
            try:
                msg = await context.bot.get_message(chat_id, message_id)
                message_text = msg.text or msg.caption or "Media message"
                preview = message_text[:100] + "..." if len(message_text) > 100 else message_text
            except:
                preview = "Message not found or inaccessible"
            
            # Store data for confirmation
            self.delold_data[user_id] = {
                'chat_id': chat_id,
                'message_id': message_id,
                'chat_title': chat.title,
                'preview': preview
            }
            
            await update.message.reply_text(
                f"✅ Found message in channel: **{chat.title}**\n"
                f"📝 Preview: {preview}\n\n"
                "Are you sure you want to delete this message?\n"
                "Reply with: `yes` to confirm or `no` to cancel.",
                parse_mode=ParseMode.MARKDOWN
            )
            
            return WAITING_FOR_CONFIRMATION
            
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
            return WAITING_FOR_LINK
    
    async def delold_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Confirm and delete the message."""
        user_id = update.effective_user.id
        response = update.message.text.lower()
        
        if user_id not in self.delold_data:
            await update.message.reply_text("❌ Session expired. Please start again with /delold")
            return ConversationHandler.END
        
        if response not in ['yes', 'y']:
            await update.message.reply_text("❌ Deletion cancelled.")
            del self.delold_data[user_id]
            return ConversationHandler.END
        
        data = self.delold_data[user_id]
        
        try:
            # Delete the message
            await context.bot.delete_message(
                chat_id=data['chat_id'],
                message_id=data['message_id']
            )
            
            # Also delete from database if it exists in mappings
            mapping = db.get_message_mapping_by_post(data['message_id'], data['chat_id'])
            if mapping:
                db.delete_message_mapping(data['message_id'], data['chat_id'])
            
            await update.message.reply_text(
                f"✅ Message successfully deleted from **{data['chat_title']}**\n"
                f"Message ID: `{data['message_id']}`",
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Deleted message {data['message_id']} from {data['chat_title']}")
            
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            await update.message.reply_text(f"❌ Failed to delete message: {str(e)}")
        
        # Clean up
        del self.delold_data[user_id]
        return ConversationHandler.END
    
    async def delold_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the /delold operation."""
        user_id = update.effective_user.id
        if user_id in self.delold_data:
            del self.delold_data[user_id]
        
        await update.message.reply_text("❌ Operation cancelled.")
        return ConversationHandler.END
    
    async def delete_old_messages_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete old messages (30+ days) from all channels."""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return
        
        if not context.args:
            days = 30
        else:
            try:
                days = int(context.args[0])
                if days < 1:
                    days = 30
            except:
                days = 30
        
        status_msg = await update.message.reply_text(f"🔍 Searching for messages older than {days} days...")
        
        try:
            old_messages = db.get_old_messages(days)
            
            if not old_messages:
                await status_msg.edit_text(f"✅ No messages found older than {days} days.")
                return
            
            await status_msg.edit_text(f"📊 Found {len(old_messages)} old messages.\n⏳ Starting deletion...")
            
            deleted_count = 0
            failed_count = 0
            
            for i, mapping in enumerate(old_messages, 1):
                if i % 10 == 0:
                    await status_msg.edit_text(f"⏳ Processing {i}/{len(old_messages)}... Deleted: {deleted_count}")
                
                try:
                    # Try to delete the message
                    await context.bot.delete_message(
                        chat_id=mapping["post_channel_id"],
                        message_id=mapping["post_message_id"]
                    )
                    
                    # Remove from database
                    db.delete_message_mapping(
                        mapping["post_message_id"],
                        mapping["post_channel_id"]
                    )
                    
                    deleted_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to delete old message: {e}")
                    failed_count += 1
                
                await asyncio.sleep(0.3)
            
            # Clean up posted messages records
            db.cleanup_old_messages(days)
            
            await status_msg.edit_text(
                f"✅ **Old Messages Cleanup Complete**\n\n"
                f"• Total processed: {len(old_messages)}\n"
                f"• Successfully deleted: {deleted_count}\n"
                f"• Failed: {failed_count}\n"
                f"• Older than: {days} days",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error in delete_old_messages_command: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    # ... (other methods: list_channels, remove_channel, stats_command, etc.)
    
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
        self.application.add_handler(CommandHandler("cleanup", self.delete_old_messages_command))
        
        # Conversation handler for /delold
        delold_conversation = ConversationHandler(
            entry_points=[CommandHandler("delold", self.delold_start)],
            states={
                WAITING_FOR_LINK: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.delold_process_link)
                ],
                WAITING_FOR_CONFIRMATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.delold_confirm)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.delold_cancel)]
        )
        self.application.add_handler(delold_conversation)
        
        # Join request handler
        self.application.add_handler(ChatJoinRequestHandler(self.handle_join_request))
        
        # Message handlers
        # For forwarding messages
        self.application.add_handler(
            MessageHandler(filters.ChatType.CHANNEL & filters.FORWARDED, self.forward_from_main_channel)
        )
        
        # For handling deleted messages (when bot sees message is deleted)
        self.application.add_handler(
            MessageHandler(filters.ChatType.CHANNEL & filters.UpdateType.EDITED_MESSAGE, self.handle_deleted_message)
        )
        
        # For regular channel posts (non-forwarded)
        self.application.add_handler(
            MessageHandler(filters.ChatType.CHANNEL & ~filters.FORWARDED, self.forward_from_main_channel)
        )
        
        # Schedule auto-approval job
        job_queue = self.application.job_queue
        if job_queue:
            job_queue.run_repeating(
                self.auto_approve_old_requests,
                interval=86400,
                first=10
            )
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
        
        # Start the Bot
        logger.info("Starting bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    bot = ChannelBot()
    bot.run()

if __name__ == '__main__':
    main()
