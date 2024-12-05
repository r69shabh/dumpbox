import os
import logging
import asyncio
from datetime import datetime
import json

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    ConversationHandler, 
    ContextTypes, 
    filters
)
from tinydb import TinyDB, Query
from tinydb.table import Table
import httpx

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    filename='bot.log'
)
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Telegram Bot Token
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Database and Storage Configuration
BASE_STORAGE_PATH = os.path.join(os.path.dirname(__file__), 'storage')
os.makedirs(BASE_STORAGE_PATH, exist_ok=True)

# Initialize TinyDB databases
files_db = TinyDB(os.path.join(BASE_STORAGE_PATH, 'files.json'))
folders_db = TinyDB(os.path.join(BASE_STORAGE_PATH, 'folders.json'))

# Conversation States
(
    SELECTING_ACTION, 
    FOLDER_NAME, 
    FILE_UPLOAD, 
    AWAITING_RENAME_CHOICE, 
    AWAITING_FILENAME, 
    AWAITING_FOLDER,
    MOVE_FILE,
    AWAITING_MOVE_DESTINATION,
    SHARING
) = range(9)

class FileManager:
    @staticmethod
    async def download_file(bot: telegram.Bot, file_id: str, user_id: int) -> dict:
        """
        Download a file from Telegram servers and save locally
        
        Args:
            bot: Telegram Bot instance
            file_id: Unique Telegram file identifier
            user_id: User ID who uploaded the file
        
        Returns:
            Dictionary with file metadata
        """
        try:
            # Get file information from Telegram
            file_info = await bot.get_file(file_id)
            
            # Generate unique filename
            original_filename = file_info.file_path.split('/')[-1]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            user_folder = os.path.join(BASE_STORAGE_PATH, str(user_id))
            os.makedirs(user_folder, exist_ok=True)
            
            # Download file
            local_filename = f"{timestamp}_{original_filename}"
            local_path = os.path.join(user_folder, local_filename)
            
            async with httpx.AsyncClient() as client:
                response = await client.get(file_info.file_url)
                with open(local_path, 'wb') as f:
                    f.write(response.content)
            
            # Prepare file metadata
            file_metadata = {
                'file_id': file_id,
                'name': local_filename,
                'original_name': original_filename,
                'path': local_path,
                'user_id': user_id,
                'size': os.path.getsize(local_path),
                'mime_type': file_info.mime_type,
                'uploaded_at': datetime.now().isoformat()
            }
            
            # Store metadata in database
            files_db.insert(file_metadata)
            
            return file_metadata
        
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            raise

    @staticmethod
    def list_user_files(user_id: int, folder_path: str = '/') -> list:
        """
        List files for a specific user in a given folder
        
        Args:
            user_id: User ID
            folder_path: Folder path to list files from
        
        Returns:
            List of file metadata
        """
        File = Query()
        return files_db.search(
            (File.user_id == user_id) & 
            (File.folder == folder_path)
        )

    @staticmethod
    def list_user_folders(user_id: int, parent_folder: str = '/') -> list:
        """
        List folders for a specific user
        
        Args:
            user_id: User ID
            parent_folder: Parent folder path
        
        Returns:
            List of folder metadata
        """
        Folder = Query()
        return folders_db.search(
            (Folder.user_id == user_id) & 
            (Folder.parent == parent_folder)
        )

class TelegramCloudStorageBot:
    def __init__(self, token):
        self.token = token
        self.file_manager = FileManager()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Start command handler
        
        Provides main menu and initial instructions
        """
        user = update.effective_user
        keyboard = [
            [
                InlineKeyboardButton("📂 Files", callback_data="list_files"),
                InlineKeyboardButton("📤 Upload", callback_data="upload_file")
            ],
            [
                InlineKeyboardButton("📁 New Folder", callback_data="create_folder"),
                InlineKeyboardButton("🔍 Search", callback_data="search_files")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Welcome, {user.first_name}! 🤖\n"
            "What would you like to do with your cloud storage?",
            reply_markup=reply_markup
        )

    async def list_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        List files and folders for the user
        """
        query = update.callback_query
        user_id = query.from_user.id
        
        try:
            # Retrieve files and folders
            files = self.file_manager.list_user_files(user_id)
            folders = self.file_manager.list_user_folders(user_id)
            
            # Prepare keyboard
            keyboard = []
            
            # Add folder buttons
            for folder in folders:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📁 {folder['name']}", 
                        callback_data=f"open_folder_{folder['name']}"
                    )
                ])
            
            # Add file buttons
            for file in files:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📄 {file['name']}", 
                        callback_data=f"file_details_{file['file_id']}"
                    )
                ])
            
            # Add navigation buttons
            keyboard.append([
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("➕ New", callback_data="upload_file")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📂 Your Files and Folders:", 
                reply_markup=reply_markup
            )
        
        except Exception as e:
            logger.error(f"Error listing files: {e}")
            await query.answer(text="❌ Could not list files. Please try again.")

    async def upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle file upload process
        """
        user_id = update.effective_user.id
        
        if update.message and update.message.document:
            file = update.message.document
            try:
                # Download and save file
                file_metadata = await self.file_manager.download_file(
                    context.bot, 
                    file.file_id, 
                    user_id
                )
                
                await update.message.reply_text(
                    f"✅ File '{file_metadata['name']}' uploaded successfully!"
                )
            
            except Exception as e:
                logger.error(f"File upload error: {e}")
                await update.message.reply_text("❌ File upload failed. Please try again.")
        
        else:
            await update.message.reply_text(
                "📤 Please upload a file. You can send documents, photos, or any other file type."
            )

    async def create_folder(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Create a new folder for the user
        """
        user_id = update.effective_user.id
        folder_name = context.user_data.get('new_folder_name')
        
        if folder_name:
            try:
                # Check if folder already exists
                Folder = Query()
                existing_folder = folders_db.search(
                    (Folder.name == folder_name) & 
                    (Folder.user_id == user_id)
                )
                
                if existing_folder:
                    await update.message.reply_text(
                        f"❌ Folder '{folder_name}' already exists!"
                    )
                    return ConversationHandler.END
                
                # Create folder entry
                folders_db.insert({
                    'name': folder_name,
                    'user_id': user_id,
                    'parent': '/',
                    'created_at': datetime.now().isoformat()
                })
                
                await update.message.reply_text(
                    f"✅ Folder '{folder_name}' created successfully!"
                )
                return ConversationHandler.END
            
            except Exception as e:
                logger.error(f"Folder creation error: {e}")
                await update.message.reply_text("❌ Could not create folder. Please try again.")
                return ConversationHandler.END
        
        else:
            await update.message.reply_text("📁 Please enter a name for the new folder:")
            return FOLDER_NAME

    async def handle_folder_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Process the folder name input
        """
        folder_name = update.message.text.strip()
        context.user_data['new_folder_name'] = folder_name
        return await self.create_folder(update, context)

    def build_application(self):
        """
        Build and configure the Telegram bot application
        """
        application = Application.builder().token(self.token).build()
        
        # Conversation handler for file operations
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', self.start),
                CallbackQueryHandler(self.list_files, pattern="list_files"),
                CallbackQueryHandler(self.upload_file, pattern="upload_file"),
                CallbackQueryHandler(self.create_folder, pattern="create_folder")
            ],
            states={
                FOLDER_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_folder_name)
                ]
            },
            fallbacks=[CommandHandler('start', self.start)]
        )
        
        # File upload handler
        upload_handler = MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO, 
            self.upload_file
        )
        
        # Add handlers
        application.add_handler(conv_handler)
        application.add_handler(upload_handler)
        
        return application

def main():
    """
    Main function to run the Telegram bot
    """
    try:
        bot = TelegramCloudStorageBot(TOKEN)
        application = bot.build_application()
        
        logger.info("Starting Telegram Cloud Storage Bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    
    except Exception as e:
        logger.error(f"Bot initialization error: {e}")

if __name__ == '__main__':
    main()
