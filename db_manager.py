# -*- coding: utf-8 -*-
import aiosqlite
import sqlite3
import logging
import time
import os

# --- Database Configuration ---
DATABASE_NAME = os.getenv("DATABASE_FILE", "chat_history.db") # Get DB name from env or default

# --- Database Initialization ---
def init_db():
    """
    Initializes the SQLite database synchronously at startup.
    Creates the messages table if it doesn't exist.
    """
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        # Create table with user_id, message content, and timestamp
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        # Create an index on timestamp for faster history retrieval
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON messages (timestamp);
        """)
        conn.commit()
        logging.info(f"Database '{DATABASE_NAME}' initialized successfully.")
    except sqlite3.Error as e:
        logging.error(f"Database initialization failed: {e}", exc_info=True)
        raise # Stop server if DB cannot be initialized
    finally:
        if conn:
            conn.close()

# --- Async Database Operations ---

async def add_message(user_id: str, message_content: str, timestamp: float):
    """
    Asynchronously adds a chat message to the database.
    """
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            await db.execute(
                "INSERT INTO messages (user_id, message, timestamp) VALUES (?, ?, ?)",
                (user_id, message_content, timestamp)
            )
            await db.commit()
        logging.debug(f"Saved message from {user_id} to database.")
        return True # Indicate success
    except Exception as e:
        logging.error(f"Failed to save message from {user_id} to database: {e}", exc_info=True)
        return False # Indicate failure

async def get_message_history(limit: int = 20) -> list[dict]:
    """
    Asynchronously retrieves the most recent messages from the database.

    Args:
        limit: The maximum number of messages to retrieve.

    Returns:
        A list of message dictionaries ({"user": ..., "message": ..., "timestamp": ...}),
        ordered chronologically (oldest of the batch first).
        Returns an empty list if an error occurs or no messages are found.
    """
    history_messages = []
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Fetch latest messages based on timestamp, descending order
            async with db.execute(
                "SELECT user_id, message, timestamp FROM messages ORDER BY timestamp DESC LIMIT ?",
                (limit,) # Pass limit as parameter
            ) as cursor:
                rows = await cursor.fetchall()
                # Reverse the list so the oldest message of the batch is first
                rows.reverse()
                # Format into dictionaries
                history_messages = [
                    {"user": row[0], "message": row[1], "timestamp": row[2]}
                    for row in rows
                ]
        logging.debug(f"Retrieved {len(history_messages)} messages from history.")
    except Exception as e:
        logging.error(f"Failed to retrieve message history: {e}", exc_info=True)
        # Return empty list on error
        history_messages = []

    return history_messages

