from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os 

load_dotenv()

uri = os.getenv('MONGO_URI')

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)


db = client["regular"]
collection = db["regular"]
counters = db["counters"]
db_contests = db["contest"]
users = db["users"]
db_admins = db["admins"]
db_rooms = db["rooms"]
super_contests = db["super_contests"]
db_ratings = db["ratings"]
db_file_urls = db['file_urls']
db_messages = db['messages']
db_submissions = db['submissions']
