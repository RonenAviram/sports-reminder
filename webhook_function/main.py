import functions_framework
from google.cloud import firestore
import datetime

db = firestore.Client()
TRACKED_EVENTS = {"email.delivered", "email.opened"}

@functions_framework.http
def resend_webhook(request):
    if request.method != "POST":
        return ("Method not allowed", 405)
    try:
        payload = request.get_json(silent=True)
    except Exception:
        return ("Bad request", 400)
    if not payload:
        return ("No payload", 400)
    event_type = payload.get("type", "")
    if event_type not in TRACKED_EVENTS:
        return ("OK", 200)
    data = payload.get("data", {})
    email_id = data.get("email_id", "")
    if not email_id:
        return ("No email_id", 200)
    try:
        docs = db.collection("email_logs").where("resend_email_id", "==", email_id).limit(1).get()
        now = datetime.datetime.utcnow()
        if docs:
            doc_ref = docs[0].reference
            if event_type == "email.delivered":
                doc_ref.update({"delivered_at": now, "status": "delivered"})
            elif event_type == "email.opened":
                doc_ref.update({"opened_at": now})
            print(f"Updated {event_type} for {email_id}")
        else:
            db.collection("email_events").add({"resend_email_id": email_id, "event_type": event_type, "timestamp": now})
            print(f"No match {event_type} for {email_id}")
    except Exception as e:
        print(f"Error {event_type} for {email_id}: {e}")
    return ("OK", 200)
