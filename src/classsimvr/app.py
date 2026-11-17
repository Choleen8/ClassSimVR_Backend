from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .routes.behavior import router as behavior_router
from .routes.health import router as health_router
from .routes.personas import router as persona_router
from .routes.sessions import router as session_router
from .routes.recordings import router as recording_router
from .routes.assessment import router as assessment_router
from .routes.turns import router as turn_router
from .routes.microphone import microphone_manager, router as microphone_router
from .routes.students import router as student_registry_router
from .routes.websocket import router as websocket_router
from .routes.auth import router as auth_router

app = FastAPI(title="ClassSim VR API")
audio_dir = Path(__file__).resolve().parents[2] / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=audio_dir), name="audio")
app.include_router(behavior_router)
app.include_router(health_router)
app.include_router(persona_router)
app.include_router(session_router)
app.include_router(recording_router)
app.include_router(assessment_router)
app.include_router(turn_router)
app.include_router(microphone_router)
app.include_router(student_registry_router)
app.include_router(websocket_router)
app.include_router(auth_router)


@app.on_event("shutdown")
def release_microphone() -> None:
    microphone_manager.shutdown()
