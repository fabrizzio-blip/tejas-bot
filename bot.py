import os
import time
import json
import threading
import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from pinecone import Pinecone
import voyageai
import fitz
from docx import Document
import io

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CREDENTIALS_FILE = "credentials.json"
FOLDER_ID = "11_WUZ0BmlAugr5KiJpwNmx4QqnojgqM4"
ADMIN_CHANNEL = "C0B2NTD8DT6"
ADMIN_USER = "U0A9NJB217B"
ADMIN_USERS = ["U0A9NJB217B", "U06BEBPV6CE", "U08LCQR3BBK"]

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "pcsk_36APWc_MhxapzxeUveaZu1CCmSQgSaRsHgFNgy1d7777wkZgYs2PNWFgRULPmYniwWTtdy")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "pa-XfIYHO-YOtcroKMitig66-uAV4fUKmUPnrO69gYojMd")

pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index("tia-docs")
vo = voyageai.Client(api_key=VOYAGE_API_KEY)

SYSTEM_PROMPT = """You are TIA (Tejas Information Assistant), the internal knowledge assistant for Tejas Equipment Rentals. 
You only answer questions using the company documents provided to you.
If the answer is not in the documents, say: 'I don't have that information in our current knowledge base. Please contact the appropriate department directly.'
Never use outside knowledge or make things up.

Format all responses for Slack using these rules:
- Use *bold* for important terms (single asterisk, not double)
- Use • for bullet points
- Use clean numbered lists (1. 2. 3.) for steps
- Never use ## or ### for headers — use *Header Title* on its own line instead
- Never use --- for dividers
- Keep responses concise and well organized
- Use relevant emojis sparingly to make responses friendly (✅ for confirmations, ⚠️ for warnings, 📋 for processes, 👤 for HR topics, 💰 for pay/finance topics)
- Leave a blank line between sections for readability

At the end of every answer, add a new line that says '📄 *Source:*' followed by the file name(s) you used, and format each Google Drive link like this so it's clickable in Slack: <https://drive.google.com/...|File Name>"""

def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

def get_all_drive_files(service, folder_id):
    files = []
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, webViewLink)"
    ).execute()
    items = results.get("files", [])
    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            files.extend(get_all_drive_files(service, item["id"]))
        else:
            files.append(item)
    return files

def read_file(service, file):
    mime = file["mimeType"]
    file_id = file["id"]
    try:
        if mime == "application/vnd.google-apps.document":
            content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
            return content.decode("utf-8", errors="ignore")
        elif mime == "application/vnd.google-apps.presentation":
            content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
            return content.decode("utf-8", errors="ignore")
        elif mime == "application/pdf":
            request = service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            doc = fitz.open(stream=buffer.read(), filetype="pdf")
            return "\n".join([page.get_text() for page in doc])
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            request = service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            doc = Document(buffer)
            return "\n".join([para.text for para in doc.paragraphs])
        else:
            return ""
    except Exception:
        return ""

def search_relevant_docs(question, top_k=8):
    try:
        embedding = vo.embed(
            [question],
            model="voyage-3",
            input_type="query"
        ).embeddings[0]

        results = index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True
        )

        docs_text = ""
        seen_files = set()
        for match in results.matches:
            metadata = match.metadata
            file_name = metadata.get("file_name", "Unknown")
            web_link = metadata.get("web_view_link", "")
            text = metadata.get("text", "")

            if file_name not in seen_files:
                seen_files.add(file_name)
                docs_text += f"\n\n--- {file_name} (Link: {web_link}) ---\n"
            docs_text += text + "\n"

        return docs_text if docs_text else "No relevant documents found."
    except Exception as e:
        return f"Error searching documents: {e}"

def ask_claude(question, docs):
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"Company documents:\n{docs}\n\nEmployee question: {question}"
            }
        ],
        system=SYSTEM_PROMPT
    )
    return message.content[0].text

def ask_claude_with_history(conversation, docs):
    messages = []
    for i, msg in enumerate(conversation):
        if i == 0 and msg["role"] == "user":
            messages.append({
                "role": "user",
                "content": f"Company documents:\n{docs}\n\nEmployee question: {msg['content']}"
            })
        else:
            messages.append(msg)

    cleaned = []
    last_role = None
    for msg in messages:
        if msg["role"] != last_role:
            cleaned.append(msg)
            last_role = msg["role"]

    if not cleaned or cleaned[0]["role"] != "user":
        cleaned = [{"role": "user", "content": "Hello"}] + cleaned

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=cleaned,
        system=SYSTEM_PROMPT
    )
    return message.content[0].text

def send_with_feedback(say, answer, question, user, thread_ts=None):
    say(
        thread_ts=thread_ts,
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": answer}
            },
            {
                "type": "actions",
                "block_id": "feedback_buttons",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Helpful"},
                        "style": "primary",
                        "action_id": "feedback_helpful",
                        "value": json.dumps({"question": question[:200], "answer": answer[:200], "user": user})
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "⚠️ Incomplete/Wrong"},
                        "style": "danger",
                        "action_id": "feedback_wrong",
                        "value": json.dumps({"question": question[:200], "answer": answer[:200], "user": user})
                    }
                ]
            }
        ]
    )

@app.event("app_home_opened")
def update_home_tab(event, client):
    client.views_publish(
        user_id=event["user"],
        view={
            "type": "home",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "👋 Welcome to TIA - Tejas Information Assistant!"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "I'm *TIA*, your internal AI assistant for Tejas Equipment Rentals. Ask me anything about company processes, policies, and procedures — I'll answer using only verified internal documents."}
                },
                {
                    "type": "divider"
                },
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🔍 How to use me"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*In any channel:*\nMention me with your question:\n`@TIA what is the hiring process?`"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Follow-up questions:*\nReply in the same thread and I'll remember the conversation context."}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Rate my answers:*\nUse the ✅ Helpful or ⚠️ Incomplete/Wrong buttons after every answer to help improve the knowledge base."}
                },
                {
                    "type": "divider"
                },
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "💡 Example questions"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "• What is the hiring process?\n• How do I process a payment waiver?\n• What are the steps for onboarding a new employee?\n• How do I generate a daily inventory report?\n• What is the process for posting ACH payments?\n• How do I process a refund?"}
                },
                {
                    "type": "divider"
                },
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "⚠️ Important notes"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "• I only answer from *verified Tejas internal documents*\n• If I don't have the answer, I'll let you know and point you to the right department\n• Every answer includes a 📄 source link to the original Google Drive document\n• Your direct message conversations with me are *completely private* 🔒"}
                },
                {
                    "type": "divider"
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "🛠️ *TIA - Tejas Information Assistant* | Questions about the bot? Contact <@U0A9NJB217B>"
                        }
                    ]
                }
            ]
        }
    )

@app.event("app_mention")
def handle_mention(event, say, client):
    question = event["text"]
    user = event["user"]
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    # Help command
    if "help" in question.lower():
        say(
            thread_ts=thread_ts,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "👋 *Hi! I'm TIA — Tejas Information Assistant!*\nHere's what I can do:"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "🔍 *Ask me anything* about company processes, SOPs, HR policies, billing, equipment, and more.\n\n💬 *Follow-up questions* — reply in the same thread and I'll remember the conversation.\n\n✅ *Rate my answers* — use the Helpful or Incomplete/Wrong buttons after every answer.\n\n📄 *Sources* — every answer includes a link to the original Google Drive document.\n\n🔒 *Privacy* — your direct message conversations with me are completely private."}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "💡 *Example questions:*\n• What is the hiring process?\n• How do I process a payment waiver?\n• What are the steps for onboarding a new employee?\n• How do I generate a daily inventory report?"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "⚠️ *Note:* I only answer from verified Tejas internal documents. If I don't have the answer, I'll let you know and point you to the right department."}
                }
            ]
        )
        return

    # Refresh command — only admins can use it
    if "refresh" in question.lower():
        if user in ADMIN_USERS:
            say(text="🔄 Re-indexing documents from Google Drive... this will take a few minutes. I'll let you know when done.", thread_ts=thread_ts)
            try:
                def reindex():
                    try:
                        drive_service = get_drive_service()
                        all_files = get_all_drive_files(drive_service, FOLDER_ID)
                        index.delete(delete_all=True)
                        time.sleep(2)
                        vectors = []
                        for file in all_files:
                            content = read_file(drive_service, file)
                            if not content.strip():
                                continue
                            chunks = [content[j:j+1000] for j in range(0, len(content), 800)]
                            for k, chunk in enumerate(chunks):
                                if not chunk.strip():
                                    continue
                                try:
                                    embedding = vo.embed([chunk], model="voyage-3", input_type="document").embeddings[0]
                                    vectors.append({
                                        "id": f"{file['id']}_{k}",
                                        "values": embedding,
                                        "metadata": {
                                            "file_name": file["name"],
                                            "file_id": file["id"],
                                            "web_view_link": file.get("webViewLink", ""),
                                            "text": chunk[:500]
                                        }
                                    })
                                    if len(vectors) >= 50:
                                        index.upsert(vectors=vectors)
                                        vectors = []
                                        time.sleep(0.5)
                                except Exception:
                                    continue
                        if vectors:
                            index.upsert(vectors=vectors)
                        app.client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text="✅ Re-indexing complete! TIA now has the latest documents from Google Drive."
                        )
                    except Exception as e:
                        app.client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"⚠️ Re-indexing failed: {e}"
                        )
                threading.Thread(target=reindex, daemon=True).start()
            except Exception as e:
                say(text=f"⚠️ Could not start re-indexing: {e}", thread_ts=thread_ts)
        else:
            say(text="Sorry, only admins can refresh the documents. 🔒", thread_ts=thread_ts)
        return

    say(
        text=f"<@{user}> Let me look that up for you... 🔍",
        thread_ts=thread_ts
    )

    docs = search_relevant_docs(question)

    conversation = []
    if event.get("thread_ts"):
        result = client.conversations_replies(
            channel=channel,
            ts=thread_ts
        )
        for msg in result["messages"]:
            if msg.get("bot_id"):
                conversation.append({
                    "role": "assistant",
                    "content": msg.get("text", "")
                })
            elif msg.get("user") and msg.get("text"):
                conversation.append({
                    "role": "user",
                    "content": msg.get("text", "")
                })
    else:
        conversation.append({
            "role": "user",
            "content": question
        })

    answer = ask_claude_with_history(conversation, docs)
    send_with_feedback(say, answer, question, user, thread_ts)

@app.event("message")
def handle_message(event, say):
    user = event.get("user")
    text = event.get("text", "")
    subtype = event.get("subtype")

    if subtype in ["message_changed", "bot_message"] or not user:
        return

    if event.get("channel_type") == "im":
        say(text="Let me look that up for you... 🔍")
        docs = search_relevant_docs(text)
        answer = ask_claude(text, docs)
        send_with_feedback(say, answer, text, user)

@app.action("feedback_helpful")
def handle_helpful(ack, body, client):
    ack()
    data = json.loads(body["actions"][0]["value"])
    user = data["user"]
    client.chat_postMessage(
        channel=ADMIN_CHANNEL,
        text=f"✅ *Positive feedback received!*\n*User:* <@{user}>\n*Question:* {data['question']}"
    )
    client.chat_postMessage(
        channel=body["channel"]["id"],
        thread_ts=body["message"]["ts"],
        text="Thanks for the feedback! Glad I could help. 😊"
    )

@app.action("feedback_wrong")
def handle_wrong(ack, body, client):
    ack()
    data = json.loads(body["actions"][0]["value"])

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "feedback_modal",
            "private_metadata": json.dumps({
                "question": data["question"],
                "answer": data["answer"],
                "user": data["user"],
                "channel": body["channel"]["id"]
            }),
            "title": {"type": "plain_text", "text": "Report an Issue"},
            "submit": {"type": "plain_text", "text": "Send Feedback"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Sorry the answer wasn't helpful! Please describe what was wrong or missing:"}
                },
                {
                    "type": "input",
                    "block_id": "feedback_input",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "feedback_text",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "e.g. The document is missing the HR contact email..."}
                    },
                    "label": {"type": "plain_text", "text": "What was wrong or missing?"}
                }
            ]
        }
    )

@app.view("feedback_modal")
def handle_feedback_submission(ack, body, client):
    ack()
    user = body["user"]["id"]
    feedback_text = body["view"]["state"]["values"]["feedback_input"]["feedback_text"]["value"]
    metadata = json.loads(body["view"]["private_metadata"])

    client.chat_postMessage(
        channel=ADMIN_CHANNEL,
        text=f"⚠️ *Incomplete/Wrong answer flagged!*\n*User:* <@{user}>\n*Original Question:* {metadata['question']}\n*What was wrong:* {feedback_text}\n\n*Action needed:* Update the relevant document in Google Drive."
    )
    client.chat_postMessage(
        channel=metadata["channel"],
        text=f"<@{user}> Thanks for the feedback! The admin team has been notified and will update the documents. 🙏"
    )

print("TIA is starting with RAG enabled...")
print("Connecting to Pinecone...")
print("Ready! TIA is now using smart document search.")

def start_bot():
    while True:
        try:
            print("TIA - Tejas Information Assistant is starting...")
            handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
            handler.start()
        except Exception as e:
            print(f"TIA crashed: {e}")
            print("Restarting in 10 seconds...")
            time.sleep(10)

if __name__ == "__main__":
    start_bot()
    