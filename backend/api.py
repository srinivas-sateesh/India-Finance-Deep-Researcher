from app.api.routes import app  # noqa: F401

if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("BACKEND_PORT", 8001)),
        reload=True,
    )
