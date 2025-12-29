#!/usr/bin/env python3
"""Run the Channel Discovery Service"""
import uvicorn
from config import API_HOST, API_PORT

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=True,
        log_level="info"
    )
