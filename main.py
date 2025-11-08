from transformers import AutoModelForCausalLM, AutoTokenizer
from openai import OpenAI
from transformers.utils import get_json_schema
from atproto import Client, models, AtUri
from time import sleep
import sqlite3
import os

# Initialize database
DB_PATH = "notifications.db"

def init_database():
    """Initialize the SQLite database and create the table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS replied_notifications (
            cid TEXT PRIMARY KEY,
            uri TEXT,
            replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def has_replied_to_cid(cid):
    """Check if we've already replied to this notification CID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT cid FROM replied_notifications WHERE cid = ?', (cid,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_replied_cid(cid, uri):
    """Save the notification CID to the database after replying"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO replied_notifications (cid, uri) VALUES (?, ?)', (cid, uri))
        conn.commit()
        conn.close()
        print(f"Saved CID to database: {cid}")
    except sqlite3.Error as e:
        print(f"Database error: {e}")

# Initialize database on startup
init_database()

client = Client()

handle_name = 'handle-here'
app_password = 'app_password-here'

open_client = OpenAI(base_url="http://127.0.0.1:1234/v1", api_key="nothing")

client.login(handle_name, app_password)

def check_replies(thread_post, handle_name):
    """Check if we've already replied by looking at thread replies"""
    didnt_reply = True
    if hasattr(thread_post.thread, 'replies') and thread_post.thread.replies:
        for i in range(len(thread_post.thread.replies)):
            if handle_name == thread_post.thread.replies[i].post.author.handle:
                print(f"Found our reply in thread: {thread_post.thread.replies[i].post.author.handle}")
                didnt_reply = False
                break
    return didnt_reply

def name_of_user(thread_post):
    name = thread_post.thread.post.author.display_name
    return name

def text_of_parent_post(thread_post):
    text = thread_post.thread.parent.post.record.text
    return str(text)

def get_chat_start(text, text2, text3):
    chat = [
        {"role": "system", "content": f"You are a helpful assistant (you better not say user thinks). Be concise and share your opinion. the post above the user is asking about: '{text3}'."},
        {"role": "user", "content": str(text).replace(('@' + handle_name), '')},
    ]
    return chat

def get_chat_start_without_context(text):
    chat = [
        {"role": "system", "content": f"You are a helpful assistant (you better not say user thinks)."},
        {"role": "user", "content": str(text).replace(('@' + handle_name), '')},
    ]
    return chat

while True:
    last_seen_at = client.get_current_time_iso()

    responses = client.app.bsky.notification.list_notifications()
    for notification in responses.notifications:
        if notification.reason in ['mention','reply']:
            # Check if we've already replied using CID (database check)
            if has_replied_to_cid(notification.cid):
                print(f"Already replied to CID (database): {notification.cid}")
                continue
            
            thread_post = client.get_post_thread(notification.uri)

            def get_name_of_user() -> str:
                """
                Gets the of the user name.
                """
                names = name_of_user(thread_post)
                return names

            def get_text_of_parent_post() -> str:
                """
                Gets the text in the parent post.
                """
                text = text_of_parent_post(thread_post)
                return text

            # Also check using the existing reply checking method
            didnt_reply = check_replies(thread_post, handle_name)
            
            if didnt_reply is True:
                chat_template = None
                if notification.reason == 'reply':
                    chat_template = get_chat_start_without_context(
                        notification.record.text,
                    )
                else:
                    chat_template = get_chat_start(
                        notification.record.text,
                        get_name_of_user(),
                        get_text_of_parent_post()
                    )
                response = open_client.responses.create(
                    model="granite-4.0-micro",
                    input=chat_template,
                    store=False,
                )
                root_post_ref = models.create_strong_ref(thread_post.thread.parent.post)
                reply_to_root = models.create_strong_ref(
                    client.get_post(
                        post_rkey=AtUri.from_str(notification.uri).rkey,
                        cid=notification.cid,
                        profile_identify=notification.author.did
                    )
                )
                responsef = response.output_text
                client.send_post(
                    text=responsef[0:300],
                    reply_to=models.AppBskyFeedPost.ReplyRef(parent=reply_to_root, root=root_post_ref),
                )
                
                # Save the CID to database after successful reply
                save_replied_cid(notification.cid, notification.uri)
                
                print(f"Response sent: {response.output_text}")
                print(f"Didn't reply (thread check): {didnt_reply}")
            else:
                print(f"Did reply (thread check): {not didnt_reply}")
                # Even if we found our reply in the thread, save the CID to database
                save_replied_cid(notification.cid, notification.uri)
                continue

    client.app.bsky.notification.update_seen({'seen_at': last_seen_at})
    print('Processed notifications')

    sleep(5)