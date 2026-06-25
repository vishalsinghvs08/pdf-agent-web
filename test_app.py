#!/usr/bin/env python3
"""Test the PDF Agent web app boots and serves pages."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ["SECRET_KEY"] = "test-key"
os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test"
os.environ["STRIPE_SECRET_KEY"] = "sk_test"

from app import app

with app.test_client() as c:
    # 1. Home page loads
    r = c.get("/")
    assert r.status_code == 200, f"Home returned {r.status_code}"
    assert b"PDF Agent" in r.data or b"PDF" in r.data
    print("✅ / (home) -> 200 OK")

    # 2. Pricing page loads
    r = c.get("/pricing")
    assert r.status_code == 200, f"Pricing returned {r.status_code}"
    assert b"Pro" in r.data
    print("✅ /pricing -> 200 OK")

    # 3. Operations API
    r = c.get("/operations")
    assert r.status_code == 200
    import json
    data = json.loads(r.data)
    assert "free" in data
    assert "pro" in data
    print(f"✅ /operations -> {len(data['free'])} free, {len(data['pro'])} pro ops")

    # 4. Upload endpoint (no file)
    r = c.post("/upload", data={})
    assert r.status_code == 400
    print("✅ /upload (empty) -> 400")

    # 5. Process without files
    r = c.post("/process", data={"op": "info"})
    assert r.status_code == 400
    print("✅ /process (no files) -> 400")

    # 6. Pro op without pro session
    r = c.post("/process", data={"op": "ocr", "file_ids": ["test"]})
    assert r.status_code == 402
    assert b"pro_required" in r.data or b"required" in r.data
    print("✅ /process (pro op, no pro) -> 402")

print("\n🎉 All basic tests passed!")
