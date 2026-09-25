#!/usr/bin/env python3
"""Mock conserver endpoint for testing FreeSWITCH + vCon pipeline.

Accepts POST /api/vcons to receive vCon JSON and saves to disk.
Provides GET /api/vcons/{uuid} to retrieve saved vCons.
"""

import json
import os
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock vCon Conserver", version="0.1.0")

VCON_STORAGE_DIR = os.environ.get("VCON_STORAGE_DIR", "./vcons")
os.makedirs(VCON_STORAGE_DIR, exist_ok=True)

# In-memory index
vcon_index: dict[str, str] = {}  # uuid -> filepath


def _load_index():
    """Load existing vCons from disk into index."""
    for filename in os.listdir(VCON_STORAGE_DIR):
        if filename.endswith(".json"):
            uuid = filename.replace(".json", "")
            vcon_index[uuid] = os.path.join(VCON_STORAGE_DIR, filename)


@app.on_event("startup")
async def startup():
    _load_index()
    print(f"Mock conserver started. Storage: {VCON_STORAGE_DIR}")
    print(f"Loaded {len(vcon_index)} existing vCons")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "mock-conserver", "vcon_count": len(vcon_index)}


@app.post("/api/vcons")
async def create_vcon(request: Request):
    """Receive and store a vCon."""
    try:
        vcon_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    uuid = vcon_data.get("uuid", "")
    if not uuid:
        raise HTTPException(status_code=400, detail="vCon missing uuid field")

    filepath = os.path.join(VCON_STORAGE_DIR, f"{uuid}.json")
    with open(filepath, "w") as f:
        json.dump(vcon_data, f, indent=2, default=str)

    vcon_index[uuid] = filepath

    parties = vcon_data.get("parties", [])
    dialog_count = len(vcon_data.get("dialog", []))
    analysis_count = len(vcon_data.get("analysis", []))

    print(f"[{datetime.now().isoformat()}] Received vCon: {uuid}")
    print(f"  Parties: {len(parties)}, Dialogs: {dialog_count}, Analysis: {analysis_count}")
    for i, party in enumerate(parties):
        tel = party.get("tel", party.get("mailto", "unknown"))
        name = party.get("name", "")
        print(f"  Party {i}: {tel} ({name})")

    return JSONResponse(
        status_code=201,
        content={"uuid": uuid, "status": "stored", "path": filepath},
    )


@app.get("/api/vcons")
async def list_vcons():
    """List all stored vCons."""
    vcons = []
    for uuid, filepath in vcon_index.items():
        try:
            with open(filepath) as f:
                data = json.load(f)
            vcons.append({
                "uuid": uuid,
                "created_at": data.get("created_at", ""),
                "parties": len(data.get("parties", [])),
                "dialogs": len(data.get("dialog", [])),
            })
        except Exception:
            vcons.append({"uuid": uuid, "error": "failed to read"})
    return {"vcons": vcons, "total": len(vcons)}


@app.get("/api/vcons/{uuid}")
async def get_vcon(uuid: str):
    """Retrieve a stored vCon by UUID."""
    if uuid not in vcon_index:
        raise HTTPException(status_code=404, detail=f"vCon {uuid} not found")

    with open(vcon_index[uuid]) as f:
        return json.load(f)


@app.delete("/api/vcons/{uuid}")
async def delete_vcon(uuid: str):
    """Delete a stored vCon."""
    if uuid not in vcon_index:
        raise HTTPException(status_code=404, detail=f"vCon {uuid} not found")

    os.remove(vcon_index[uuid])
    del vcon_index[uuid]
    return {"status": "deleted", "uuid": uuid}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting mock conserver on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
