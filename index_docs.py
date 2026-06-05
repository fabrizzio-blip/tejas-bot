import os
import io
import json
from dotenv import load_dotenv
from pinecone import Pinecone
import voyageai
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
import fitz
from docx import Document

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CREDENTIALS_FILE = "credentials.json"
FOLDER_ID = "11_WUZ0BmlAugr5KiJpwNmx4QqnojgqM4"
PINECONE_API_KEY = "pcsk_36APWc_MhxapzxeUveaZu1CCmSQgSaRsHgFNgy1d7777wkZgYs2PNWFgRULPmYniwWTtdy"
VOYAGE_API_KEY = "pa-XfIYHO-YOtcroKMitig66-uAV4fUKmUPnrO69gYojMd"

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

def get_all_files(service, folder_id):
    files = []
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, webViewLink)"
    ).execute()
    items = results.get("files", [])
    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            files.extend(get_all_files(service, item["id"]))
        else:
            files.append(item)
    return files

def read_drive_file(service, file):
    mime = file["mimeType"]
    file_id = file["id"]
    try:
        if mime == "application/vnd.google-apps.document":
            content = service.files().export(
                fileId=file_id, mimeType="text/plain").execute()
            return content.decode("utf-8", errors="ignore")
        elif mime == "application/vnd.google-apps.presentation":
            content = service.files().export(
                fileId=file_id, mimeType="text/plain").execute()
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
    except Exception as e:
        print(f"Could not read {file['name']}: {e}")
        return ""

def chunk_text(text, chunk_size=1000, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

print("Connecting to Pinecone...")
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index("tia-docs")

print("Connecting to Voyage AI...")
vo = voyageai.Client(api_key=VOYAGE_API_KEY)

print("Connecting to Google Drive...")
service = get_drive_service()
files = get_all_files(service, FOLDER_ID)
print(f"Found {len(files)} files")

vectors = []
for i, file in enumerate(files):
    print(f"Processing {i+1}/{len(files)}: {file['name']}")
    content = read_drive_file(service, file)
    if not content.strip():
        continue
    
    chunks = chunk_text(content)
    for j, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            embedding = vo.embed(
                [chunk],
                model="voyage-3",
                input_type="document"
            ).embeddings[0]
            
            vectors.append({
                "id": f"{file['id']}_{j}",
                "values": embedding,
                "metadata": {
                    "file_name": file["name"],
                    "file_id": file["id"],
                    "web_view_link": file.get("webViewLink", ""),
                    "chunk_index": j,
                    "text": chunk[:500]
                }
            })
            
            if len(vectors) >= 100:
                index.upsert(vectors=vectors)
                print(f"Uploaded batch of {len(vectors)} vectors")
                vectors = []
                
        except Exception as e:
            print(f"Error embedding chunk: {e}")

if vectors:
    index.upsert(vectors=vectors)
    print(f"Uploaded final batch of {len(vectors)} vectors")

print("✅ All documents indexed successfully!")
stats = index.describe_index_stats()
print(f"Total vectors in index: {stats.total_vector_count}")
