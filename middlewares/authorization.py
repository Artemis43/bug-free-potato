from config import REQUIRED_CHANNELS
#from main import bot
from aiogram import types
import logging

# Helper function to check if the user is in Private chat with the bot
def is_private_chat(message: types.Message) -> bool:
    return message.chat.type == 'private'

# Helper function to check if the user is a member of the required channels (ForcedSubs)
async def is_user_member(user_id):
    from main import bot
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logging.error(f"Error checking membership for channel {channel}: {e}")
            return False
    return True