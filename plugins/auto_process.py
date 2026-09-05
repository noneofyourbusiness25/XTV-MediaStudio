import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from db import db

logger = logging.getLogger("AutoProcess")

# Cache source channels to avoid DB hit on every single message
_SOURCE_CHANNELS_CACHE = {}
_LAST_CACHE_UPDATE = 0

async def update_cache():
    global _LAST_CACHE_UPDATE, _SOURCE_CHANNELS_CACHE
    now = asyncio.get_event_loop().time()
    if now - _LAST_CACHE_UPDATE > 60: # refresh every 60s
        try:
            # Let's add a method to get a flattened map of {channel_id: user_id}
            all_sources = await db.get_all_source_channels_map()
            _SOURCE_CHANNELS_CACHE = all_sources
            _LAST_CACHE_UPDATE = now
        except Exception as e:
            logger.error(f"Failed to update source channels cache: {e}")

@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio | filters.photo) & ~filters.forwarded, group=-1)
async def auto_process_source(client: Client, message: Message):
    await update_cache()

    chat_id = str(message.chat.id)
    if chat_id in _SOURCE_CHANNELS_CACHE or message.chat.id in _SOURCE_CHANNELS_CACHE:
        user_id = _SOURCE_CHANNELS_CACHE.get(chat_id) or _SOURCE_CHANNELS_CACHE.get(message.chat.id)

        # Route to processing pipeline on behalf of user
        logger.info(f"Auto-processing new file from source channel {chat_id} for user {user_id}")

        from pyrogram.types import User
        # Mocking user to avoid API call failures for cached users
        message.from_user = User(id=user_id, is_self=False, is_bot=False, first_name="Auto", is_contact=False, is_mutual_contact=False, is_deleted=False, is_verified=False, is_restricted=False, is_scam=False, is_fake=False, is_support=False, is_premium=True)
        message.chat.id = user_id

        from plugins.flow.upload import handle_file_upload
        # Forward it to the flow logic
        await handle_file_upload(client, message)
