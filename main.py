import json
from typing import List
import uuid
from datetime import datetime, timedelta
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from models import Question, Contest, Token, User, ConnectionManager, Room
from fastapi.middleware.cors import CORSMiddleware
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from utils import get_next_sequence_value
from dotenv import load_dotenv
from awsbucket import S3_BUCKET_NAME, s3_client
from database import collection, db_contests, users, db_admins, db_rooms
from auth import create_access_token, get_current_user, get_current_active_user, admin_auth, ACCESS_TOKEN_EXPIRE_MINUTES, user_auth
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
import bcrypt
from typing import List, Dict, Optional

load_dotenv()

app = FastAPI()

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Cross-Origin-Opener-Policy"],
)


manager = ConnectionManager()

contests: Dict[str, Contest] = {}
rooms: Dict[str, Room] = {}


@app.websocket("/ws/room/{room_name}/user/{user_name}")
async def websocket_endpoint(websocket: WebSocket, room_name: str, user_name: str):
    await manager.connect(websocket, room_name, user_name)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)  # Ensure we correctly parse JSON
            except json.JSONDecodeError:
                await websocket.send_text("Error: Received data is not in JSON format.")
                continue
            user = users.find_one({"username": user_name})
            if user:
                message_with_username = {"type": "message", "data": f"{user['username']}: {message['message']}"}
                await manager.user_broadcast(message_with_username)
            await manager.broadcast_json({"type": "message", "data": f"Room {room_name} {user_name}: {message['message']}"}, room_name)
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_name, user_name)
        await manager.broadcast_active_users(room_name)
        await manager.broadcast_json({"type": "notification", "data": f"Room {room_name}: A user left the chat"}, room_name)


@app.post("/rooms/", response_model=Room)
async def create_room(room: Room):
    room_dict = room.dict()
    room_dict["_id"] = await get_next_sequence_value("roomId")
    result = db_rooms.insert_one(room_dict)
    return room_dict

@app.get("/rooms/", response_model=List[Room])
async def get_rooms():
    rooms = list(db_rooms.find({}))
    return rooms

@app.get("/rooms/{room_name}")
async def get_room(room_name: str):
    room = db_rooms.find_one({'name': room_name})
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@app.post("/register")
async def register_user(user: User):

    existing_user = users.find_one({"username": user.username})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    

    hashed_password = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())

    new_user = {
        "username": user.username,
        "email": user.email,
        "password": hashed_password
    }
    result = users.insert_one(new_user)
    return {"message": "User registered successfully"}


@app.post("/register_admin")
async def register_user(user: User):

    existing_user = db_admins.find_one({"username": user.username})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    

    hashed_password = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())

    new_user = {
        "username": user.username,
        "email": user.email,
        "password": hashed_password
    }
    result = db_admins.insert_one(new_user)
    return {"message": "Admin registered successfully"}


@app.post("/token_user", response_model=Token)
async def login_user_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = user_auth(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user['username']}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me/", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = admin_auth(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user['username']}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/uploadimage/")
async def upload_image(file: UploadFile = File(...)):
    try:
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        file_contents = await file.read()
        s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=unique_filename, Body=file_contents)
        return {"image_url": unique_filename, "status": "Successful"}
    except (NoCredentialsError, PartialCredentialsError):
        raise HTTPException(status_code=500, detail="AWS credentials not configured properly")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.post("/questions/", response_model=Question)
async def create_question(question: Question):
    question_dict = question.dict()
    question_dict["_id"] = await get_next_sequence_value("questionId")
    result = collection.insert_one(question_dict)
    return question_dict

@app.get("/questions/", response_model=List[Question])
async def get_questions():
    questions = list(collection.find({}))
    return questions

@app.get("/questions/{question_id}", response_model=Question)
async def get_question(question_id: int):
    question = collection.find_one({"_id": question_id})
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return question

@app.delete("/questions/{question_id}")
async def delete_question(question_id: int):
    result = collection.delete_one({"_id": question_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Question not found")
    return {"message": "Question deleted"}


@app.post("/contests/", response_model=Contest)
async def create_contest(contest: Contest):
    contest_dict = contest.dict()
    contest_dict["_id"] = await get_next_sequence_value("contestId")
    result = db_contests.insert_one(contest_dict)
    return contest_dict

@app.get("/contests/", response_model=List[Contest])
async def get_contests():
    contests = list(db_contests.find({}))
    return contests

@app.get("/contests/{contest_id}", response_model=Contest)
async def get_contest(contest_id: int):
    contest = db_contests.find_one({"_id": contest_id})
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest not found")
    return contest
