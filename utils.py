from database import counters

async def get_next_sequence_value(sequence_name: str):
    sequence_document = counters.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=True
    )
    return sequence_document["sequence_value"]
