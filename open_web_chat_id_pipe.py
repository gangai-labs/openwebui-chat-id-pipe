"""
title: Chat ID Tracker
author: gangAI team
author_url: https://github.com/gangai-labs
version: 0.4
description: Tracks chat_id per conversation. Reuses chat_id if conversation continues, even across multiple requests. Session IDs assigned if missing.Stop button works.Will stop backend too 
"""


from pydantic import BaseModel, Field
from typing import Optional
import uuid
import time
import hashlib
import requests
from fastapi import HTTPException


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0)
        backend_url: str = Field(default="http://host.docker.internal:8081")
        stop_endpoint: str = Field(default="/stop")

    def __init__(self):
        self.valves = self.Valves()
        self._conversation_store = {}
        self._active_streams = {}  # Track active streams by session_id

    @staticmethod
    def _hash_user_messages(messages: list) -> str:
        """Hash only the first user message to identify the conversation."""
        for message in messages:
            if message.get("role") == "user":
                return hashlib.sha256(message.get("content", "").encode()).hexdigest()
        return hashlib.sha256("".encode()).hexdigest()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        messages = body.get("messages", [])
        conv_hash = self._hash_user_messages(messages)

        # Check if chat_id is provided and valid
        provided_chat_id = body.get("chat_id")
        if provided_chat_id and provided_chat_id in self._conversation_store.values():
            body["chat_id"] = provided_chat_id
        else:
            if conv_hash in self._conversation_store:
                body["chat_id"] = self._conversation_store[conv_hash]
            else:
                chat_id = str(uuid.uuid4())
                body["chat_id"] = chat_id
                self._conversation_store[conv_hash] = chat_id

        # Assign session_id if missing
        session_id = body.get("session_id")
        if not session_id:
            session_id = f"session-{int(time.time())}"
            body["session_id"] = session_id

        # Store session info for potential stop requests
        self._active_streams[session_id] = {
            "chat_id": body["chat_id"],
            "start_time": time.time(),
            "active": True,
        }

        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Clean up completed streams
        session_id = body.get("session_id")
        if session_id and session_id in self._active_streams:
            # Check if this is the end of the stream
            if body.get("done", False) or body.get("stop_reason"):
                self._active_streams[session_id]["active"] = False
                self._active_streams[session_id]["end_time"] = time.time()

        return body

    async def handle_stop(
        self, session_id: str, __user__: Optional[dict] = None
    ) -> dict:
        """Handle stop requests from OpenWebUI"""
        if session_id not in self._active_streams:
            return {"status": "error", "message": "Session not found"}

        stream_info = self._active_streams[session_id]

        try:
            # Send stop request to your backend
            stop_payload = {"session_id": session_id, "chat_id": stream_info["chat_id"]}

            response = requests.post(
                f"{self.valves.backend_url}{self.valves.stop_endpoint}",
                json=stop_payload,
                timeout=5,
            )

            if response.status_code == 200:
                stream_info["active"] = False
                stream_info["stopped_by_user"] = True
                stream_info["stop_time"] = time.time()
                return {"status": "success", "message": "Stream stopped"}
            else:
                return {
                    "status": "error",
                    "message": f"Backend returned {response.status_code}",
                }

        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": f"Failed to reach backend: {str(e)}"}

