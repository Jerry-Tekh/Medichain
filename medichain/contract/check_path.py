from pathlib import Path
import os

npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"
candidate = npm_global / "genlayer" / "node_modules" / "genlayer-js" / "dist" / "index.js"
print(f"APPDATA: {os.environ.get('APPDATA', '')}")
print(f"Candidate: {candidate}")
print(f"Exists: {candidate.exists()}")
