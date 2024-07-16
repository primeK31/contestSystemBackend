from collections import defaultdict
from pydantic import BaseModel
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from database import db_rooms


class Question(BaseModel):
    image_url: Optional[str] = None
    question: str
    options: List[str]
    correct_answer: str


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
    time_limit: int


class User(BaseModel):
    username: str
    email: Optional[str] = None
    password: str
    full_name: Optional[str] = None
    disabled: Optional[bool] = None


class Rating(BaseModel):
    username: str
    rating: float
    room_name: str


class Room(BaseModel):
    name: str
    contests: Contest
    users_list: Optional[List[User]] = None


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.active_users: Dict[str, List[str]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, room_name: str, user_name: str):
        await websocket.accept()
        self.active_connections[room_name].append(websocket)
        self.active_users[room_name].append(user_name)
        await self.broadcast_active_users(room_name)

    def disconnect(self, websocket: WebSocket, room_name: str, user_name: str):
        self.active_connections[room_name].remove(websocket)
        self.active_users[room_name].remove(user_name)
        if not self.active_connections[room_name]:
            del self.active_connections[room_name]
            del self.active_users[room_name]

    async def broadcast(self, message: str, room_name: str):
        if room_name in self.active_connections:
            for connection in self.active_connections[room_name]:
                await connection.send_text(message)

    async def user_broadcast(self, message: str):
        for room_connections in self.active_connections.values():
            for connection in room_connections:
                await connection.send_text(message)

    async def broadcast_active_users(self, room_name: str):
        active_users = ", ".join(self.active_users[room_name])
        message = f"Active users: {active_users}"
        await self.broadcast(message, room_name)


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserInDB(User):
    hashed_password: str
