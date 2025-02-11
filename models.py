from collections import defaultdict
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from database import db_rooms


class Question(BaseModel):
    image_url: Optional[str] = None
    question: str
    options: List[str]
    correct_answer: str
    time_limit: int


class Contest(BaseModel):
    name: str
    description: str
    question_ids: List[str]
    time_limit: int


class SuperContest(BaseModel):
    name: str
    description: str
    question_ids: List[str]
    questions: List[Question]


class User(BaseModel):
    username: str
    email: Optional[str] = None
    password: str
    full_name: Optional[str] = None
    disabled: Optional[bool] = None


class Submission(BaseModel):
    username: str
    room_name: str
    question_name: str
    selected_option: str
    is_correct: bool
    submitted_at: datetime


class Rating(BaseModel):
    username: str
    rating: float
    room_name: str


class Room(BaseModel):
    name: str
    contest_name: str
    start_time: datetime = Field(default_factory=datetime.utcnow)


class Message(BaseModel):
    username: str
    content: str
    room_name: str


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, room_name: str, websocket: WebSocket):
        await websocket.accept()
        if room_name not in self.active_connections:
            self.active_connections[room_name] = []
        self.active_connections[room_name].append(websocket)

    def disconnect(self, room_name: str, websocket: WebSocket):
        self.active_connections[room_name].remove(websocket)
        if not self.active_connections[room_name]:
            del self.active_connections[room_name]

    async def broadcast(self, message: str, room_name: str):
        if room_name in self.active_connections:
            for connection in self.active_connections[room_name]:
                try:
                    await connection.send_text(message)  # send_text
                except WebSocketDisconnect:
                    self.disconnect(room_name, connection)


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserInDB(User):
    hashed_password: str
