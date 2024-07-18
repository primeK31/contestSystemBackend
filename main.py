import json
from typing import List
import uuid
from datetime import datetime, timedelta
from PyPDF2 import PdfReader
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from models import Question, Contest, Rating, Token, User, ConnectionManager, Room, SuperContest
from fastapi.middleware.cors import CORSMiddleware
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from utils import get_next_sequence_value
from dotenv import load_dotenv
from awsbucket import AWS_REGION, S3_BUCKET_NAME, s3_client
from database import collection, db_contests, users, db_admins, db_rooms, super_contests, db_ratings, db_file_urls
from auth import create_access_token, get_current_user, get_current_active_user, admin_auth, ACCESS_TOKEN_EXPIRE_MINUTES, user_auth
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
import bcrypt
from typing import List, Dict, Optional
from openai import OpenAI
import asyncio
import fitz

load_dotenv()

app = FastAPI()

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://contest-system-frontend.vercel.app",
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

aiclient = OpenAI()

contests: Dict[str, Contest] = {}
rooms: Dict[str, Room] = {}


@app.websocket("/ws/room/{room_name}/user/{username}")
async def websocket_endpoint(websocket: WebSocket, room_name: str, username: str):
    await manager.connect(room_name, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(f"{username}: {data}", room_name)
    except WebSocketDisconnect:
        manager.disconnect(room_name, websocket)


def convert_object_id(data):
    if isinstance(data, list):
        for item in data:
            if "_id" in item:
                item["_id"] = str(item["_id"])
    elif isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
    return data


@app.get("/get_room_rating/{room_name}")
async def get_room_rating(room_name: str):
    ratings_list = list(db_ratings.find({"room_name": room_name}).sort("rating", -1))
    ratings_list = convert_object_id(ratings_list)
    await manager.broadcast(json.dumps(ratings_list), room_name)
    return ratings_list


@app.put("/update_rating/")
async def update_rating(rating: Rating):
    existing_rating = db_ratings.find_one({"username": rating.username, "room_name": rating.room_name})
    if not existing_rating:
        raise HTTPException(status_code=404, detail="Rating not found")

    db_ratings.update_one(
        {"username": rating.username, "room_name": rating.room_name},
        {"$set": {"rating": rating.rating}}
    )

    ratings_list = list(db_ratings.find({"room_name": rating.room_name}).sort("rating", -1))
    ratings_list = convert_object_id(ratings_list)
    await manager.broadcast(json.dumps(ratings_list), rating.room_name)
    return ratings_list


@app.post("/rate/")
async def rate_item(rating: Rating):
    print(rating)
    existing_rating = db_ratings.find_one({"username": rating.username, "room_name": rating.room_name})
    if existing_rating:
        new_rating_value = existing_rating["rating"] + rating.rating
        db_ratings.update_one(
            {"username": rating.username, "room_name": rating.room_name},
            {"$set": {"rating": new_rating_value}}
        )
        updated_rating = Rating(username=rating.username, room_name=rating.room_name, rating=new_rating_value)
    else:
        db_ratings.insert_one(rating.dict())
        updated_rating = rating
    
    await get_room_rating(rating.room_name)
    return {"username": updated_rating.username, "rating": updated_rating.rating}


@app.post("/quiz/")
async def create_quiz(quiz: SuperContest):
    room_dict = jsonable_encoder(quiz)
    room_dict["_id"] = await get_next_sequence_value("superContestId")
    result = super_contests.insert_one(room_dict)
    return room_dict


@app.post("/rooms/", response_model=Room)
async def create_room(room: Room):
    room_dict = room.dict()
    room_dict["_id"] = await get_next_sequence_value("roomId")
    result = db_rooms.insert_one(room_dict)
    return room_dict

@app.get("/supercontests/", response_model=List[SuperContest])
async def get_supercontest():
    contests = list(super_contests.find({}))
    return contests

@app.post("/supercontests/", response_model=SuperContest)
async def create_supercontest(contest: SuperContest):
    room_dict = contest.dict()
    room_dict["_id"] = await get_next_sequence_value("superContestId")
    result = super_contests.insert_one(room_dict)
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
        res = s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=unique_filename, Body=file_contents, ACL="public-read")
        image_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{unique_filename}"
        db_file_urls.insert_one({"file_url": unique_filename})
        return {"image_url": image_url, "status": "Successful"}
    except (NoCredentialsError, PartialCredentialsError):
        raise HTTPException(status_code=500, detail="AWS credentials not configured properly")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.post("/aigen/")
async def generate_contest(file_name: str = Form(None), prompt: str = Form(None)):
    vector_data = db_file_urls.find_one({"file_url": file_name})
    if not vector_data:
        raise HTTPException(status_code=404, detail="Vector not found")
    
    response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=file_name)

    file_contents = response['Body'].read()

    pdf_document = fitz.open(stream=file_contents, filetype="pdf")

    pdf_text = ""
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        pdf_text += page.get_text()
    example = '{"name": "lol", "description": "string", "question_ids": ["3+3="], "questions": [{"image_url": null, "question": "3+3=", "options": ["1", "2", "3", "6"], "correct_answer": "6"}], "time_limit": 5}'
    response = aiclient.chat.completions.create(
        model="gpt-4o",
        response_format={ "type": "json_object" },
        messages=[
            {
                "role": "system",
                "content": f'You are creater of my website api jsons. As examples you can use {example}',
            },
            {
                "role": "user",
                "content": f"{prompt} based on this content {pdf_text}"
            },
            {
                "role": "user",
                "content": f"output in json format without your comments using this example where question_ids is list which contains questions names and give time_limit 5, also give a test name in 'name' column and give a short description of the quiz in 'decription' column"
            }
        ]
    )
    return {"text": response.choices[0].message.content}

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
