#!/usr/bin/env python3
"""Quick smoke test of the PDF Agent web app."""
import os, sys
sys.path.insert(0, "/Users/vishalsingh/Desktop/DeepSeek Projects/pdf-agent-web")
os.environ["SECRET_KEY"] = "test-key"
os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test"
os.environ["STRIPE_SECRET_KEY"] = "sk_test"
os.environ["PORT"] = "8080"

from app import app

print("✅ App imported successfully")

with app.test_client() as c:
    r = c.get("/")
    print(f"✅ GET / → {r.status_code}")
    assert r.status_code == 200

    r = c.get("/pricing")
    print(f"✅ GET /pricing → {r.status_code}")
    assert r.status_code == 200

    r = c.get("/operations")
    print(f"✅ GET /operations → {r.status_code}")
    assert r.status_code == 200

    # Upload a real PDF
    with open("/tmp/test_doc1.pdf", "rb") as f:
        r = c.post("/upload", data={"files": (f, "test_doc1.pdf")})
    print(f"✅ POST /upload → {r.status_code}")
    assert r.status_code == 200
    import json
    upload_data = json.loads(r.data)
    fid = upload_data["files"][0]["id"]
    print(f"   File ID: {fid}")

    # Process: info (free)
    r = c.post("/process", data={"op": "info", "file_ids": fid})
    print(f"✅ POST /process (info) → {r.status_code}")
    data = json.loads(r.data)
    assert data["status"] == "completed"
    print(f"   Pages: {data['data']['page_count']}, Name: {data['data']['filename']}")

    # Process: merge (free)
    r = c.post("/process", data={"op": "merge", "file_ids": [fid, fid]})
    print(f"✅ POST /process (merge) → {r.status_code}")
    data = json.loads(r.data)
    assert data["status"] == "completed"
    print(f"   Merged: {data['summary']}")

    # Process: redact (pro - should be gated)
    r = c.post("/process", data={"op": "redact", "file_ids": fid, "patterns": "SSN"})
    print(f"✅ POST /process (redact, no pro) → {r.status_code}")
    assert r.status_code == 402
    data = json.loads(r.data)
    assert "pro_required" in data.get("error", "")

    # Process: encrypt (pro - should be gated)
    r = c.post("/process", data={"op": "encrypt", "file_ids": fid, "password": "test123"})
    print(f"✅ POST /process (encrypt, no pro) → {r.status_code}")
    assert r.status_code == 402

    # Test with pro session
    with app.test_client() as c2:
        with c2.session_transaction() as sess:
            sess["pro"] = True
        r = c2.post("/process", data={"op": "redact", "file_ids": fid, "patterns": "SSN"})
        print(f"✅ POST /process (redact, WITH pro) → {r.status_code}")
        data = json.loads(r.data)
        print(f"   Result: {data.get('summary', data.get('error', 'unknown'))}")

print("\n🎉 ALL TESTS PASSED!")
