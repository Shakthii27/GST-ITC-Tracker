from pymongo import MongoClient

uri = "mongodb+srv://invoiceuser:Invoice123456@cluster0.c6lboeu.mongodb.net/?appName=Cluster0"

client = MongoClient(uri)

try:
    client.admin.command("ping")
    print("MongoDB Connected Successfully!")
except Exception as e:
    print("ERROR:", e)