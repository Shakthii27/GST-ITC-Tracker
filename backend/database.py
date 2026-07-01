from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "invoice_tracker")
print("MONGO_URI =", os.getenv("MONGO_URI"))
print("DB_NAME =", os.getenv("DB_NAME"))
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

invoices_col = db["invoices"]
payments_col = db["payments"]

def get_invoice_totals():
    """Aggregate total invoiced amount per vendor (by gstin)."""
    pipeline = [
        {
            "$group": {
                "_id": "$gstin",
                "vendor_name": {"$first": "$vendor_name"},
                "total_invoiced": {"$sum": "$total_amount"},
                "invoice_count": {"$sum": 1},
                "invoices": {
                    "$push": {
                        "invoice_no": "$invoice_no",
                        "date": "$date",
                        "total_amount": "$total_amount",
                        "source_file": "$source_file"
                    }
                }
            }
        }
    ]
    return list(invoices_col.aggregate(pipeline))

def get_payment_totals():
    """Aggregate total paid amount per vendor (by gstin)."""
    pipeline = [
        {
            "$group": {
                "_id": "$gstin",
                "vendor_name": {"$first": "$vendor_name"},
                "total_paid": {"$sum": "$amount_paid"},
                "payment_count": {"$sum": 1},
                "source_files": {"$addToSet": "$source_file"}
            }
        }
    ]
    return list(payments_col.aggregate(pipeline))

def get_all_invoices():
    docs = list(invoices_col.find({}, {"_id": 0}))
    return docs

def get_all_payments():
    docs = list(payments_col.find({}, {"_id": 0}))
    return docs

def insert_invoices(invoice_list: list):
    if invoice_list:
        invoices_col.insert_many(invoice_list)

def insert_payments(payment_list: list):
    if payment_list:
        payments_col.insert_many(payment_list)

def reset_all():
    invoices_col.delete_many({})
    payments_col.delete_many({})