from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
service = build("drive", "v3", credentials=creds)

results = service.files().list(
    q="'11_WUZ0BmlAugr5KiJpwNmx4QqnojgqM4' in parents",
    fields="files(name, webViewLink)"
).execute()

for f in results.get("files", [])[:5]:
    print(f["name"], "->", f.get("webViewLink", "NO LINK"))
