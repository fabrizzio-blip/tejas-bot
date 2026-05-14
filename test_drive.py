from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
service = build("drive", "v3", credentials=creds)

results = service.files().list(
    q="'1-MrLLnYFZWxymkmB9HJlNkr4V9A-tCSS' in parents",
    fields="files(name, mimeType, webViewLink)"
).execute()

for f in results.get("files", []):
    print(f["name"], "->", f["mimeType"])
    