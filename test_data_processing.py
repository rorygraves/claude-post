#!/usr/bin/env python3
"""Runnable demonstration of declarative collection transforms."""

import pandas as pd

from email_client.data_processing import DataStore


def demonstrate_data_processing() -> None:
    """Create a collection and calculate sender counts without executing Python input."""
    datastore = DataStore()
    sample_emails = [
        {"id": "1", "from": "alice@example.com", "subject": "Meeting tomorrow", "date": "2024-01-15"},
        {"id": "2", "from": "bob@example.com", "subject": "Project update", "date": "2024-01-15"},
        {"id": "3", "from": "alice@example.com", "subject": "Follow up", "date": "2024-01-16"},
        {"id": "4", "from": "charlie@example.com", "subject": "New proposal", "date": "2024-01-16"},
        {"id": "5", "from": "alice@example.com", "subject": "Meeting notes", "date": "2024-01-17"},
        {"id": "6", "from": "bob@example.com", "subject": "Budget review", "date": "2024-01-17"},
        {"id": "7", "from": "alice@example.com", "subject": "Quick question", "date": "2024-01-18"},
    ]

    metadata = datastore.create(pd.DataFrame(sample_emails), "email_search_results")
    collection_id = metadata["id"]
    print(f"Created {metadata['name']}: {metadata['shape']}")
    print(f"Preview: {datastore.preview(collection_id, rows=3)['preview']}")

    datastore.update(collection_id, "select_columns", {"columns": ["from"]})
    datastore.update(collection_id, "group_count", {"columns": ["from"]})
    datastore.update(collection_id, "sort", {"by": "count", "ascending": False})

    results = datastore.fetch(collection_id, format="records")
    print("Sender counts:")
    for row in results["data"]:
        print(f"  {row['from']}: {row['count']}")

    datastore.delete(collection_id)


if __name__ == "__main__":
    demonstrate_data_processing()
