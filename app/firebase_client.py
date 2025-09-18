# app/firebase_client.py
import os, json
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

# 1) Ensure .env is loaded BEFORE reading env vars
load_dotenv()

# 2) Support three ways to supply credentials:
#    A) FIREBASE_CREDENTIALS_JSON  (full JSON as one line in .env)
#    B) FIREBASE_CREDENTIALS_PATH  (path to the JSON file)
#    C) GOOGLE_APPLICATION_CREDENTIALS (standard GCP env var)
cred_obj = None

cred_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
project_id = os.getenv("FIREBASE_PROJECT_ID")

if cred_json:
    try:
        cred_obj = credentials.Certificate(json.loads(cred_json))
    except json.JSONDecodeError as e:
        raise RuntimeError("FIREBASE_CREDENTIALS_JSON is not valid JSON (remember to keep it on one line, with \\n inside the key).") from e
elif cred_path and os.path.exists(cred_path):
    cred_obj = credentials.Certificate(cred_path)
else:
    # Last resort: try default app creds (works on Cloud Run/GCE if service account attached)
    try:
        cred_obj = credentials.ApplicationDefault()
    except Exception:
        raise RuntimeError(
            "No Firebase credentials found. Set FIREBASE_CREDENTIALS_JSON (preferred) "
            "or FIREBASE_CREDENTIALS_PATH/GOOGLE_APPLICATION_CREDENTIALS to a JSON key file."
        )

# Initialize app (projectId optional but helpful locally)
if firebase_admin._apps:
    app = firebase_admin.get_app()
else:
    app = firebase_admin.initialize_app(cred_obj, {"projectId": project_id} if project_id else None)

db = firestore.client()
